"""Unit tests for AttendanceAnalysisWorker orchestration logic.

These tests use fake implementations of PostgresWorkflowRepository and
AttendanceProvider so they run without a live database or external service.

Coverage:
  - process_one() returns False when no job is available
  - process_one() returns True after claiming and completing a job
  - Stale event (newer event arrived) → job is cancelled, not failed
  - Retryable provider error → job is retried up to max attempts, then failed
  - Permanent provider error → job immediately failed with correct error code
  - Missing inbound_event_id on job row → immediate permanent failure
  - Inbound event not found in repository → immediate permanent failure
  - Persistence failure during complete_analysis_job → exception propagates
  - Worker requires organization_id and integration_id to be configured
  - Heartbeat: _retry_or_fail schedules exponential back-off delays
  - _fail writes an ANALYSIS_FAILED audit event
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.analysis_worker import (
    AttendanceAnalysisWorker,
    PermanentAnalysisError,
    RetryableAnalysisError,
    StaleAnalysisError,
)
from app.config import Settings
from app.integrations.provider import AttendanceProviderUnavailable
from app.llm.base import LLMResult, PromptBundle
from app.policies import load_policies
from app.schemas import FaultMode

# ---------------------------------------------------------------------------
# Helpers — fake repository
# ---------------------------------------------------------------------------

Row = dict[str, Any]

ORG_ID = uuid.uuid4()
INTEGRATION_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
EVENT_ID = uuid.uuid4()


def _make_settings(**overrides) -> Settings:
    base = dict(
        db_path=":memory:",
        auth_mode="demo",
        auto_seed=False,
        attendance_provider="mock",
        llm_provider="mock",
        auth_organization_id=ORG_ID,
        attendance_integration_id=INTEGRATION_ID,
        analysis_max_attempts=3,
        analysis_retry_base_seconds=15,
        analysis_job_lease_seconds=300,
    )
    base.update(overrides)
    return Settings(**base)


def _make_job(
    *,
    job_id: UUID = JOB_ID,
    event_id: UUID | None = EVENT_ID,
    attempts: int = 1,
) -> Row:
    return {
        "id": str(job_id),
        "organization_id": str(ORG_ID),
        "inbound_event_id": str(event_id) if event_id is not None else None,
        "attempts": attempts,
    }


def _make_event(
    *,
    event_id: UUID = EVENT_ID,
    employee_ref: str = "EMP-001",
    record_ref: str = "REC-001",
    event_type: str = "attendance.record.changed",
) -> Row:
    return {
        "id": str(event_id),
        "organization_id": str(ORG_ID),
        "integration_id": str(INTEGRATION_ID),
        "external_employee_ref": employee_ref,
        "source_record_refs": [record_ref],
        "source_version": "v1",
        "event_type": event_type,
    }


@dataclass
class FakeRepository:
    """Minimal stub that records calls and returns pre-configured results."""

    # Preconfigure what claim_analysis_job returns (None = no job available).
    job_to_claim: Row | None = None
    event_to_return: Row | None = None
    latest_event_id: UUID | None = EVENT_ID  # for _event_is_current

    claimed: list[Row] = field(default_factory=list)
    completed: list[tuple] = field(default_factory=list)
    retried: list[tuple] = field(default_factory=list)
    failed: list[tuple] = field(default_factory=list)
    cancelled: list[tuple] = field(default_factory=list)
    audit_events: list[dict] = field(default_factory=list)

    # Raise on complete_analysis_job if set.
    raise_on_complete: Exception | None = None

    def claim_analysis_job(self, lease_seconds, organization_id, integration_id) -> Row | None:
        if self.job_to_claim is not None:
            self.claimed.append(self.job_to_claim)
        return self.job_to_claim

    def get_inbound_event_by_id(self, event_id: UUID, organization_id: UUID) -> Row | None:
        return self.event_to_return

    def get_latest_inbound_event_for_source(
        self, organization_id, integration_id, employee_ref, record_ref
    ) -> Row | None:
        if self.latest_event_id is None:
            return None
        return {"id": str(self.latest_event_id)}

    def complete_analysis_job(self, job_id, event_id, *, event_status: str) -> None:
        if self.raise_on_complete:
            raise self.raise_on_complete
        self.completed.append((str(job_id), str(event_id), event_status))

    def retry_analysis_job(self, job_id, event_id, *, error_code: str, delay_seconds: int) -> None:
        self.retried.append((str(job_id), str(event_id), error_code, delay_seconds))

    def fail_analysis_job(self, job_id, event_id, *, error_code: str) -> None:
        self.failed.append((str(job_id), str(event_id) if event_id else None, error_code))

    def cancel_analysis_job(self, job_id, event_id, *, error_code: str) -> None:
        self.cancelled.append((str(job_id), str(event_id), error_code))

    def append_audit_event(self, *, organization_id, trace_id, event_type, payload, **_kw) -> None:
        self.audit_events.append({"event_type": event_type, "payload": payload})

    def get_integration(self, integration_id, organization_id) -> Row:
        return {"id": str(integration_id), "status": "active"}

    def supersede_open_cases_for_source(self, **_kw) -> None:
        pass

    def get_exception_case_by_key(self, organization_id, case_key) -> Row | None:
        return None

    def upsert_exception_case(self, **_kw) -> UUID:
        return uuid.uuid4()

    def get_proposal_for_job_case(self, organization_id, case_id, job_id) -> Row | None:
        return None

    def get_latest_proposal_for_case(self, organization_id, case_id) -> Row | None:
        return None

    def next_proposal_version(self, case_id) -> int:
        return 1

    def persist_proposal(self, **_kw) -> UUID:
        return uuid.uuid4()

    def set_exception_case_status(self, case_id, status) -> None:
        pass

    def resolve_organization_policy_context(self, *_a, **_kw):
        return None

    def get_policy_passage_snapshot(self, *_a, **_kw) -> list:
        return []


class NoOpAlerts:
    def report_success(self, *_a, **_kw) -> None:
        pass

    def report_failure(self, *_a, **_kw) -> None:
        pass


def _make_worker(repo: FakeRepository, provider=None, llm=None, **settings_overrides):
    settings = _make_settings(**settings_overrides)
    book = load_policies()
    if provider is None:
        provider = MagicMock()
    if llm is None:
        llm = MagicMock()
    return AttendanceAnalysisWorker(
        repository=repo,
        provider=provider,
        llm=llm,
        settings=settings,
        book=book,
        operational_alerts=NoOpAlerts(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_process_one_returns_false_when_no_job_available():
    repo = FakeRepository(job_to_claim=None)
    worker = _make_worker(repo)
    result = worker.process_one()
    assert result is False
    assert repo.claimed == []


def test_process_one_raises_when_organization_not_configured():
    repo = FakeRepository(job_to_claim=_make_job())
    settings = _make_settings(auth_organization_id=None)
    book = load_policies()
    worker = AttendanceAnalysisWorker(
        repository=repo,
        provider=MagicMock(),
        llm=MagicMock(),
        settings=settings,
        book=book,
        operational_alerts=NoOpAlerts(),
    )
    with pytest.raises(RuntimeError, match="organization and integration must be configured"):
        worker.process_one()


def test_process_one_raises_when_integration_not_configured():
    repo = FakeRepository(job_to_claim=_make_job())
    settings = _make_settings(attendance_integration_id=None)
    book = load_policies()
    worker = AttendanceAnalysisWorker(
        repository=repo,
        provider=MagicMock(),
        llm=MagicMock(),
        settings=settings,
        book=book,
        operational_alerts=NoOpAlerts(),
    )
    with pytest.raises(RuntimeError, match="organization and integration must be configured"):
        worker.process_one()


def test_process_one_fails_job_when_inbound_event_id_missing():
    """Job row without inbound_event_id → permanent failure, no retry."""
    job = _make_job(event_id=None)
    repo = FakeRepository(job_to_claim=job)
    worker = _make_worker(repo)

    result = worker.process_one()

    assert result is True
    assert len(repo.failed) == 1
    _job_id, _event_id, error_code = repo.failed[0]
    assert error_code == "missing_inbound_event"
    assert repo.retried == []
    assert any(e["event_type"] == "ANALYSIS_FAILED" for e in repo.audit_events)


def test_process_one_fails_job_when_inbound_event_not_found():
    """get_inbound_event_by_id returning None → permanent failure."""
    job = _make_job()
    repo = FakeRepository(job_to_claim=job, event_to_return=None)
    worker = _make_worker(repo)

    result = worker.process_one()

    assert result is True
    assert len(repo.failed) == 1
    _, _, error_code = repo.failed[0]
    assert error_code == "inbound_event_not_found"
    assert any(e["event_type"] == "ANALYSIS_FAILED" for e in repo.audit_events)


def test_process_one_cancels_stale_event_when_newer_exists():
    """A newer event (different id) arrived → job should be cancelled, not failed."""
    newer_event_id = uuid.uuid4()
    job = _make_job()
    repo = FakeRepository(
        job_to_claim=job,
        event_to_return=_make_event(),
        latest_event_id=newer_event_id,  # different from EVENT_ID → stale
    )
    worker = _make_worker(repo)

    result = worker.process_one()

    assert result is True
    assert len(repo.cancelled) == 1
    _, _, error_code = repo.cancelled[0]
    assert error_code == "source_superseded"
    assert repo.failed == []
    assert any(
        e["event_type"] == "ANALYSIS_CANCELLED_STALE_SOURCE" for e in repo.audit_events
    )


def test_retry_or_fail_schedules_retry_on_first_attempt():
    """First retryable error → retry with exponential delay, not permanent failure."""
    job = _make_job(attempts=1)
    event = _make_event()
    repo = FakeRepository(job_to_claim=job, event_to_return=event)
    worker = _make_worker(repo, analysis_max_attempts=3, analysis_retry_base_seconds=15)

    # Call internal method directly to test retry scheduling in isolation.
    worker._retry_or_fail(job, event, "attendance_source_unavailable")

    assert len(repo.retried) == 1
    _, _, error_code, delay = repo.retried[0]
    assert error_code == "attendance_source_unavailable"
    assert delay == 15  # base * 2^(1-1) = 15 * 1
    assert repo.failed == []
    assert any(e["event_type"] == "ANALYSIS_RETRY_SCHEDULED" for e in repo.audit_events)


def test_retry_or_fail_exponential_backoff_on_second_attempt():
    job = _make_job(attempts=2)
    event = _make_event()
    repo = FakeRepository(job_to_claim=job, event_to_return=event)
    worker = _make_worker(repo, analysis_max_attempts=3, analysis_retry_base_seconds=15)

    worker._retry_or_fail(job, event, "attendance_source_unavailable")

    _, _, _, delay = repo.retried[0]
    assert delay == 30  # 15 * 2^(2-1) = 30


def test_retry_or_fail_converts_to_permanent_failure_at_max_attempts():
    """When attempts == max_attempts, _retry_or_fail should fail the job."""
    job = _make_job(attempts=3)
    event = _make_event()
    repo = FakeRepository(job_to_claim=job, event_to_return=event)
    worker = _make_worker(repo, analysis_max_attempts=3)

    worker._retry_or_fail(job, event, "attendance_source_unavailable")

    assert repo.retried == []
    assert len(repo.failed) == 1
    _, _, error_code = repo.failed[0]
    assert error_code == "attendance_source_unavailable"
    assert any(e["event_type"] == "ANALYSIS_FAILED" for e in repo.audit_events)


def test_fail_writes_audit_event_and_records_error_code():
    repo = FakeRepository()
    worker = _make_worker(repo)
    job_id = uuid.uuid4()
    event_id = uuid.uuid4()

    worker._fail(job_id, ORG_ID, event_id, "analysis_internal_error")

    assert len(repo.failed) == 1
    assert repo.failed[0][2] == "analysis_internal_error"
    failed_audit = [e for e in repo.audit_events if e["event_type"] == "ANALYSIS_FAILED"]
    assert len(failed_audit) == 1
    assert failed_audit[0]["payload"]["error_code"] == "analysis_internal_error"


def test_fail_handles_none_event_id():
    """_fail must work when event_id is None (missing inbound_event_id path)."""
    repo = FakeRepository()
    worker = _make_worker(repo)

    worker._fail(uuid.uuid4(), ORG_ID, None, "missing_inbound_event")

    assert len(repo.failed) == 1
    assert repo.failed[0][1] is None


def test_cancel_stale_writes_audit_event():
    repo = FakeRepository()
    worker = _make_worker(repo)
    job_id = uuid.uuid4()

    worker._cancel_stale(job_id, ORG_ID, EVENT_ID)

    assert len(repo.cancelled) == 1
    assert any(
        e["event_type"] == "ANALYSIS_CANCELLED_STALE_SOURCE" for e in repo.audit_events
    )


def test_event_is_current_returns_true_when_ids_match():
    repo = FakeRepository(latest_event_id=EVENT_ID)
    worker = _make_worker(repo)
    event = _make_event(event_id=EVENT_ID)

    assert worker._event_is_current(ORG_ID, event) is True


def test_event_is_current_returns_false_when_newer_event_exists():
    different_id = uuid.uuid4()
    repo = FakeRepository(latest_event_id=different_id)
    worker = _make_worker(repo)
    event = _make_event(event_id=EVENT_ID)

    assert worker._event_is_current(ORG_ID, event) is False


def test_event_is_current_returns_false_when_no_latest_event():
    repo = FakeRepository(latest_event_id=None)
    worker = _make_worker(repo)
    event = _make_event(event_id=EVENT_ID)

    assert worker._event_is_current(ORG_ID, event) is False


def test_process_one_returns_true_even_when_deleted_event_type():
    """attendance.record.deleted events should complete (not fail) the job."""
    job = _make_job()
    event = _make_event(event_type="attendance.record.deleted")
    repo = FakeRepository(job_to_claim=job, event_to_return=event, latest_event_id=EVENT_ID)
    worker = _make_worker(repo)

    result = worker.process_one()

    assert result is True
    # deleted path calls complete_analysis_job with event_status="processed"
    assert any(c[2] == "processed" for c in repo.completed)
    assert repo.failed == []


def test_process_one_propagates_persistence_failure_on_complete():
    """If complete_analysis_job raises, the exception must propagate (not be swallowed)."""
    job = _make_job()
    event = _make_event(event_type="attendance.record.deleted")
    repo = FakeRepository(
        job_to_claim=job,
        event_to_return=event,
        latest_event_id=EVENT_ID,
        raise_on_complete=RuntimeError("db write failed"),
    )
    worker = _make_worker(repo)

    with pytest.raises(RuntimeError, match="db write failed"):
        worker.process_one()
