"""Fetch version-pinned synthetic/company evidence for a previously accepted event."""

from __future__ import annotations

import argparse
import json

from app.config import get_settings
from app.integrations.factory import make_attendance_provider
from app.workflow_repository import PostgresWorkflowRepository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the referenced attendance evidence for a persisted company event."
    )
    parser.add_argument("event_id", help="The X-Company-Event-Id accepted by ShiftGuard")
    args = parser.parse_args()

    settings = get_settings()
    if settings.auth_organization_id is None or settings.attendance_integration_id is None:
        raise SystemExit(
            "Set AUTH_ORGANIZATION_ID and ATTENDANCE_INTEGRATION_ID in the backend environment."
        )

    provider = make_attendance_provider(settings)
    workflow_repo: PostgresWorkflowRepository | None = None
    try:
        workflow_repo = PostgresWorkflowRepository.from_settings(settings)
        event = workflow_repo.get_inbound_event(
            settings.attendance_integration_id,
            settings.auth_organization_id,
            args.event_id,
        )
        if event is None:
            raise SystemExit(
                "No matching inbound event exists for this integration and organization."
            )
        if event["event_type"] == "attendance.record.deleted":
            raise SystemExit(
                "The source event marks this record as deleted; there is no evidence to fetch."
            )
        references = event["source_record_refs"]
        if not isinstance(references, list) or len(references) != 1:
            raise SystemExit(
                "The inbound event does not contain one supported timecard-day reference."
            )
        record = provider.fetch_attendance_record(
            str(references[0]),
            version=str(event["source_version"]) if event["source_version"] is not None else None,
        )
        if record is None:
            raise SystemExit("The referenced source record or version is unavailable.")
        print(json.dumps(record.model_dump(mode="json"), indent=2))
    finally:
        provider.close()
        if workflow_repo is not None:
            workflow_repo.close()


if __name__ == "__main__":
    main()
