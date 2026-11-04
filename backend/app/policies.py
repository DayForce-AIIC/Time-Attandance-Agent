"""Validated, structured policy data and deterministic policy retrieval."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import Action, FlagType, PolicyPassage, Severity

FROZEN_RULE_IDS = (
    "PUNCH-01",
    "PUNCH-02",
    "PUNCH-03",
    "PUNCH-04",
    "PUNCH-99",
    "MAX-01",
    "BRK-01",
    "BRK-02",
    "OT-01",
    "OT-02",
    "OT-03",
    "SCHED-01",
    "SCHED-02",
    "REST-01",
    "DAY-01",
)
POLICY_PATH = Path(__file__).with_name("policies.json")


class GlobalParams(BaseModel):
    """Global thresholds used by deterministic detection and validation."""

    model_config = ConfigDict(extra="forbid")

    grace_missing_out_minutes: int = Field(ge=0)
    dup_window_minutes: int = Field(ge=0)
    sched_deviation_minutes: int = Field(ge=0)
    max_shift_hours: int = Field(gt=0)
    window_pad_minutes: int = Field(ge=0)
    min_adjusted_worked_hours: int = Field(gt=0)
    low_confidence_threshold: float = Field(ge=0.0, le=1.0)


class JurisdictionParams(BaseModel):
    """Overtime, meal, rest, and consecutive-day thresholds."""

    model_config = ConfigDict(extra="forbid")

    weekly_ot_hours: int = Field(gt=0)
    daily_ot_hours: int | None = Field(default=None, gt=0)
    meal_after_hours: int = Field(gt=0)
    meal_min_minutes: int = Field(gt=0)
    min_rest_hours: int = Field(gt=0)
    max_consecutive_days: int = Field(gt=0)


class PolicyRule(BaseModel):
    """One frozen policy rule with its allowed remediation options."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    category: Literal["punch", "break", "overtime", "schedule", "rest", "days"]
    severity: Severity
    allowed_actions: list[Action]
    flag_type: FlagType | None
    related: list[str]
    text: str


class PolicyBook(BaseModel):
    """Full versioned policy bundle validated at application startup."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    global_params: GlobalParams = Field(alias="global")
    jurisdictions: dict[str, JurisdictionParams]
    rules: list[PolicyRule]

    @model_validator(mode="after")
    def frozen_ids_and_references_are_valid(self) -> "PolicyBook":
        """Enforce the frozen rule inventory, jurisdiction keys, and references."""
        actual_ids = tuple(rule.id for rule in self.rules)
        if actual_ids != FROZEN_RULE_IDS:
            raise ValueError("policy rule IDs must match the frozen list in order")
        if set(self.jurisdictions) != {"US-STD", "US-CA"}:
            raise ValueError("jurisdictions must contain exactly US-STD and US-CA")
        known_ids = set(actual_ids)
        for rule in self.rules:
            unknown = set(rule.related) - known_ids
            if unknown:
                raise ValueError(f"{rule.id} references unknown related rules: {sorted(unknown)}")
        return self


@lru_cache(maxsize=8)
def _load_policies(path: str) -> PolicyBook:
    """Read and validate a policy file, caching by its resolved path."""
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    return PolicyBook.model_validate(raw)


def load_policies(path: str | Path | None = None) -> PolicyBook:
    """Load the cached built-in policy book or validate a supplied policy file."""
    source = Path(path).resolve() if path is not None else POLICY_PATH.resolve()
    return _load_policies(str(source))


POLICY_BOOK = load_policies()


def params_for(jurisdiction: str) -> JurisdictionParams:
    """Return policy thresholds for a known jurisdiction."""
    try:
        return POLICY_BOOK.jurisdictions[jurisdiction]
    except KeyError as exc:
        raise KeyError(f"unknown jurisdiction: {jurisdiction}") from exc


def global_params() -> GlobalParams:
    """Return the validated global policy thresholds."""
    return POLICY_BOOK.global_params


def get_rule(rule_id: str) -> PolicyRule:
    """Return a rule by its frozen ID."""
    for rule in POLICY_BOOK.rules:
        if rule.id == rule_id:
            return rule
    raise KeyError(f"unknown policy rule: {rule_id}")


def _passage(rule: PolicyRule, extra_text: str | None = None) -> PolicyPassage:
    return PolicyPassage(
        policy_id=rule.id,
        title=rule.title,
        text=f"{rule.text} {extra_text}" if extra_text else rule.text,
    )


def all_passages() -> list[PolicyPassage]:
    """Return one plain policy passage for every frozen rule."""
    return [_passage(rule) for rule in POLICY_BOOK.rules]


def retrieve_policies(rule_id: str, jurisdiction: str) -> list[PolicyPassage]:
    """Return the breached rule followed by up to two related passages."""
    if jurisdiction not in POLICY_BOOK.jurisdictions:
        raise KeyError(f"unknown jurisdiction: {jurisdiction}")
    rule = get_rule(rule_id)
    selected = [rule, *(get_rule(related_id) for related_id in rule.related[:2])]
    jurisdiction_note = None
    if jurisdiction == "US-CA" and rule_id in {"OT-01", "OT-03"}:
        jurisdiction_note = "Jurisdiction US-CA applies daily overtime above 8 hours."
    return [
        _passage(selected_rule, jurisdiction_note if selected_rule.id == rule_id else None)
        for selected_rule in selected[:3]
    ]
