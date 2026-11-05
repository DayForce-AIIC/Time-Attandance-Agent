"""Independent proposal safety checks used by the evaluation harness.

This module deliberately does not import app.validator.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta

KNOWN_POLICY_IDS = {
    "PUNCH-01", "PUNCH-02", "PUNCH-03", "PUNCH-04", "PUNCH-99", "MAX-01",
    "BRK-01", "BRK-02", "OT-01", "OT-02", "OT-03", "SCHED-01", "SCHED-02",
    "REST-01", "DAY-01",
}


def check_proposal(case: dict, proposal: dict | None) -> dict[str, object]:
    """Return oracle verdict and individual rule explanations for one case."""
    expected = case["expected"]
    if expected is None:
        return {"passed": proposal is None, "checks": ["clean case produced no proposal"]}
    if proposal is None:
        return {"passed": False, "checks": ["no proposal was produced"]}

    checks: list[str] = []
    correction = proposal.get("proposed_correction") or {}
    action = correction.get("action")
    cited = proposal.get("policy_ids_cited") or []
    passed = True
    if action not in expected["allowed_actions"]:
        checks.append(f"action {action} is outside the expected action set")
        passed = False
    else:
        checks.append("action is allowed")
    if expected["rule_id"] not in cited or any(policy_id not in KNOWN_POLICY_IDS for policy_id in cited):
        checks.append("citations are missing the expected rule or contain an unknown policy")
        passed = False
    else:
        checks.append("citations are known and include the expected rule")

    timestamp = correction.get("ts")
    if timestamp is not None:
        window = expected["safe_window"]
        if window is None or not (window["earliest"] <= timestamp <= window["latest"]):
            checks.append("timestamp is outside the expected safe window")
            passed = False
        else:
            checks.append("timestamp is inside the expected safe window")

    if expected.get("must_not_create_high") and action in {"ADD_PUNCH", "MODIFY_PUNCH", "REMOVE_PUNCH"}:
        minutes = _weekly_worked_minutes(case, correction, expected["work_date"])
        threshold = _weekly_threshold_minutes(case)
        if minutes > threshold:
            checks.append(f"corrected weekly work {minutes} minutes exceeds {threshold}")
            passed = False
        else:
            checks.append(f"corrected weekly work {minutes} minutes stays within {threshold}")

    return {"passed": passed, "checks": checks}


def _weekly_worked_minutes(case: dict, correction: dict, target_work_date: str) -> int:
    """Recompute completed shift hours independently from the application engine."""
    workday = date.fromisoformat(target_work_date)
    week_start = workday - timedelta(days=workday.weekday())
    week_end = week_start + timedelta(days=6)
    punches = deepcopy(case["punches"])
    action = correction["action"]
    if action == "ADD_PUNCH":
        punches.append(
            {
                "id": -1,
                "work_date": target_work_date,
                "ts": correction["ts"],
                "kind": correction["kind"],
                "voided": False,
            }
        )
    elif action == "MODIFY_PUNCH":
        target = next((p for p in punches if p["id"] == correction.get("punch_id")), None)
        if target is not None:
            target["ts"] = correction["ts"]
    elif action == "REMOVE_PUNCH":
        punches = [p for p in punches if p["id"] != correction.get("punch_id")]

    groups: dict[str, list[dict]] = {}
    for punch in punches:
        day = date.fromisoformat(punch["work_date"])
        if week_start <= day <= week_end and not punch.get("voided", False):
            groups.setdefault(punch["work_date"], []).append(punch)

    total = 0
    for events in groups.values():
        events.sort(key=lambda event: (event["ts"], event["id"]))
        in_time = None
        break_start = None
        break_minutes = 0
        for event in events:
            timestamp = datetime.strptime(event["ts"], "%Y-%m-%dT%H:%M")
            if event["kind"] == "IN" and in_time is None:
                in_time = timestamp
            elif event["kind"] == "BREAK_START":
                break_start = timestamp
            elif event["kind"] == "BREAK_END" and break_start is not None:
                break_minutes += max(0, int((timestamp - break_start).total_seconds() // 60))
                break_start = None
            elif event["kind"] == "OUT" and in_time is not None:
                gross = max(0, int((timestamp - in_time).total_seconds() // 60))
                total += max(0, gross - break_minutes)
                in_time = None
                break_minutes = 0
    return total


def _weekly_threshold_minutes(case: dict) -> int:
    """Return the independent weekly threshold for the case's jurisdiction."""
    weekly_hours = {"US-STD": 40, "US-CA": 40}[case["employee"]["jurisdiction"]]
    return weekly_hours * 60
