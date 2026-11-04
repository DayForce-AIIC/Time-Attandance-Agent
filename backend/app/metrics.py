"""Operational metrics derived from exceptions, proposals, and decisions."""

import json
import sqlite3
from datetime import date, datetime, timedelta
from statistics import median

from app import repo
from app.clock import Clock
from app.config import Settings
from app.policies import PolicyBook, retrieve_policies
from app.schemas import Constraints, MetricsOut, Severity
from app.validator import ExceptionRecord, ValidationContext, validate_proposal


def calculate_metrics(
    conn: sqlite3.Connection, clock: Clock, book: PolicyBook, settings: Settings
) -> MetricsOut:
    """Compute throughput, review, savings, and current safety metrics."""
    exceptions = repo.list_exception_rows(conn)
    proposals = repo.list_proposal_rows(conn)
    decisions = repo.list_decision_rows(conn)

    by_status: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for row in exceptions:
        _increment(by_status, str(row["status"]))
        _increment(by_rule, str(row["rule_id"]))
        _increment(by_severity, str(row["severity"]))

    proposal_count = len(proposals)
    llm_proposals = [row for row in proposals if row["source"] == "llm"]
    first_pass = sum(1 for row in llm_proposals if int(row["attempts"]) == 1)
    fallbacks = sum(1 for row in proposals if row["source"] == "fallback")
    approve_count = sum(1 for row in decisions if row["action"] == "approve")
    edit_count = sum(1 for row in decisions if row["action"] == "edit")
    decided_durations = []
    for row in decisions:
        created = row["proposal_created_at"]
        if created is None:
            continue
        try:
            start = datetime.strptime(str(created), "%Y-%m-%dT%H:%M")
            end = datetime.strptime(str(row["decided_at"]), "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        decided_durations.append((end - start).total_seconds())

    unsafe_pending = _count_unsafe_pending(conn, clock, book)
    decision_count = approve_count + edit_count
    return MetricsOut(
        total_exceptions=len(exceptions),
        by_status=by_status,
        by_rule=by_rule,
        by_severity=by_severity,
        proposals_total=proposal_count,
        first_pass_validation_rate=_ratio(first_pass, proposal_count),
        final_pass_rate=_ratio(len(llm_proposals), proposal_count),
        escalation_rate=_ratio(fallbacks, proposal_count),
        approved_total=decision_count,
        approved_without_edit_rate=_ratio(approve_count, decision_count),
        median_seconds_to_decision=median(decided_durations) if decided_durations else None,
        estimated_minutes_saved=approve_count
        * (settings.manual_minutes_per_exception - settings.review_minutes_per_exception),
        assumptions={
            "manual_minutes_per_exception": settings.manual_minutes_per_exception,
            "review_minutes_per_exception": settings.review_minutes_per_exception,
        },
        unsafe_proposals_reaching_manager=unsafe_pending,
    )


def _count_unsafe_pending(conn: sqlite3.Connection, clock: Clock, book: PolicyBook) -> int:
    unsafe = 0
    as_of = clock.now()
    for row in repo.list_pending_llm_proposals(conn):
        employee = repo.get_employee(conn, str(row["employee_id"]))
        if employee is None:
            unsafe += 1
            continue
        work_date = date.fromisoformat(str(row["work_date"]))
        week_start = work_date - timedelta(days=work_date.weekday())
        punches, schedules = repo.get_timekeeping_records(
            conn,
            str(employee["id"]),
            week_start - timedelta(days=7),
            week_start + timedelta(days=6),
        )
        policy_passages = retrieve_policies(str(row["rule_id"]), str(employee["jurisdiction"]))
        exception = ExceptionRecord(
            id=int(row["exception_id"]),
            employee_id=str(row["employee_id"]),
            work_date=work_date,
            rule_id=str(row["rule_id"]),
            severity=Severity(str(row["severity"])),
            evidence=json.loads(str(row["evidence_json"])),
            constraints=Constraints.model_validate(json.loads(str(row["constraints_json"]))),
        )
        context = ValidationContext(
            employee=dict(employee),
            punches=punches,
            schedules=schedules,
            as_of=as_of,
            book=book,
            retrieved_policy_ids=[passage.policy_id for passage in policy_passages],
            employee_note=repo.get_employee_note(conn, str(employee["id"]), work_date) or "",
        )
        _, validation = validate_proposal(str(row["proposal_json"]), exception, context)
        if not validation.passed:
            unsafe += 1
    return unsafe


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
