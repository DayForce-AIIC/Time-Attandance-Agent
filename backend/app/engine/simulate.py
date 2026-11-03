"""Apply a proposed correction to copied in-memory timekeeping records."""

from dataclasses import replace
from datetime import datetime

from app.engine.rules import DetectedException
from app.engine.shifts import Punch, Sched
from app.schemas import Action, ProposedCorrection


def _parse_ts(value: str | None) -> datetime:
    if value is None:
        raise ValueError("correction requires a timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def apply_in_memory(
    punches: list[Punch],
    schedules: list[Sched],
    correction: ProposedCorrection,
    exception: DetectedException,
) -> tuple[list[Punch], list[Sched]]:
    """Return copied records with the requested change applied and no DB writes."""
    updated_punches = list(punches)
    updated_schedules = list(schedules)

    if correction.action is Action.ADD_PUNCH:
        if correction.kind is None:
            raise ValueError("ADD_PUNCH requires a punch kind")
        updated_punches.append(
            Punch(
                id=-1,
                employee_id=exception.employee_id,
                work_date=exception.work_date,
                ts=_parse_ts(correction.ts),
                kind=correction.kind,
            )
        )
    elif correction.action is Action.MODIFY_PUNCH:
        if correction.punch_id is None:
            raise ValueError("MODIFY_PUNCH requires a punch id")
        target_index = next(
            (index for index, punch in enumerate(updated_punches) if punch.id == correction.punch_id),
            None,
        )
        if target_index is None:
            raise ValueError(f"punch {correction.punch_id} does not belong to the correction context")
        updated_punches[target_index] = replace(
            updated_punches[target_index], ts=_parse_ts(correction.ts)
        )
    elif correction.action is Action.REMOVE_PUNCH:
        if correction.punch_id is None:
            raise ValueError("REMOVE_PUNCH requires a punch id")
        target_index = next(
            (index for index, punch in enumerate(updated_punches) if punch.id == correction.punch_id),
            None,
        )
        if target_index is None:
            raise ValueError(f"punch {correction.punch_id} does not belong to the correction context")
        updated_punches[target_index] = replace(updated_punches[target_index], voided=True)
    elif correction.action is Action.ADJUST_SCHEDULE:
        if correction.schedule_id is None:
            raise ValueError("ADJUST_SCHEDULE requires a schedule id")
        target_index = next(
            (index for index, schedule in enumerate(updated_schedules) if schedule.id == correction.schedule_id),
            None,
        )
        if target_index is None:
            raise ValueError(
                f"schedule {correction.schedule_id} does not belong to the correction context"
            )
        updated_schedules[target_index] = replace(
            updated_schedules[target_index], end=_parse_ts(correction.ts)
        )
    elif correction.action not in {Action.FLAG_FOR_APPROVAL, Action.ESCALATE}:
        raise ValueError(f"unsupported correction action: {correction.action}")

    return updated_punches, updated_schedules
