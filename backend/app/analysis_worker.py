"""Durable source-event analysis worker backed by Supabase PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as day_time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.engine.rules import DetectedException, detect_employee
from app.engine.shifts import Punch, Sched, build_shifts
from app.integrations.provider import (
    AttendanceProvider,
    AttendanceProviderError,
    AttendanceProviderUnavailable,
    AttendanceSourceChanged,
)
from app.integrations.schemas import AttendanceRecord
from app.llm.base import LLMClient, LLMTimeout, LLMUnavailable
from app.llm.prompt import build_prompt
from app.ops_alerts import OperationalAlertReporter
from app.organization_policies import (
    OrganizationPolicyContext,
    resolve_organization_policy_context,
)
from app.policies import POLICY_BOOK, PolicyBook
from app.schemas import (
    Action,
    CheckResult,
    LLMProposal,
    ProposalSource,
    ProposedCorrection,
    PunchKind,
    ValidationResult,
)
from app.validator import ExceptionRecord, ValidationContext, validate_proposal
from app.workflow_repository import PostgresWorkflowRepository, Row

logger = logging.getLogger(__name__)

PROMPT_VERSION = "attendance-proposal-v3"
VALIDATOR_VERSION = "shiftguard-validator-v1"
MAX_RETRY_DELAY_SECONDS = 3600
FINAL_CASE_STATUSES = {
    "resolved",
    "rejected",
    "superseded",
    "writeback_pending",
    "writeback_failed",
}


class RetryableAnalysisError(RuntimeError):
    """A transient upstream condition that can be retried as a whole job."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class PermanentAnalysisError(RuntimeError):
    """Invalid or incomplete source evidence that needs integration attention."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class StaleAnalysisError(RuntimeError):
    """A newer company event arrived while this job was being processed."""


class AttendanceAnalysisWorker:
    """Claim source events, detect issues, and persist validated proposals."""

    def __init__(
        self,
        repository: PostgresWorkflowRepository,
        provider: AttendanceProvider,
        llm: LLMClient,
        settings: Settings,
        book: PolicyBook = POLICY_BOOK,
        operational_alerts: OperationalAlertReporter | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.llm = llm
        self.settings = settings
        self.book = book
        self.base_book = book
        self.policy_context = OrganizationPolicyContext(book=book)
        self.operational_alerts = operational_alerts or OperationalAlertReporter(settings)

    def process_one(self) -> bool:
        """Claim and process one available job; return whether a job was claimed."""
        organization_id = self.settings.auth_organization_id
        integration_id = self.settings.attendance_integration_id
        if organization_id is None or integration_id is None:
            raise RuntimeError("analysis worker organization and integration must be configured")
        job = self.repository.claim_analysis_job(
            self.settings.analysis_job_lease_seconds,
            organization_id,
            integration_id,
        )
        if job is None:
            return False

        job_id = UUID(str(job["id"]))
        organization_id = UUID(str(job["organization_id"]))
        event_id_value = job.get("inbound_event_id")
        if event_id_value is None:
            self._fail(job_id, organization_id, None, "missing_inbound_event")
            return True
        event_id = UUID(str(event_id_value))
        event = self.repository.get_inbound_event_by_id(event_id, organization_id)
        if event is None:
            self._fail(job_id, organization_id, event_id, "inbound_event_not_found")
            return True

        if not self._event_is_current(organization_id, event):
            self._cancel_stale(job_id, organization_id, event_id)
            return True

        try:
            self.policy_context = resolve_organization_policy_context(
                self.repository, organization_id, self.base_book
            )
            self.book = self.policy_context.book
            self._process_event(job, event)
            self.operational_alerts.report_success("analysis_worker")
            logger.info(
                "analysis job processed",
                extra={
                    "analysis_job_id": str(job_id),
                    "integration_event_id": str(event_id),
                },
            )
        except StaleAnalysisError:
            self._cancel_stale(job_id, organization_id, event_id)
        except RetryableAnalysisError as exc:
            self._retry_or_fail(job, event, exc.error_code)
        except (AttendanceProviderUnavailable, LLMTimeout, LLMUnavailable) as exc:
            # Provider and LLM boundaries should normally be translated closer
            # to their call site; retain a safe code if a future adapter leaks one.
            self._retry_or_fail(job, event, _safe_error_code(exc))
        except PermanentAnalysisError as exc:
            self._fail(
                job_id,
                organization_id,
                event_id,
                exc.error_code,
            )
        except (AttendanceProviderError, ValueError, KeyError):
            self._fail(job_id, organization_id, event_id, "invalid_or_unsupported_source_evidence")
        except Exception:
            # Do not write exception text to the database: adapters can include
            # request details. The job remains visible through a safe error code.
            self._fail(job_id, organization_id, event_id, "analysis_internal_error")
        return True

    def _process_event(self, job: Row, event: Row) -> None:
        job_id = UUID(str(job["id"]))
        organization_id = UUID(str(job["organization_id"]))
        event_id = UUID(str(event["id"]))
        integration_id = UUID(str(event["integration_id"]))
        employee_ref = str(event.get("external_employee_ref") or "")
        refs = event.get("source_record_refs")
        record_refs = refs if isinstance(refs, list) else []
        if not employee_ref or len(record_refs) != 1 or not isinstance(record_refs[0], str):
            raise PermanentAnalysisError("invalid_event_references")
        record_ref = record_refs[0]
        trace_id = f"analysis-{job_id.hex}"

        integration = self.repository.get_integration(integration_id, organization_id)
        if integration is None or integration["status"] != "active":
            raise PermanentAnalysisError("integration_inactive")

        if str(event["event_type"]) == "attendance.record.deleted":
            if not self._event_is_current(organization_id, event):
                self._cancel_stale(job_id, organization_id, event_id)
                return
            self.repository.supersede_open_cases_for_source(
                organization_id=organization_id,
                integration_id=integration_id,
                record_ref=record_ref,
                keep_case_ids=set(),
                reason="source_deleted",
            )
            self.repository.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type="SOURCE_RECORD_DELETED",
                payload={"source_record_ref": record_ref},
            )
            self.repository.complete_analysis_job(job_id, event_id, event_status="processed")
            return

        try:
            source_record = self.provider.fetch_attendance_record(
                record_ref, version=str(event.get("source_version") or "") or None
            )
        except AttendanceSourceChanged as exc:
            raise PermanentAnalysisError("source_version_changed") from exc
        except AttendanceProviderUnavailable as exc:
            raise RetryableAnalysisError("attendance_source_unavailable") from exc
        except AttendanceProviderError as exc:
            raise PermanentAnalysisError("attendance_source_invalid") from exc

        if source_record is None:
            if not self._event_is_current(organization_id, event):
                self._cancel_stale(job_id, organization_id, event_id)
                return
            self.repository.supersede_open_cases_for_source(
                organization_id=organization_id,
                integration_id=integration_id,
                record_ref=record_ref,
                keep_case_ids=set(),
                reason="source_record_missing",
            )
            self.repository.complete_analysis_job(job_id, event_id, event_status="ignored")
            return

        if source_record.employee_ref != employee_ref:
            raise PermanentAnalysisError("source_employee_mismatch")
        event_version = str(event.get("source_version") or "")
        if event_version and source_record.version != event_version:
            raise PermanentAnalysisError("source_version_changed")
        if source_record.record_ref != record_ref:
            raise PermanentAnalysisError("source_record_mismatch")
        if source_record.jurisdiction not in self.book.jurisdictions:
            raise PermanentAnalysisError("unsupported_jurisdiction")

        week_start = source_record.work_date - timedelta(days=source_record.work_date.weekday())
        range_start = week_start - timedelta(days=7)
        range_end = week_start + timedelta(days=6)
        try:
            evidence = self.provider.fetch_attendance_range(employee_ref, range_start, range_end)
        except AttendanceProviderUnavailable as exc:
            raise RetryableAnalysisError("attendance_range_unavailable") from exc
        except AttendanceProviderError as exc:
            raise PermanentAnalysisError("attendance_range_invalid") from exc
        self.operational_alerts.report_success("attendance_provider")
        if not evidence.coverage_complete:
            raise RetryableAnalysisError("attendance_range_incomplete")
        if (
            evidence.employee_ref != employee_ref
            or evidence.covered_from != range_start
            or evidence.covered_through != range_end
        ):
            raise PermanentAnalysisError("attendance_range_mismatch")
        records = evidence.records
        if any(
            record.employee_ref != employee_ref or not range_start <= record.work_date <= range_end
            for record in records
        ):
            raise PermanentAnalysisError("attendance_range_contains_out_of_scope_record")
        if len({record.record_ref for record in records}) != len(records):
            raise PermanentAnalysisError("attendance_range_contains_duplicate_record")
        matching_records = [record for record in records if record.record_ref == record_ref]
        if len(matching_records) != 1:
            raise RetryableAnalysisError("attendance_range_missing_changed_record")
        if _canonical_record_hash(matching_records[0]) != _canonical_record_hash(source_record):
            raise RetryableAnalysisError("attendance_range_source_mismatch")
        if any(
            record.jurisdiction != source_record.jurisdiction
            or record.timezone != source_record.timezone
            for record in records
        ):
            raise PermanentAnalysisError("attendance_history_policy_context_changed")

        try:
            zone = ZoneInfo(source_record.timezone)
        except ZoneInfoNotFoundError as exc:
            raise PermanentAnalysisError("unsupported_source_timezone") from exc

        try:
            employee, punches, schedules, punch_refs, schedule_refs = _normalize_records(
                records, source_record, zone
            )
        except (ValueError, TypeError) as exc:
            raise PermanentAnalysisError("attendance_evidence_normalization_failed") from exc

        as_of = _analysis_time(source_record.work_date, zone)
        try:
            findings = detect_employee(
                employee,
                punches,
                schedules,
                as_of,
                self.book,
                disabled_rule_ids=self.policy_context.disabled_rule_ids,
            )
        except (ValueError, KeyError) as exc:
            raise PermanentAnalysisError("deterministic_detection_failed") from exc

        source_hash = _range_hash(records)
        source_record_refs = sorted(record.record_ref for record in records)
        latest = self.repository.get_latest_inbound_event_for_source(
            organization_id,
            integration_id,
            employee_ref,
            record_ref,
        )
        if latest is None or UUID(str(latest["id"])) != event_id:
            self._cancel_stale(job_id, organization_id, event_id)
            return

        week_shifts = build_shifts(
            punches,
            schedules,
            dup_window_minutes=self.book.global_params.dup_window_minutes,
        )
        completed_minutes = sum(
            shift.worked_minutes
            for workday, shift in week_shifts.items()
            if week_start <= workday <= as_of.date() and shift.complete
        )
        jurisdiction = str(source_record.jurisdiction)
        week_summary = {
            "completed_minutes": completed_minutes,
            "threshold_minutes": self.book.jurisdictions[jurisdiction].weekly_ot_hours * 60,
        }
        schedule_by_id = {schedule.id: schedule for schedule in schedules}
        kept_case_ids: set[UUID] = set()

        self.repository.append_audit_event(
            organization_id=organization_id,
            trace_id=trace_id,
            event_type="DETERMINISTIC_DETECTION_COMPLETED",
            payload={
                "source_record_ref": record_ref,
                "source_version": source_record.version,
                "source_hash": source_hash,
                "findings_count": len(findings),
                "analysis_window": [range_start.isoformat(), range_end.isoformat()],
            },
        )

        for finding in findings:
            if not self._event_is_current(organization_id, event):
                self._cancel_stale(job_id, organization_id, event_id)
                return
            case_key = _case_key(
                integration_id,
                employee_ref,
                source_hash,
                finding,
            )
            existing = self.repository.get_exception_case_by_key(organization_id, case_key)
            if existing is not None and str(existing["status"]) in FINAL_CASE_STATUSES:
                continue

            minimal_snapshot = _minimal_snapshot(
                finding,
                source_record,
                range_start,
                range_end,
                punch_refs,
                schedule_refs,
            )
            case_id = self.repository.upsert_exception_case(
                organization_id=organization_id,
                integration_id=integration_id,
                case_key=case_key,
                external_employee_ref=employee_ref,
                rule_id=finding.rule_id,
                severity=finding.severity.value,
                source_record_refs=source_record_refs,
                source_version=source_record.version,
                source_updated_at=source_record.updated_at,
                source_hash=source_hash,
                minimal_evidence_snapshot=minimal_snapshot,
                status="open",
            )
            kept_case_ids.add(case_id)
            self.repository.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type="EXCEPTION_DETECTED",
                exception_case_id=case_id,
                payload={
                    "rule_id": finding.rule_id,
                    "work_date": finding.work_date.isoformat(),
                    "source_hash": source_hash,
                    "evidence_hash": _json_hash(finding.evidence),
                },
            )

            prior_for_job = self.repository.get_proposal_for_job_case(
                organization_id, case_id, job_id
            )
            latest_proposal = prior_for_job or self.repository.get_latest_proposal_for_case(
                organization_id, case_id
            )
            if latest_proposal is not None:
                self.repository.set_exception_case_status(
                    case_id, _proposal_case_status(latest_proposal)
                )
                continue

            if not self._event_is_current(organization_id, event):
                self._cancel_stale(job_id, organization_id, event_id)
                return
            self._propose_for_finding(
                job=job,
                event=event,
                trace_id=trace_id,
                case_id=case_id,
                case_key=case_key,
                source_record=source_record,
                source_hash=source_hash,
                finding=finding,
                employee=employee,
                punches=punches,
                schedules=schedules,
                schedule_by_id=schedule_by_id,
                as_of=as_of,
                week_summary=week_summary,
            )

        if not self._event_is_current(organization_id, event):
            self._cancel_stale(job_id, organization_id, event_id)
            return
        self.repository.supersede_open_cases_for_source(
            organization_id=organization_id,
            integration_id=integration_id,
            record_ref=record_ref,
            keep_case_ids=kept_case_ids,
            reason="finding_cleared_or_source_context_changed",
        )
        self.repository.complete_analysis_job(job_id, event_id)

    def _propose_for_finding(
        self,
        *,
        job: Row,
        event: Row,
        trace_id: str,
        case_id: UUID,
        case_key: str,
        source_record: AttendanceRecord,
        source_hash: str,
        finding: DetectedException,
        employee: dict[str, object],
        punches: list[Punch],
        schedules: list[Sched],
        schedule_by_id: dict[int, Sched],
        as_of: datetime,
        week_summary: dict[str, int],
    ) -> None:
        organization_id = UUID(str(job["organization_id"]))
        case_id_num = _model_correlation_id(case_key)
        synthetic_exception = ExceptionRecord(
            id=case_id_num,
            employee_id=str(employee["id"]),
            work_date=finding.work_date,
            rule_id=finding.rule_id,
            severity=finding.severity,
            evidence=finding.evidence,
            constraints=finding.constraints,
        )
        policies = self.policy_context.passages_for_rule(
            finding.rule_id, str(employee["jurisdiction"])
        )
        policy_ids = [passage.policy_id for passage in policies]
        day_punches = [punch for punch in punches if punch.work_date == finding.work_date]
        target_schedule = (
            schedule_by_id.get(finding.constraints.target_schedule_id)
            if finding.constraints.target_schedule_id is not None
            else next(
                (schedule for schedule in schedules if schedule.work_date == finding.work_date),
                None,
            )
        )
        context = ValidationContext(
            employee=employee,
            punches=punches,
            schedules=schedules,
            as_of=as_of,
            book=self.book,
            retrieved_policy_ids=policy_ids,
            disabled_rule_ids=self.policy_context.disabled_rule_ids,
        )
        last_feedback: str | list[CheckResult] | None = None
        total_latency_ms = 0
        generation_calls = 0
        transient_error = False
        chosen: LLMProposal | None = None
        validation: ValidationResult | None = None
        model_name = str(getattr(self.llm, "model", self.settings.llm_provider))

        for model_attempt in (1, 2):
            prompt_exception = {
                "id": case_id_num,
                "employee_id": finding.employee_id,
                "work_date": finding.work_date,
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "evidence": finding.evidence,
                "constraints": finding.constraints,
            }
            bundle = build_prompt(
                prompt_exception,
                employee,
                day_punches,
                target_schedule,
                week_summary,
                policies,
                notes=None,
                feedback=last_feedback,
            )
            prompt_hash = hashlib.sha256(
                (bundle.system + "\n" + bundle.user).encode("utf-8")
            ).hexdigest()
            self.repository.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type="PROMPT_BUILT",
                exception_case_id=case_id,
                payload={
                    "attempt": model_attempt,
                    "prompt_version": PROMPT_VERSION,
                    "prompt_hash": prompt_hash,
                    "policy_ids": policy_ids,
                },
            )
            generation_calls += 1
            started = time.monotonic()
            try:
                result = self.llm.generate(bundle, attempt=model_attempt)
            except (LLMTimeout, LLMUnavailable) as exc:
                transient_error = True
                chosen = None
                validation = None
                total_latency_ms += round((time.monotonic() - started) * 1000)
                self.repository.append_audit_event(
                    organization_id=organization_id,
                    trace_id=trace_id,
                    event_type="LLM_ERROR",
                    exception_case_id=case_id,
                    payload={"attempt": model_attempt, "error_code": _safe_error_code(exc)},
                )
                self.operational_alerts.report_failure(
                    "ollama",
                    _safe_error_code(exc),
                    trace_id=trace_id,
                    event_id=str(event["id"]),
                    job_id=str(job["id"]),
                    case_id=str(case_id),
                )
                last_feedback = (
                    "The model was temporarily unavailable. Retry using the supplied context."
                )
                continue

            self.operational_alerts.report_success("ollama")
            model_name = result.model
            total_latency_ms += result.latency_ms
            self.repository.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type="LLM_CALL",
                exception_case_id=case_id,
                payload={
                    "attempt": model_attempt,
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                },
            )
            chosen, validation = validate_proposal(result.raw_text, synthetic_exception, context)
            self.repository.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type="VALIDATION_RESULT",
                exception_case_id=case_id,
                payload={
                    "attempt": model_attempt,
                    "passed": validation.passed,
                    "failed_check_ids": [
                        check.id for check in validation.checks if not check.passed
                    ],
                },
            )
            if validation.passed and chosen is not None:
                break
            last_feedback = validation.checks

        if (
            transient_error
            and (validation is None or not validation.passed)
            and int(job["attempts"]) < self.settings.analysis_max_attempts
        ):
            raise RetryableAnalysisError("llm_temporarily_unavailable")

        if validation is None or chosen is None or not validation.passed:
            failed_checks = (
                [check.id for check in validation.checks if not check.passed]
                if validation is not None
                else ["LLM_UNAVAILABLE"]
            )
            self.repository.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type="FALLBACK",
                exception_case_id=case_id,
                payload={
                    "reason": "automated_proposal_unavailable",
                    "failed_check_ids": failed_checks,
                },
            )
            chosen = LLMProposal(
                exception_id=case_id_num,
                explanation=(
                    "An automated correction could not be validated from the available evidence. "
                    f"Manager review is required under {finding.rule_id}."
                ),
                proposed_correction=ProposedCorrection(action=Action.ESCALATE),
                policy_ids_cited=[finding.rule_id],
                confidence=0.0,
            )
            validation = ValidationResult(
                passed=True,
                checks=[
                    CheckResult(
                        id="FB-01",
                        name="Fallback used",
                        passed=True,
                        level="warning",
                        detail="Manager review is required because a safe automated suggestion was unavailable.",
                    )
                ],
            )
            source = ProposalSource.FALLBACK
        else:
            source = ProposalSource.LLM

        if not self._event_is_current(UUID(str(event["organization_id"])), event):
            raise StaleAnalysisError("source_changed_during_analysis")

        version = self.repository.next_proposal_version(case_id)
        attempt_total = max(1, (int(job["attempts"]) - 1) * 2 + generation_calls)
        proposal_id = self.repository.persist_proposal(
            organization_id=organization_id,
            exception_case_id=case_id,
            analysis_job_id=UUID(str(job["id"])),
            version=version,
            source=source.value,
            model=model_name,
            prompt_version=PROMPT_VERSION,
            expected_source_version=source_record.version,
            expected_source_hash=source_hash,
            proposal=chosen.model_dump(mode="json"),
            policy_ids=chosen.policy_ids_cited,
            policy_snapshot=self.policy_context.snapshot_for_rule(
                finding.rule_id, str(employee["jurisdiction"])
            ),
            validation_passed=validation.passed,
            validation_checks=[check.model_dump(mode="json") for check in validation.checks],
            validator_version=VALIDATOR_VERSION,
            attempts=attempt_total,
            latency_ms=total_latency_ms,
        )
        status = (
            "escalated"
            if source is ProposalSource.FALLBACK
            or chosen.proposed_correction.action is Action.ESCALATE
            else "pending_review"
        )
        self.repository.set_exception_case_status(case_id, status)
        self.repository.append_audit_event(
            organization_id=organization_id,
            trace_id=trace_id,
            event_type="PROPOSAL_CREATED",
            exception_case_id=case_id,
            payload={
                "proposal_id": str(proposal_id),
                "source": source.value,
                "model": model_name,
                "prompt_version": PROMPT_VERSION,
                "source_version": source_record.version,
                "source_hash": source_hash,
                "policy_ids": chosen.policy_ids_cited,
                "validation_passed": validation.passed,
                "attempts": attempt_total,
                "latency_ms": total_latency_ms,
            },
        )

    def _event_is_current(self, organization_id: UUID, event: Row) -> bool:
        employee_ref = event.get("external_employee_ref")
        refs = event.get("source_record_refs")
        if not isinstance(employee_ref, str) or not isinstance(refs, list) or len(refs) != 1:
            return False
        latest = self.repository.get_latest_inbound_event_for_source(
            organization_id,
            UUID(str(event["integration_id"])),
            employee_ref,
            str(refs[0]),
        )
        return latest is not None and UUID(str(latest["id"])) == UUID(str(event["id"]))

    def _cancel_stale(self, job_id: UUID, organization_id: UUID, event_id: UUID) -> None:
        self.repository.cancel_analysis_job(job_id, event_id, error_code="source_superseded")
        self.repository.append_audit_event(
            organization_id=organization_id,
            trace_id=f"analysis-{job_id.hex}",
            event_type="ANALYSIS_CANCELLED_STALE_SOURCE",
            payload={"reason": "a newer source event exists"},
        )

    def _retry_or_fail(self, job: Row, event: Row, error_code: str) -> None:
        attempts = int(job["attempts"])
        job_id = UUID(str(job["id"]))
        organization_id = UUID(str(job["organization_id"]))
        event_id = UUID(str(event["id"]))
        if attempts >= self.settings.analysis_max_attempts:
            self._fail(job_id, organization_id, event_id, error_code)
            return
        delay = min(
            self.settings.analysis_retry_base_seconds * (2 ** (attempts - 1)),
            MAX_RETRY_DELAY_SECONDS,
        )
        self.repository.retry_analysis_job(
            job_id,
            event_id,
            error_code=error_code,
            delay_seconds=delay,
        )
        self.repository.append_audit_event(
            organization_id=organization_id,
            trace_id=f"analysis-{job_id.hex}",
            event_type="ANALYSIS_RETRY_SCHEDULED",
            payload={"attempt": attempts, "delay_seconds": delay, "error_code": error_code},
        )
        self.operational_alerts.report_failure(
            _failure_component(error_code),
            error_code,
            trace_id=f"analysis-{job_id.hex}",
            event_id=str(event_id),
            job_id=str(job_id),
        )
        logger.warning(
            "analysis job retry scheduled",
            extra={
                "analysis_job_id": str(job_id),
                "integration_event_id": str(event_id),
                "retry_after_seconds": delay,
                "error_code": error_code,
            },
        )

    def _fail(
        self,
        job_id: UUID,
        organization_id: UUID,
        event_id: UUID | None,
        error_code: str,
    ) -> None:
        self.repository.fail_analysis_job(job_id, event_id, error_code=error_code)
        self.repository.append_audit_event(
            organization_id=organization_id,
            trace_id=f"analysis-{job_id.hex}",
            event_type="ANALYSIS_FAILED",
            payload={"error_code": error_code},
        )
        component = _failure_component(error_code)
        self.operational_alerts.report_failure(
            component,
            error_code,
            trace_id=f"analysis-{job_id.hex}",
            event_id=str(event_id) if event_id is not None else None,
            job_id=str(job_id),
        )
        logger.error(
            "analysis job failed",
            extra={
                "analysis_job_id": str(job_id),
                "integration_event_id": str(event_id) if event_id is not None else None,
                "error_code": error_code,
            },
        )


def _normalize_records(
    records: list[AttendanceRecord], source_record: AttendanceRecord, zone: ZoneInfo
) -> tuple[dict[str, object], list[Punch], list[Sched], dict[int, str], dict[int, str]]:
    eligibility_values = {record.ot_eligible for record in records}
    ot_eligible = next(iter(eligibility_values)) if len(eligibility_values) == 1 else None
    employee: dict[str, object] = {
        "id": source_record.employee_ref,
        "jurisdiction": source_record.jurisdiction,
        "ot_eligible": ot_eligible,
    }
    punches: list[Punch] = []
    schedules: list[Sched] = []
    punch_refs: dict[int, str] = {}
    schedule_refs: dict[int, str] = {}
    seen_punch_refs: set[str] = set()
    seen_schedule_refs: set[str] = set()

    for record in sorted(records, key=lambda item: (item.work_date, item.record_ref)):
        for item in record.punches:
            if item.record_ref in seen_punch_refs:
                raise ValueError("duplicate punch reference")
            seen_punch_refs.add(item.record_ref)
            punch_id = _stable_internal_id(item.record_ref)
            if punch_id in punch_refs:
                raise ValueError("punch reference hash collision")
            punch_refs[punch_id] = item.record_ref
            punches.append(
                Punch(
                    id=punch_id,
                    employee_id=record.employee_ref,
                    work_date=record.work_date,
                    ts=item.at.astimezone(zone).replace(tzinfo=None),
                    kind=PunchKind(item.kind),
                )
            )
        if record.schedule is not None:
            ref = record.schedule.record_ref
            if ref in seen_schedule_refs:
                raise ValueError("duplicate schedule reference")
            seen_schedule_refs.add(ref)
            schedule_id = _stable_internal_id(ref)
            if schedule_id in schedule_refs:
                raise ValueError("schedule reference hash collision")
            schedule_refs[schedule_id] = ref
            schedules.append(
                Sched(
                    id=schedule_id,
                    employee_id=record.employee_ref,
                    work_date=record.work_date,
                    start=record.schedule.start.astimezone(zone).replace(tzinfo=None),
                    end=record.schedule.end.astimezone(zone).replace(tzinfo=None),
                    planned_break_minutes=record.schedule.planned_break_minutes,
                )
            )
    return employee, punches, schedules, punch_refs, schedule_refs


def _analysis_time(work_date: date, zone: ZoneInfo) -> datetime:
    now = datetime.now(timezone.utc).astimezone(zone)
    if work_date < now.date():
        return datetime.combine(work_date, day_time(23, 59))
    if work_date > now.date():
        # Analyze future schedules from the start of their work date. This
        # includes planned shifts in projections without treating absent future
        # punches as missing punches.
        return datetime.combine(work_date, day_time.min)
    return now.replace(tzinfo=None)


def _stable_internal_id(reference: str) -> int:
    """Map an opaque company ID to a repeatable positive ID for existing validators."""
    return int(hashlib.sha256(reference.encode("utf-8")).hexdigest()[:8], 16) or 1


def _model_correlation_id(case_key: str) -> int:
    """Return a deterministic case reference that JSON clients can represent exactly.

    This is only used inside the model proposal contract; the durable workflow
    identity remains the UUID stored in ``exception_cases``. Thirteen hex digits
    fit comfortably below JavaScript's maximum safe integer, unlike the prior
    fifteen-digit slice.
    """
    return int(case_key[:13], 16) or 1


def _canonical_record_hash(record: AttendanceRecord) -> str:
    return _json_hash(record.model_dump(mode="json"))


def _range_hash(records: list[AttendanceRecord]) -> str:
    canonical_records = [
        record.model_dump(mode="json")
        for record in sorted(records, key=lambda item: (item.work_date, item.record_ref))
    ]
    return _json_hash(canonical_records)


def _json_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case_key(
    integration_id: UUID,
    employee_ref: str,
    source_hash: str,
    finding: DetectedException,
) -> str:
    material = "|".join(
        (
            str(integration_id),
            employee_ref,
            source_hash,
            finding.rule_id,
            finding.work_date.isoformat(),
            finding.fingerprint,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _minimal_snapshot(
    finding: DetectedException,
    source_record: AttendanceRecord,
    range_start: date,
    range_end: date,
    punch_refs: dict[int, str],
    schedule_refs: dict[int, str],
) -> dict[str, object]:
    punch_targets = {
        str(target_id): punch_refs[target_id]
        for target_id in finding.constraints.target_punch_ids
        if target_id in punch_refs
    }
    schedule_target = finding.constraints.target_schedule_id
    schedule_targets = (
        {str(schedule_target): schedule_refs[schedule_target]}
        if schedule_target is not None and schedule_target in schedule_refs
        else {}
    )
    return {
        "work_date": finding.work_date.isoformat(),
        "timezone": source_record.timezone,
        "jurisdiction": source_record.jurisdiction,
        "trigger_record_ref": source_record.record_ref,
        "analysis_window": {"from": range_start.isoformat(), "through": range_end.isoformat()},
        "evidence": finding.evidence,
        "constraints": finding.constraints.model_dump(mode="json"),
        "correction_targets": {"punches": punch_targets, "schedules": schedule_targets},
    }


def _proposal_case_status(proposal_row: Row) -> str:
    proposal = proposal_row.get("proposal")
    if isinstance(proposal, str):
        try:
            proposal = json.loads(proposal)
        except json.JSONDecodeError:
            proposal = None
    correction = proposal.get("proposed_correction") if isinstance(proposal, dict) else None
    action = correction.get("action") if isinstance(correction, dict) else None
    if (
        str(proposal_row.get("source")) == ProposalSource.FALLBACK.value
        or action == Action.ESCALATE.value
    ):
        return "escalated"
    return "pending_review"


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, LLMTimeout):
        return "llm_timeout"
    if isinstance(exc, LLMUnavailable):
        return "llm_unavailable"
    if isinstance(exc, AttendanceProviderUnavailable):
        return "attendance_provider_unavailable"
    return "upstream_unavailable"


def _failure_component(error_code: str) -> str:
    """Classify safe worker failure codes for separate operational alert streams."""
    if error_code.startswith("llm_"):
        return "ollama"
    if error_code.startswith("attendance_") or error_code.startswith("company_api_"):
        return "attendance_provider"
    return "analysis_worker"
