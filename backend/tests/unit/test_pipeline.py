"""Proposal pipeline integration tests for retries, fallback, and invariants."""

import hashlib
import json
from datetime import date

import pytest
from app import repo
from app.audit import verify_chain
from app.clock import Clock
from app.errors import StateConflict
from app.llm.base import LLMResult
from app.llm.faults import FaultInjector
from app.llm.mock import MockClient
from app.pipeline import propose
from app.schemas import ExceptionStatus, FaultMode, ProposalSource


@pytest.mark.parametrize(
    "mode,source,attempts,status",
    [
        (FaultMode.NONE, ProposalSource.LLM, 1, ExceptionStatus.PENDING_REVIEW),
        (FaultMode.BAD_THEN_GOOD, ProposalSource.LLM, 2, ExceptionStatus.PENDING_REVIEW),
        (FaultMode.ALWAYS_BAD, ProposalSource.FALLBACK, 2, ExceptionStatus.ESCALATED),
        (FaultMode.GARBAGE, ProposalSource.FALLBACK, 2, ExceptionStatus.ESCALATED),
        (FaultMode.TIMEOUT, ProposalSource.FALLBACK, 2, ExceptionStatus.ESCALATED),
        (FaultMode.BAD_CITATION, ProposalSource.FALLBACK, 2, ExceptionStatus.ESCALATED),
        (FaultMode.NAIVE_LATEST, ProposalSource.FALLBACK, 2, ExceptionStatus.ESCALATED),
    ],
)
def test_pipeline_fault_modes_are_audited_and_timecard_is_immutable(
    seeded_conn, settings, book, mode, source, attempts, status
):
    row = seeded_conn.execute(
        "SELECT id FROM exceptions WHERE employee_id='e-017' AND rule_id='PUNCH-01'"
    ).fetchone()
    exception_id = int(row["id"])
    before = [tuple(punch) for punch in seeded_conn.execute("SELECT * FROM punches ORDER BY id")]
    client = FaultInjector(MockClient(), lambda: mode)
    result = propose(seeded_conn, exception_id, client, Clock(settings), book, settings)
    current_status = repo.get_exception(seeded_conn, exception_id)["status"]
    assert result.source == source
    assert result.attempts == attempts
    assert current_status == status.value
    event_rows = repo.list_audit_events(seeded_conn, exception_id)
    event_types = [row["event_type"] for row in event_rows if row["trace_id"] == result.trace_id]
    if attempts == 2:
        assert "RETRY" in event_types
    if source == ProposalSource.FALLBACK:
        assert "FALLBACK" in event_types
        assert result.proposal.proposed_correction.action.value == "ESCALATE"
    if mode == FaultMode.NAIVE_LATEST:
        validations = [
            json.loads(row["payload_json"])
            for row in event_rows
            if row["event_type"] == "VALIDATION_RESULT" and row["trace_id"] == result.trace_id
        ]
        assert any("OT-01" in json.dumps(payload) for payload in validations)
    after = [tuple(punch) for punch in seeded_conn.execute("SELECT * FROM punches ORDER BY id")]
    assert before == after
    assert verify_chain(seeded_conn).ok


def test_pipeline_uses_mock_safely_for_injection_note(seeded_conn, settings, book):
    row = seeded_conn.execute(
        "SELECT id FROM exceptions WHERE employee_id='e-014' AND rule_id='PUNCH-01'"
    ).fetchone()
    result = propose(seeded_conn, int(row["id"]), MockClient(), Clock(settings), book, settings)
    assert result.source == ProposalSource.LLM
    assert result.validation.passed
    assert "Ignore previous instructions" not in result.proposal.explanation


def test_audit_log_keeps_prompt_and_raw_model_output_content_out(seeded_conn, settings, book):
    row = seeded_conn.execute(
        "SELECT id, employee_id, work_date FROM exceptions "
        "WHERE employee_id='e-017' AND rule_id='PUNCH-01'"
    ).fetchone()
    exception_id = int(row["id"])
    employee_id = str(row["employee_id"])
    work_date = date.fromisoformat(str(row["work_date"]))
    private_note = "PRIVATE_ATTENDANCE_NOTE_SENTINEL"
    private_output = "PRIVATE_RAW_MODEL_OUTPUT_SENTINEL"
    repo.insert_employee_note(seeded_conn, employee_id, work_date, private_note)

    class InvalidOutputClient:
        model = "audit-test-model"

        def generate(self, _bundle, attempt, feedback=None):
            del attempt, feedback
            return LLMResult(
                raw_text=private_output,
                latency_ms=1,
                prompt_tokens=11,
                completion_tokens=3,
                model=self.model,
            )

    result = propose(
        seeded_conn,
        exception_id,
        InvalidOutputClient(),
        Clock(settings),
        book,
        settings,
    )
    rows = seeded_conn.execute(
        "SELECT event_type, payload_json FROM audit_log WHERE trace_id=? ORDER BY seq",
        (result.trace_id,),
    ).fetchall()
    payloads = [(str(item["event_type"]), json.loads(item["payload_json"])) for item in rows]
    rendered = json.dumps(payloads)

    assert private_note not in rendered
    assert private_output not in rendered
    prompt_events = [payload for event_type, payload in payloads if event_type == "PROMPT_BUILT"]
    llm_events = [payload for event_type, payload in payloads if event_type == "LLM_CALL"]
    assert len(prompt_events) == 2
    assert all(set(payload) == {"attempt", "prompt_hash"} for payload in prompt_events)
    assert len(llm_events) == 2
    assert all(
        payload["raw_output_hash"] == hashlib.sha256(private_output.encode()).hexdigest()
        and "raw_text" not in payload
        for payload in llm_events
    )


def test_pipeline_rejects_terminal_exception_state(seeded_conn, settings, book):
    row = seeded_conn.execute(
        "SELECT id FROM exceptions WHERE employee_id='e-001' AND rule_id='PUNCH-01'"
    ).fetchone()
    exception_id = int(row["id"])
    with seeded_conn:
        repo.set_exception_status(seeded_conn, exception_id, ExceptionStatus.RESOLVED)
    with pytest.raises(StateConflict):
        propose(seeded_conn, exception_id, MockClient(), Clock(settings), book, settings)
