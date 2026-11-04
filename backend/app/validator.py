"""Validate model proposals and manager edits against case constraints and policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Sequence

from pydantic import ValidationError

from app.engine.rules import DetectedException, detect_employee
from app.engine.shifts import Punch, Sched
from app.engine.simulate import apply_in_memory
from app.policies import PolicyBook
from app.schemas import (
    Action,
    CheckResult,
    Constraints,
    LLMProposal,
    ProposedCorrection,
    Severity,
    SideEffect,
    ValidationResult,
)


@dataclass(frozen=True)
class ExceptionRecord:
    """Normalized exception data consumed by proposal validation."""

    id: int
    employee_id: str
    work_date: date
    rule_id: str
    severity: Severity
    evidence: dict[str, Any]
    constraints: Constraints


@dataclass
class ValidationContext:
    """Timekeeping and policy state needed to validate a proposed change."""

    employee: dict[str, object]
    punches: list[Punch]
    schedules: list[Sched]
    as_of: datetime
    book: PolicyBook
    retrieved_policy_ids: Sequence[str]
    employee_note: str = ""


_RULE_TOKEN = re.compile(r"\b[A-Z]{3,5}-\d{2}\b")
_INJECTION_PHRASES = (
    "ignore previous",
    "ignore all",
    "system prompt",
    "disregard",
    "pol-999",
)


def validate_proposal(
    raw_text_or_obj: str | dict[str, Any] | LLMProposal,
    exc: object,
    ctx: ValidationContext,
) -> tuple[LLMProposal | None, ValidationResult]:
    """Run every model-output check and return parsed output plus its checklist."""
    proposal, schema_error = _parse_proposal(raw_text_or_obj)
    checks: list[CheckResult] = [
        _check(
            "V-01",
            "Schema valid",
            proposal is not None,
            schema_error or "proposal matches the required schema",
        )
    ]
    if proposal is None:
        checks.extend(
            _skipped_check(check_id, name)
            for check_id, name in (
                ("V-02", "Exception id matches"),
                ("V-03", "Citations grounded"),
                ("V-04", "Action allowed"),
                ("V-05", "Timestamp in window"),
                ("V-06", "Target belongs to case"),
                ("V-07", "Simulation is safe"),
                ("V-08", "Confidence gate"),
                ("V-09", "Explanation hygiene"),
                ("V-10", "No injection echo"),
            )
        )
        return None, ValidationResult(passed=False, checks=checks)

    record = _normalize_exception(exc)
    checks.append(
        _check(
            "V-02",
            "Exception id matches",
            proposal.exception_id == record.id,
            f"proposal exception_id={proposal.exception_id}; expected {record.id}",
        )
    )
    cited = set(proposal.policy_ids_cited)
    retrieved = set(ctx.retrieved_policy_ids)
    citations_ok = cited.issubset(retrieved) and record.rule_id in cited
    checks.append(
        _check(
            "V-03",
            "Citations grounded",
            citations_ok,
            "all citations are retrieved and the exception rule is cited"
            if citations_ok
            else f"citations must be retrieved and include {record.rule_id}",
        )
    )

    correction = proposal.proposed_correction
    checks.append(_check_action(correction, record))
    timestamp_check = _check_timestamp(correction, record)
    checks.append(timestamp_check)
    checks.append(_check_target(correction, record))
    simulation_check, side_effects = _check_simulation(correction, record, ctx)
    checks.append(simulation_check)
    if side_effects:
        checks.append(_side_effect_warning(side_effects))
    threshold = ctx.book.global_params.low_confidence_threshold
    confidence_ok = proposal.confidence >= threshold
    checks.append(
        _check(
            "V-08",
            "Confidence gate",
            confidence_ok,
            f"confidence {proposal.confidence:.2f}; required at least {threshold:.2f}",
        )
    )
    checks.append(_check_explanation(proposal, record, ctx))
    checks.append(_check_injection(proposal, record, ctx))
    passed = all(check.passed for check in checks if check.level == "error")
    return proposal, ValidationResult(passed=passed, checks=checks, side_effects=side_effects)


def validate_correction(
    correction: ProposedCorrection, exc: object, ctx: ValidationContext
) -> ValidationResult:
    """Validate a manager edit using the case, timestamp, target, and simulation checks."""
    record = _normalize_exception(exc)
    checks = [
        _check_action(correction, record),
        _check_timestamp(correction, record),
        _check_target(correction, record),
    ]
    simulation_check, side_effects = _check_simulation(correction, record, ctx)
    checks.append(simulation_check)
    if side_effects:
        checks.append(_side_effect_warning(side_effects))
    passed = all(check.passed for check in checks if check.level == "error")
    return ValidationResult(passed=passed, checks=checks, side_effects=side_effects)


def _parse_proposal(
    raw_text_or_obj: str | dict[str, Any] | LLMProposal,
) -> tuple[LLMProposal | None, str | None]:
    try:
        if isinstance(raw_text_or_obj, LLMProposal):
            return raw_text_or_obj, None
        if isinstance(raw_text_or_obj, str):
            raw = raw_text_or_obj.strip()
            if raw.startswith("```json") and raw.endswith("```"):
                raw = raw[len("```json") : -len("```")].strip()
            value = json.loads(raw)
        elif isinstance(raw_text_or_obj, dict):
            value = raw_text_or_obj
        else:
            return None, "proposal must be JSON text, an object, or LLMProposal"
        return LLMProposal.model_validate(value), None
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        return None, f"invalid proposal: {exc}"


def _normalize_exception(exc: object) -> ExceptionRecord:
    if isinstance(exc, ExceptionRecord):
        return exc
    raw_constraints = _field(exc, "constraints", "constraints_json", default={})
    constraints = _json_mapping(raw_constraints)
    raw_evidence = _field(exc, "evidence", "evidence_json", default={})
    evidence = _json_mapping(raw_evidence)
    raw_date = _field(exc, "work_date")
    if isinstance(raw_date, datetime):
        work_date = raw_date.date()
    elif isinstance(raw_date, date):
        work_date = raw_date
    else:
        work_date = date.fromisoformat(str(raw_date))
    severity_value = _field(exc, "severity", default=Severity.MEDIUM)
    return ExceptionRecord(
        id=int(str(_field(exc, "id", default=0))),
        employee_id=str(_field(exc, "employee_id")),
        work_date=work_date,
        rule_id=str(_field(exc, "rule_id")),
        severity=Severity(severity_value),
        evidence=evidence,
        constraints=Constraints.model_validate(constraints),
    )


def _json_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    return dict(value) if isinstance(value, dict) else {}


def _field(value: object, key: str, *alternates: str, default: object = None) -> object:
    keys = (key, *alternates)
    for candidate in keys:
        if isinstance(value, dict) and candidate in value:
            return value[candidate]
        keys_method = getattr(value, "keys", None)
        if callable(keys_method):
            available = keys_method()
            if candidate in available:
                return value[candidate]  # type: ignore[index]
        if hasattr(value, candidate):
            return getattr(value, candidate)
    return default


def _check(
    check_id: str,
    name: str,
    passed: bool,
    detail: str,
    level: Literal["error", "warning"] = "error",
) -> CheckResult:
    return CheckResult(id=check_id, name=name, passed=passed, level=level, detail=detail)


def _skipped_check(check_id: str, name: str) -> CheckResult:
    return _check(check_id, name, False, "skipped: no valid proposal")


def _check_action(correction: ProposedCorrection, exc: ExceptionRecord) -> CheckResult:
    allowed = set(exc.constraints.allowed_actions)
    passed = correction.action in allowed
    return _check(
        "V-04",
        "Action allowed",
        passed,
        f"{correction.action.value} is allowed"
        if passed
        else f"{correction.action.value} is not in allowed_actions",
    )


def _check_timestamp(correction: ProposedCorrection, exc: ExceptionRecord) -> CheckResult:
    if correction.action == Action.ESCALATE:
        return _check("V-05", "Timestamp in window", True, "no change proposed")
    if correction.ts is None:
        return _check("V-05", "Timestamp in window", True, "correction has no timestamp")
    window = exc.constraints.ts_window
    if window is None:
        return _check(
            "V-05", "Timestamp in window", False, "timestamp supplied but case has no window"
        )
    try:
        timestamp = datetime.strptime(correction.ts, "%Y-%m-%dT%H:%M")
        earliest = datetime.strptime(window.earliest, "%Y-%m-%dT%H:%M")
        latest = datetime.strptime(window.latest, "%Y-%m-%dT%H:%M")
    except ValueError:
        return _check("V-05", "Timestamp in window", False, "timestamp or window is invalid")
    in_window = earliest <= timestamp <= latest
    if correction.action == Action.ADD_PUNCH:
        in_window = in_window and correction.kind == exc.constraints.target_kind
    return _check(
        "V-05",
        "Timestamp in window",
        in_window,
        "timestamp is within the inclusive window and punch kind matches"
        if in_window
        else "timestamp is outside the inclusive window or ADD_PUNCH kind mismatches target_kind",
    )


def _check_target(correction: ProposedCorrection, exc: ExceptionRecord) -> CheckResult:
    target_ok = True
    detail = "no target id supplied"
    if correction.punch_id is not None:
        target_ok = correction.punch_id in exc.constraints.target_punch_ids
        detail = (
            f"punch {correction.punch_id} belongs to this case"
            if target_ok
            else f"punch {correction.punch_id} is not a target of this case"
        )
    if correction.schedule_id is not None:
        target_ok = correction.schedule_id == exc.constraints.target_schedule_id
        detail = (
            f"schedule {correction.schedule_id} belongs to this case"
            if target_ok
            else f"schedule {correction.schedule_id} is not a target of this case"
        )
    return _check("V-06", "Target belongs to case", target_ok, detail)


def _check_simulation(
    correction: ProposedCorrection, exc: ExceptionRecord, ctx: ValidationContext
) -> tuple[CheckResult, list[SideEffect]]:
    if correction.action == Action.ESCALATE:
        return _check("V-07", "Simulation is safe", True, "no change proposed"), []
    try:
        detected_exception = DetectedException(
            rule_id=exc.rule_id,
            employee_id=exc.employee_id,
            work_date=exc.work_date,
            severity=exc.severity,
            evidence=exc.evidence,
            constraints=exc.constraints,
            fingerprint=f"validation|{exc.rule_id}|{exc.work_date.isoformat()}",
        )
        updated_punches, updated_schedules = apply_in_memory(
            ctx.punches, ctx.schedules, correction, detected_exception
        )
        before = detect_employee(ctx.employee, ctx.punches, ctx.schedules, ctx.as_of, ctx.book)
        after = detect_employee(
            ctx.employee, updated_punches, updated_schedules, ctx.as_of, ctx.book
        )
    except (TypeError, ValueError, KeyError, AssertionError) as error:
        return (
            _check(
                "V-07", "Simulation is safe", False, f"simulation could not be applied: {error}"
            ),
            [],
        )

    before_keys = {_finding_key(finding) for finding in before}
    after_keys = {_finding_key(finding) for finding in after}
    failures: list[str] = []
    if correction.action in {Action.ADD_PUNCH, Action.MODIFY_PUNCH, Action.REMOVE_PUNCH}:
        if (exc.rule_id, exc.work_date) in after_keys:
            failures.append(f"original {exc.rule_id} still fires on {exc.work_date.isoformat()}")
    if correction.action == Action.ADJUST_SCHEDULE:
        if ("OT-02", exc.work_date) in after_keys:
            failures.append("OT-02 still fires after the schedule adjustment")
        target = next((s for s in updated_schedules if s.id == correction.schedule_id), None)
        if target is None:
            failures.append("target schedule is missing from simulation context")
        else:
            minimum = ctx.book.global_params.min_adjusted_worked_hours * 60
            planned = max(
                0,
                int((target.end - target.start).total_seconds() // 60)
                - target.planned_break_minutes,
            )
            if planned < minimum:
                failures.append(
                    f"adjusted schedule leaves {planned} planned worked minutes; minimum is {minimum}"
                )

    new_findings = [finding for finding in after if _finding_key(finding) not in before_keys]
    new_high = [finding for finding in new_findings if finding.severity == Severity.HIGH]
    side_effects = [
        SideEffect(
            rule_id=finding.rule_id,
            severity=finding.severity,
            work_date=finding.work_date.isoformat(),
            summary=f"New {finding.rule_id} exception detected after proposed correction.",
        )
        for finding in new_findings
        if finding.severity != Severity.HIGH
    ]
    if new_high:
        failures.extend(
            f"new high-severity {finding.rule_id} on {finding.work_date.isoformat()}"
            for finding in new_high
        )
    if failures:
        return _check("V-07", "Simulation is safe", False, "; ".join(failures)), side_effects
    if side_effects:
        summary = "; ".join(f"{item.rule_id} ({item.severity.value})" for item in side_effects)
        return _check(
            "V-07",
            "Simulation is safe",
            True,
            f"no new high-severity exceptions; lower-severity side effects: {summary}",
        ), side_effects
    return _check(
        "V-07", "Simulation is safe", True, "no original or new high-severity exception remains"
    ), []


def _side_effect_warning(side_effects: list[SideEffect]) -> CheckResult:
    details = "; ".join(
        f"{item.rule_id} ({item.severity.value}) on {item.work_date}" for item in side_effects
    )
    return _check(
        "V-07-SIDE",
        "Lower-severity side effects",
        True,
        details,
        level="warning",
    )


def _finding_key(finding: DetectedException) -> tuple[str, date]:
    return finding.rule_id, finding.work_date


def _check_explanation(
    proposal: LLMProposal, exc: ExceptionRecord, ctx: ValidationContext
) -> CheckResult:
    known_rules = {rule.id for rule in ctx.book.rules}
    citations = set(proposal.policy_ids_cited)
    tokens = set(_RULE_TOKEN.findall(proposal.explanation))
    unknown = tokens - known_rules
    uncited = tokens - citations
    has_http = "http" in proposal.explanation.lower()
    passed = not unknown and not uncited and not has_http
    issues = []
    if unknown:
        issues.append(f"unknown rule tokens: {', '.join(sorted(unknown))}")
    if uncited:
        issues.append(f"uncited rule tokens: {', '.join(sorted(uncited))}")
    if has_http:
        issues.append("explanation contains a URL")
    detail = (
        "; ".join(issues)
        if issues
        else f"rule references are known and cited for {exc.rule_id}; no URL"
    )
    return _check("V-09", "Explanation hygiene", passed, detail)


def _check_injection(
    proposal: LLMProposal, exc: ExceptionRecord, ctx: ValidationContext
) -> CheckResult:
    correction = proposal.proposed_correction
    controlled_text = f"{proposal.explanation} {correction.note or ''}".lower()
    echoed = [phrase for phrase in _INJECTION_PHRASES if phrase in controlled_text]
    note_text = (ctx.employee_note or "").lower()
    note_only_citations = [
        policy_id
        for policy_id in proposal.policy_ids_cited
        if policy_id.lower() in note_text and policy_id not in set(ctx.retrieved_policy_ids)
    ]
    passed = not echoed and not note_only_citations
    issues = []
    if echoed:
        issues.append(f"model output echoes blocked text: {', '.join(echoed)}")
    if note_only_citations:
        issues.append(f"citation appears only in employee note: {', '.join(note_only_citations)}")
    detail = "; ".join(issues) if issues else "model-controlled fields contain no injection echo"
    return _check("V-10", "No injection echo", passed, detail)
