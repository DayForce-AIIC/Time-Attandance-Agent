"""PostgreSQL repository for durable ShiftGuard workflow records.

This store deliberately has no employee, punch, or schedule CRUD. Those records
remain owned by the company's HR/timekeeping system and are fetched by its API.
The existing SQLite repository remains the synthetic-data demo adapter until
the company connector and authenticated tenant context are added in later steps.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings

Row = dict[str, Any]
GENESIS_HASH = "GENESIS"


class PostgresWorkflowRepository:
    """Persist and reload workflow data from a Supabase PostgreSQL connection."""

    def __init__(self, connection: Connection[dict[str, Any]]) -> None:
        self.connection = connection

    @classmethod
    def connect(cls, database_url: str, *, connect_timeout: int = 5) -> PostgresWorkflowRepository:
        """Open a connection configured to return rows as dictionaries."""
        import psycopg

        return cls(
            psycopg.connect(
                database_url,
                row_factory=dict_row,
                autocommit=True,
                connect_timeout=connect_timeout,
            )
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> PostgresWorkflowRepository:
        """Create the repository from server-only settings or fail with a clear message."""
        database_url = settings.supabase_database_url
        if database_url is None or not database_url.get_secret_value().strip():
            raise RuntimeError("SUPABASE_DATABASE_URL is not configured")
        return cls.connect(database_url.get_secret_value(), connect_timeout=5)

    def close(self) -> None:
        """Close the underlying database connection."""
        self.connection.close()

    def create_organization(self, name: str) -> UUID:
        """Create one ShiftGuard tenant."""
        with self.connection.transaction():
            row = self.connection.execute(
                "INSERT INTO public.organizations (name) VALUES (%s) RETURNING id", (name,)
            ).fetchone()
            if row is None:
                raise RuntimeError("organization insert did not return an ID")
            return UUID(str(row["id"]))

    def get_user_profile(self, user_id: UUID) -> Row | None:
        """Load the server-managed organization and role mapping for an Auth user."""
        return self.connection.execute(
            """SELECT user_id, organization_id, role, manager_ref
               FROM public.user_profiles WHERE user_id = %s""",
            (user_id,),
        ).fetchone()

    def ensure_standard_policies(self, organization_id: UUID, policy_book: Any) -> None:
        """Create the organization's editable rows from the bundled rule catalog once."""
        with self.connection.transaction():
            for rule in policy_book.rules:
                row = self.connection.execute(
                    """INSERT INTO public.organization_policies
                       (organization_id, policy_id, kind, rule_id, category, title, text,
                        severity, applies_to_rule_ids)
                       VALUES (%s, %s, 'standard', %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (organization_id, policy_id) DO NOTHING
                       RETURNING *""",
                    (
                        organization_id,
                        rule.id,
                        rule.id,
                        rule.category,
                        rule.title,
                        rule.text,
                        rule.severity.value,
                        list(rule.related),
                    ),
                ).fetchone()
                if row is not None:
                    self._record_policy_revision(
                        row,
                        change_kind="created",
                        changed_by=None,
                    )

    def list_organization_policies(self, organization_id: UUID) -> list[Row]:
        """List policy rows and archived entries for one organization."""
        return list(
            self.connection.execute(
                """SELECT * FROM public.organization_policies
                   WHERE organization_id = %s
                   ORDER BY CASE WHEN kind = 'standard' THEN 0 ELSE 1 END,
                            category, policy_id""",
                (organization_id,),
            ).fetchall()
        )

    def create_company_policy(
        self,
        *,
        organization_id: UUID,
        policy_id: str,
        title: str,
        text: str,
        applies_to_rule_ids: list[str],
        actor_id: UUID,
    ) -> Row:
        """Create a custom reference policy and its immutable revision."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.organization_policies
                   (organization_id, policy_id, kind, category, title, text,
                    applies_to_rule_ids, created_by, updated_by)
                   VALUES (%s, %s, 'custom', 'company', %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    organization_id,
                    policy_id,
                    title,
                    text,
                    applies_to_rule_ids,
                    actor_id,
                    actor_id,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("company policy insert did not return a row")
            self._record_policy_revision(row, change_kind="created", changed_by=actor_id)
            self.append_audit_event(
                organization_id=organization_id,
                trace_id=f"policy-{row['id']}-r{row['revision']}",
                event_type="POLICY_CREATED",
                payload={"policy_id": policy_id, "kind": "custom", "revision": row["revision"]},
            )
            return row

    def update_organization_policy(
        self,
        *,
        organization_id: UUID,
        policy_id: str,
        updates: dict[str, Any],
        actor_id: UUID,
    ) -> Row | None:
        """Update policy content for one organization and append a revision/audit event."""
        allowed_columns = {"title", "text", "severity", "is_active", "applies_to_rule_ids"}
        if not updates or not set(updates).issubset(allowed_columns):
            raise ValueError("unsupported organization policy update")
        with self.connection.transaction():
            current = self.connection.execute(
                """SELECT * FROM public.organization_policies
                   WHERE organization_id = %s AND policy_id = %s FOR UPDATE""",
                (organization_id, policy_id),
            ).fetchone()
            if current is None:
                return None
            assignments: list[str] = []
            values: list[Any] = []
            change_kind = "updated"
            event_type = "POLICY_UPDATED"
            for key, value in updates.items():
                assignments.append(f"{key} = %s")
                values.append(value)
            if current["kind"] == "custom" and "is_active" in updates:
                assignments.append(
                    "archived_at = CASE WHEN %s THEN NULL ELSE COALESCE(archived_at, now()) END"
                )
                values.append(bool(updates["is_active"]))
                if not updates["is_active"]:
                    change_kind = "archived"
                    event_type = "POLICY_ARCHIVED"
                elif current.get("archived_at") is not None:
                    event_type = "POLICY_RESTORED"
            assignments.extend(["revision = revision + 1", "updated_by = %s", "updated_at = now()"])
            values.extend([actor_id, organization_id, policy_id])
            row = self.connection.execute(
                """UPDATE public.organization_policies SET """
                + ", ".join(assignments)
                + " WHERE organization_id = %s AND policy_id = %s RETURNING *",
                values,
            ).fetchone()
            if row is None:
                raise RuntimeError("organization policy update did not return a row")
            self._record_policy_revision(row, change_kind=change_kind, changed_by=actor_id)
            self.append_audit_event(
                organization_id=organization_id,
                trace_id=f"policy-{row['id']}-r{row['revision']}",
                event_type=event_type,
                payload={
                    "policy_id": policy_id,
                    "kind": row["kind"],
                    "revision": row["revision"],
                    "changed_fields": sorted(updates),
                },
            )
            return row

    def archive_company_policy(
        self, *, organization_id: UUID, policy_id: str, actor_id: UUID
    ) -> Row | None:
        """Soft-archive a custom policy so cited historic content remains traceable."""
        with self.connection.transaction():
            row = self.connection.execute(
                """UPDATE public.organization_policies
                   SET is_active = false, archived_at = now(), revision = revision + 1,
                       updated_by = %s, updated_at = now()
                   WHERE organization_id = %s AND policy_id = %s AND kind = 'custom'
                   RETURNING *""",
                (actor_id, organization_id, policy_id),
            ).fetchone()
            if row is None:
                return None
            self._record_policy_revision(row, change_kind="archived", changed_by=actor_id)
            self.append_audit_event(
                organization_id=organization_id,
                trace_id=f"policy-{row['id']}-r{row['revision']}",
                event_type="POLICY_ARCHIVED",
                payload={"policy_id": policy_id, "kind": "custom", "revision": row["revision"]},
            )
            return row

    def _record_policy_revision(
        self, row: Row, *, change_kind: str, changed_by: UUID | None
    ) -> None:
        snapshot = {
            key: (
                value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                if isinstance(value, UUID)
                else value
            )
            for key, value in row.items()
        }
        self.connection.execute(
            """INSERT INTO public.organization_policy_revisions
               (organization_id, policy_id, revision, change_kind, snapshot, changed_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                row["organization_id"],
                row["id"],
                row["revision"],
                change_kind,
                Jsonb(snapshot),
                changed_by,
            ),
        )

    def create_integration(
        self,
        *,
        organization_id: UUID,
        provider: str,
        display_name: str,
        credential_ref: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> UUID:
        """Create an integration record without storing its credential secret."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.integrations
                   (organization_id, provider, display_name, credential_ref, settings)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (organization_id, provider, display_name, credential_ref, Jsonb(settings or {})),
            ).fetchone()
            if row is None:
                raise RuntimeError("integration insert did not return an ID")
            return UUID(str(row["id"]))

    def record_inbound_event(
        self,
        *,
        organization_id: UUID,
        integration_id: UUID,
        external_event_id: str,
        event_type: str,
        payload_hash: str,
        external_employee_ref: str | None = None,
        source_record_refs: list[str] | None = None,
        source_version: str | None = None,
        minimal_payload: dict[str, Any] | None = None,
    ) -> tuple[UUID, bool]:
        """Insert an event once; return its ID and whether it was newly accepted."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.inbound_events
                   (organization_id, integration_id, external_event_id, event_type,
                    external_employee_ref, source_record_refs, source_version,
                    payload_hash, minimal_payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (integration_id, external_event_id) DO NOTHING
                   RETURNING id""",
                (
                    organization_id,
                    integration_id,
                    external_event_id,
                    event_type,
                    external_employee_ref,
                    Jsonb(source_record_refs or []),
                    source_version,
                    payload_hash,
                    Jsonb(minimal_payload or {}),
                ),
            ).fetchone()
            if row is not None:
                return UUID(str(row["id"])), True
            existing = self.connection.execute(
                """SELECT id, payload_hash FROM public.inbound_events
                   WHERE integration_id = %s AND external_event_id = %s""",
                (integration_id, external_event_id),
            ).fetchone()
            if existing is None:
                raise RuntimeError("inbound event conflict occurred but no prior event was found")
            if str(existing["payload_hash"]) != payload_hash:
                raise ValueError("the same external event ID was received with a different payload")
            return UUID(str(existing["id"])), False

    def upsert_exception_case(
        self,
        *,
        organization_id: UUID,
        case_key: str,
        external_employee_ref: str,
        rule_id: str,
        severity: str,
        source_record_refs: list[str],
        integration_id: UUID | None = None,
        source_version: str | None = None,
        source_updated_at: datetime | None = None,
        source_hash: str | None = None,
        minimal_evidence_snapshot: dict[str, Any] | None = None,
        status: str = "open",
    ) -> UUID:
        """Create or refresh a case using only opaque company references and minimal evidence."""
        # case_key must include the deterministic finding fingerprint and source
        # version. A changed source therefore gets a new case instead of erasing
        # the evidence/version that an earlier HR decision reviewed.
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.exception_cases
                   (organization_id, integration_id, case_key, external_employee_ref,
                    source_record_refs, source_version, source_updated_at, source_hash,
                    minimal_evidence_snapshot, rule_id, severity, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (organization_id, case_key) DO UPDATE SET
                     integration_id = EXCLUDED.integration_id,
                     external_employee_ref = EXCLUDED.external_employee_ref,
                     source_record_refs = EXCLUDED.source_record_refs,
                     source_version = EXCLUDED.source_version,
                     source_updated_at = EXCLUDED.source_updated_at,
                     source_hash = EXCLUDED.source_hash,
                     minimal_evidence_snapshot = EXCLUDED.minimal_evidence_snapshot,
                     rule_id = EXCLUDED.rule_id,
                     severity = EXCLUDED.severity,
                     status = CASE
                       WHEN public.exception_cases.status IN ('resolved', 'rejected')
                         THEN public.exception_cases.status
                       ELSE EXCLUDED.status
                     END,
                     updated_at = now()
                   RETURNING id""",
                (
                    organization_id,
                    integration_id,
                    case_key,
                    external_employee_ref,
                    Jsonb(source_record_refs),
                    source_version,
                    source_updated_at,
                    source_hash,
                    Jsonb(minimal_evidence_snapshot)
                    if minimal_evidence_snapshot is not None
                    else None,
                    rule_id,
                    severity,
                    status,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("case upsert did not return an ID")
        return UUID(str(row["id"]))

    def enqueue_analysis_job(
        self,
        *,
        organization_id: UUID,
        exception_case_id: UUID | None = None,
        idempotency_key: str,
        inbound_event_id: UUID | None = None,
        job_type: str = "analyze",
    ) -> tuple[UUID, bool]:
        """Queue one durable job per idempotency key."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.analysis_jobs
                   (organization_id, exception_case_id, inbound_event_id, job_type, idempotency_key)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (organization_id, idempotency_key) DO NOTHING
                   RETURNING id""",
                (organization_id, exception_case_id, inbound_event_id, job_type, idempotency_key),
            ).fetchone()
            if row is not None:
                return UUID(str(row["id"])), True
            existing = self.connection.execute(
                """SELECT id FROM public.analysis_jobs
                   WHERE organization_id = %s AND idempotency_key = %s""",
                (organization_id, idempotency_key),
            ).fetchone()
            if existing is None:
                raise RuntimeError("job conflict occurred but no prior job was found")
            return UUID(str(existing["id"])), False

    def persist_proposal(
        self,
        *,
        organization_id: UUID,
        exception_case_id: UUID,
        version: int,
        source: str,
        model: str,
        prompt_version: str,
        proposal: dict[str, Any],
        validation_passed: bool,
        validation_checks: list[dict[str, Any]],
        validator_version: str,
        analysis_job_id: UUID | None = None,
        expected_source_version: str | None = None,
        expected_source_hash: str | None = None,
        policy_ids: list[str] | None = None,
        policy_snapshot: list[dict[str, Any]] | None = None,
        attempts: int = 1,
        latency_ms: int = 0,
    ) -> UUID:
        """Persist a proposal and its deterministic validation result atomically."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.proposals
                   (organization_id, exception_case_id, analysis_job_id, version, source,
                    model, prompt_version, expected_source_version, expected_source_hash,
                    proposal, policy_ids, policy_snapshot, attempts, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    organization_id,
                    exception_case_id,
                    analysis_job_id,
                    version,
                    source,
                    model,
                    prompt_version,
                    expected_source_version,
                    expected_source_hash,
                    Jsonb(proposal),
                    policy_ids or [],
                    Jsonb(policy_snapshot or []),
                    attempts,
                    latency_ms,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("proposal insert did not return an ID")
            proposal_id = UUID(str(row["id"]))
            self.connection.execute(
                """INSERT INTO public.validation_results
                   (organization_id, proposal_id, passed, checks, validator_version)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    organization_id,
                    proposal_id,
                    validation_passed,
                    Jsonb(validation_checks),
                    validator_version,
                ),
            )
            return proposal_id

    def record_decision(
        self,
        *,
        organization_id: UUID,
        exception_case_id: UUID,
        decided_by: UUID,
        action: str,
        proposal_id: UUID | None = None,
        note: str | None = None,
        final_correction: dict[str, Any] | None = None,
        expected_source_version: str | None = None,
        writeback_status: str = "not_requested",
        case_status_after: str | None = None,
    ) -> UUID:
        """Persist an HR decision without applying it to the company system."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.decisions
                   (organization_id, exception_case_id, proposal_id, decided_by, action,
                    note, final_correction, expected_source_version, writeback_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    organization_id,
                    exception_case_id,
                    proposal_id,
                    decided_by,
                    action,
                    note,
                    Jsonb(final_correction) if final_correction is not None else None,
                    expected_source_version,
                    writeback_status,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("decision insert did not return an ID")
            decision_id = UUID(str(row["id"]))
            if case_status_after is not None:
                self.connection.execute(
                    """UPDATE public.exception_cases
                       SET status = %s, reviewed_at = now(), updated_at = now()
                       WHERE id = %s AND organization_id = %s""",
                    (case_status_after, exception_case_id, organization_id),
                )
            return decision_id

    def append_audit_event(
        self,
        *,
        organization_id: UUID,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
        exception_case_id: UUID | None = None,
    ) -> Row:
        """Append a tenant-scoped hash-chained event under a transaction lock."""
        payload_json = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        with self.connection.transaction():
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(organization_id),),
            )
            previous = self.connection.execute(
                """SELECT sequence_number, event_hash FROM public.audit_events
                   WHERE organization_id = %s ORDER BY sequence_number DESC LIMIT 1""",
                (organization_id,),
            ).fetchone()
            sequence_number = int(previous["sequence_number"]) + 1 if previous else 1
            previous_hash = str(previous["event_hash"]) if previous else GENESIS_HASH
            now = datetime.now(timezone.utc)
            created_at = now.replace(microsecond=(now.microsecond // 1000) * 1000)
            created_at_text = created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            event_hash = _event_hash(
                previous_hash,
                trace_id,
                exception_case_id,
                sequence_number,
                event_type,
                payload_json,
                created_at_text,
            )
            row = self.connection.execute(
                """INSERT INTO public.audit_events
                   (organization_id, trace_id, exception_case_id, sequence_number,
                    event_type, payload, previous_hash, event_hash, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    organization_id,
                    trace_id,
                    exception_case_id,
                    sequence_number,
                    event_type,
                    Jsonb(payload),
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("audit insert did not return an event")
            return row

    def get_integration(self, integration_id: UUID, organization_id: UUID) -> Row | None:
        """Load one connector only inside its owning organization."""
        return self.connection.execute(
            """SELECT * FROM public.integrations
               WHERE id = %s AND organization_id = %s""",
            (integration_id, organization_id),
        ).fetchone()

    def record_event_and_enqueue_detection(
        self,
        *,
        organization_id: UUID,
        integration_id: UUID,
        external_event_id: str,
        event_type: str,
        payload_hash: str,
        external_employee_ref: str | None = None,
        source_record_refs: list[str] | None = None,
        source_version: str | None = None,
        minimal_payload: dict[str, Any] | None = None,
    ) -> tuple[UUID, bool, UUID]:
        """Durably deduplicate a source event and queue its analysis exactly once."""
        with self.connection.transaction():
            event_id, created = self.record_inbound_event(
                organization_id=organization_id,
                integration_id=integration_id,
                external_event_id=external_event_id,
                event_type=event_type,
                payload_hash=payload_hash,
                external_employee_ref=external_employee_ref,
                source_record_refs=source_record_refs,
                source_version=source_version,
                minimal_payload=minimal_payload,
            )
            job_id, _ = self.enqueue_analysis_job(
                organization_id=organization_id,
                idempotency_key=f"detect:{integration_id}:{external_event_id}",
                inbound_event_id=event_id,
            )
            self.connection.execute(
                """UPDATE public.inbound_events
                   SET status = CASE WHEN status = 'received' THEN 'queued' ELSE status END
                   WHERE id = %s AND organization_id = %s""",
                (event_id, organization_id),
            )
            return event_id, created, job_id

    def get_inbound_event_by_id(self, event_id: UUID, organization_id: UUID) -> Row | None:
        """Read one persisted event within its organization."""
        return self.connection.execute(
            """SELECT * FROM public.inbound_events
               WHERE id = %s AND organization_id = %s""",
            (event_id, organization_id),
        ).fetchone()

    def get_latest_inbound_event_for_source(
        self,
        organization_id: UUID,
        integration_id: UUID,
        employee_ref: str,
        record_ref: str,
    ) -> Row | None:
        """Find the newest event for a specific employee timecard record."""
        return self.connection.execute(
            """SELECT * FROM public.inbound_events
               WHERE organization_id = %s AND integration_id = %s
                 AND external_employee_ref = %s
                 AND source_record_refs @> jsonb_build_array(%s::text)
               ORDER BY received_at DESC, id DESC LIMIT 1""",
            (organization_id, integration_id, employee_ref, record_ref),
        ).fetchone()

    def claim_analysis_job(
        self,
        lease_seconds: int,
        organization_id: UUID,
        integration_id: UUID,
    ) -> Row | None:
        """Claim the oldest eligible job with row locking for concurrent workers."""
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.analysis_jobs AS jobs
                   SET status = 'retry', locked_at = NULL,
                       available_at = LEAST(available_at, now())
                   FROM public.inbound_events AS events
                   WHERE jobs.inbound_event_id = events.id
                     AND jobs.organization_id = %s AND events.integration_id = %s
                     AND jobs.status = 'processing'
                     AND jobs.locked_at < now() - make_interval(secs => %s)""",
                (organization_id, integration_id, lease_seconds),
            )
            job = self.connection.execute(
                """WITH candidate AS (
                     SELECT jobs.id
                     FROM public.analysis_jobs AS jobs
                     JOIN public.inbound_events AS events ON events.id = jobs.inbound_event_id
                     WHERE jobs.organization_id = %s AND events.integration_id = %s
                       AND jobs.status IN ('queued', 'retry') AND jobs.available_at <= now()
                     ORDER BY jobs.available_at, jobs.created_at, jobs.id
                     FOR UPDATE OF jobs SKIP LOCKED
                     LIMIT 1
                   )
                   UPDATE public.analysis_jobs AS jobs
                   SET status = 'processing', attempts = jobs.attempts + 1, locked_at = now()
                   FROM candidate
                   WHERE jobs.id = candidate.id
                   RETURNING jobs.*""",
                (organization_id, integration_id),
            ).fetchone()
            if job is not None and job.get("inbound_event_id") is not None:
                self.connection.execute(
                    """UPDATE public.inbound_events SET status = 'processing'
                       WHERE id = %s AND organization_id = %s""",
                    (job["inbound_event_id"], organization_id),
                )
            return job

    def complete_analysis_job(
        self, job_id: UUID, event_id: UUID, *, event_status: str = "processed"
    ) -> None:
        """Mark a claimed job and its inbound event complete atomically."""
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.analysis_jobs
                   SET status = 'completed', completed_at = now(), locked_at = NULL,
                       last_error_code = NULL
                   WHERE id = %s""",
                (job_id,),
            )
            self.connection.execute(
                """UPDATE public.inbound_events
                   SET status = %s, processed_at = now(), error_code = NULL
                   WHERE id = %s""",
                (event_status, event_id),
            )

    def retry_analysis_job(
        self, job_id: UUID, event_id: UUID, *, error_code: str, delay_seconds: int
    ) -> None:
        """Release a job for a later retry while preserving the last safe error code."""
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.analysis_jobs
                   SET status = 'retry', available_at = now() + make_interval(secs => %s),
                       locked_at = NULL, last_error_code = %s
                   WHERE id = %s""",
                (delay_seconds, error_code, job_id),
            )
            self.connection.execute(
                """UPDATE public.inbound_events SET status = 'queued', error_code = %s
                   WHERE id = %s""",
                (error_code, event_id),
            )

    def fail_analysis_job(self, job_id: UUID, event_id: UUID | None, *, error_code: str) -> None:
        """Stop a permanently failing job and expose only its safe error code."""
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.analysis_jobs
                   SET status = 'failed', completed_at = now(), locked_at = NULL,
                       last_error_code = %s
                   WHERE id = %s""",
                (error_code, job_id),
            )
            if event_id is not None:
                self.connection.execute(
                    """UPDATE public.inbound_events
                       SET status = 'failed', processed_at = now(), error_code = %s
                       WHERE id = %s""",
                    (error_code, event_id),
                )

    def cancel_analysis_job(self, job_id: UUID, event_id: UUID, *, error_code: str) -> None:
        """Cancel work that has been superseded by a newer source event."""
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.analysis_jobs
                   SET status = 'cancelled', completed_at = now(), locked_at = NULL,
                       last_error_code = %s
                   WHERE id = %s""",
                (error_code, job_id),
            )
            self.connection.execute(
                """UPDATE public.inbound_events
                   SET status = 'ignored', processed_at = now(), error_code = %s
                   WHERE id = %s""",
                (error_code, event_id),
            )

    def get_exception_case_by_key(self, organization_id: UUID, case_key: str) -> Row | None:
        """Find a deterministic finding within one organization."""
        return self.connection.execute(
            """SELECT * FROM public.exception_cases
               WHERE organization_id = %s AND case_key = %s""",
            (organization_id, case_key),
        ).fetchone()

    def list_exception_cases(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        severity: str | None = None,
        rule_id: str | None = None,
        employee_ref: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Row]:
        """List organization cases with optional review filters and stable paging."""
        return list(
            self.connection.execute(
                """SELECT * FROM public.exception_cases
                   WHERE organization_id = %s
                     AND (%s::text IS NULL OR status = %s)
                     AND (%s::text IS NULL OR severity = %s)
                     AND (%s::text IS NULL OR rule_id = %s)
                     AND (%s::text IS NULL OR external_employee_ref = %s)
                   ORDER BY detected_at DESC, id DESC LIMIT %s OFFSET %s""",
                (
                    organization_id,
                    status,
                    status,
                    severity,
                    severity,
                    rule_id,
                    rule_id,
                    employee_ref,
                    employee_ref,
                    limit,
                    offset,
                ),
            ).fetchall()
        )

    def set_exception_case_status(self, case_id: UUID, status: str) -> None:
        """Update one case's workflow state after analysis."""
        self.connection.execute(
            """UPDATE public.exception_cases
               SET status = %s, updated_at = now()
               WHERE id = %s""",
            (status, case_id),
        )

    def supersede_open_cases_for_source(
        self,
        *,
        organization_id: UUID,
        integration_id: UUID,
        record_ref: str,
        keep_case_ids: set[UUID],
        reason: str,
    ) -> None:
        """Close active cases for a source record that no longer match detection."""
        case_ids = list(keep_case_ids)
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.exception_cases
                   SET status = 'superseded', reviewed_at = now(), updated_at = now()
                   WHERE organization_id = %s AND integration_id = %s
                     AND source_record_refs @> %s
                     AND status IN ('open', 'pending_review', 'escalated')
                     AND NOT (id = ANY(%s::uuid[]))""",
                (organization_id, integration_id, Jsonb([record_ref]), case_ids),
            )

    def get_proposal_for_job_case(
        self, organization_id: UUID, case_id: UUID, job_id: UUID
    ) -> Row | None:
        """Load the proposal already generated for this job and case, if any."""
        return self.connection.execute(
            """SELECT * FROM public.proposals
               WHERE organization_id = %s AND exception_case_id = %s AND analysis_job_id = %s
               ORDER BY version DESC LIMIT 1""",
            (organization_id, case_id, job_id),
        ).fetchone()

    def get_latest_proposal_for_case(self, organization_id: UUID, case_id: UUID) -> Row | None:
        """Load the newest proposal for a case."""
        return self.connection.execute(
            """SELECT * FROM public.proposals
               WHERE organization_id = %s AND exception_case_id = %s
               ORDER BY version DESC LIMIT 1""",
            (organization_id, case_id),
        ).fetchone()

    def next_proposal_version(self, case_id: UUID) -> int:
        """Allocate the next monotonically increasing proposal version."""
        row = self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
            "FROM public.proposals WHERE exception_case_id = %s",
            (case_id,),
        ).fetchone()
        return int(row["next_version"]) if row else 1

    def supersede_case_with_audit(
        self,
        *,
        case_id: UUID,
        organization_id: UUID,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Mark a case stale and append the corresponding audit event atomically."""
        with self.connection.transaction():
            self.connection.execute(
                """UPDATE public.exception_cases
                   SET status = 'superseded', reviewed_at = now(), updated_at = now()
                   WHERE id = %s AND organization_id = %s
                     AND status IN ('open', 'pending_review', 'escalated')""",
                (case_id, organization_id),
            )
            self.append_audit_event(
                organization_id=organization_id,
                trace_id=trace_id,
                event_type=event_type,
                exception_case_id=case_id,
                payload=payload,
            )

    def get_latest_proposal_bundle_for_case(
        self, case_id: UUID, organization_id: UUID
    ) -> Row | None:
        """Load the latest proposal and its validation result for a case."""
        return self.connection.execute(
            """SELECT p.*,
                      COALESCE(
                        jsonb_agg(jsonb_build_object(
                          'id', v.id, 'passed', v.passed, 'checks', v.checks,
                          'validator_version', v.validator_version, 'created_at', v.created_at
                        ) ORDER BY v.created_at) FILTER (WHERE v.id IS NOT NULL),
                        '[]'::jsonb
                      ) AS validation_results
               FROM public.proposals AS p
               LEFT JOIN public.validation_results AS v
                 ON v.proposal_id = p.id AND v.organization_id = p.organization_id
               WHERE p.exception_case_id = %s AND p.organization_id = %s
               GROUP BY p.id ORDER BY p.version DESC LIMIT 1""",
            (case_id, organization_id),
        ).fetchone()

    def get_latest_decision_for_case(self, case_id: UUID, organization_id: UUID) -> Row | None:
        """Load the most recent HR decision for a case."""
        return self.connection.execute(
            """SELECT * FROM public.decisions
               WHERE exception_case_id = %s AND organization_id = %s
               ORDER BY decided_at DESC, id DESC LIMIT 1""",
            (case_id, organization_id),
        ).fetchone()

    def list_all_audit_events(self, organization_id: UUID) -> list[Row]:
        """Return a complete organization audit chain in sequence order."""
        return list(
            self.connection.execute(
                """SELECT * FROM public.audit_events
                   WHERE organization_id = %s ORDER BY sequence_number""",
                (organization_id,),
            ).fetchall()
        )

    def update_decision_writeback(
        self,
        *,
        decision_id: UUID,
        organization_id: UUID,
        writeback_status: str,
        case_status: str,
        external_operation_id: str | None,
        writeback_result: dict[str, Any] | None,
    ) -> Row:
        """Persist the company writeback result and corresponding case state."""
        with self.connection.transaction():
            row = self.connection.execute(
                """UPDATE public.decisions
                   SET writeback_status = %s, external_operation_id = %s,
                       writeback_result = %s,
                       writeback_completed_at = CASE WHEN %s = 'pending' THEN NULL ELSE now() END
                   WHERE id = %s AND organization_id = %s
                   RETURNING *""",
                (
                    writeback_status,
                    external_operation_id,
                    Jsonb(writeback_result) if writeback_result is not None else None,
                    writeback_status,
                    decision_id,
                    organization_id,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("decision was not found while saving writeback result")
            self.connection.execute(
                """UPDATE public.exception_cases SET status = %s,
                       reviewed_at = CASE WHEN %s IN ('resolved', 'superseded') THEN now() ELSE reviewed_at END,
                       updated_at = now()
                   WHERE id = %s AND organization_id = %s""",
                (case_status, case_status, row["exception_case_id"], organization_id),
            )
            return row

    def record_service_heartbeat(
        self, organization_id: UUID, service_name: str, instance_id: str
    ) -> None:
        """Upsert the latest liveness timestamp for a named service instance."""
        self.connection.execute(
            """INSERT INTO public.service_heartbeats
               (organization_id, service_name, instance_id, started_at, last_seen_at)
               VALUES (%s, %s, %s, now(), now())
               ON CONFLICT (organization_id, service_name, instance_id)
               DO UPDATE SET last_seen_at = now()""",
            (organization_id, service_name, instance_id),
        )

    def remove_service_heartbeat(
        self, organization_id: UUID, service_name: str, instance_id: str
    ) -> None:
        """Remove a heartbeat when a service instance exits cleanly."""
        self.connection.execute(
            """DELETE FROM public.service_heartbeats
               WHERE organization_id = %s AND service_name = %s AND instance_id = %s""",
            (organization_id, service_name, instance_id),
        )

    def get_latest_service_heartbeat(self, organization_id: UUID, service_name: str) -> Row | None:
        """Load the latest worker heartbeat for a deployment readiness probe."""
        return self.connection.execute(
            """SELECT * FROM public.service_heartbeats
               WHERE organization_id = %s AND service_name = %s
               ORDER BY last_seen_at DESC LIMIT 1""",
            (organization_id, service_name),
        ).fetchone()

    def persist_conversation_exchange(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None,
        title: str,
        question: str,
        answer: str,
        model: str,
        source_references: list[dict[str, Any]],
    ) -> UUID:
        """Persist a user question and assistant answer as one ordered exchange."""
        with self.connection.transaction():
            if conversation_id is None:
                row = self.connection.execute(
                    """INSERT INTO public.conversations
                       (organization_id, user_id, title)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (organization_id, user_id, title[:160]),
                ).fetchone()
                if row is None:
                    raise RuntimeError("conversation insert did not return an ID")
                conversation_id = UUID(str(row["id"]))
            else:
                row = self.connection.execute(
                    """UPDATE public.conversations SET updated_at = now()
                       WHERE id = %s AND organization_id = %s AND user_id = %s
                       RETURNING id""",
                    (conversation_id, organization_id, user_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError("conversation was not found while saving exchange")

            last = self.connection.execute(
                """SELECT COALESCE(MAX(sequence_number), 0) AS max_sequence
                   FROM public.messages WHERE conversation_id = %s""",
                (conversation_id,),
            ).fetchone()
            next_sequence = int(last["max_sequence"]) + 1 if last else 1
            self.connection.execute(
                """INSERT INTO public.messages
                   (organization_id, conversation_id, sequence_number, role, content,
                    model, source_references)
                   VALUES (%s, %s, %s, 'user', %s, NULL, '[]'::jsonb),
                          (%s, %s, %s, 'assistant', %s, %s, %s)""",
                (
                    organization_id,
                    conversation_id,
                    next_sequence,
                    question,
                    organization_id,
                    conversation_id,
                    next_sequence + 1,
                    answer,
                    model,
                    Jsonb(source_references),
                ),
            )
            return conversation_id

    def list_conversations(
        self, organization_id: UUID, user_id: UUID, *, limit: int = 100
    ) -> list[Row]:
        """List a user's conversations with a short most-recent-message preview."""
        return list(
            self.connection.execute(
                """SELECT conversations.id, conversations.title, conversations.created_at,
                          conversations.updated_at,
                          (SELECT left(messages.content, 240) FROM public.messages
                           WHERE messages.conversation_id = conversations.id
                           ORDER BY messages.sequence_number DESC LIMIT 1) AS last_message_preview
                   FROM public.conversations
                   WHERE organization_id = %s AND user_id = %s
                   ORDER BY updated_at DESC LIMIT %s""",
                (organization_id, user_id, limit),
            ).fetchall()
        )

    def get_conversation(
        self, conversation_id: UUID, organization_id: UUID, user_id: UUID
    ) -> Row | None:
        """Load conversation metadata only for its owner and organization."""
        return self.connection.execute(
            """SELECT * FROM public.conversations
               WHERE id = %s AND organization_id = %s AND user_id = %s""",
            (conversation_id, organization_id, user_id),
        ).fetchone()

    def list_conversation_messages(
        self,
        conversation_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        *,
        limit: int = 200,
    ) -> list[Row]:
        """List chronologically ordered messages after confirming conversation ownership."""
        return list(
            self.connection.execute(
                """SELECT messages.* FROM public.messages
                   JOIN public.conversations ON conversations.id = messages.conversation_id
                   WHERE messages.conversation_id = %s
                     AND messages.organization_id = %s
                     AND conversations.organization_id = %s AND conversations.user_id = %s
                   ORDER BY messages.sequence_number LIMIT %s""",
                (conversation_id, organization_id, organization_id, user_id, limit),
            ).fetchall()
        )

    def delete_conversation(
        self, conversation_id: UUID, organization_id: UUID, user_id: UUID
    ) -> bool:
        """Delete a conversation and its messages only for the authenticated owner."""
        row = self.connection.execute(
            """DELETE FROM public.conversations
               WHERE id = %s AND organization_id = %s AND user_id = %s RETURNING id""",
            (conversation_id, organization_id, user_id),
        ).fetchone()
        return row is not None

    def get_exception_case(self, case_id: UUID, organization_id: UUID) -> Row | None:
        """Reload one case only within its organization."""
        return self.connection.execute(
            """SELECT * FROM public.exception_cases
               WHERE id = %s AND organization_id = %s""",
            (case_id, organization_id),
        ).fetchone()

    def get_proposal(self, proposal_id: UUID, organization_id: UUID) -> Row | None:
        """Reload one proposal only within its organization."""
        return self.connection.execute(
            """SELECT * FROM public.proposals
               WHERE id = %s AND organization_id = %s""",
            (proposal_id, organization_id),
        ).fetchone()

    def get_proposal_bundle(self, proposal_id: UUID, organization_id: UUID) -> Row | None:
        """Reload a proposal together with its persisted validation results."""
        return self.connection.execute(
            """SELECT p.*,
                      COALESCE(
                        jsonb_agg(jsonb_build_object(
                          'id', v.id,
                          'passed', v.passed,
                          'checks', v.checks,
                          'validator_version', v.validator_version,
                          'created_at', v.created_at
                        ) ORDER BY v.created_at) FILTER (WHERE v.id IS NOT NULL),
                        '[]'::jsonb
                      ) AS validation_results
               FROM public.proposals AS p
               LEFT JOIN public.validation_results AS v
                 ON v.proposal_id = p.id AND v.organization_id = p.organization_id
               WHERE p.id = %s AND p.organization_id = %s
               GROUP BY p.id""",
            (proposal_id, organization_id),
        ).fetchone()

    def get_decision(self, decision_id: UUID, organization_id: UUID) -> Row | None:
        """Reload one decision only within its organization."""
        return self.connection.execute(
            """SELECT * FROM public.decisions
               WHERE id = %s AND organization_id = %s""",
            (decision_id, organization_id),
        ).fetchone()

    def list_audit_events(self, organization_id: UUID, case_id: UUID) -> list[Row]:
        """Reload a case's audit trail within its organization."""
        return list(
            self.connection.execute(
                """SELECT * FROM public.audit_events
                   WHERE organization_id = %s AND exception_case_id = %s
                   ORDER BY sequence_number""",
                (organization_id, case_id),
            ).fetchall()
        )


def _event_hash(
    previous_hash: str,
    trace_id: str,
    exception_case_id: UUID | None,
    sequence_number: int,
    event_type: str,
    payload_json: str,
    created_at: str,
) -> str:
    material = (
        f"{previous_hash}|{trace_id}|{exception_case_id}|{sequence_number}|"
        f"{event_type}|{payload_json}|{created_at}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
