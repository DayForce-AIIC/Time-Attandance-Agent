"""Decision service state, authorization, validation, and write tests."""

import json

import pytest
from app import repo
from app.audit import verify_chain
from app.clock import Clock
from app.decisions import decide
from app.errors import BadRequest, Forbidden, StateConflict, ValidationFailed
from app.llm.faults import FaultInjector
from app.llm.mock import MockClient
from app.pipeline import propose
from app.schemas import Action, DecisionAction, DecisionIn, FaultMode


def _exception(conn, employee_id, rule_id):
    return conn.execute(
        "SELECT id FROM exceptions WHERE employee_id=? AND rule_id=?", (employee_id, rule_id)
    ).fetchone()


def test_approve_adds_correction_punch_and_audit(seeded_conn, settings, book):
    row = _exception(seeded_conn, "e-001", "PUNCH-01")
    exception_id = int(row["id"])
    proposal = propose(seeded_conn, exception_id, MockClient(), Clock(settings), book, settings)
    decision = decide(
        seeded_conn,
        exception_id,
        "m-001",
        DecisionIn(action=DecisionAction.APPROVE),
        Clock(settings),
        book,
    )
    punch = seeded_conn.execute(
        "SELECT * FROM punches WHERE employee_id='e-001' AND source='correction'"
    ).fetchone()
    assert punch["source"] == "correction"
    assert punch["ts"] == proposal.proposal.proposed_correction.ts
    assert decision.resulting_status.value == "RESOLVED"
    assert (
        seeded_conn.execute(
            "SELECT COUNT(*) FROM adjustments WHERE decision_id=?", (decision.id,)
        ).fetchone()[0]
        == 1
    )
    events = [row["event_type"] for row in repo.list_audit_events(seeded_conn, exception_id)]
    assert "DECISION_MADE" in events and "CORRECTION_APPLIED" in events
    assert verify_chain(seeded_conn).ok


def test_approve_adjust_schedule_and_rerun_detection(seeded_conn, settings, book):
    exception_id = int(_exception(seeded_conn, "e-002", "OT-02")["id"])
    proposal = propose(seeded_conn, exception_id, MockClient(), Clock(settings), book, settings)
    correction = proposal.proposal.proposed_correction
    before = repo.get_schedule(seeded_conn, correction.schedule_id)["end_ts"]
    assert before != correction.ts
    decision = decide(
        seeded_conn,
        exception_id,
        "m-001",
        DecisionIn(action=DecisionAction.APPROVE),
        Clock(settings),
        book,
    )
    assert repo.get_schedule(seeded_conn, correction.schedule_id)["end_ts"] == correction.ts
    assert (
        seeded_conn.execute(
            "SELECT type FROM adjustments WHERE decision_id=?", (decision.id,)
        ).fetchone()["type"]
        == "ADJUST_SCHEDULE"
    )
    assert (
        seeded_conn.execute(
            "SELECT COUNT(*) FROM exceptions WHERE employee_id='e-002' AND rule_id='OT-02' AND status IN ('OPEN','PENDING_REVIEW','ESCALATED')"
        ).fetchone()[0]
        == 0
    )


def test_invalid_edit_is_write_free_and_valid_edit_is_recorded(seeded_conn, settings, book):
    exception_id = int(_exception(seeded_conn, "e-001", "PUNCH-01")["id"])
    proposal = propose(seeded_conn, exception_id, MockClient(), Clock(settings), book, settings)
    correct = proposal.proposal.proposed_correction
    invalid = correct.model_copy(update={"ts": "2025-03-12T23:59"})
    counts_before = (
        seeded_conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
        seeded_conn.execute("SELECT COUNT(*) FROM adjustments").fetchone()[0],
        seeded_conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0],
    )
    with pytest.raises(ValidationFailed):
        decide(
            seeded_conn,
            exception_id,
            "m-001",
            DecisionIn(action=DecisionAction.EDIT, edited_correction=invalid),
            Clock(settings),
            book,
        )
    counts_after = (
        seeded_conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
        seeded_conn.execute("SELECT COUNT(*) FROM adjustments").fetchone()[0],
        seeded_conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0],
    )
    assert counts_after == counts_before
    decision = decide(
        seeded_conn,
        exception_id,
        "m-001",
        DecisionIn(action=DecisionAction.EDIT, edited_correction=correct),
        Clock(settings),
        book,
    )
    stored = seeded_conn.execute(
        "SELECT final_correction_json FROM decisions WHERE id=?", (decision.id,)
    ).fetchone()[0]
    assert json.loads(stored) == correct.model_dump(mode="json")


def test_reject_requires_note_and_leaves_timekeeping_unchanged(seeded_conn, settings, book):
    exception_id = int(_exception(seeded_conn, "e-014", "PUNCH-01")["id"])
    propose(seeded_conn, exception_id, MockClient(), Clock(settings), book, settings)
    punches = [tuple(row) for row in seeded_conn.execute("SELECT * FROM punches ORDER BY id")]
    schedules = [tuple(row) for row in seeded_conn.execute("SELECT * FROM schedules ORDER BY id")]
    with pytest.raises(BadRequest) as error:
        decide(
            seeded_conn,
            exception_id,
            "m-002",
            DecisionIn(action=DecisionAction.REJECT),
            Clock(settings),
            book,
        )
    assert error.value.code == "reject_note_required"
    decision = decide(
        seeded_conn,
        exception_id,
        "m-002",
        DecisionIn(action=DecisionAction.REJECT, note="Please investigate"),
        Clock(settings),
        book,
    )
    assert decision.resulting_status.value == "REJECTED"
    assert punches == [
        tuple(row) for row in seeded_conn.execute("SELECT * FROM punches ORDER BY id")
    ]
    assert schedules == [
        tuple(row) for row in seeded_conn.execute("SELECT * FROM schedules ORDER BY id")
    ]


def test_owner_double_decision_and_escalation_guards(seeded_conn, settings, book):
    exception_id = int(_exception(seeded_conn, "e-017", "PUNCH-01")["id"])
    fallback = propose(
        seeded_conn,
        exception_id,
        FaultInjector(MockClient(), lambda: FaultMode.TIMEOUT),
        Clock(settings),
        book,
        settings,
    )
    assert fallback.proposal.proposed_correction.action == Action.ESCALATE
    with pytest.raises(BadRequest) as error:
        decide(
            seeded_conn,
            exception_id,
            "m-002",
            DecisionIn(action=DecisionAction.APPROVE),
            Clock(settings),
            book,
        )
    assert error.value.code == "cannot_approve_escalation"
    with pytest.raises(Forbidden):
        decide(
            seeded_conn,
            exception_id,
            "m-001",
            DecisionIn(action=DecisionAction.REJECT, note="no"),
            Clock(settings),
            book,
        )
    decide(
        seeded_conn,
        exception_id,
        "m-002",
        DecisionIn(action=DecisionAction.REJECT, note="no"),
        Clock(settings),
        book,
    )
    with pytest.raises(StateConflict):
        decide(
            seeded_conn,
            exception_id,
            "m-002",
            DecisionIn(action=DecisionAction.REJECT, note="again"),
            Clock(settings),
            book,
        )
