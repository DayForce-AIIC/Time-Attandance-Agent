"""Signed company attendance event intake."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from psycopg import Error as PostgresError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.deps import get_settings
from app.integrations.event_store import event_payload_hash, minimal_event_payload
from app.integrations.schemas import AttendanceChangedEvent, IntegrationEventAck
from app.logging_setup import trace_id_var
from app.workflow_repository import PostgresWorkflowRepository

router = APIRouter(prefix="/integrations/v1", tags=["company integration"])
logger = logging.getLogger(__name__)


def _event_request_schema() -> dict[str, object]:
    """Return an inline JSON schema for the raw-body-signed webhook contract."""
    schema = AttendanceChangedEvent.model_json_schema()
    definitions = schema.pop("$defs", {})
    record_schema = definitions.get("AttendanceSourceReference")
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(record_schema, dict):
        properties["record"] = record_schema
    return schema


@router.post(
    "/events/attendance-changed",
    response_model=IntegrationEventAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a signed attendance change event",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _event_request_schema()}},
        }
    },
    responses={
        401: {"description": "Missing, expired, or invalid webhook signature"},
        409: {"description": "Event ID was reused with a different payload"},
        413: {"description": "Webhook body exceeds the configured limit"},
        429: {"description": "Webhook rate limit exceeded; retry after the indicated delay"},
        503: {"description": "Integration intake is not configured or temporarily unavailable"},
    },
)
async def receive_attendance_event(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    x_company_event_id: str | None = Header(default=None),
    x_company_timestamp: str | None = Header(default=None),
    x_company_signature: str | None = Header(default=None),
) -> IntegrationEventAck:
    """Authenticate, validate, and idempotently record a minimal webhook envelope."""
    if settings.auth_mode != "supabase":
        raise HTTPException(
            status_code=503,
            detail="Company event intake requires Supabase workflow storage to be configured",
        )
    organization_id = settings.auth_organization_id
    integration_id = settings.attendance_integration_id
    secret = settings.integration_webhook_secret
    if (
        organization_id is None
        or integration_id is None
        or secret is None
        or not secret.get_secret_value().strip()
    ):
        raise HTTPException(status_code=503, detail="Company event intake is not configured")

    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit():
        if int(content_length) > settings.integration_webhook_max_body_bytes:
            raise HTTPException(status_code=413, detail="Webhook body is too large")
    body_chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > settings.integration_webhook_max_body_bytes:
            raise HTTPException(status_code=413, detail="Webhook body is too large")
        body_chunks.append(chunk)
    raw_body = b"".join(body_chunks)

    _verify_webhook_signature(
        raw_body,
        x_company_timestamp,
        x_company_signature,
        secret.get_secret_value(),
        settings.integration_webhook_max_skew_seconds,
    )
    if not x_company_event_id or len(x_company_event_id) > 200:
        raise HTTPException(status_code=400, detail="X-Company-Event-Id is required")
    try:
        event = AttendanceChangedEvent.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid attendance event payload") from exc
    if event.event_id != x_company_event_id:
        raise HTTPException(status_code=400, detail="Event ID header does not match the payload")

    try:
        payload_hash = event_payload_hash(event)
        minimal_payload = minimal_event_payload(event)
        created = await run_in_threadpool(
            _persist_inbound_event,
            settings,
            organization_id=organization_id,
            integration_id=integration_id,
            event=event,
            payload_hash=payload_hash,
            minimal_payload=minimal_payload,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Event ID was already received with a different payload",
        ) from exc
    except PostgresError as exc:
        await run_in_threadpool(
            request.app.state.operational_alerts.report_failure,
            "integration_storage",
            "workflow_database_unavailable",
            trace_id=trace_id_var.get(),
            event_id=x_company_event_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Company event storage is temporarily unavailable",
        ) from exc
    request.app.state.operational_alerts.report_success("integration_storage")
    response.headers["X-Company-Event-Status"] = "accepted"
    logger.info(
        "attendance event accepted",
        extra={
            "integration_event_id": event.event_id,
            "integration_id": str(integration_id),
            "duplicate": not created,
        },
    )
    return IntegrationEventAck(event_id=event.event_id, status="accepted", duplicate=not created)


def _persist_inbound_event(
    settings: Settings,
    *,
    organization_id: UUID,
    integration_id: UUID,
    event: AttendanceChangedEvent,
    payload_hash: str,
    minimal_payload: dict[str, str],
) -> bool:
    workflow_repo: PostgresWorkflowRepository | None = None
    try:
        workflow_repo = PostgresWorkflowRepository.from_settings(settings)
        integration = workflow_repo.get_integration(integration_id, organization_id)
        if integration is None or integration["status"] != "active":
            raise HTTPException(
                status_code=503, detail="Configured attendance integration is inactive"
            )
        _, created, _ = workflow_repo.record_event_and_enqueue_detection(
            organization_id=organization_id,
            integration_id=integration_id,
            external_event_id=event.event_id,
            event_type=event.event_type,
            payload_hash=payload_hash,
            external_employee_ref=event.record.employee_ref,
            source_record_refs=[event.record.record_ref],
            source_version=event.record.version,
            minimal_payload=minimal_payload,
        )
        return created
    finally:
        if workflow_repo is not None:
            workflow_repo.close()


def _verify_webhook_signature(
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: str,
    max_skew_seconds: int,
) -> None:
    if not timestamp_header or not signature_header:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if len(timestamp_header) > 80 or len(signature_header) > 80:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        timestamp = datetime.fromisoformat(timestamp_header.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if (
        abs((datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds())
        > max_skew_seconds
    ):
        raise HTTPException(
            status_code=401, detail="Webhook timestamp is outside the accepted window"
        )

    signed_payload = timestamp_header.encode("utf-8") + b"." + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def make_webhook_signature(raw_body: bytes, timestamp_header: str, secret: str) -> str:
    """Sign a webhook using the draft contract's timestamp-dot-raw-body scheme."""
    signed_payload = timestamp_header.encode("utf-8") + b"." + raw_body
    return "sha256=" + hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
