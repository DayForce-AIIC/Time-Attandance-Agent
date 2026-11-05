"""Canonical hashing and privacy-minimal storage fields for source events."""

import hashlib

from app.integrations.schemas import AttendanceChangedEvent


def event_payload_hash(event: AttendanceChangedEvent) -> str:
    """Hash the validated JSON shape so webhook and cursor deliveries deduplicate."""
    canonical_payload = event.model_dump_json(exclude_none=True).encode("utf-8")
    return hashlib.sha256(canonical_payload).hexdigest()


def minimal_event_payload(event: AttendanceChangedEvent) -> dict[str, str]:
    """Keep event metadata needed for sync while leaving attendance evidence external."""
    payload = {
        "occurred_at": event.occurred_at.isoformat(),
        "record_type": event.record.record_type,
        "source_updated_at": event.record.updated_at.isoformat(),
    }
    if event.record.employee_name is not None:
        payload["employee_name"] = event.record.employee_name
    return payload
