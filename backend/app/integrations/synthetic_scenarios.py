"""Bundled mock timecards that exercise every implemented attendance rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.integrations.schemas import AttendanceRecord, PunchEvidence, ScheduleEvidence

SCENARIO_WEEK = date(2025, 3, 10)
_ZONE = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class SyntheticEventScenario:
    """A source record whose attendance range is designed to trigger a rule."""

    rule_id: str
    employee_ref: str
    record_ref: str
    version: str = "1"


def synthetic_scenario_records() -> tuple[list[AttendanceRecord], list[SyntheticEventScenario]]:
    """Return deterministic records plus one event target for each frozen rule."""
    records: list[AttendanceRecord] = []
    scenarios: list[SyntheticEventScenario] = []

    def add_record(
        employee_ref: str,
        slug: str,
        work_date: date,
        *,
        schedule: tuple[str, str] | None,
        punches: list[tuple[str, str]],
        jurisdiction: str = "US-STD",
        planned_break_minutes: int = 30,
    ) -> AttendanceRecord:
        record_ref = f"tcday_demo_{slug}_{work_date.isoformat()}"
        schedule_evidence = (
            ScheduleEvidence(
                record_ref=f"shift_demo_{slug}_{work_date.isoformat()}",
                start=_local(work_date, schedule[0]),
                end=_local(work_date, schedule[1]),
                planned_break_minutes=planned_break_minutes,
            )
            if schedule is not None
            else None
        )
        punch_evidence = [
            PunchEvidence(
                record_ref=f"punch_demo_{slug}_{work_date.isoformat()}_{index}",
                kind=kind,
                at=_local(work_date, at),
                source="synthetic-device",
            )
            for index, (kind, at) in enumerate(punches, start=1)
        ]
        record = AttendanceRecord(
            record_ref=record_ref,
            employee_ref=employee_ref,
            version="1",
            updated_at=datetime.combine(work_date, time(23, 59), _ZONE).astimezone(timezone.utc),
            work_date=work_date,
            timezone="America/Chicago",
            jurisdiction=jurisdiction,
            ot_eligible=True,
            schedule=schedule_evidence,
            punches=punch_evidence,
        )
        records.append(record)
        return record

    def target(rule_id: str, employee_ref: str, record: AttendanceRecord) -> None:
        scenarios.append(
            SyntheticEventScenario(
                rule_id=rule_id,
                employee_ref=employee_ref,
                record_ref=record.record_ref,
            )
        )

    week = SCENARIO_WEEK

    employee = "emp_demo_punch_01"
    target(
        "PUNCH-01",
        employee,
        add_record(
            employee,
            "punch_01",
            week,
            schedule=("09:00", "17:00"),
            punches=[("IN", "09:00")],
        ),
    )

    employee = "emp_demo_punch_02"
    target(
        "PUNCH-02",
        employee,
        add_record(
            employee,
            "punch_02",
            week,
            schedule=("09:00", "17:00"),
            punches=[("OUT", "17:00")],
        ),
    )

    employee = "emp_demo_punch_03"
    target(
        "PUNCH-03",
        employee,
        add_record(
            employee,
            "punch_03",
            week,
            schedule=("09:00", "17:00"),
            punches=[
                ("IN", "09:00"),
                ("IN", "09:02"),
                ("BREAK_START", "12:00"),
                ("BREAK_END", "12:30"),
                ("OUT", "17:00"),
            ],
        ),
    )

    employee = "emp_demo_punch_04"
    target(
        "PUNCH-04",
        employee,
        add_record(
            employee,
            "punch_04",
            week,
            schedule=("09:00", "17:00"),
            punches=[("IN", "09:00"), ("BREAK_START", "12:00"), ("OUT", "17:00")],
        ),
    )

    employee = "emp_demo_punch_99"
    target(
        "PUNCH-99",
        employee,
        add_record(
            employee,
            "punch_99",
            week,
            schedule=("09:00", "17:00"),
            punches=[("IN", "09:00"), ("BREAK_END", "12:00"), ("OUT", "17:00")],
        ),
    )

    employee = "emp_demo_max_01"
    target(
        "MAX-01",
        employee,
        add_record(
            employee,
            "max_01",
            week,
            schedule=("09:00", "17:00"),
            punches=[
                ("IN", "06:00"),
                ("BREAK_START", "14:00"),
                ("BREAK_END", "14:30"),
                ("OUT", "23:00"),
            ],
        ),
    )

    employee = "emp_demo_brk_01"
    target(
        "BRK-01",
        employee,
        add_record(
            employee,
            "brk_01",
            week,
            schedule=("09:00", "17:00"),
            punches=[("IN", "09:00"), ("OUT", "17:00")],
        ),
    )

    employee = "emp_demo_brk_02"
    target(
        "BRK-02",
        employee,
        add_record(
            employee,
            "brk_02",
            week,
            schedule=("09:00", "17:00"),
            punches=[
                ("IN", "09:00"),
                ("BREAK_START", "12:00"),
                ("BREAK_END", "12:15"),
                ("OUT", "17:00"),
            ],
        ),
    )

    employee = "emp_demo_ot_01"
    ot_record = None
    for offset in range(3):
        work_date = date.fromordinal(week.toordinal() + offset)
        ot_record = add_record(
            employee,
            "ot_01",
            work_date,
            schedule=("06:00", "20:30"),
            punches=[
                ("IN", "06:00"),
                ("BREAK_START", "13:00"),
                ("BREAK_END", "13:30"),
                ("OUT", "20:30"),
            ],
        )
    assert ot_record is not None
    target("OT-01", employee, ot_record)

    employee = "emp_demo_ot_02"
    add_record(
        employee,
        "ot_02_completed",
        week,
        schedule=("08:00", "17:30"),
        punches=[
            ("IN", "08:00"),
            ("BREAK_START", "12:00"),
            ("BREAK_END", "12:30"),
            ("OUT", "17:30"),
        ],
    )
    target(
        "OT-02",
        employee,
        add_record(
            employee,
            "ot_02_event",
            date.fromordinal(week.toordinal() + 1),
            schedule=None,
            punches=[],
        ),
    )
    for offset in (2, 3, 4):
        work_date = date.fromordinal(week.toordinal() + offset)
        add_record(
            employee,
            "ot_02_future",
            work_date,
            schedule=("08:00", "19:30"),
            punches=[],
        )

    employee = "emp_demo_ot_03"
    target(
        "OT-03",
        employee,
        add_record(
            employee,
            "ot_03",
            week,
            schedule=("08:00", "17:30"),
            punches=[
                ("IN", "08:00"),
                ("BREAK_START", "12:00"),
                ("BREAK_END", "12:30"),
                ("OUT", "17:30"),
            ],
            jurisdiction="US-CA",
        ),
    )

    employee = "emp_demo_sched_01"
    target(
        "SCHED-01",
        employee,
        add_record(
            employee,
            "sched_01",
            week,
            schedule=("09:00", "17:30"),
            punches=[
                ("IN", "09:45"),
                ("BREAK_START", "12:30"),
                ("BREAK_END", "13:00"),
                ("OUT", "17:30"),
            ],
        ),
    )

    employee = "emp_demo_sched_02"
    target(
        "SCHED-02",
        employee,
        add_record(
            employee,
            "sched_02",
            week,
            schedule=None,
            punches=[
                ("IN", "09:00"),
                ("BREAK_START", "12:00"),
                ("BREAK_END", "12:30"),
                ("OUT", "17:00"),
            ],
        ),
    )

    employee = "emp_demo_rest_01"
    add_record(
        employee,
        "rest_01_previous",
        week,
        schedule=("09:00", "23:00"),
        punches=[
            ("IN", "09:00"),
            ("BREAK_START", "13:00"),
            ("BREAK_END", "13:30"),
            ("OUT", "23:00"),
        ],
    )
    target(
        "REST-01",
        employee,
        add_record(
            employee,
            "rest_01_event",
            date.fromordinal(week.toordinal() + 1),
            schedule=("07:00", "15:30"),
            punches=[
                ("IN", "07:00"),
                ("BREAK_START", "11:00"),
                ("BREAK_END", "11:30"),
                ("OUT", "15:30"),
            ],
        ),
    )

    employee = "emp_demo_day_01"
    day_record = None
    for offset in range(7):
        work_date = date.fromordinal(week.toordinal() + offset)
        day_record = add_record(
            employee,
            "day_01",
            work_date,
            schedule=("09:00", "17:00"),
            punches=[
                ("IN", "09:00"),
                ("BREAK_START", "12:00"),
                ("BREAK_END", "12:30"),
                ("OUT", "17:00"),
            ],
        )
    assert day_record is not None
    target("DAY-01", employee, day_record)

    return records, scenarios


def _local(work_date: date, value: str) -> datetime:
    """Build one synthetic time in the provider's stable local timezone."""
    return datetime.combine(work_date, time.fromisoformat(value), _ZONE)
