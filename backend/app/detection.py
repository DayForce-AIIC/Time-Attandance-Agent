"""Persist deterministic rule-engine findings and retire stale open findings."""

import sqlite3
import uuid
from datetime import date, datetime, timedelta

from app import repo
from app.audit import EXCEPTION_DETECTED, EXCEPTION_SUPERSEDED, append_event
from app.engine.rules import detect_employee
from app.policies import PolicyBook
from app.schemas import DetectOut, ExceptionStatus


def _week_start(as_of: datetime) -> date:
    current_date = as_of.date()
    return current_date - timedelta(days=current_date.weekday())


def _trace_id() -> str:
    return f"detect-{uuid.uuid4().hex[:8]}"


def run_detection(
    conn: sqlite3.Connection,
    as_of: datetime,
    book: PolicyBook,
    employee_ids: list[str] | None = None,
) -> DetectOut:
    """Run detection and atomically upsert findings for selected employees."""
    if as_of.utcoffset() is not None:
        raise ValueError("as_of must be a timezone-naive local datetime")
    week_start = _week_start(as_of)
    history_start = week_start - timedelta(days=7)
    week_end = week_start + timedelta(days=6)
    detected_at = as_of.strftime("%Y-%m-%dT%H:%M")
    created = 0
    unchanged = 0
    superseded = 0

    with conn:
        for employee in repo.list_employees(conn, employee_ids):
            employee_id = str(employee["id"])
            # Include scheduled future days through Sunday so OT-02 can project
            # remaining hours; detect_employee filters findings to as_of.
            punches, schedules = repo.get_timekeeping_records(
                conn, employee_id, history_start, week_end
            )
            findings = detect_employee(employee, punches, schedules, as_of, book)
            current_fingerprints = {finding.fingerprint for finding in findings}

            for finding in findings:
                existing = repo.get_exception_by_fingerprint(conn, finding.fingerprint)
                if existing is not None:
                    unchanged += 1
                    continue
                exception_id = repo.create_exception(
                    conn,
                    fingerprint=finding.fingerprint,
                    employee_id=finding.employee_id,
                    work_date=finding.work_date,
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    evidence=finding.evidence,
                    constraints=finding.constraints.model_dump(mode="json"),
                    detected_at=detected_at,
                )
                append_event(
                    conn,
                    trace_id=_trace_id(),
                    event_type=EXCEPTION_DETECTED,
                    exception_id=exception_id,
                    payload={
                        "fingerprint": finding.fingerprint,
                        "employee_id": finding.employee_id,
                        "work_date": finding.work_date.isoformat(),
                        "rule_id": finding.rule_id,
                        "severity": finding.severity.value,
                        "evidence": finding.evidence,
                    },
                )
                created += 1

            for row in repo.list_exceptions_for_employee(conn, employee_id):
                if row["fingerprint"] in current_fingerprints:
                    continue
                status = str(row["status"])
                if status not in {
                    ExceptionStatus.OPEN.value,
                    ExceptionStatus.PENDING_REVIEW.value,
                    ExceptionStatus.ESCALATED.value,
                }:
                    continue
                exception_id = int(row["id"])
                repo.set_exception_status(conn, exception_id, ExceptionStatus.SUPERSEDED)
                append_event(
                    conn,
                    trace_id=_trace_id(),
                    event_type=EXCEPTION_SUPERSEDED,
                    exception_id=exception_id,
                    payload={
                        "fingerprint": str(row["fingerprint"]),
                        "employee_id": employee_id,
                        "rule_id": str(row["rule_id"]),
                        "previous_status": status,
                    },
                )
                superseded += 1

    return DetectOut(
        as_of=detected_at,
        created=created,
        unchanged=unchanged,
        superseded=superseded,
    )
