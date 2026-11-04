"""Synthetic company attendance API used for safe connector development."""

from __future__ import annotations

import hashlib
import threading
from datetime import date, datetime

from app.integrations.provider import (
    AttendanceProviderError,
    AttendanceRecordNotFound,
    AttendanceSourceChanged,
    normalized_now,
)
from app.integrations.schemas import (
    AttendanceChangedEvent,
    AttendanceChangePage,
    AttendanceRangeEvidence,
    AttendanceRecord,
    CompanyCorrectionRequest,
    CorrectionResult,
    PunchEvidence,
    ScheduleEvidence,
)


class MockAttendanceProvider:
    """In-memory company-system simulator with version checks and idempotent writes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records = {
            "tcday_2026-11-03_8F39A": AttendanceRecord(
                record_ref="tcday_2026-11-03_8F39A",
                employee_ref="emp_8F39A",
                version="17",
                # Keep the bundled source record stable between API and worker
                # processes. A newly constructed mock provider must represent
                # the same source version, rather than making existing cases
                # appear stale solely because the process restarted.
                updated_at=datetime.fromisoformat("2026-11-04T09:14:52Z"),
                work_date=date(2026, 11, 3),
                timezone="America/Chicago",
                jurisdiction="US-STD",
                ot_eligible=True,
                schedule=ScheduleEvidence(
                    record_ref="shift_7712",
                    start=datetime.fromisoformat("2026-11-03T08:00:00-06:00"),
                    end=datetime.fromisoformat("2026-11-03T16:30:00-06:00"),
                    planned_break_minutes=30,
                ),
                punches=[
                    PunchEvidence(
                        record_ref="p_1001",
                        kind="IN",
                        at=datetime.fromisoformat("2026-11-03T08:02:00-06:00"),
                        source="device",
                    ),
                    PunchEvidence(
                        record_ref="p_1002",
                        kind="BREAK_START",
                        at=datetime.fromisoformat("2026-11-03T12:00:00-06:00"),
                        source="device",
                    ),
                    PunchEvidence(
                        record_ref="p_1003",
                        kind="BREAK_END",
                        at=datetime.fromisoformat("2026-11-03T12:30:00-06:00"),
                        source="device",
                    ),
                ],
            ),
            "tcday_2026-11-02_8F39A": AttendanceRecord(
                record_ref="tcday_2026-11-02_8F39A",
                employee_ref="emp_8F39A",
                version="9",
                updated_at=datetime.fromisoformat("2026-11-03T00:02:00Z"),
                work_date=date(2026, 11, 2),
                timezone="America/Chicago",
                jurisdiction="US-STD",
                ot_eligible=True,
                schedule=ScheduleEvidence(
                    record_ref="shift_7711",
                    start=datetime.fromisoformat("2026-11-02T08:00:00-06:00"),
                    end=datetime.fromisoformat("2026-11-02T16:30:00-06:00"),
                    planned_break_minutes=30,
                ),
                punches=[
                    PunchEvidence(
                        record_ref="p_0991",
                        kind="IN",
                        at=datetime.fromisoformat("2026-11-02T08:00:00-06:00"),
                        source="device",
                    ),
                    PunchEvidence(
                        record_ref="p_0992",
                        kind="BREAK_START",
                        at=datetime.fromisoformat("2026-11-02T12:00:00-06:00"),
                        source="device",
                    ),
                    PunchEvidence(
                        record_ref="p_0993",
                        kind="BREAK_END",
                        at=datetime.fromisoformat("2026-11-02T12:30:00-06:00"),
                        source="device",
                    ),
                    PunchEvidence(
                        record_ref="p_0994",
                        kind="OUT",
                        at=datetime.fromisoformat("2026-11-02T16:30:00-06:00"),
                        source="device",
                    ),
                ],
            ),
        }
        self._operations: dict[str, CorrectionResult] = {}
        self._operation_fingerprints: dict[str, str] = {}
        self._change_event = AttendanceChangedEvent.model_validate(
            {
                "event_id": "mock-attendance-change-20261104-001",
                "event_type": "attendance.record.changed",
                "occurred_at": "2026-11-04T09:14:52Z",
                "record": {
                    "employee_ref": "emp_8F39A",
                    "record_type": "timecard_day",
                    "record_ref": "tcday_2026-11-03_8F39A",
                    "version": "17",
                    "updated_at": "2026-11-04T09:14:52Z",
                },
            }
        )

    def fetch_attendance_record(
        self, record_ref: str, *, version: str | None = None
    ) -> AttendanceRecord | None:
        with self._lock:
            record = self._records.get(record_ref)
            if record is None:
                return None
            if version is not None and version != record.version:
                raise AttendanceSourceChanged("requested source version is no longer current")
            return record.model_copy(deep=True)

    def fetch_changed_events(self, cursor: str | None) -> AttendanceChangePage:
        if cursor is None:
            return AttendanceChangePage(
                events=[self._change_event.model_copy(deep=True)],
                next_cursor="mock-page-1",
                has_more=False,
            )
        return AttendanceChangePage(events=[], next_cursor=cursor, has_more=False)

    def fetch_attendance_range(
        self, employee_ref: str, start_date: date, end_date: date
    ) -> AttendanceRangeEvidence:
        with self._lock:
            records = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.employee_ref == employee_ref
                and start_date <= record.work_date <= end_date
            ]
        return AttendanceRangeEvidence(
            employee_ref=employee_ref,
            covered_from=start_date,
            covered_through=end_date,
            coverage_complete=True,
            records=sorted(records, key=lambda item: (item.work_date, item.record_ref)),
        )

    def submit_correction(
        self,
        request: CompanyCorrectionRequest,
        *,
        idempotency_key: str,
    ) -> CorrectionResult:
        if not idempotency_key or len(idempotency_key) > 200:
            raise AttendanceProviderError("mock provider received an invalid idempotency key")
        with self._lock:
            request_fingerprint = hashlib.sha256(
                request.model_dump_json().encode("utf-8")
            ).hexdigest()
            existing = self._operations.get(idempotency_key)
            if existing is not None:
                if self._operation_fingerprints[idempotency_key] != request_fingerprint:
                    raise AttendanceProviderError(
                        "idempotency key was reused with a different correction"
                    )
                return existing.model_copy(deep=True)

            record = self._records.get(request.record_ref)
            if record is None:
                raise AttendanceRecordNotFound("source record is unavailable")
            if record.employee_ref != request.employee_ref:
                raise AttendanceProviderError(
                    "correction employee does not match the source record"
                )
            expected_versions = request.expected_source_versions or {
                request.record_ref: request.expected_version
            }
            for source_ref, expected_version in expected_versions.items():
                source_record = self._records.get(source_ref)
                if source_record is None:
                    raise AttendanceRecordNotFound("a reviewed source record is unavailable")
                if source_record.employee_ref != request.employee_ref:
                    raise AttendanceProviderError(
                        "reviewed source records belong to another employee"
                    )
                if source_record.version != expected_version:
                    raise AttendanceSourceChanged("a reviewed source record version changed")
            if record.version != request.expected_version:
                raise AttendanceSourceChanged("source record version changed")

            correction = request.correction
            action = correction.get("action")
            target_ref = correction.get("target_record_ref", request.record_ref)
            target_record = self._records.get(target_ref) if isinstance(target_ref, str) else None
            if target_record is None or target_record.employee_ref != request.employee_ref:
                raise AttendanceProviderError("correction target record is unavailable")
            target_version = correction.get("target_expected_version", request.expected_version)
            if target_record.version != target_version:
                raise AttendanceSourceChanged("correction target version changed")

            if action == "ADD_PUNCH":
                at = correction.get("at")
                kind = correction.get("kind")
                if not isinstance(at, str) or not isinstance(kind, str):
                    raise AttendanceProviderError(
                        "mock provider received an invalid punch correction"
                    )
                punch_time = _parse_offset_time(at)
                digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]
                target_record.punches.append(
                    PunchEvidence(
                        record_ref=f"mock-punch-{digest}",
                        kind=kind,
                        at=punch_time,
                        source="shiftguard-mock",
                    )
                )
            elif action in {"MODIFY_PUNCH", "REMOVE_PUNCH"}:
                punch_ref = correction.get("punch_ref")
                if not isinstance(punch_ref, str):
                    raise AttendanceProviderError("mock provider received an invalid punch target")
                index = next(
                    (
                        i
                        for i, punch in enumerate(target_record.punches)
                        if punch.record_ref == punch_ref
                    ),
                    None,
                )
                if index is None:
                    raise AttendanceRecordNotFound("correction punch no longer exists")
                if action == "REMOVE_PUNCH":
                    target_record.punches.pop(index)
                else:
                    at = correction.get("at")
                    if not isinstance(at, str):
                        raise AttendanceProviderError(
                            "mock provider received an invalid punch time"
                        )
                    target_record.punches[index].at = _parse_offset_time(at)
            elif action == "ADJUST_SCHEDULE":
                schedule_ref = correction.get("schedule_ref")
                end = correction.get("end")
                if (
                    target_record.schedule is None
                    or target_record.schedule.record_ref != schedule_ref
                    or not isinstance(end, str)
                ):
                    raise AttendanceRecordNotFound("correction schedule no longer exists")
                target_record.schedule.end = _parse_offset_time(end)
            elif action == "FLAG_FOR_APPROVAL":
                flag_type = correction.get("flag_type")
                if not isinstance(flag_type, str) or not flag_type:
                    raise AttendanceProviderError("mock provider received an invalid approval flag")
            else:
                raise AttendanceProviderError("mock provider received an unsupported correction")

            digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]
            next_version = (
                str(int(target_record.version) + 1)
                if target_record.version.isdigit()
                else f"{target_record.version}-next"
            )
            target_record.version = next_version
            target_record.updated_at = normalized_now()
            result = CorrectionResult(
                operation_id=f"mock-op-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]}",
                status="applied",
                record_ref=record.record_ref,
                new_version=next_version,
                applied_at=target_record.updated_at,
            )
            self._operations[idempotency_key] = result
            self._operation_fingerprints[idempotency_key] = request_fingerprint
            return result.model_copy(deep=True)

    def close(self) -> None:
        """No external resources are held by the mock provider."""


def _parse_offset_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttendanceProviderError("mock provider received an invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AttendanceProviderError("mock provider requires timestamps with an offset")
    return parsed
