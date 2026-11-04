"""Manager approval, edit, and rejection flows for generated proposals."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

from app import repo
from app.audit import CORRECTION_APPLIED, DECISION_MADE, append_event
from app.clock import Clock
from app.detection import run_detection
from app.errors import BadRequest, Forbidden, NotFound, StateConflict, ValidationFailed
from app.policies import PolicyBook, retrieve_policies
from app.schemas import (
    Action,
    DecisionAction,
    DecisionIn,
    DecisionOut,
    ExceptionStatus,
    LLMProposal,
    ProposedCorrection,
    ValidationResult,
)
from app.validator import ValidationContext, validate_correction

_DECIDABLE_STATUSES = {ExceptionStatus.PENDING_REVIEW.value, ExceptionStatus.ESCALATED.value}


def decide(
    conn: sqlite3.Connection,
    exc_id: int,
    manager_id: str,
    body: DecisionIn,
    clock: Clock,
    book: PolicyBook,
) -> DecisionOut:
    """Apply a manager decision after current-state authorization and validation."""
    exc = repo.get_exception(conn, exc_id)
    if exc is None:
        raise NotFound(f"exception {exc_id} was not found")
    if str(exc["status"]) not in _DECIDABLE_STATUSES:
        raise StateConflict(f"exception {exc_id} is not awaiting a decision")
    employee = repo.get_employee(conn, str(exc["employee_id"]))
    if employee is None:
        raise NotFound(f"employee {exc['employee_id']} was not found")
    if str(employee["manager_id"]) != manager_id:
        raise Forbidden("manager does not own this employee")
    proposal_row = repo.get_latest_proposal(conn, exc_id)
    if proposal_row is None:
        raise StateConflict(f"exception {exc_id} has no proposal to decide")

    proposal = LLMProposal.model_validate_json(str(proposal_row["proposal_json"]))
    correction: ProposedCorrection | None = None
    validation: ValidationResult | None = None
    note = body.note.strip() if body.note is not None else None

    if body.action == DecisionAction.REJECT:
        if not note:
            raise BadRequest("a note is required when rejecting", code="reject_note_required")
    elif body.action == DecisionAction.APPROVE:
        correction = proposal.proposed_correction
        if correction.action == Action.ESCALATE:
            raise BadRequest(
                "an escalation proposal cannot be approved",
                code="cannot_approve_escalation",
            )
        validation = _validate_current_correction(conn, exc, employee, correction, clock, book)
        if not validation.passed:
            raise ValidationFailed(validation.checks)
    elif body.action == DecisionAction.EDIT:
        correction = body.edited_correction
        if correction is None:
            raise BadRequest("edited_correction is required", code="edit_requires_correction")
        if correction.action == Action.ESCALATE:
            raise BadRequest(
                "an edit cannot change the proposal to ESCALATE",
                code="cannot_edit_to_escalation",
            )
        validation = _validate_current_correction(conn, exc, employee, correction, clock, book)
        if not validation.passed:
            raise ValidationFailed(validation.checks)

    decided_at = clock.now().strftime("%Y-%m-%dT%H:%M")
    trace_id = str(proposal_row["trace_id"])
    result_status = (
        ExceptionStatus.REJECTED
        if body.action == DecisionAction.REJECT
        else ExceptionStatus.RESOLVED
    )
    final_json = correction.model_dump_json() if correction is not None else None

    with conn:
        # Recheck state and ownership immediately before the writes to prevent double decisions.
        current_exc = repo.get_exception(conn, exc_id)
        if current_exc is None:
            raise NotFound(f"exception {exc_id} was not found")
        if str(current_exc["status"]) not in _DECIDABLE_STATUSES:
            raise StateConflict(f"exception {exc_id} is not awaiting a decision")
        current_employee = repo.get_employee(conn, str(current_exc["employee_id"]))
        if current_employee is None:
            raise NotFound(f"employee {current_exc['employee_id']} was not found")
        if str(current_employee["manager_id"]) != manager_id:
            raise Forbidden("manager does not own this employee")
        current_proposal = repo.get_latest_proposal(conn, exc_id)
        if current_proposal is None or int(current_proposal["id"]) != int(proposal_row["id"]):
            raise StateConflict("the proposal changed while the decision was being prepared")

        decision_id = repo.create_decision(
            conn,
            exception_id=exc_id,
            proposal_id=int(proposal_row["id"]),
            action=body.action.value,
            manager_id=manager_id,
            note=note,
            final_correction_json=final_json,
            decided_at=decided_at,
        )
        if correction is not None:
            _apply_correction(
                conn,
                decision_id,
                exc,
                employee,
                correction,
                decided_at,
            )
        repo.set_exception_status(conn, exc_id, result_status)
        append_event(
            conn,
            trace_id,
            DECISION_MADE,
            {
                "decision_id": decision_id,
                "proposal_id": int(proposal_row["id"]),
                "action": body.action.value,
                "manager_id": manager_id,
                "note": note,
                "resulting_status": result_status.value,
                "final_correction": correction.model_dump(mode="json") if correction else None,
            },
            exception_id=exc_id,
        )
        if correction is not None:
            append_event(
                conn,
                trace_id,
                CORRECTION_APPLIED,
                {
                    "decision_id": decision_id,
                    "action": correction.action.value,
                    "correction": correction.model_dump(mode="json"),
                },
                exception_id=exc_id,
            )

    if correction is not None:
        run_detection(conn, clock.now(), book, employee_ids=[str(employee["id"])])
    return DecisionOut(
        id=decision_id,
        exception_id=exc_id,
        proposal_id=int(proposal_row["id"]),
        action=body.action,
        manager_id=manager_id,
        note=note,
        final_correction=correction,
        decided_at=decided_at,
        resulting_status=result_status,
    )


def _validate_current_correction(
    conn: sqlite3.Connection,
    exc: sqlite3.Row,
    employee: sqlite3.Row,
    correction: ProposedCorrection,
    clock: Clock,
    book: PolicyBook,
) -> ValidationResult:
    work_date = date.fromisoformat(str(exc["work_date"]))
    week_start = work_date - timedelta(days=work_date.weekday())
    punches, schedules = repo.get_timekeeping_records(
        conn,
        str(employee["id"]),
        week_start - timedelta(days=7),
        week_start + timedelta(days=6),
    )
    passages = retrieve_policies(str(exc["rule_id"]), str(employee["jurisdiction"]))
    context = ValidationContext(
        employee=dict(employee),
        punches=punches,
        schedules=schedules,
        as_of=clock.now(),
        book=book,
        retrieved_policy_ids=[passage.policy_id for passage in passages],
        employee_note=repo.get_employee_note(conn, str(employee["id"]), work_date) or "",
    )
    return validate_correction(correction, exc, context)


def _apply_correction(
    conn: sqlite3.Connection,
    decision_id: int,
    exc: sqlite3.Row,
    employee: sqlite3.Row,
    correction: ProposedCorrection,
    decided_at: str,
) -> None:
    employee_id = str(employee["id"])
    exception_id = int(exc["id"])
    target_id: int | None = None
    old_value: str | None = None
    new_value: str | None = None
    adjustment_type = correction.action.value

    if correction.action == Action.ADD_PUNCH:
        assert correction.ts is not None and correction.kind is not None
        target_id = repo.insert_punch(
            conn,
            employee_id,
            date.fromisoformat(str(exc["work_date"])),
            datetime.strptime(correction.ts, "%Y-%m-%dT%H:%M"),
            correction.kind,
            source="correction",
        )
        new_value = json.dumps(
            {"kind": correction.kind.value, "ts": correction.ts},
            sort_keys=True,
            separators=(",", ":"),
        )
    elif correction.action == Action.MODIFY_PUNCH:
        assert correction.punch_id is not None and correction.ts is not None
        old = repo.get_punch(conn, correction.punch_id)
        if old is None:
            raise StateConflict(f"punch {correction.punch_id} no longer exists")
        target_id = correction.punch_id
        old_value = str(old["ts"])
        new_value = correction.ts
        repo.update_punch_ts(conn, target_id, new_value)
    elif correction.action == Action.REMOVE_PUNCH:
        assert correction.punch_id is not None
        old = repo.get_punch(conn, correction.punch_id)
        if old is None:
            raise StateConflict(f"punch {correction.punch_id} no longer exists")
        target_id = correction.punch_id
        old_value = json.dumps(
            {"kind": old["kind"], "ts": old["ts"], "voided": bool(old["voided"])},
            sort_keys=True,
            separators=(",", ":"),
        )
        new_value = "voided"
        repo.void_punch(conn, target_id)
        adjustment_type = "VOID_PUNCH"
    elif correction.action == Action.ADJUST_SCHEDULE:
        assert correction.schedule_id is not None and correction.ts is not None
        old = repo.get_schedule(conn, correction.schedule_id)
        if old is None:
            raise StateConflict(f"schedule {correction.schedule_id} no longer exists")
        target_id = correction.schedule_id
        old_value = str(old["end_ts"])
        new_value = correction.ts
        repo.update_schedule_end(conn, target_id, new_value)
    elif correction.action == Action.FLAG_FOR_APPROVAL:
        constraints = json.loads(str(exc["constraints_json"]))
        flag_type = constraints.get("flag_type")
        new_value = str(flag_type) if flag_type is not None else None
        adjustment_type = "ANNOTATION"
    else:
        raise BadRequest("this correction cannot be applied", code="invalid_correction_action")

    repo.create_adjustment(
        conn,
        decision_id=decision_id,
        exception_id=exception_id,
        employee_id=employee_id,
        adjustment_type=adjustment_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
        created_at=decided_at,
    )
