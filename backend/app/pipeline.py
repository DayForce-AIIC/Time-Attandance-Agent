"""Audited LLM proposal generation with validation, retry, and safe fallback."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, timedelta
from time import perf_counter

from app import repo
from app.audit import (
    FALLBACK,
    LLM_CALL,
    LLM_ERROR,
    PROPOSAL_CREATED,
    PROMPT_BUILT,
    RETRY,
    SCHEMA_VALIDATION,
    VALIDATION_RESULT,
    append_event,
)
from app.clock import Clock
from app.config import Settings
from app.engine.shifts import build_shifts
from app.errors import NotFound, StateConflict
from app.llm.base import LLMClient, LLMTimeout, LLMUnavailable, PromptBundle
from app.llm.prompt import build_prompt
from app.policies import PolicyBook, retrieve_policies
from app.schemas import (
    Action,
    CheckResult,
    ExceptionStatus,
    LLMProposal,
    ProposalOut,
    ProposalSource,
    ProposedCorrection,
    Constraints,
    Severity,
    ValidationResult,
)
from app.validator import ExceptionRecord, ValidationContext, validate_proposal
from app.logging_setup import trace_id_var


_NON_PROPOSABLE_STATUSES = {
    ExceptionStatus.RESOLVED.value,
    ExceptionStatus.REJECTED.value,
    ExceptionStatus.SUPERSEDED.value,
}


def propose(
    conn: sqlite3.Connection,
    exc_id: int,
    llm: LLMClient,
    clock: Clock,
    book: PolicyBook,
    settings: Settings,
) -> ProposalOut:
    """Generate, validate, audit, and persist one manager-review proposal."""
    del settings  # Reserved for provider/pipeline policy extensions.
    exc = repo.get_exception(conn, exc_id)
    if exc is None:
        raise NotFound(f"exception {exc_id} was not found")
    if str(exc["status"]) in _NON_PROPOSABLE_STATUSES:
        raise StateConflict(f"exception {exc_id} cannot be proposed in status {exc['status']}")

    employee = repo.get_employee(conn, str(exc["employee_id"]))
    if employee is None:
        raise NotFound(f"employee {exc['employee_id']} was not found")
    work_date = date.fromisoformat(str(exc["work_date"]))
    as_of = clock.now()
    week_start = work_date - timedelta(days=work_date.weekday())
    history_start = week_start - timedelta(days=7)
    week_end = week_start + timedelta(days=6)
    punches, schedules = repo.get_timekeeping_records(
        conn, str(employee["id"]), history_start, week_end
    )
    day_punches = [punch for punch in punches if punch.work_date == work_date]
    schedule_for_day = next(
        (schedule for schedule in schedules if schedule.work_date == work_date), None
    )
    note = repo.get_employee_note(conn, str(employee["id"]), work_date)
    policies = retrieve_policies(str(exc["rule_id"]), str(employee["jurisdiction"]))
    policy_ids = [passage.policy_id for passage in policies]
    week_shifts = build_shifts(
        [punch for punch in punches if week_start <= punch.work_date <= min(week_end, as_of.date())],
        [schedule for schedule in schedules if week_start <= schedule.work_date <= week_end],
        dup_window_minutes=book.global_params.dup_window_minutes,
    )
    completed_minutes = sum(
        shift.worked_minutes
        for workday, shift in week_shifts.items()
        if week_start <= workday <= as_of.date() and shift.complete
    )
    weekly_threshold = book.jurisdictions[str(employee["jurisdiction"])].weekly_ot_hours * 60
    week_summary = {
        "completed_minutes": completed_minutes,
        "threshold_minutes": weekly_threshold,
    }
    ctx = ValidationContext(
        employee=dict(employee),
        punches=punches,
        schedules=schedules,
        as_of=as_of,
        book=book,
        retrieved_policy_ids=policy_ids,
        employee_note=note or "",
    )
    record = ExceptionRecord(
        id=int(exc["id"]),
        employee_id=str(exc["employee_id"]),
        work_date=work_date,
        rule_id=str(exc["rule_id"]),
        severity=Severity(str(exc["severity"])),
        evidence=json.loads(str(exc["evidence_json"])),
        constraints=Constraints.model_validate(json.loads(str(exc["constraints_json"]))),
    )

    trace_id = uuid.uuid4().hex
    context_token = trace_id_var.set(trace_id)
    try:
        total_latency_ms = 0
        results: list[tuple[LLMProposal | None, ValidationResult]] = []
        last_feedback: str | list[CheckResult] | None = None
        failed_checks: list[dict[str, object]] = []
        attempt_count = 0
        with conn:
            for attempt in (1, 2):
                attempt_count = attempt
                bundle = build_prompt(
                    exc,
                    dict(employee),
                    day_punches,
                    schedule_for_day,
                    week_summary,
                    policies,
                    note,
                    feedback=last_feedback,
                )
                _audit_prompt(conn, trace_id, exc_id, bundle, attempt)
                generation_started = perf_counter()
                try:
                    llm_result = llm.generate(bundle, attempt=attempt)
                    total_latency_ms += llm_result.latency_ms
                except (LLMTimeout, LLMUnavailable) as error:
                    total_latency_ms += round((perf_counter() - generation_started) * 1000)
                    append_event(
                        conn,
                        trace_id,
                        LLM_ERROR,
                        {"attempt": attempt, "error": str(error)},
                        exception_id=exc_id,
                    )
                    proposal, validation = validate_proposal(
                        "LLM unavailable", record, ctx
                    )
                    last_feedback = "LLM unavailable"
                else:
                    append_event(
                        conn,
                        trace_id,
                        LLM_CALL,
                        {
                            "attempt": attempt,
                            "model": llm_result.model,
                            "latency_ms": llm_result.latency_ms,
                            "prompt_tokens": llm_result.prompt_tokens,
                            "completion_tokens": llm_result.completion_tokens,
                            "raw_output": llm_result.raw_text,
                        },
                        exception_id=exc_id,
                    )
                    proposal, validation = validate_proposal(
                        llm_result.raw_text, record, ctx
                    )
                    last_feedback = validation.checks

                results.append((proposal, validation))
                _audit_validation(conn, trace_id, exc_id, attempt, validation)
                if validation.passed and proposal is not None:
                    source = ProposalSource.LLM
                    chosen_proposal = proposal
                    final_validation = validation
                    break
                failed_checks = _failed_check_payload(validation)
                if attempt == 1:
                    append_event(
                        conn,
                        trace_id,
                        RETRY,
                        {"attempt": 2, "failed_checks": failed_checks},
                        exception_id=exc_id,
                    )
            else:
                reason = _fallback_reason(results)
                append_event(
                    conn,
                    trace_id,
                    FALLBACK,
                    {"reason": reason, "failed_checks": failed_checks},
                    exception_id=exc_id,
                )
                chosen_proposal = LLMProposal(
                    exception_id=exc_id,
                    explanation=(
                        f"Automated suggestion unavailable ({reason}). "
                        f"Manager review is required under {exc['rule_id']}."
                    ),
                    proposed_correction=ProposedCorrection(action=Action.ESCALATE),
                    policy_ids_cited=[str(exc["rule_id"])],
                    confidence=0.0,
                )
                final_validation = ValidationResult(
                    passed=True,
                    checks=[
                        CheckResult(
                            id="FB-01",
                            name="Fallback used",
                            passed=True,
                            level="warning",
                            detail=_format_failed_checks(results),
                        )
                    ],
                )
                source = ProposalSource.FALLBACK

            model = str(getattr(llm, "model", "unknown"))
            created_at = as_of.strftime("%Y-%m-%dT%H:%M")
            proposal_id = repo.create_proposal(
                conn,
                exception_id=exc_id,
                trace_id=trace_id,
                source=source.value,
                proposal_json=chosen_proposal.model_dump_json(),
                validation_json=final_validation.model_dump_json(),
                attempts=attempt_count,
                model=model,
                latency_ms=total_latency_ms,
                created_at=created_at,
            )
            resulting_status = (
                ExceptionStatus.ESCALATED
                if source == ProposalSource.FALLBACK
                or chosen_proposal.proposed_correction.action == Action.ESCALATE
                else ExceptionStatus.PENDING_REVIEW
            )
            repo.set_exception_status(conn, exc_id, resulting_status)
            append_event(
                conn,
                trace_id,
                PROPOSAL_CREATED,
                {
                    "proposal_id": proposal_id,
                    "source": source.value,
                    "attempts": attempt_count,
                    "status": resulting_status.value,
                    "model": model,
                    "latency_ms": total_latency_ms,
                },
                exception_id=exc_id,
            )

        return ProposalOut(
            id=proposal_id,
            exception_id=exc_id,
            trace_id=trace_id,
            source=source,
            proposal=chosen_proposal,
            validation=final_validation,
            attempts=attempt_count,
            model=model,
            latency_ms=total_latency_ms,
            created_at=created_at,
        )
    finally:
        trace_id_var.reset(context_token)


def _audit_prompt(
    conn: sqlite3.Connection,
    trace_id: str,
    exception_id: int,
    bundle: PromptBundle,
    attempt: int,
) -> None:
    prompt_hash = hashlib.sha256(
        f"{bundle.system}\n{bundle.user}".encode("utf-8")
    ).hexdigest()
    append_event(
        conn,
        trace_id,
        PROMPT_BUILT,
        {
            "attempt": attempt,
            "prompt_hash": prompt_hash,
            "system": bundle.system,
            "user": bundle.user,
        },
        exception_id=exception_id,
    )


def _audit_validation(
    conn: sqlite3.Connection,
    trace_id: str,
    exception_id: int,
    attempt: int,
    validation: ValidationResult,
) -> None:
    schema_check = next((check for check in validation.checks if check.id == "V-01"), None)
    append_event(
        conn,
        trace_id,
        SCHEMA_VALIDATION,
        {
            "attempt": attempt,
            "passed": bool(schema_check and schema_check.passed),
            "detail": schema_check.detail if schema_check else "schema check unavailable",
        },
        exception_id=exception_id,
    )
    append_event(
        conn,
        trace_id,
        VALIDATION_RESULT,
        {
            "attempt": attempt,
            "passed": validation.passed,
            "failed_checks": _failed_check_payload(validation),
            "side_effects": [effect.model_dump(mode="json") for effect in validation.side_effects],
        },
        exception_id=exception_id,
    )


def _failed_check_payload(validation: ValidationResult) -> list[dict[str, object]]:
    return [
        {"id": check.id, "name": check.name, "detail": check.detail}
        for check in validation.checks
        if not check.passed and check.level == "error"
    ]


def _fallback_reason(
    results: list[tuple[LLMProposal | None, ValidationResult]],
) -> str:
    reasons = []
    for attempt, (_, validation) in enumerate(results, start=1):
        failed = _failed_check_payload(validation)
        if failed:
            check_ids = ", ".join(str(item["id"]) for item in failed)
            reasons.append(f"attempt {attempt} failed checks {check_ids}")
    reason = "; ".join(reasons) or "automated validation did not pass"
    return reason[:320]


def _format_failed_checks(
    results: list[tuple[LLMProposal | None, ValidationResult]],
) -> str:
    return _fallback_reason(results)
