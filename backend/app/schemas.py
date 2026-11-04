"""Frozen API and model contracts shared across the ShiftGuard backend."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_local_ts(value: str) -> str:
    """Require a real naive local timestamp in the frozen minute format."""
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise ValueError("timestamp must be a valid YYYY-MM-DDTHH:MM value") from exc
    return value


LocalTs = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"),
    AfterValidator(_validate_local_ts),
]


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PunchKind(str, Enum):
    IN = "IN"
    OUT = "OUT"
    BREAK_START = "BREAK_START"
    BREAK_END = "BREAK_END"


class Action(str, Enum):
    ADD_PUNCH = "ADD_PUNCH"
    MODIFY_PUNCH = "MODIFY_PUNCH"
    REMOVE_PUNCH = "REMOVE_PUNCH"
    ADJUST_SCHEDULE = "ADJUST_SCHEDULE"
    FLAG_FOR_APPROVAL = "FLAG_FOR_APPROVAL"
    ESCALATE = "ESCALATE"


class FlagType(str, Enum):
    OT_APPROVAL = "OT_APPROVAL"
    MEAL_PREMIUM_REVIEW = "MEAL_PREMIUM_REVIEW"
    SCHEDULE_REVIEW = "SCHEDULE_REVIEW"
    REST_PREMIUM_REVIEW = "REST_PREMIUM_REVIEW"
    CONSECUTIVE_DAY_REVIEW = "CONSECUTIVE_DAY_REVIEW"


class ExceptionStatus(str, Enum):
    OPEN = "OPEN"
    PENDING_REVIEW = "PENDING_REVIEW"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    WRITEBACK_PENDING = "WRITEBACK_PENDING"
    WRITEBACK_FAILED = "WRITEBACK_FAILED"


class ProposalSource(str, Enum):
    LLM = "llm"
    FALLBACK = "fallback"


class DecisionAction(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class FaultMode(str, Enum):
    NONE = "none"
    BAD_THEN_GOOD = "bad_then_good"
    ALWAYS_BAD = "always_bad"
    GARBAGE = "garbage"
    TIMEOUT = "timeout"
    BAD_CITATION = "bad_citation"
    NAIVE_LATEST = "naive_latest"


class ProposedCorrection(BaseModel):
    """A flat, strictly validated correction proposed for human review."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    punch_id: int | None = None
    kind: PunchKind | None = None
    ts: LocalTs | None = None
    schedule_id: int | None = None
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def required_fields_per_action(self) -> "ProposedCorrection":
        """Enforce exactly the target fields that apply to the selected action."""
        required = {
            Action.ADD_PUNCH: {"kind", "ts"},
            Action.MODIFY_PUNCH: {"punch_id", "ts"},
            Action.REMOVE_PUNCH: {"punch_id"},
            Action.ADJUST_SCHEDULE: {"schedule_id", "ts"},
            Action.FLAG_FOR_APPROVAL: set(),
            Action.ESCALATE: set(),
        }[self.action]
        for field_name in required:
            if getattr(self, field_name) is None:
                raise ValueError(f"{self.action.value} requires {field_name}")
        for field_name in ("punch_id", "kind", "ts", "schedule_id"):
            if field_name not in required and getattr(self, field_name) is not None:
                raise ValueError(f"{self.action.value} must not set {field_name}")
        return self


class LLMProposal(BaseModel):
    """Exact structured output contract expected from an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    exception_id: int
    explanation: str = Field(min_length=20, max_length=600)
    proposed_correction: ProposedCorrection
    policy_ids_cited: list[str] = Field(min_length=1, max_length=5)
    confidence: float = Field(ge=0.0, le=1.0)


class TimeWindow(BaseModel):
    earliest: LocalTs
    latest: LocalTs

    @model_validator(mode="after")
    def earliest_not_after_latest(self) -> "TimeWindow":
        """Reject inverted correction windows."""
        if self.earliest > self.latest:
            raise ValueError("earliest must be no later than latest")
        return self


class Constraints(BaseModel):
    allowed_actions: list[Action]
    flag_type: FlagType | None = None
    ts_window: TimeWindow | None = None
    target_kind: PunchKind | None = None
    target_punch_ids: list[int] = Field(default_factory=list)
    target_schedule_id: int | None = None
    headroom_minutes: int | None = None


class CheckResult(BaseModel):
    id: str
    name: str
    passed: bool
    level: Literal["error", "warning"] = "error"
    detail: str = ""


class SideEffect(BaseModel):
    rule_id: str
    severity: Severity
    work_date: str
    summary: str


class ValidationResult(BaseModel):
    passed: bool
    checks: list[CheckResult]
    side_effects: list[SideEffect] = Field(default_factory=list)


class PolicyPassage(BaseModel):
    policy_id: str
    title: str
    text: str


class PolicySnapshotEntry(PolicyPassage):
    """One policy passage together with the persisted organization revision used."""

    revision: int | None = None


class OrganizationPolicyOut(BaseModel):
    """One organization-owned implemented rule or AI reference policy."""

    policy_id: str
    kind: Literal["standard", "custom"]
    rule_id: str | None = None
    category: str
    title: str
    text: str
    severity: Severity | None = None
    applies_to_rule_ids: list[str] = Field(default_factory=list)
    is_active: bool
    revision: int
    updated_at: datetime
    archived_at: datetime | None = None


class OrganizationPolicyCreate(BaseModel):
    """Create a company reference policy for model retrieval."""

    title: str = Field(min_length=3, max_length=160)
    text: str = Field(min_length=20, max_length=12000)
    applies_to_rule_ids: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("title", "text", mode="before")
    @classmethod
    def trim_policy_content(cls, value: object) -> object:
        """Trim policy content before enforcing its minimum length."""
        return value.strip() if isinstance(value, str) else value


class OrganizationPolicyUpdate(BaseModel):
    """Edit an organization policy or update an implemented rule's text."""

    title: str | None = Field(default=None, min_length=3, max_length=160)
    text: str | None = Field(default=None, min_length=20, max_length=12000)
    severity: Severity | None = None
    is_active: bool | None = None
    applies_to_rule_ids: list[str] | None = Field(default=None, max_length=15)

    @field_validator("title", "text", mode="before")
    @classmethod
    def trim_policy_content(cls, value: object) -> object:
        """Trim optional policy content before enforcing its minimum length."""
        return value.strip() if isinstance(value, str) else value


class AssistantQuestion(BaseModel):
    """Bounded manager question for the read-only attendance assistant."""

    question: str = Field(min_length=3, max_length=500)
    conversation_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def question_has_content(cls, value: str) -> str:
        """Reject whitespace-only questions before retrieval or model calls."""
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("question must contain at least three non-space characters")
        return cleaned


class AssistantSource(BaseModel):
    """One manager-authorized record or policy excerpt used in an answer."""

    source_id: str
    kind: Literal["case", "policy"]
    label: str
    excerpt: str
    href: str | None = None


class AssistantModelAnswer(BaseModel):
    """Strict model response contract for the single-call attendance assistant."""

    model_config = ConfigDict(extra="forbid")

    in_scope: bool = Field(description="Whether the user's question is relevant to attendance.")
    uses_evidence: bool = Field(
        description="Whether the answer relies on supplied evidence sources."
    )
    answer: str = Field(min_length=5, max_length=1200)
    source_ids: list[str] = Field(max_length=55)


class AssistantAnswer(BaseModel):
    """Read-only answer plus the exact evidence shown to its manager."""

    answer: str
    sources: list[AssistantSource]
    model: str
    used_fallback: bool
    conversation_id: UUID | None = None


class ConversationSummaryOut(BaseModel):
    """One private conversation in a user's organization-scoped chat history."""

    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    last_message_preview: str | None


class ConversationMessageOut(BaseModel):
    """One persisted user or assistant message with its retained citations."""

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    model: str | None
    source_references: list[AssistantSource]
    created_at: datetime


class ConversationDetailOut(BaseModel):
    """Conversation metadata and chronological messages for reopening a chat."""

    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageOut]


class ConversationDeleteOut(BaseModel):
    deleted: bool


class PunchOut(BaseModel):
    id: int
    employee_id: str
    work_date: str
    ts: LocalTs
    kind: PunchKind
    source: str | None
    voided: bool = False


class ScheduleOut(BaseModel):
    id: int
    employee_id: str
    work_date: str
    start: LocalTs
    end: LocalTs
    planned_break_minutes: int


class EmployeeOut(BaseModel):
    id: str
    name: str
    role: str | None = None
    site: str | None = None
    jurisdiction: str
    manager_id: str | None = None
    ot_eligible: bool | None = None


class ExceptionOut(BaseModel):
    id: int | UUID
    employee_id: str
    employee_name: str
    work_date: str
    rule_id: str
    title: str
    severity: Severity
    status: ExceptionStatus
    detected_at: str
    evidence: dict
    constraints: Constraints


class ProposalOut(BaseModel):
    id: int | UUID
    exception_id: int | UUID
    trace_id: str
    source: ProposalSource
    proposal: LLMProposal
    validation: ValidationResult
    attempts: int
    model: str
    latency_ms: int
    created_at: str
    policy_snapshot: list[PolicySnapshotEntry] = Field(default_factory=list)


class DecisionIn(BaseModel):
    action: DecisionAction
    note: str | None = Field(default=None, max_length=500)
    edited_correction: ProposedCorrection | None = None

    @model_validator(mode="after")
    def edited_correction_matches_action(self) -> "DecisionIn":
        """Require an edited correction exactly when the decision is EDIT."""
        has_correction = self.edited_correction is not None
        if (self.action is DecisionAction.EDIT) != has_correction:
            if self.action is DecisionAction.EDIT:
                raise ValueError("edited_correction is required when action is edit")
            raise ValueError("edited_correction is only allowed when action is edit")
        return self


class DecisionOut(BaseModel):
    id: int | UUID
    exception_id: int | UUID
    proposal_id: int | UUID | None
    action: DecisionAction
    manager_id: str
    note: str | None
    final_correction: ProposedCorrection | None
    decided_at: str
    resulting_status: ExceptionStatus
    writeback_status: Literal["not_requested", "pending", "applied", "stale", "failed"] = (
        "not_requested"
    )
    external_operation_id: str | None = None


class TimecardOut(BaseModel):
    employee: EmployeeOut
    week_start: str
    punches: list[PunchOut]
    schedules: list[ScheduleOut]
    weekly_worked_minutes: int
    weekly_threshold_minutes: int | None


class ExceptionDetail(BaseModel):
    exception: ExceptionOut
    employee: EmployeeOut
    timecard: TimecardOut
    policies: list[PolicyPassage]
    latest_proposal: ProposalOut | None
    decision: DecisionOut | None
    source_current: bool = True
    source_version: str | None = None
    current_source_version: str | None = None


class AuditEventOut(BaseModel):
    id: int
    trace_id: str
    exception_id: int | UUID | None
    seq: int
    event_type: str
    payload: dict
    prev_hash: str
    hash: str
    created_at: str


class AuditVerifyOut(BaseModel):
    ok: bool
    events_checked: int
    first_bad_event_id: int | None


class DetectIn(BaseModel):
    as_of: LocalTs | None = None
    employee_ids: list[str] | None = None


class DetectOut(BaseModel):
    as_of: LocalTs
    created: int
    unchanged: int
    superseded: int


class MetricsOut(BaseModel):
    total_exceptions: int
    by_status: dict[str, int]
    by_rule: dict[str, int]
    by_severity: dict[str, int]
    proposals_total: int
    first_pass_validation_rate: float
    final_pass_rate: float
    escalation_rate: float
    approved_total: int
    approved_without_edit_rate: float
    median_seconds_to_decision: float | None
    estimated_minutes_saved: float
    assumptions: dict[str, float]
    unsafe_proposals_reaching_manager: int


class LLMModeIn(BaseModel):
    mode: FaultMode


class LLMModeOut(BaseModel):
    mode: FaultMode
    provider: str
    model: str


class HealthOut(BaseModel):
    status: Literal["ok"]
    provider: str
    model: str
    llm_reachable: bool
    now: LocalTs


class ReadinessOut(BaseModel):
    """Operational dependency checks for deployment readiness probes."""

    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]
    worker_heartbeat_age_seconds: int | None = None
    now: datetime


class CurrentUserOut(BaseModel):
    """Authenticated principal resolved from Supabase Auth and user_profiles."""

    user_id: str
    organization_id: str | None
    role: Literal["HR", "manager", "integration_service"]
    manager_ref: str | None = None
    email: str | None = None


class ErrorOut(BaseModel):
    detail: str
    code: str
    checks: list[CheckResult] | None = None
