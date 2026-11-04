"""Vendor-neutral event and attendance evidence contracts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


class AttendanceSourceReference(BaseModel):
    """Opaque company record identifiers and source version from a webhook."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    employee_ref: str = Field(min_length=1, max_length=200)
    record_type: Literal["timecard_day"]
    record_ref: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    updated_at: datetime

    _updated_at_has_timezone = field_validator("updated_at")(_require_timezone)


class AttendanceChangedEvent(BaseModel):
    """Minimal webhook envelope; employee and punch histories are not accepted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=200)
    event_type: Literal["attendance.record.changed", "attendance.record.deleted"]
    occurred_at: datetime
    record: AttendanceSourceReference

    _occurred_at_has_timezone = field_validator("occurred_at")(_require_timezone)


class AttendanceChangePage(BaseModel):
    """One page from the company's cursor-based attendance change feed."""

    model_config = ConfigDict(extra="forbid")

    events: list[AttendanceChangedEvent] = Field(default_factory=list, max_length=500)
    next_cursor: str | None = Field(default=None, max_length=1000)
    has_more: bool = False


class ScheduleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    record_ref: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime
    planned_break_minutes: int = Field(ge=0, le=1440)

    _times_have_timezone = field_validator("start", "end")(_require_timezone)


class PunchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    record_ref: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    at: datetime
    source: str | None = Field(default=None, max_length=80)

    _at_has_timezone = field_validator("at")(_require_timezone)


class LeaveEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    record_ref: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    start: datetime
    end: datetime
    hours: float | None = Field(default=None, ge=0, le=24)

    _times_have_timezone = field_validator("start", "end")(_require_timezone)


class AttendanceRecord(BaseModel):
    """Normalized, case-scoped evidence fetched from the company system."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    record_ref: str = Field(min_length=1, max_length=200)
    employee_ref: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    updated_at: datetime
    work_date: date
    timezone: str = Field(min_length=1, max_length=80)
    jurisdiction: str | None = Field(default=None, max_length=80)
    ot_eligible: bool | None = None
    schedule: ScheduleEvidence | None = None
    punches: list[PunchEvidence] = Field(default_factory=list, max_length=100)
    leave: list[LeaveEvidence] = Field(default_factory=list, max_length=100)

    _updated_at_has_timezone = field_validator("updated_at")(_require_timezone)


class AttendanceRangeEvidence(BaseModel):
    """Bounded source history plus an explicit completeness assertion."""

    model_config = ConfigDict(extra="forbid")

    employee_ref: str = Field(min_length=1, max_length=200)
    covered_from: date
    covered_through: date
    coverage_complete: bool
    records: list[AttendanceRecord] = Field(default_factory=list, max_length=32)


class CorrectionResult(BaseModel):
    """Company API acknowledgement for an approved timekeeping correction."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_id: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "applied"]
    record_ref: str = Field(min_length=1, max_length=200)
    new_version: str | None = Field(default=None, max_length=200)
    applied_at: datetime | None = None

    _applied_at_has_timezone = field_validator("applied_at")(_require_timezone)


class IntegrationEventAck(BaseModel):
    event_id: str
    status: Literal["accepted"]
    duplicate: bool


class CompanyCorrectionRequest(BaseModel):
    """Typed correction body sent after an authorized HR decision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_ref: str = Field(min_length=1, max_length=200)
    record_ref: str = Field(min_length=1, max_length=200)
    employee_ref: str = Field(min_length=1, max_length=200)
    expected_version: str = Field(min_length=1, max_length=200)
    expected_source_versions: dict[str, str] = Field(default_factory=dict, max_length=32)
    correction: dict[str, object]
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: datetime

    _approved_at_has_timezone = field_validator("approved_at")(_require_timezone)
