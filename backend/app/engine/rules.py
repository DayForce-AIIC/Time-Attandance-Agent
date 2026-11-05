"""Pure deterministic timecard exception detection rules."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from app.engine.shifts import Punch, Sched, Shift, build_shifts
from app.policies import GlobalParams, JurisdictionParams, PolicyBook, PolicyRule
from app.schemas import (
    Constraints,
    EmployeeOut,
    PunchKind,
    Severity,
    TimeWindow,
)


@dataclass
class DetectedException:
    """Deterministic exception record emitted by the rules engine."""

    rule_id: str
    employee_id: str
    work_date: date
    severity: Severity
    evidence: dict[str, Any]
    constraints: Constraints
    fingerprint: str


def _employee_value(employee: object, key: str) -> object:
    if isinstance(employee, Mapping):
        return employee.get(key)
    return getattr(employee, key, None)


def _local_ts(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M") if value is not None else None


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def _window(earliest: datetime, latest: datetime) -> TimeWindow | None:
    if earliest > latest:
        return None
    return TimeWindow(
        earliest=earliest.strftime("%Y-%m-%dT%H:%M"),
        latest=latest.strftime("%Y-%m-%dT%H:%M"),
    )


def _constraints(
    rule: PolicyRule,
    *,
    ts_window: TimeWindow | None = None,
    target_kind: PunchKind | None = None,
    target_punch_ids: list[int] | None = None,
    target_schedule_id: int | None = None,
    headroom_minutes: int | None = None,
) -> Constraints:
    return Constraints(
        allowed_actions=rule.allowed_actions,
        flag_type=rule.flag_type,
        ts_window=ts_window,
        target_kind=target_kind,
        target_punch_ids=target_punch_ids or [],
        target_schedule_id=target_schedule_id,
        headroom_minutes=headroom_minutes,
    )


def _fingerprint(
    rule_id: str, employee_id: str, work_date: date, discriminator: object = ""
) -> str:
    value = f"{rule_id}|{employee_id}|{work_date.isoformat()}|{discriminator}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _exception(
    rule: PolicyRule,
    employee_id: str,
    work_date: date,
    evidence: dict[str, Any],
    constraints: Constraints,
    discriminator: object = "",
) -> DetectedException:
    return DetectedException(
        rule_id=rule.id,
        employee_id=employee_id,
        work_date=work_date,
        severity=rule.severity,
        evidence=evidence,
        constraints=constraints,
        fingerprint=_fingerprint(rule.id, employee_id, work_date, discriminator),
    )


def _punch_evidence(shift: Shift) -> list[dict[str, Any]]:
    return [
        {"id": punch.id, "kind": punch.kind.value, "ts": _local_ts(punch.ts)}
        for punch in shift.punches
    ]


def _detect_punch_rules(
    shift: Shift,
    employee_id: str,
    as_of: datetime,
    grace_minutes: int,
    window_pad_minutes: int,
    book_rules: Mapping[str, PolicyRule],
) -> list[DetectedException]:
    found: list[DetectedException] = []
    work_date = shift.work_date

    if (
        "MISSING_OUT" in shift.anomalies
        and shift.in_ts is not None
        and as_of >= shift.expected_end + timedelta(minutes=grace_minutes)
    ):
        rule = book_rules["PUNCH-01"]
        closed_break_ends = [end.ts for _, end in shift.breaks if end is not None]
        evidence = {
            "in_ts": _local_ts(shift.in_ts),
            "scheduled_start": _local_ts(shift.sched.start if shift.sched else None),
            "scheduled_end": _local_ts(shift.sched.end if shift.sched else None),
            "last_punch_ts": _local_ts(shift.punches[-1].ts if shift.punches else None),
            "as_of": _local_ts(as_of),
        }
        earliest_base = max(closed_break_ends) if closed_break_ends else shift.in_ts
        found.append(
            _exception(
                rule,
                employee_id,
                work_date,
                evidence,
                _constraints(
                    rule,
                    target_kind=PunchKind.OUT,
                    ts_window=_window(
                        earliest_base + timedelta(minutes=1),
                        min(
                            shift.expected_end + timedelta(minutes=window_pad_minutes),
                            as_of,
                        ),
                    ),
                ),
            )
        )

    if "MISSING_IN" in shift.anomalies and shift.out_ts is not None:
        rule = book_rules["PUNCH-02"]
        evidence = {
            "out_ts": _local_ts(shift.out_ts),
            "scheduled_start": _local_ts(shift.sched.start if shift.sched else None),
            "scheduled_end": _local_ts(shift.sched.end if shift.sched else None),
        }
        found.append(
            _exception(
                rule,
                employee_id,
                work_date,
                evidence,
                _constraints(
                    rule,
                    target_kind=PunchKind.IN,
                    ts_window=_window(
                        max(
                            shift.expected_start - timedelta(minutes=window_pad_minutes),
                            shift.out_ts - timedelta(hours=12),
                        ),
                        shift.out_ts - timedelta(minutes=1),
                    ),
                ),
            )
        )

    duplicate_rule = book_rules["PUNCH-03"]
    for kept, dropped in shift.duplicates:
        found.append(
            _exception(
                duplicate_rule,
                employee_id,
                work_date,
                {
                    "kept_punch_id": kept.id,
                    "dropped_punch_id": dropped.id,
                    "kind": dropped.kind.value,
                    "gap_minutes": _minutes(dropped.ts - kept.ts),
                },
                _constraints(duplicate_rule, target_punch_ids=[dropped.id]),
                discriminator=dropped.id,
            )
        )

    open_breaks = [(start, end) for start, end in shift.breaks if end is None]
    for break_start, _ in open_breaks:
        if shift.out_ts is None and as_of < shift.expected_end + timedelta(minutes=grace_minutes):
            continue
        rule = book_rules["PUNCH-04"]
        break_index = shift.punches.index(break_start)
        next_punch = (
            shift.punches[break_index + 1] if break_index + 1 < len(shift.punches) else None
        )
        latest_reference = shift.out_ts or as_of
        found.append(
            _exception(
                rule,
                employee_id,
                work_date,
                {
                    "break_start_ts": _local_ts(break_start.ts),
                    "next_punch_ts": _local_ts(next_punch.ts if next_punch else None),
                },
                _constraints(
                    rule,
                    target_kind=PunchKind.BREAK_END,
                    ts_window=_window(
                        break_start.ts + timedelta(minutes=1),
                        min(
                            break_start.ts + timedelta(minutes=120),
                            latest_reference - timedelta(minutes=1),
                        ),
                    ),
                ),
            )
        )

    if "UNPARSEABLE" in shift.anomalies:
        rule = book_rules["PUNCH-99"]
        found.append(
            _exception(
                rule,
                employee_id,
                work_date,
                {"punch_sequence": _punch_evidence(shift)},
                _constraints(rule),
            )
        )
    return found


def _detect_single_shift_rules(
    shift: Shift,
    employee_id: str,
    global_params: GlobalParams,
    jurisdiction_params: JurisdictionParams,
    book_rules: Mapping[str, PolicyRule],
    overtime_eligible: bool,
) -> list[DetectedException]:
    if not shift.complete:
        return []
    found: list[DetectedException] = []
    work_date = shift.work_date
    assert shift.in_ts is not None and shift.out_ts is not None

    max_hours = global_params.max_shift_hours
    window_pad = global_params.window_pad_minutes
    max_shift_exceeded = shift.gross_minutes > max_hours * 60
    if max_shift_exceeded:
        rule = book_rules["MAX-01"]
        out_punch = next(
            punch
            for punch in reversed(shift.punches)
            if punch.kind is PunchKind.OUT and punch.ts == shift.out_ts
        )
        found.append(
            _exception(
                rule,
                employee_id,
                work_date,
                {
                    "in_ts": _local_ts(shift.in_ts),
                    "out_ts": _local_ts(shift.out_ts),
                    "gross_minutes": shift.gross_minutes,
                    "scheduled_end": _local_ts(shift.sched.end if shift.sched else None),
                },
                _constraints(
                    rule,
                    target_punch_ids=[out_punch.id],
                    ts_window=_window(
                        shift.in_ts + timedelta(minutes=1),
                        min(
                            shift.expected_end + timedelta(minutes=window_pad),
                            shift.out_ts - timedelta(minutes=1),
                        ),
                    ),
                ),
            )
        )

    meal_after = jurisdiction_params.meal_after_hours * 60
    meal_required = jurisdiction_params.meal_min_minutes
    closed_breaks = [_minutes(end.ts - start.ts) for start, end in shift.breaks if end is not None]
    if shift.gross_minutes > meal_after:
        if not closed_breaks:
            rule = book_rules["BRK-01"]
            found.append(
                _exception(
                    rule,
                    employee_id,
                    work_date,
                    {
                        "gross_minutes": shift.gross_minutes,
                        "required_break_minutes": meal_required,
                    },
                    _constraints(rule),
                )
            )
        elif max(closed_breaks) < meal_required:
            rule = book_rules["BRK-02"]
            found.append(
                _exception(
                    rule,
                    employee_id,
                    work_date,
                    {
                        "gross_minutes": shift.gross_minutes,
                        "longest_break_minutes": max(closed_breaks),
                        "required_break_minutes": meal_required,
                    },
                    _constraints(rule),
                )
            )

    daily_ot_hours = jurisdiction_params.daily_ot_hours
    if (
        overtime_eligible
        and daily_ot_hours is not None
        and shift.worked_minutes > daily_ot_hours * 60
    ):
        rule = book_rules["OT-03"]
        found.append(
            _exception(
                rule,
                employee_id,
                work_date,
                {
                    "worked_minutes": shift.worked_minutes,
                    "threshold_minutes": daily_ot_hours * 60,
                },
                _constraints(rule),
            )
        )

    # MAX-01 already captures the primary defect when an overnight overlong
    # shift also differs from its scheduled end; avoid a duplicate SCHED-01.
    if shift.sched is not None and not max_shift_exceeded:
        in_deviation = _minutes(shift.in_ts - shift.sched.start)
        out_deviation = _minutes(shift.out_ts - shift.sched.end)
        deviation_threshold = global_params.sched_deviation_minutes
        if abs(in_deviation) > deviation_threshold or abs(out_deviation) > deviation_threshold:
            rule = book_rules["SCHED-01"]
            found.append(
                _exception(
                    rule,
                    employee_id,
                    work_date,
                    {
                        "scheduled_start": _local_ts(shift.sched.start),
                        "scheduled_end": _local_ts(shift.sched.end),
                        "actual_in": _local_ts(shift.in_ts),
                        "actual_out": _local_ts(shift.out_ts),
                        "in_dev_minutes": in_deviation,
                        "out_dev_minutes": out_deviation,
                    },
                    _constraints(rule),
                )
            )
    return found


def _detect_unscheduled_rule(
    shift: Shift,
    employee_id: str,
    book_rules: Mapping[str, PolicyRule],
) -> DetectedException | None:
    if shift.sched is not None or (shift.in_ts is None and shift.out_ts is None):
        return None
    rule = book_rules["SCHED-02"]
    return _exception(
        rule,
        employee_id,
        shift.work_date,
        {
            "first_punch_ts": _local_ts(shift.punches[0].ts if shift.punches else None),
            "last_punch_ts": _local_ts(shift.punches[-1].ts if shift.punches else None),
        },
        _constraints(rule),
    )


def _week_bounds(as_of: datetime) -> tuple[date, date, date]:
    current = as_of.date()
    start = current - timedelta(days=current.weekday())
    return start, start - timedelta(days=7), start + timedelta(days=6)


def _detect_weekly_overtime(
    shifts: Mapping[date, Shift],
    employee_id: str,
    as_of: datetime,
    week_start: date,
    week_end: date,
    jurisdiction_params: JurisdictionParams,
    global_params: GlobalParams,
    book_rules: Mapping[str, PolicyRule],
    suppress_ot02: bool = False,
) -> list[DetectedException]:
    threshold = jurisdiction_params.weekly_ot_hours * 60
    complete_week_shifts = [
        shift
        for work_date, shift in sorted(shifts.items())
        if week_start <= work_date <= min(week_end, as_of.date()) and shift.complete
    ]
    cumulative = 0
    overtime_found = False
    found: list[DetectedException] = []
    for shift in complete_week_shifts:
        cumulative += shift.worked_minutes
        if cumulative > threshold:
            rule = book_rules["OT-01"]
            found.append(
                _exception(
                    rule,
                    employee_id,
                    shift.work_date,
                    {
                        "week_start": week_start.isoformat(),
                        "weekly_minutes": cumulative,
                        "threshold_minutes": threshold,
                        "overage_minutes": cumulative - threshold,
                    },
                    _constraints(rule),
                    discriminator=week_start.isoformat(),
                )
            )
            overtime_found = True
            break
    if overtime_found:
        return found

    completed_minutes = sum(shift.worked_minutes for shift in complete_week_shifts)
    incomplete_minutes = 0
    for work_date, shift in shifts.items():
        if not (week_start <= work_date <= as_of.date()):
            continue
        has_activity = bool(shift.anomalies or shift.punches)
        if has_activity and not shift.complete:
            incomplete_minutes += shift.planned_worked_minutes

    future_scheduled = [
        shift
        for work_date, shift in shifts.items()
        if week_start <= work_date <= week_end
        and shift.sched is not None
        and shift.sched.start > as_of
        and not shift.punches
    ]
    future_minutes = sum(shift.planned_worked_minutes for shift in future_scheduled)
    projected = completed_minutes + incomplete_minutes + future_minutes
    if projected <= threshold or suppress_ot02:
        return []

    rule = book_rules["OT-02"]
    target = (
        max(
            future_scheduled,
            key=lambda shift: shift.sched.start if shift.sched is not None else datetime.min,
        )
        if future_scheduled
        else None
    )
    ts_window = None
    target_schedule_id = None
    if target is not None and target.sched is not None:
        target_schedule_id = target.sched.id
        ts_window = _window(
            target.sched.start
            + timedelta(
                minutes=(
                    global_params.min_adjusted_worked_hours * 60
                    + target.sched.planned_break_minutes
                )
            ),
            target.sched.end - timedelta(minutes=1),
        )
    headroom = threshold - completed_minutes - incomplete_minutes
    found.append(
        _exception(
            rule,
            employee_id,
            as_of.date(),
            {
                "week_start": week_start.isoformat(),
                "completed_minutes": completed_minutes,
                "remaining_scheduled_minutes": incomplete_minutes + future_minutes,
                "projected_minutes": projected,
                "threshold_minutes": threshold,
            },
            _constraints(
                rule,
                ts_window=ts_window,
                target_schedule_id=target_schedule_id,
                headroom_minutes=headroom,
            ),
            discriminator=week_start.isoformat(),
        )
    )
    return found


def _detect_rest_rules(
    shifts: Mapping[date, Shift],
    employee_id: str,
    as_of: datetime,
    week_start: date,
    min_rest_hours: int,
    book_rules: Mapping[str, PolicyRule],
) -> list[DetectedException]:
    found: list[DetectedException] = []
    history_start = week_start - timedelta(days=7)
    ordered = [
        shift
        for work_date, shift in sorted(shifts.items())
        if history_start <= work_date <= as_of.date()
    ]
    for previous, current in zip(ordered, ordered[1:]):
        if not (week_start <= current.work_date <= as_of.date()):
            continue
        previous_end = previous.out_ts or (previous.sched.end if previous.sched else None)
        next_start = current.in_ts or (current.sched.start if current.sched else None)
        if previous_end is None or next_start is None:
            continue
        has_started = current.in_ts is not None or (
            current.sched is not None and current.sched.start <= as_of
        )
        if not has_started:
            continue
        gap_minutes = _minutes(next_start - previous_end)
        required_minutes = min_rest_hours * 60
        if gap_minutes < required_minutes:
            rule = book_rules["REST-01"]
            found.append(
                _exception(
                    rule,
                    employee_id,
                    current.work_date,
                    {
                        "previous_end": _local_ts(previous_end),
                        "next_start": _local_ts(next_start),
                        "gap_minutes": gap_minutes,
                        "required_minutes": required_minutes,
                    },
                    _constraints(rule),
                )
            )
    return found


def _detect_consecutive_days(
    shifts: Mapping[date, Shift],
    employee_id: str,
    as_of: datetime,
    week_start: date,
    max_consecutive_days: int,
    book_rules: Mapping[str, PolicyRule],
) -> list[DetectedException]:
    streak_dates = sorted(
        work_date
        for work_date, shift in shifts.items()
        if week_start - timedelta(days=7) <= work_date <= as_of.date() and shift.in_ts is not None
    )
    required_days = max_consecutive_days + 1
    if not streak_dates:
        return []

    runs: list[list[date]] = []
    run = [streak_dates[0]]
    for work_date in streak_dates[1:]:
        if work_date == run[-1] + timedelta(days=1):
            run.append(work_date)
        else:
            runs.append(run)
            run = [work_date]
    runs.append(run)

    found: list[DetectedException] = []
    rule = book_rules["DAY-01"]
    for dates in runs:
        if len(dates) < required_days:
            continue
        violation_date = dates[required_days - 1]
        if not (week_start <= violation_date <= as_of.date()):
            continue
        streak_start = dates[0]
        found.append(
            _exception(
                rule,
                employee_id,
                violation_date,
                {
                    "streak_start": streak_start.isoformat(),
                    "streak_length": required_days,
                },
                _constraints(rule),
                discriminator=streak_start.isoformat(),
            )
        )
    return found


def detect_employee(
    employee: EmployeeOut | Mapping[str, object],
    punches: list[Punch],
    schedules: list[Sched],
    as_of: datetime,
    book: PolicyBook,
    disabled_rule_ids: frozenset[str] | set[str] = frozenset(),
) -> list[DetectedException]:
    """Detect all policy exceptions for one employee without mutating inputs."""
    if as_of.utcoffset() is not None:
        raise ValueError("as_of must be a timezone-naive local datetime")
    employee_id_value = _employee_value(employee, "id") or _employee_value(employee, "employee_id")
    jurisdiction = _employee_value(employee, "jurisdiction")
    if not isinstance(employee_id_value, str) or not employee_id_value:
        raise ValueError("employee must provide a non-empty id")
    if not isinstance(jurisdiction, str) or jurisdiction not in book.jurisdictions:
        raise ValueError("employee must provide a jurisdiction present in the policy book")

    employee_id = employee_id_value
    # SQLite returns INTEGER values while external providers commonly return bools.
    eligibility = _employee_value(employee, "ot_eligible")
    overtime_eligible = eligibility is True or (isinstance(eligibility, int) and eligibility == 1)
    start, history_start, week_end = _week_bounds(as_of)
    lower_bound = history_start
    relevant_punches = [
        punch
        for punch in punches
        if lower_bound <= punch.work_date <= week_end and punch.employee_id == employee_id
    ]
    relevant_schedules = [
        sched
        for sched in schedules
        if lower_bound <= sched.work_date <= week_end and sched.employee_id == employee_id
    ]
    global_policy = book.global_params
    jurisdiction_policy = book.jurisdictions[jurisdiction]
    book_rules = {rule.id: rule for rule in book.rules}
    shifts = build_shifts(
        relevant_punches,
        relevant_schedules,
        dup_window_minutes=global_policy.dup_window_minutes,
    )

    detected: list[DetectedException] = []
    for work_date, shift in sorted(shifts.items()):
        if not (start <= work_date <= as_of.date()):
            continue
        detected.extend(
            _detect_punch_rules(
                shift,
                employee_id,
                as_of,
                global_policy.grace_missing_out_minutes,
                global_policy.window_pad_minutes,
                book_rules,
            )
        )
        if not shift.anomalies:
            unscheduled = _detect_unscheduled_rule(shift, employee_id, book_rules)
            if unscheduled is not None:
                detected.append(unscheduled)
            detected.extend(
                _detect_single_shift_rules(
                    shift,
                    employee_id,
                    global_policy,
                    jurisdiction_policy,
                    book_rules,
                    overtime_eligible,
                )
            )

    suppress_weekly_risk = any(
        finding.rule_id in {"MAX-01", "OT-03"}
        and finding.rule_id not in disabled_rule_ids
        and start <= finding.work_date <= as_of.date()
        for finding in detected
    )
    weekly = (
        _detect_weekly_overtime(
            shifts,
            employee_id,
            as_of,
            start,
            week_end,
            jurisdiction_policy,
            global_policy,
            book_rules,
            suppress_ot02=suppress_weekly_risk,
        )
        if overtime_eligible
        else []
    )
    detected.extend(weekly)
    detected.extend(
        _detect_rest_rules(
            shifts,
            employee_id,
            as_of,
            start,
            jurisdiction_policy.min_rest_hours,
            book_rules,
        )
    )
    detected.extend(
        _detect_consecutive_days(
            shifts,
            employee_id,
            as_of,
            start,
            jurisdiction_policy.max_consecutive_days,
            book_rules,
        )
    )
    if disabled_rule_ids:
        detected = [finding for finding in detected if finding.rule_id not in disabled_rule_ids]
    return sorted(
        detected,
        key=lambda item: (item.work_date, item.rule_id, item.fingerprint),
    )
