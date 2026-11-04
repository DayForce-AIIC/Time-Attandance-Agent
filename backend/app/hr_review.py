"""Supabase-backed HR case review and approved company writeback."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from app.analysis_worker import (
    _analysis_time,
    _canonical_record_hash,
    _normalize_records,
    _range_hash,
)
from app.errors import BadRequest, NotFound, StateConflict, ValidationFailed
from app.integrations.provider import (
    AttendanceProvider,
    AttendanceProviderError,
    AttendanceProviderUnavailable,
    AttendanceRecordNotFound,
    AttendanceSourceChanged,
)
from app.integrations.schemas import (
    AttendanceRangeEvidence,
    AttendanceRecord,
    CompanyCorrectionRequest,
)
from app.logging_setup import trace_id_var
from app.ops_alerts import OperationalAlertReporter
from app.organization_policies import OrganizationPolicyContext
from app.policies import PolicyBook, get_rule
from app.schemas import (
    Action,
    AuditEventOut,
    AuditVerifyOut,
    Constraints,
    DecisionAction,
    DecisionIn,
    DecisionOut,
    EmployeeOut,
    ExceptionDetail,
    ExceptionOut,
    ExceptionStatus,
    LLMProposal,
    PolicyPassage,
    PolicySnapshotEntry,
    ProposalOut,
    ProposalSource,
    ProposedCorrection,
    PunchOut,
    ScheduleOut,
    Severity,
    TimecardOut,
    ValidationResult,
)
from app.validator import ExceptionRecord, ValidationContext, validate_correction
from app.workflow_repository import GENESIS_HASH, PostgresWorkflowRepository, Row, _event_hash

_ACTIVE_STATUSES = {"open", "pending_review", "escalated"}
logger = logging.getLogger(__name__)


@dataclass
class SourceContext:
    """Current source evidence kept in memory for one HR review request."""

    trigger_record_ref: str
    current_record: AttendanceRecord | None
    range_evidence: AttendanceRangeEvidence | None
    employee: dict[str, object] | None
    punches: list[Any]
    schedules: list[Any]
    punch_refs: dict[int, str]
    schedule_refs: dict[int, str]
    source_current: bool
    current_source_version: str | None
    as_of: datetime | None


class HRReviewService:
    """Translate durable workflow rows into review DTOs and handle HR decisions."""

    def __init__(
        self,
        repository: PostgresWorkflowRepository,
        provider: AttendanceProvider,
        organization_id: UUID,
        book: PolicyBook,
        operational_alerts: OperationalAlertReporter | None = None,
        policy_context: OrganizationPolicyContext | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.organization_id = organization_id
        self.book = book
        self.policy_context = policy_context or OrganizationPolicyContext(book=book)
        self.operational_alerts = operational_alerts

    def list_cases(
        self,
        *,
        status: ExceptionStatus | None,
        severity: Severity | None,
        rule_id: str | None,
        employee_id: str | None,
        limit: int,
        offset: int,
    ) -> list[ExceptionOut]:
        rows = self.repository.list_exception_cases(
            self.organization_id,
            status=status.value.lower() if status else None,
            severity=severity.value if severity else None,
            rule_id=rule_id,
            employee_ref=employee_id,
            limit=limit,
            offset=offset,
        )
        return [self._exception_out(row) for row in rows]

    def get_case_detail(self, case_id: UUID) -> ExceptionDetail:
        case = self.repository.get_exception_case(case_id, self.organization_id)
        if case is None:
            raise NotFound(f"case {case_id} was not found")
        context = self._load_source_context(case)
        case_status = str(case["status"])
        if case_status in _ACTIVE_STATUSES and not context.source_current:
            self.repository.supersede_case_with_audit(
                case_id=case_id,
                organization_id=self.organization_id,
                trace_id=f"review-{case_id.hex}",
                event_type="SOURCE_CHANGED_BEFORE_REVIEW",
                payload={
                    "expected_source_version": case.get("source_version"),
                    "current_source_version": context.current_source_version,
                    "expected_source_hash": case.get("source_hash"),
                },
            )
            case = {**case, "status": "superseded"}

        employee = self._employee_out(case, context)
        exception = self._exception_out(case, context=context)
        timecard = self._timecard(case, context, employee)
        jurisdiction = (
            str(context.current_record.jurisdiction)
            if context.current_record and context.current_record.jurisdiction
            else self._snapshot(case).get("jurisdiction")
        )
        policies = self._policies(str(case["rule_id"]), jurisdiction)
        proposal_row = self.repository.get_latest_proposal_bundle_for_case(
            case_id, self.organization_id
        )
        decision_row = self.repository.get_latest_decision_for_case(case_id, self.organization_id)
        return ExceptionDetail(
            exception=exception,
            employee=employee,
            timecard=timecard,
            policies=policies,
            latest_proposal=self._proposal_out(proposal_row) if proposal_row else None,
            decision=(self._decision_out(decision_row, case_status) if decision_row else None),
            source_current=context.source_current,
            source_version=(str(case["source_version"]) if case.get("source_version") else None),
            current_source_version=context.current_source_version,
        )

    def decide(
        self,
        case_id: UUID,
        body: DecisionIn,
        actor_id: UUID,
    ) -> DecisionOut:
        case = self.repository.get_exception_case(case_id, self.organization_id)
        if case is None:
            raise NotFound(f"case {case_id} was not found")
        if str(case["status"]) not in {"pending_review", "escalated"}:
            raise StateConflict("case is no longer awaiting an HR decision")
        proposal_row = self.repository.get_latest_proposal_bundle_for_case(
            case_id, self.organization_id
        )
        if proposal_row is None:
            raise StateConflict("case does not have a persisted proposal yet")

        note = body.note.strip() if body.note is not None else None
        if body.action is DecisionAction.REJECT:
            if not note:
                raise BadRequest("a note is required when rejecting", code="reject_note_required")
            decision_id = self.repository.record_decision(
                organization_id=self.organization_id,
                exception_case_id=case_id,
                decided_by=actor_id,
                action=body.action.value,
                proposal_id=UUID(str(proposal_row["id"])),
                note=note,
                writeback_status="not_requested",
                case_status_after="rejected",
            )
            decision = self.repository.get_decision(decision_id, self.organization_id)
            if decision is None:
                raise RuntimeError("decision was not found after insert")
            return self._decision_out(decision, "rejected")

        context = self._load_source_context(case)
        if not context.source_current or context.current_record is None:
            self._mark_stale(case, context)
            raise StateConflict(
                "source attendance changed since this proposal; refresh the queue for its new analysis"
            )
        exception = self._exception_record(case, context)
        proposal = self._proposal_model(proposal_row)
        correction: ProposedCorrection
        if body.action is DecisionAction.APPROVE:
            correction = proposal.proposed_correction
            if correction.action is Action.ESCALATE:
                raise BadRequest(
                    "an escalation proposal cannot be approved",
                    code="cannot_approve_escalation",
                )
        else:
            edited_correction = body.edited_correction
            if edited_correction is None:
                raise BadRequest("edited_correction is required", code="edit_requires_correction")
            correction = edited_correction
            if correction.action is Action.ESCALATE:
                raise BadRequest(
                    "an edit cannot change the proposal to ESCALATE",
                    code="cannot_edit_to_escalation",
                )

        validation = self._validate_current_correction(case, context, exception, correction)
        if not validation.passed:
            raise ValidationFailed(validation.checks)
        final_correction = correction.model_dump(mode="json")
        decision_id = self.repository.record_decision(
            organization_id=self.organization_id,
            exception_case_id=case_id,
            decided_by=actor_id,
            action=body.action.value,
            proposal_id=UUID(str(proposal_row["id"])),
            note=note,
            final_correction=final_correction,
            expected_source_version=str(case["source_version"]),
            writeback_status="pending",
            case_status_after="writeback_pending",
        )
        return self._submit_writeback(decision_id, case, context, correction, actor_id)

    def retry_writeback(self, decision_id: UUID) -> DecisionOut:
        decision = self.repository.get_decision(decision_id, self.organization_id)
        if decision is None:
            raise NotFound(f"decision {decision_id} was not found")
        if str(decision["writeback_status"]) not in {"pending", "failed"}:
            raise StateConflict("this decision does not have a retryable company writeback")
        case_id = UUID(str(decision["exception_case_id"]))
        case = self.repository.get_exception_case(case_id, self.organization_id)
        if case is None:
            raise NotFound(f"case {case_id} was not found")
        context = self._load_source_context(case)
        correction_value = decision.get("final_correction")
        if isinstance(correction_value, str):
            correction_value = json.loads(correction_value)
        correction = ProposedCorrection.model_validate(correction_value)
        actor_id = UUID(str(decision["decided_by"]))
        return self._submit_writeback(decision_id, case, context, correction, actor_id)

    def audit_events(self, case_id: UUID) -> list[AuditEventOut]:
        if self.repository.get_exception_case(case_id, self.organization_id) is None:
            raise NotFound(f"case {case_id} was not found")
        return [
            AuditEventOut(
                id=int(row["id"]),
                trace_id=str(row["trace_id"]),
                exception_id=UUID(str(row["exception_case_id"]))
                if row.get("exception_case_id") is not None
                else None,
                seq=int(row["sequence_number"]),
                event_type=str(row["event_type"]),
                payload=row["payload"] if isinstance(row["payload"], dict) else {},
                prev_hash=str(row["previous_hash"]),
                hash=str(row["event_hash"]),
                created_at=_iso_timestamp(row["created_at"]),
            )
            for row in self.repository.list_audit_events(self.organization_id, case_id)
        ]

    def verify_audit_chain(self) -> AuditVerifyOut:
        """Verify this organization's PostgreSQL audit hash chain."""
        rows = self.repository.list_all_audit_events(self.organization_id)
        expected_previous = GENESIS_HASH
        for row in rows:
            event_id = int(row["id"])
            previous_hash = str(row["previous_hash"])
            if previous_hash != expected_previous:
                return AuditVerifyOut(
                    ok=False,
                    events_checked=len(rows),
                    first_bad_event_id=event_id,
                )
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return AuditVerifyOut(
                        ok=False,
                        events_checked=len(rows),
                        first_bad_event_id=event_id,
                    )
            if not isinstance(payload, dict):
                return AuditVerifyOut(
                    ok=False,
                    events_checked=len(rows),
                    first_bad_event_id=event_id,
                )
            payload_json = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            case_id = (
                UUID(str(row["exception_case_id"]))
                if row.get("exception_case_id") is not None
                else None
            )
            expected_hash = _event_hash(
                previous_hash,
                str(row["trace_id"]),
                case_id,
                int(row["sequence_number"]),
                str(row["event_type"]),
                payload_json,
                _audit_hash_timestamp(row["created_at"]),
            )
            if expected_hash != str(row["event_hash"]):
                return AuditVerifyOut(
                    ok=False,
                    events_checked=len(rows),
                    first_bad_event_id=event_id,
                )
            expected_previous = str(row["event_hash"])
        return AuditVerifyOut(ok=True, events_checked=len(rows), first_bad_event_id=None)

    def _load_source_context(self, case: Row) -> SourceContext:
        snapshot = self._snapshot(case)
        trigger_ref = snapshot.get("trigger_record_ref")
        if not isinstance(trigger_ref, str) or not trigger_ref:
            trigger_ref = self._infer_trigger_record(case)
        if trigger_ref not in (case.get("source_record_refs") or []):
            raise StateConflict(
                "case is missing its source record reference and must be reanalyzed"
            )
        try:
            source_record = self.provider.fetch_attendance_record(trigger_ref)
        except AttendanceProviderUnavailable as exc:
            if self.operational_alerts is not None:
                self.operational_alerts.report_failure(
                    "attendance_provider",
                    "attendance_source_unavailable",
                    trace_id=trace_id_var.get(),
                    case_id=str(case["id"]),
                )
            raise HTTPException(
                status_code=503,
                detail="Company attendance source is temporarily unavailable; retry shortly.",
            ) from exc
        except AttendanceProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail="Company attendance evidence could not be loaded.",
            ) from exc
        if source_record is None:
            return SourceContext(
                trigger_record_ref=trigger_ref,
                current_record=None,
                range_evidence=None,
                employee=None,
                punches=[],
                schedules=[],
                punch_refs={},
                schedule_refs={},
                source_current=False,
                current_source_version=None,
                as_of=None,
            )
        if source_record.employee_ref != str(case["external_employee_ref"]):
            raise StateConflict("company returned attendance for a different employee")
        if source_record.record_ref != trigger_ref:
            raise StateConflict("company returned a different attendance record")
        window = snapshot.get("analysis_window")
        work_date = _case_work_date(case, snapshot)
        window_start = window.get("from") if isinstance(window, dict) else None
        window_end = window.get("through") if isinstance(window, dict) else None
        if isinstance(window_start, str) and isinstance(window_end, str):
            try:
                range_start = date.fromisoformat(window_start)
                range_end = date.fromisoformat(window_end)
            except ValueError as exc:
                raise StateConflict(
                    "case analysis window is invalid and must be reanalyzed"
                ) from exc
        else:
            monday = work_date - timedelta(days=work_date.weekday())
            range_start = monday - timedelta(days=7)
            range_end = monday + timedelta(days=6)
        try:
            range_evidence = self.provider.fetch_attendance_range(
                source_record.employee_ref, range_start, range_end
            )
        except AttendanceProviderUnavailable as exc:
            if self.operational_alerts is not None:
                self.operational_alerts.report_failure(
                    "attendance_provider",
                    "attendance_range_unavailable",
                    trace_id=trace_id_var.get(),
                    case_id=str(case["id"]),
                )
            raise HTTPException(
                status_code=503,
                detail="Company attendance history is temporarily unavailable; retry shortly.",
            ) from exc
        except AttendanceProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail="Company attendance history could not be loaded.",
            ) from exc
        if self.operational_alerts is not None:
            self.operational_alerts.report_success("attendance_provider")
        if (
            not range_evidence.coverage_complete
            or range_evidence.employee_ref != source_record.employee_ref
            or range_evidence.covered_from != range_start
            or range_evidence.covered_through != range_end
        ):
            raise StateConflict(
                "company attendance history is incomplete; this case cannot be decided"
            )
        records = range_evidence.records
        if any(
            record.employee_ref != source_record.employee_ref
            or not range_start <= record.work_date <= range_end
            or record.timezone != source_record.timezone
            or record.jurisdiction != source_record.jurisdiction
            for record in records
        ) or len({record.record_ref for record in records}) != len(records):
            raise StateConflict("company attendance history contains out-of-scope evidence")
        matching = [record for record in records if record.record_ref == trigger_ref]
        if len(matching) != 1:
            return SourceContext(
                trigger_ref,
                source_record,
                range_evidence,
                None,
                [],
                [],
                {},
                {},
                False,
                source_record.version,
                None,
            )
        if _canonical_record_hash(matching[0]) != _canonical_record_hash(source_record):
            raise StateConflict("company attendance reads disagree; retry case review")
        try:
            zone = ZoneInfo(source_record.timezone)
            employee, punches, schedules, punch_refs, schedule_refs = _normalize_records(
                records, source_record, zone
            )
            as_of = _analysis_time(work_date, zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise StateConflict("company attendance evidence has an unsupported time zone") from exc
        source_current = source_record.version == str(
            case.get("source_version") or ""
        ) and _range_hash(records) == str(case.get("source_hash") or "")
        return SourceContext(
            trigger_ref,
            source_record,
            range_evidence,
            employee,
            punches,
            schedules,
            punch_refs,
            schedule_refs,
            source_current,
            source_record.version,
            as_of,
        )

    def _infer_trigger_record(self, case: Row) -> str:
        refs = case.get("source_record_refs")
        if not isinstance(refs, list):
            raise StateConflict("case is missing its source record references")
        candidates: list[str] = []
        for record_ref in refs:
            if not isinstance(record_ref, str):
                continue
            try:
                record = self.provider.fetch_attendance_record(record_ref)
            except AttendanceProviderUnavailable as exc:
                if self.operational_alerts is not None:
                    self.operational_alerts.report_failure(
                        "attendance_provider",
                        "attendance_source_unavailable",
                        trace_id=trace_id_var.get(),
                        case_id=str(case["id"]),
                    )
                raise HTTPException(
                    status_code=503,
                    detail="Company attendance source is temporarily unavailable; retry shortly.",
                ) from exc
            except AttendanceProviderError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Company attendance evidence could not be loaded.",
                ) from exc
            if self.operational_alerts is not None:
                self.operational_alerts.report_success("attendance_provider")
            if record is not None and record.version == str(case.get("source_version") or ""):
                candidates.append(record_ref)
        if len(candidates) != 1:
            raise StateConflict("case source anchor is ambiguous; reanalysis is required")
        return candidates[0]

    def _mark_stale(self, case: Row, context: SourceContext) -> None:
        case_id = UUID(str(case["id"]))
        self.repository.supersede_case_with_audit(
            case_id=case_id,
            organization_id=self.organization_id,
            trace_id=f"review-{case_id.hex}",
            event_type="SOURCE_CHANGED_BEFORE_WRITEBACK",
            payload={
                "expected_source_version": case.get("source_version"),
                "current_source_version": context.current_source_version,
                "expected_source_hash": case.get("source_hash"),
            },
        )

    def _validate_current_correction(
        self,
        case: Row,
        context: SourceContext,
        exception: ExceptionRecord,
        correction: ProposedCorrection,
    ) -> ValidationResult:
        assert context.employee is not None and context.as_of is not None
        jurisdiction = str(context.employee["jurisdiction"])
        if jurisdiction not in self.book.jurisdictions:
            raise BadRequest(
                "the employee jurisdiction has no configured attendance policy",
                code="unsupported_jurisdiction",
            )
        policies = self.policy_context.passages_for_rule(exception.rule_id, jurisdiction)
        validation_context = ValidationContext(
            employee=context.employee,
            punches=context.punches,
            schedules=context.schedules,
            as_of=context.as_of,
            book=self.book,
            retrieved_policy_ids=[passage.policy_id for passage in policies],
            disabled_rule_ids=self.policy_context.disabled_rule_ids,
        )
        return validate_correction(correction, exception, validation_context)

    def _submit_writeback(
        self,
        decision_id: UUID,
        case: Row,
        context: SourceContext,
        correction: ProposedCorrection,
        actor_id: UUID,
    ) -> DecisionOut:
        if context.current_record is None:
            result = None
            status = "stale"
            case_status = "superseded"
            operation_id = None
            result_data: dict[str, Any] = {"error_code": "source_record_missing"}
        else:
            try:
                request = self._company_request(decision_id, case, context, correction, actor_id)
                result = self.provider.submit_correction(
                    request,
                    idempotency_key=f"shiftguard-decision-{decision_id.hex}",
                )
            except (AttendanceSourceChanged, AttendanceRecordNotFound):
                result = None
                status = "stale"
                case_status = "superseded"
                operation_id = None
                result_data = {"error_code": "source_version_changed"}
            except (AttendanceProviderUnavailable, AttendanceProviderError, BadRequest) as exc:
                result = None
                status = "failed"
                case_status = "writeback_failed"
                operation_id = None
                result_data = {"error_code": _writeback_error_code(exc)}
            else:
                status = result.status
                case_status = "resolved" if result.status == "applied" else "writeback_pending"
                operation_id = result.operation_id
                result_data = result.model_dump(mode="json")

        if status == "failed":
            error_code = str(result_data.get("error_code", "company_writeback_failed"))
            if self.operational_alerts is not None:
                self.operational_alerts.report_failure(
                    "company_writeback",
                    error_code,
                    trace_id=trace_id_var.get(),
                    case_id=str(case["id"]),
                    decision_id=str(decision_id),
                )
            logger.error(
                "company writeback failed",
                extra={
                    "exception_case_id": str(case["id"]),
                    "decision_id": str(decision_id),
                    "error_code": error_code,
                    "trace_id": trace_id_var.get(),
                },
            )
        else:
            if self.operational_alerts is not None:
                self.operational_alerts.report_success("company_writeback")
            logger.info(
                "company writeback outcome recorded",
                extra={
                    "exception_case_id": str(case["id"]),
                    "decision_id": str(decision_id),
                    "external_operation_id": operation_id,
                    "writeback_status": status,
                    "trace_id": trace_id_var.get(),
                },
            )

        row = self.repository.update_decision_writeback(
            decision_id=decision_id,
            organization_id=self.organization_id,
            writeback_status=status,
            case_status=case_status,
            external_operation_id=operation_id,
            writeback_result=result_data,
        )
        return self._decision_out(row, case_status)

    def _company_request(
        self,
        decision_id: UUID,
        case: Row,
        context: SourceContext,
        correction: ProposedCorrection,
        actor_id: UUID,
    ) -> CompanyCorrectionRequest:
        assert context.current_record is not None
        if not context.source_current and str(case["status"]) not in {
            "writeback_pending",
            "writeback_failed",
        }:
            self._mark_stale(case, context)
            raise AttendanceSourceChanged("company attendance changed before writeback")
        if correction.action is Action.ESCALATE:
            raise BadRequest("an escalation cannot be sent as a correction")
        if correction.action is Action.ADD_PUNCH:
            assert correction.ts is not None and correction.kind is not None
            payload: dict[str, object] = {
                "action": correction.action.value,
                "kind": correction.kind.value,
                "target_record_ref": context.current_record.record_ref,
                "target_expected_version": context.current_record.version,
                "at": _offset_timestamp(correction.ts, context.current_record.timezone),
            }
        elif correction.action is Action.MODIFY_PUNCH:
            assert correction.punch_id is not None and correction.ts is not None
            punch_ref = context.punch_refs.get(correction.punch_id)
            if punch_ref is None:
                raise BadRequest("the correction target no longer exists", code="target_missing")
            target_record = _record_for_target(context, punch_ref, "punch")
            payload = {
                "action": correction.action.value,
                "punch_ref": punch_ref,
                "target_record_ref": target_record.record_ref,
                "target_expected_version": target_record.version,
                "at": _offset_timestamp(correction.ts, context.current_record.timezone),
            }
        elif correction.action is Action.REMOVE_PUNCH:
            assert correction.punch_id is not None
            punch_ref = context.punch_refs.get(correction.punch_id)
            if punch_ref is None:
                raise BadRequest("the correction target no longer exists", code="target_missing")
            target_record = _record_for_target(context, punch_ref, "punch")
            payload = {
                "action": correction.action.value,
                "punch_ref": punch_ref,
                "target_record_ref": target_record.record_ref,
                "target_expected_version": target_record.version,
            }
        elif correction.action is Action.ADJUST_SCHEDULE:
            assert correction.schedule_id is not None and correction.ts is not None
            schedule_ref = context.schedule_refs.get(correction.schedule_id)
            if schedule_ref is None:
                raise BadRequest("the schedule target no longer exists", code="target_missing")
            target_record = _record_for_target(context, schedule_ref, "schedule")
            payload = {
                "action": correction.action.value,
                "schedule_ref": schedule_ref,
                "target_record_ref": target_record.record_ref,
                "target_expected_version": target_record.version,
                "end": _offset_timestamp(correction.ts, context.current_record.timezone),
            }
        elif correction.action is Action.FLAG_FOR_APPROVAL:
            payload = {
                "action": correction.action.value,
                "flag_type": self._snapshot(case).get("constraints", {}).get("flag_type"),
                "target_record_ref": context.current_record.record_ref,
                "target_expected_version": context.current_record.version,
            }
        else:
            raise BadRequest("unsupported correction action")

        latest_decision = self.repository.get_decision(decision_id, self.organization_id)
        approved_at = (
            _aware_datetime(latest_decision["decided_at"])
            if latest_decision is not None
            else datetime.now(timezone.utc)
        )
        return CompanyCorrectionRequest(
            decision_ref=str(decision_id),
            record_ref=context.trigger_record_ref,
            employee_ref=context.current_record.employee_ref,
            expected_version=str(case["source_version"]),
            expected_source_versions=(
                {record.record_ref: record.version for record in context.range_evidence.records}
                if context.range_evidence is not None
                else {context.current_record.record_ref: context.current_record.version}
            ),
            correction=payload,
            approved_by=str(actor_id),
            approved_at=approved_at,
        )

    def _exception_record(self, case: Row, context: SourceContext) -> ExceptionRecord:
        snapshot = self._snapshot(case)
        constraints = self._constraints(case, context)
        work_date = _case_work_date(case, snapshot)
        return ExceptionRecord(
            id=1,
            employee_id=str(case["external_employee_ref"]),
            work_date=work_date,
            rule_id=str(case["rule_id"]),
            severity=Severity(str(case["severity"])),
            evidence=snapshot.get("evidence", {})
            if isinstance(snapshot.get("evidence"), dict)
            else {},
            constraints=constraints,
        )

    def _constraints(self, case: Row, context: SourceContext) -> Constraints:
        raw = self._snapshot(case).get("constraints")
        if isinstance(raw, dict):
            return Constraints.model_validate(raw)
        if context.employee is not None and context.as_of is not None:
            from app.engine.rules import detect_employee

            findings = detect_employee(
                context.employee,
                context.punches,
                context.schedules,
                context.as_of,
                self.book,
                disabled_rule_ids=self.policy_context.disabled_rule_ids,
            )
            work_date = _case_work_date(case, self._snapshot(case))
            match = next(
                (
                    finding
                    for finding in findings
                    if finding.rule_id == str(case["rule_id"]) and finding.work_date == work_date
                ),
                None,
            )
            if match is not None:
                return match.constraints
        return Constraints(allowed_actions=[Action.ESCALATE])

    def _exception_out(self, case: Row, *, context: SourceContext | None = None) -> ExceptionOut:
        snapshot = self._snapshot(case)
        evidence = snapshot.get("evidence")
        raw_constraints = snapshot.get("constraints")
        if isinstance(raw_constraints, dict):
            constraints = Constraints.model_validate(raw_constraints)
        elif context is not None:
            constraints = self._constraints(case, context)
        else:
            constraints = Constraints(allowed_actions=[Action.ESCALATE])
        rule = get_rule(str(case["rule_id"]), self.book)
        return ExceptionOut(
            id=UUID(str(case["id"])),
            employee_id=str(case["external_employee_ref"]),
            employee_name=str(case["external_employee_ref"]),
            work_date=_case_work_date(case, snapshot).isoformat(),
            rule_id=rule.id,
            title=rule.title,
            severity=Severity(str(case["severity"])),
            status=_case_status(case["status"]),
            detected_at=_minute_timestamp(case.get("detected_at")),
            evidence=evidence if isinstance(evidence, dict) else {},
            constraints=constraints,
        )

    def _employee_out(self, case: Row, context: SourceContext) -> EmployeeOut:
        snapshot = self._snapshot(case)
        jurisdiction = (
            str(context.current_record.jurisdiction)
            if context.current_record and context.current_record.jurisdiction
            else str(snapshot.get("jurisdiction") or "US-STD")
        )
        eligibility = context.employee.get("ot_eligible") if context.employee else None
        return EmployeeOut(
            id=str(case["external_employee_ref"]),
            name=str(case["external_employee_ref"]),
            role=None,
            site=None,
            jurisdiction=jurisdiction,
            manager_id=None,
            ot_eligible=eligibility if isinstance(eligibility, bool) else None,
        )

    def _timecard(self, case: Row, context: SourceContext, employee: EmployeeOut) -> TimecardOut:
        work_date = _case_work_date(case, self._snapshot(case))
        week_start = work_date - timedelta(days=work_date.weekday())
        punches = context.punches
        schedules = context.schedules
        week_punches = [punch for punch in punches if week_start <= punch.work_date <= work_date]
        week_schedules = [
            schedule
            for schedule in schedules
            if week_start <= schedule.work_date <= week_start + timedelta(days=6)
        ]
        from app.engine.shifts import build_shifts

        shifts = build_shifts(
            week_punches,
            week_schedules,
            self.book.global_params.dup_window_minutes,
        )
        weekly_minutes = sum(shift.worked_minutes for shift in shifts.values() if shift.complete)
        jurisdiction = employee.jurisdiction
        jurisdiction_policy = self.book.jurisdictions.get(jurisdiction)
        return TimecardOut(
            employee=employee,
            week_start=week_start.isoformat(),
            punches=[
                PunchOut(
                    id=punch.id,
                    employee_id=punch.employee_id,
                    work_date=punch.work_date.isoformat(),
                    ts=punch.ts.strftime("%Y-%m-%dT%H:%M"),
                    kind=punch.kind,
                    source=_source_label(punch.id, context),
                    voided=punch.voided,
                )
                for punch in week_punches
            ],
            schedules=[
                ScheduleOut(
                    id=schedule.id,
                    employee_id=schedule.employee_id,
                    work_date=schedule.work_date.isoformat(),
                    start=schedule.start.strftime("%Y-%m-%dT%H:%M"),
                    end=schedule.end.strftime("%Y-%m-%dT%H:%M"),
                    planned_break_minutes=schedule.planned_break_minutes,
                )
                for schedule in week_schedules
            ],
            weekly_worked_minutes=weekly_minutes,
            weekly_threshold_minutes=(
                jurisdiction_policy.weekly_ot_hours * 60 if jurisdiction_policy else None
            ),
        )

    def _proposal_out(self, row: Row) -> ProposalOut:
        validations = row.get("validation_results")
        latest_validation = validations[-1] if isinstance(validations, list) and validations else {}
        proposal_value = row.get("proposal")
        if isinstance(proposal_value, str):
            proposal_value = json.loads(proposal_value)
        return ProposalOut(
            id=UUID(str(row["id"])),
            exception_id=UUID(str(row["exception_case_id"])),
            trace_id=str(row.get("analysis_job_id") or row["id"]),
            source=ProposalSource(str(row["source"])),
            proposal=LLMProposal.model_validate(proposal_value),
            validation=ValidationResult.model_validate(
                {
                    "passed": bool(latest_validation.get("passed", False)),
                    "checks": latest_validation.get("checks", []),
                    "side_effects": [],
                }
            ),
            attempts=int(row["attempts"]),
            model=str(row["model"]),
            latency_ms=int(row["latency_ms"]),
            created_at=_minute_timestamp(row.get("created_at")),
            policy_snapshot=[
                PolicySnapshotEntry.model_validate(item)
                for item in self._as_list(row.get("policy_snapshot"))
                if isinstance(item, dict) and {"policy_id", "title", "text"}.issubset(item)
            ],
        )

    def _decision_out(self, row: Row, resulting_status: str) -> DecisionOut:
        correction_value = row.get("final_correction")
        if isinstance(correction_value, str):
            correction_value = json.loads(correction_value)
        return DecisionOut(
            id=UUID(str(row["id"])),
            exception_id=UUID(str(row["exception_case_id"])),
            proposal_id=(UUID(str(row["proposal_id"])) if row.get("proposal_id") else None),
            action=DecisionAction(str(row["action"])),
            manager_id=str(row["decided_by"]),
            note=str(row["note"]) if row.get("note") is not None else None,
            final_correction=(
                ProposedCorrection.model_validate(correction_value)
                if isinstance(correction_value, dict)
                else None
            ),
            decided_at=_minute_timestamp(row.get("decided_at")),
            resulting_status=_case_status(resulting_status),
            writeback_status=cast(
                Literal["not_requested", "pending", "applied", "stale", "failed"],
                str(row["writeback_status"]),
            ),
            external_operation_id=(
                str(row["external_operation_id"])
                if row.get("external_operation_id") is not None
                else None
            ),
        )

    def _proposal_model(self, row: Row) -> LLMProposal:
        value = row.get("proposal")
        if isinstance(value, str):
            value = json.loads(value)
        return LLMProposal.model_validate(value)

    def _snapshot(self, case: Row) -> dict[str, Any]:
        snapshot = case.get("minimal_evidence_snapshot")
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except json.JSONDecodeError:
                snapshot = None
        return snapshot if isinstance(snapshot, dict) else {}

    @staticmethod
    def _as_list(value: object) -> list[object]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = None
        return value if isinstance(value, list) else []

    def _policies(self, rule_id: str, jurisdiction: object) -> list[PolicyPassage]:
        if isinstance(jurisdiction, str) and jurisdiction in self.book.jurisdictions:
            return self.policy_context.passages_for_rule(rule_id, jurisdiction)
        relevant_ids = {rule_id, *get_rule(rule_id, self.book).related[:2]}
        return [
            passage
            for passage in self.policy_context.all_passages()
            if passage.policy_id in relevant_ids
        ]


def _case_work_date(case: Row, snapshot: dict[str, Any]) -> date:
    value = snapshot.get("work_date")
    if not isinstance(value, str):
        evidence = snapshot.get("evidence")
        value = evidence.get("work_date") if isinstance(evidence, dict) else None
    if not isinstance(value, str):
        raise StateConflict("case has no work date and must be reanalyzed")
    return date.fromisoformat(value)


def _case_status(value: object) -> ExceptionStatus:
    try:
        return ExceptionStatus(str(value).upper())
    except ValueError as exc:
        raise StateConflict("case has an unsupported review status") from exc


def _minute_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    return "1970-01-01T00:00"


def _iso_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _audit_hash_timestamp(value: object) -> str:
    parsed = _aware_datetime(value)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _aware_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _offset_timestamp(value: str, timezone_name: str) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise BadRequest("case time zone is unsupported", code="unsupported_timezone") from exc
    local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local:
            if aware.utcoffset() not in {candidate.utcoffset() for candidate in candidates}:
                candidates.append(aware)
    if len(candidates) != 1:
        raise BadRequest(
            "the proposed local time is ambiguous or does not exist because of a clock change",
            code="ambiguous_local_time",
        )
    return candidates[0].isoformat()


def _source_label(punch_id: int, source: SourceContext) -> str:
    if source.range_evidence is None:
        return "device"
    ref = source.punch_refs.get(punch_id)
    for record in source.range_evidence.records:
        for punch in record.punches:
            if punch.record_ref == ref:
                return punch.source or "device"
    return "device"


def _record_for_target(
    context: SourceContext, target_ref: str, target_kind: str
) -> AttendanceRecord:
    if context.range_evidence is not None:
        for record in context.range_evidence.records:
            items = record.punches if target_kind == "punch" else [record.schedule]
            if any(item is not None and item.record_ref == target_ref for item in items):
                return record
    raise BadRequest("the correction target no longer exists", code="target_missing")


def _writeback_error_code(exc: Exception) -> str:
    if isinstance(exc, AttendanceProviderUnavailable):
        return "company_api_unavailable"
    if isinstance(exc, AttendanceProviderError):
        return "company_api_rejected_correction"
    if isinstance(exc, BadRequest):
        return exc.code
    return "company_writeback_failed"
