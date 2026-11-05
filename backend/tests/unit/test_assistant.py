"""Grounding and failure behavior for the read-only attendance assistant."""

import json
from uuid import uuid4

from app.assistant import (
    _list_all_workflow_cases,
    _retrieve_workflow_sources,
    answer_attendance_question,
)
from app.llm.base import LLMResult, PromptBundle
from app.organization_policies import CompanyPassage, OrganizationPolicyContext
from app.schemas import AssistantModelAnswer, PolicyPassage


class WorkflowCaseRepository:
    """Small paginated stand-in for organization-scoped exception storage."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_exception_cases(self, organization_id, *, limit, offset):
        del organization_id
        self.calls += 1
        return self.rows[offset : offset + limit]


def _workflow_case(case_id, *, status="open", rule_id="PUNCH-01"):
    return {
        "id": case_id,
        "external_employee_ref": "EMP-104",
        "rule_id": rule_id,
        "severity": "high",
        "status": status,
        "minimal_evidence_snapshot": {
            "work_date": "2025-03-12",
            "evidence": {"punch_state": "missing out punch"},
        },
    }


def test_assistant_answer_schema_allows_many_matches_and_long_all_answer():
    source_ids = [f"case:{index}" for index in range(60)]
    answer = AssistantModelAnswer(
        in_scope=True,
        uses_evidence=True,
        answer="All matching records were retrieved. " + ("record " * 200),
        source_ids=source_ids,
    )

    assert len(answer.source_ids) == 60
    assert len(answer.answer) > 1200


class UnknownCitationClient:
    """Return a well-shaped answer that cites a source outside the retrieved set."""

    model = "bad-citer"

    def __init__(self):
        self.calls = 0

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult:
        del bundle, attempt, feedback
        self.calls += 1
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


def test_assistant_retries_then_returns_sources_for_unknown_citations(seeded_conn, book):
    llm = UnknownCitationClient()

    answer = answer_attendance_question(
        seeded_conn,
        "m-001",
        "What policy applies to missed punches?",
        llm,
        book,
    )

    assert llm.calls == 2
    assert answer.used_fallback is True
    assert answer.sources
    assert "Matching active policies" in answer.answer


def test_workflow_retrieval_uses_company_policy_text_and_rule_tags(book):
    custom_policy = CompanyPassage(
        passage=PolicyPassage(
            policy_id="COMPANY-CLOSURE",
            title="Temporary site closure",
            text="During a temporary site closure, staff must report missing clock-out events.",
        ),
        applies_to_rule_ids=("PUNCH-01",),
        revision=3,
    )
    context = OrganizationPolicyContext(book=book, custom_passages=(custom_policy,))
    repository = WorkflowCaseRepository([_workflow_case("case-open")])

    sources = _retrieve_workflow_sources(
        repository,
        uuid4(),
        "What does the temporary site closure guidance say?",
        context,
    )

    assert any(source.source_id == "case:case-open" for source in sources)
    company_source = next(
        source for source in sources if source.source_id == "policy:COMPANY-CLOSURE"
    )
    assert "Applies to rules: PUNCH-01" in company_source.excerpt
    assert "temporary site closure" in company_source.excerpt


def test_workflow_retrieval_filters_all_open_cases_without_including_resolved(book):
    context = OrganizationPolicyContext(book=book)
    repository = WorkflowCaseRepository(
        [
            _workflow_case("case-open", status="open"),
            _workflow_case("case-resolved", status="resolved"),
        ]
    )

    sources = _retrieve_workflow_sources(repository, uuid4(), "Show all open cases", context)

    case_ids = {source.source_id for source in sources if source.kind == "case"}
    assert case_ids == {"case:case-open"}


def test_workflow_case_retrieval_pages_past_first_500_rows():
    rows = [_workflow_case(f"case-{index}") for index in range(501)]
    repository = WorkflowCaseRepository(rows)

    retrieved = _list_all_workflow_cases(repository, uuid4())

    assert len(retrieved) == 501
    assert repository.calls == 2
