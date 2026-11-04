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
        exception_case_id: UUID,
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
        attempts: int = 1,
        latency_ms: int = 0,
    ) -> UUID:
        """Persist a proposal and its deterministic validation result atomically."""
        with self.connection.transaction():
            row = self.connection.execute(
                """INSERT INTO public.proposals
                   (organization_id, exception_case_id, analysis_job_id, version, source,
                    model, prompt_version, expected_source_version, expected_source_hash,
                    proposal, policy_ids, attempts, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        return UUID(str(row["id"]))

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
