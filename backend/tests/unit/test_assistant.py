"""Grounding and failure behavior for the read-only attendance assistant."""

import json

import pytest
from app.assistant import answer_attendance_question
from app.llm.base import LLMResult, LLMUnavailable, PromptBundle
from app.schemas import AssistantModelAnswer


class UnknownCitationClient:
    """Return a well-shaped answer that cites a source outside the retrieved set."""

    model = "bad-citer"

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult:
        del bundle, attempt, feedback
        answer = AssistantModelAnswer(
            in_scope=True,
            uses_evidence=True,
            answer="An unsupported claim.",
            source_ids=["case:999"],
        )
        return LLMResult(
            raw_text=json.dumps(answer.model_dump(mode="json")),
            latency_ms=2,
            prompt_tokens=None,
            completion_tokens=None,
            model=self.model,
        )


def test_assistant_rejects_unknown_citations_without_fallback(seeded_conn, book):
    with pytest.raises(LLMUnavailable, match="invalid or ungrounded"):
        answer_attendance_question(
            seeded_conn,
            "m-001",
            "What policy applies to missed punches?",
            UnknownCitationClient(),
            book,
        )
