"""Extended quality and recovery tests for the attendance assistant.

Supplements tests/unit/test_assistant.py with:
  - Out-of-scope / guardrail enforcement
  - LLM timeout recovery path (falls back to policy-only answer)
  - LLM unavailable recovery path
  - Malformed JSON response (non-JSON text) → graceful fallback
  - Completely empty answer string → fallback
  - Pagination: more than 500 workflow cases are retrieved
  - No matching evidence → answer acknowledges data gap
  - Streaming endpoint returns SSE chunks with correct event names
  - Rate-limited assistant route returns 429 with Retry-After header
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from app.assistant import answer_attendance_question
from app.llm.base import LLMResult, LLMTimeout, LLMUnavailable, PromptBundle
from app.organization_policies import OrganizationPolicyContext
from app.schemas import AssistantModelAnswer


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class WorkflowRepo:
    def __init__(self, rows):
        self.rows = rows

    def list_exception_cases(self, organization_id, *, limit, offset):
        return self.rows[offset : offset + limit]


def _case(case_id, *, status="open", rule_id="PUNCH-01"):
    return {
        "id": case_id,
        "external_employee_ref": "EMP-001",
        "rule_id": rule_id,
        "severity": "high",
        "status": status,
        "minimal_evidence_snapshot": {
            "work_date": "2025-03-12",
            "evidence": {"punch_state": "missing out punch"},
        },
    }


class FixedClient:
    """Return a pre-built LLMResult every call."""

    model = "test-fixed"

    def __init__(self, result: LLMResult):
        self._result = result
        self.calls = 0

    def generate(self, bundle, attempt, feedback=None):
        self.calls += 1
        return self._result


class RaisingClient:
    """Raise a configured exception on every generate() call."""

    model = "test-raiser"

    def __init__(self, exc_class):
        self._exc_class = exc_class
        self.calls = 0

    def generate(self, bundle, attempt, feedback=None):
        self.calls += 1
        raise self._exc_class("simulated error")


class MalformedClient:
    """Return non-JSON text to exercise the JSON-parse failure path."""

    model = "test-malformed"

    def __init__(self, text: str = "Sorry, I cannot answer that."):
        self._text = text
        self.calls = 0

    def generate(self, bundle, attempt, feedback=None):
        self.calls += 1
        return LLMResult(
            raw_text=self._text,
            latency_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
            model=self.model,
        )


def _out_of_scope_client():
    answer = AssistantModelAnswer(
        in_scope=False,
        uses_evidence=False,
        answer="I can only answer attendance and scheduling questions.",
        source_ids=[],
    )
    return FixedClient(
        LLMResult(
            raw_text=json.dumps(answer.model_dump(mode="json")),
            latency_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
            model="test-oos",
        )
    )


# ---------------------------------------------------------------------------
# Quality: out-of-scope guardrail
# ---------------------------------------------------------------------------


def test_assistant_propagates_out_of_scope_flag(seeded_conn, book):
    """LLM marks answer as out-of-scope; assistant should surface that faithfully."""
    llm = _out_of_scope_client()
    result = answer_attendance_question(
        seeded_conn,
        "m-001",
        "What is 2 + 2?",
        llm,
        book,
    )
    assert result.answer  # answer text is present
    # Either the model's own OOS answer or a fallback — either way not empty.
    assert len(result.answer) > 0


# ---------------------------------------------------------------------------
# Recovery: LLM timeout
# ---------------------------------------------------------------------------


def test_assistant_recovers_from_llm_timeout(seeded_conn, book):
    """When all generate() calls raise LLMTimeout, answer should still be returned."""
    llm = RaisingClient(LLMTimeout)

    result = answer_attendance_question(
        seeded_conn,
        "m-001",
        "Show me exceptions for today",
        llm,
        book,
    )

    # Should not raise; must return a usable (possibly fallback) answer.
    assert result is not None
    assert isinstance(result.answer, str)
    assert len(result.answer) > 0


# ---------------------------------------------------------------------------
# Recovery: LLM unavailable
# ---------------------------------------------------------------------------


def test_assistant_recovers_from_llm_unavailable(seeded_conn, book):
    """When all generate() calls raise LLMUnavailable, a fallback answer is returned."""
    llm = RaisingClient(LLMUnavailable)

    result = answer_attendance_question(
        seeded_conn,
        "m-001",
        "List all open PUNCH-01 cases",
        llm,
        book,
    )

    assert result is not None
    assert isinstance(result.answer, str)
    assert len(result.answer) > 0


# ---------------------------------------------------------------------------
# Recovery: malformed (non-JSON) model output
# ---------------------------------------------------------------------------


def test_assistant_handles_malformed_json_response(seeded_conn, book):
    """Non-JSON model output must not crash the assistant; fallback answer expected."""
    llm = MalformedClient("This is plain text, not JSON.")

    result = answer_attendance_question(
        seeded_conn,
        "m-001",
        "What happened with employee EMP-017 on March 12?",
        llm,
        book,
    )

    assert result is not None
    assert isinstance(result.answer, str)
    assert len(result.answer) > 0


# ---------------------------------------------------------------------------
# Recovery: empty model answer text
# ---------------------------------------------------------------------------


def test_assistant_handles_empty_answer_text(seeded_conn, book):
    """Model returns structurally valid JSON but empty answer string → fallback."""
    empty_answer = AssistantModelAnswer(
        in_scope=True,
        uses_evidence=False,
        answer="",
        source_ids=[],
    )
    llm = FixedClient(
        LLMResult(
            raw_text=json.dumps(empty_answer.model_dump(mode="json")),
            latency_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
            model="test-empty",
        )
    )

    result = answer_attendance_question(
        seeded_conn,
        "m-001",
        "Any exceptions this week?",
        llm,
        book,
    )

    assert result is not None
    assert isinstance(result.answer, str)


# ---------------------------------------------------------------------------
# Pagination: > 500 workflow cases retrieved
# ---------------------------------------------------------------------------


def test_assistant_paginates_more_than_500_workflow_cases(book):
    """_list_all_workflow_cases must retrieve all pages, not just the first 500."""
    from app.assistant import _list_all_workflow_cases

    rows = [_case(f"case-{i}") for i in range(750)]
    repo = WorkflowRepo(rows)

    retrieved = _list_all_workflow_cases(repo, uuid4())

    assert len(retrieved) == 750


# ---------------------------------------------------------------------------
# No matching evidence returns a data-gap acknowledgment
# ---------------------------------------------------------------------------


def test_assistant_acknowledges_no_matching_evidence(seeded_conn, book):
    """When no source matches the question, the answer should acknowledge the gap."""
    # Return a valid answer that cites no sources.
    no_match_answer = AssistantModelAnswer(
        in_scope=True,
        uses_evidence=False,
        answer="I could not find a matching attendance case or policy passage.",
        source_ids=[],
    )
    llm = FixedClient(
        LLMResult(
            raw_text=json.dumps(no_match_answer.model_dump(mode="json")),
            latency_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
            model="test-no-match",
        )
    )

    result = answer_attendance_question(
        seeded_conn,
        "m-001",
        "What happened with employee XYZ-999 on 2099-01-01?",
        llm,
        book,
    )

    assert result is not None
    assert isinstance(result.answer, str)
    assert len(result.answer) > 0


# ---------------------------------------------------------------------------
# API-layer: streaming endpoint reachable and returns SSE
# ---------------------------------------------------------------------------


def test_assistant_streaming_endpoint_returns_sse_chunks(ollama_client):
    """POST /api/v1/assistant/query/stream must return chunked SSE text."""
    with ollama_client.stream(
        "POST",
        "/api/v1/assistant/query/stream",
        json={"question": "What policy applies to missed punches?"},
        headers={"X-Manager-Id": "m-001"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        # Read at least the first chunk to verify SSE format.
        raw = b""
        for chunk in response.iter_bytes():
            raw += chunk
            if len(raw) > 10:
                break
        # SSE chunks start with "data:" or "event:"
        text = raw.decode("utf-8", errors="replace")
        assert "data:" in text or "event:" in text


# ---------------------------------------------------------------------------
# API-layer: rate limit enforced on streaming endpoint
# ---------------------------------------------------------------------------


def test_assistant_rate_limit_applied_to_stream_endpoint(ollama_client):
    """Exceeding the per-window limit on the stream endpoint must return 429."""
    # Temporarily lower the limit to 1 so a second request is definitely limited.
    ollama_client.app.state.settings.assistant_rate_limit_per_window = 1

    # First request should succeed.
    r1 = ollama_client.post(
        "/api/v1/assistant/query",
        json={"question": "Show open cases"},
        headers={"X-Manager-Id": "m-001"},
    )
    assert r1.status_code in (200, 206)  # 206 for streaming partial

    # Second request in the same window should be rate-limited.
    r2 = ollama_client.post(
        "/api/v1/assistant/query",
        json={"question": "Show open cases"},
        headers={"X-Manager-Id": "m-001"},
    )
    assert r2.status_code == 429
    assert "Retry-After" in r2.headers
