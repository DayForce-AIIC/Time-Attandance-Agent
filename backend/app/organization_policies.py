"""Resolve tenant-owned policy edits for detection, review, and LLM context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.policies import POLICY_BOOK, PolicyBook, PolicyRule, retrieve_policies
from app.schemas import PolicyPassage, Severity
from app.workflow_repository import PostgresWorkflowRepository, Row


@dataclass(frozen=True)
class CompanyPassage:
    passage: PolicyPassage
    applies_to_rule_ids: tuple[str, ...]
    revision: int


@dataclass(frozen=True)
class OrganizationPolicyContext:
    """Effective detector book and reference passages for one organization."""

    book: PolicyBook
    custom_passages: tuple[CompanyPassage, ...] = ()
    disabled_rule_ids: frozenset[str] = frozenset()
    revisions_by_policy_id: dict[str, int] | None = None

    def passages_for_rule(self, rule_id: str, jurisdiction: str) -> list[PolicyPassage]:
        """Return the rule, its related rules, and company guidance for this rule."""
        passages = [
            passage
            for passage in retrieve_policies(rule_id, jurisdiction, self.book)
            if passage.policy_id == rule_id or passage.policy_id not in self.disabled_rule_ids
        ]
        passages.extend(
            item.passage
            for item in self.custom_passages
            if not item.applies_to_rule_ids or rule_id in item.applies_to_rule_ids
        )
        return passages

    def all_passages(self) -> list[PolicyPassage]:
        return [
            *(
                PolicyPassage(policy_id=rule.id, title=rule.title, text=rule.text)
                for rule in self.book.rules
                if rule.id not in self.disabled_rule_ids
            ),
            *(item.passage for item in self.custom_passages),
        ]

    def snapshot_for_rule(self, rule_id: str, jurisdiction: str) -> list[dict[str, Any]]:
        """Capture the exact active policy passages and revisions used for a proposal."""
        revisions = self.revisions_by_policy_id or {}
        return [
            {
                "policy_id": passage.policy_id,
                "title": passage.title,
                "text": passage.text,
                "revision": revisions.get(passage.policy_id),
            }
            for passage in self.passages_for_rule(rule_id, jurisdiction)
        ]


def resolve_organization_policy_context(
    repository: PostgresWorkflowRepository,
    organization_id: UUID,
    base_book: PolicyBook = POLICY_BOOK,
) -> OrganizationPolicyContext:
    """Bootstrap bundled standards and construct one organization-specific view."""
    repository.ensure_standard_policies(organization_id, base_book)
    rows = repository.list_organization_policies(organization_id)
    by_rule = {str(row["rule_id"]): row for row in rows if row["kind"] == "standard"}
    disabled = frozenset(
        str(row["rule_id"]) for row in rows if row["kind"] == "standard" and not row["is_active"]
    )
    effective_rules: list[dict[str, Any]] = []
    for base_rule in base_book.rules:
        rule_row = by_rule.get(base_rule.id)
        if rule_row is None:
            effective_rules.append(base_rule.model_dump(mode="json"))
            continue
        effective_rules.append(
            base_rule.model_copy(
                update={
                    "title": str(rule_row["title"]),
                    "text": str(rule_row["text"]),
                    "severity": Severity(str(rule_row["severity"])),
                }
            ).model_dump(mode="json")
        )
    effective_book = base_book.model_copy(
        update={"rules": [PolicyRule.model_validate(rule) for rule in effective_rules]}
    )
    custom = tuple(
        CompanyPassage(
            passage=PolicyPassage(
                policy_id=str(row["policy_id"]),
                title=str(row["title"]),
                text=str(row["text"]),
            ),
            applies_to_rule_ids=tuple(str(item) for item in row["applies_to_rule_ids"]),
            revision=int(row["revision"]),
        )
        for row in rows
        if row["kind"] == "custom" and row["is_active"]
    )
    revisions = {str(row["policy_id"]): int(row["revision"]) for row in rows}
    return OrganizationPolicyContext(
        book=effective_book,
        custom_passages=custom,
        disabled_rule_ids=disabled,
        revisions_by_policy_id=revisions,
    )


def standard_policy_row_to_output(row: Row) -> dict[str, Any]:
    """Convert a database policy row into the stable API representation."""
    return {
        "policy_id": str(row["policy_id"]),
        "kind": str(row["kind"]),
        "rule_id": str(row["rule_id"]) if row.get("rule_id") is not None else None,
        "category": str(row["category"]),
        "title": str(row["title"]),
        "text": str(row["text"]),
        "severity": str(row["severity"]) if row.get("severity") is not None else None,
        "applies_to_rule_ids": [str(item) for item in row.get("applies_to_rule_ids", [])],
        "is_active": bool(row["is_active"]),
        "revision": int(row["revision"]),
        "updated_at": row["updated_at"],
        "archived_at": row.get("archived_at"),
    }
