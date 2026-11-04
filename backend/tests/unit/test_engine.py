"""Timecard reconstruction, detection, simulation, and policy tests."""

from datetime import date, datetime

from app import repo
from app.detection import run_detection
from app.engine.rules import detect_employee
from app.engine.shifts import Punch, Sched, build_shifts
from app.engine.simulate import apply_in_memory
from app.policies import load_policies, retrieve_policies
from app.schemas import Action, Constraints, PunchKind, ProposedCorrection, Severity
from app.engine.rules import DetectedException


def test_build_shifts_marks_open_break_and_duplicate():
    work_date = date(2025, 1, 1)
    punches = [
        Punch(1, "e", work_date, datetime(2025, 1, 1, 8), PunchKind.IN),
        Punch(2, "e", work_date, datetime(2025, 1, 1, 8, 2), PunchKind.IN),
        Punch(3, "e", work_date, datetime(2025, 1, 1, 12), PunchKind.BREAK_START),
    ]
    shifts = build_shifts(punches, [], dup_window_minutes=5)
    shift = shifts[work_date]
    assert shift.duplicates == [(punches[0], punches[1])]
    assert "OPEN_BREAK" in shift.anomalies
    assert "MISSING_OUT" in shift.anomalies


def test_detection_is_idempotent_and_does_not_change_timekeeping(seeded_conn, settings, book):
    before = [tuple(row) for row in seeded_conn.execute("SELECT * FROM punches ORDER BY id")]
    second = run_detection(seeded_conn, settings.demo_now, book)
    after = [tuple(row) for row in seeded_conn.execute("SELECT * FROM punches ORDER BY id")]
    assert second.created == 0
    assert second.unchanged == 15
    assert before == after


def test_detection_supersedes_disappeared_open_exception(seeded_conn, settings, book):
    exception = seeded_conn.execute(
        "SELECT * FROM exceptions WHERE employee_id='e-001' AND rule_id='PUNCH-01'"
    ).fetchone()
    assert exception is not None
    repo.insert_punch(
        seeded_conn,
        "e-001",
        date.fromisoformat(exception["work_date"]),
        datetime(2025, 3, 12, 17, 0),
        PunchKind.OUT,
    )
    result = run_detection(seeded_conn, settings.demo_now, book, employee_ids=["e-001"])
    assert result.superseded >= 1
    updated = repo.get_exception(seeded_conn, int(exception["id"]))
    assert updated["status"] == "SUPERSEDED"


def test_simulation_add_modify_remove_and_adjust_schedule_are_copy_only(seeded_conn, book):
    exception = seeded_conn.execute(
        "SELECT * FROM exceptions WHERE employee_id='e-006' AND rule_id='PUNCH-03'"
    ).fetchone()
    employee = repo.get_employee(seeded_conn, "e-006")
    work_date = date.fromisoformat(exception["work_date"])
    punches, schedules = repo.get_timekeeping_records(
        seeded_conn, "e-006", work_date, work_date
    )
    detected = DetectedException(
        rule_id="PUNCH-03",
        employee_id="e-006",
        work_date=work_date,
        severity=Severity.MEDIUM,
        evidence={},
        constraints=Constraints.model_validate_json(exception["constraints_json"]),
        fingerprint="test",
    )
    target_id = detected.constraints.target_punch_ids[0]
    original_ids = [p.id for p in punches]
    removed_punches, _ = apply_in_memory(
        punches,
        schedules,
        ProposedCorrection(action=Action.REMOVE_PUNCH, punch_id=target_id),
        detected,
    )
    assert next(p for p in removed_punches if p.id == target_id).voided
    assert [p.id for p in punches] == original_ids
    with_value = apply_in_memory(
        punches,
        schedules,
        ProposedCorrection(action=Action.ADD_PUNCH, kind=PunchKind.OUT, ts="2025-03-10T17:00"),
        detected,
    )[0]
    assert with_value[-1].id == -1
    assert employee is not None


def test_policy_loader_has_frozen_rules_and_safe_retrieval():
    book = load_policies()
    assert len(book.rules) == 15
    assert book.global_params.low_confidence_threshold == 0.6
    retrieved = retrieve_policies("PUNCH-01", "US-STD")
    assert retrieved[0].policy_id == "PUNCH-01"
    assert len(retrieved) <= 3
