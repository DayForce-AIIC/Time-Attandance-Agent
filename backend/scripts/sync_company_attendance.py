"""Recover missed webhook events by polling the configured cursor-based change feed."""

from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.integrations.event_store import event_payload_hash, minimal_event_payload
from app.integrations.factory import make_attendance_provider
from app.logging_setup import configure_logging
from app.workflow_repository import PostgresWorkflowRepository

CURSOR_NAME = "attendance_events"
logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Poll attendance changes and persist each event before advancing the cursor."
    )
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_pages > 1000:
        raise SystemExit("--max-pages must be between 1 and 1000")

    settings = get_settings()
    organization_id = settings.auth_organization_id
    integration_id = settings.attendance_integration_id
    if settings.auth_mode != "supabase" or organization_id is None or integration_id is None:
        raise SystemExit(
            "Set AUTH_MODE=supabase, AUTH_ORGANIZATION_ID, and ATTENDANCE_INTEGRATION_ID first."
        )

    provider = make_attendance_provider(settings)
    workflow_repo: PostgresWorkflowRepository | None = None
    new_events = 0
    duplicate_events = 0
    pages_read = 0
    cursor = None
    try:
        workflow_repo = PostgresWorkflowRepository.from_settings(settings)
        integration = workflow_repo.get_integration(integration_id, organization_id)
        if integration is None or integration["status"] != "active":
            raise SystemExit("The configured attendance integration is missing or inactive.")
        cursor = workflow_repo.get_sync_cursor(integration_id, CURSOR_NAME)

        for pages_read in range(1, args.max_pages + 1):
            page = provider.fetch_changed_events(cursor)
            for event in page.events:
                event_row_id, created, job_id = workflow_repo.record_event_and_enqueue_detection(
                    organization_id=organization_id,
                    integration_id=integration_id,
                    external_event_id=event.event_id,
                    event_type=event.event_type,
                    payload_hash=event_payload_hash(event),
                    external_employee_ref=event.record.employee_ref,
                    source_record_refs=[event.record.record_ref],
                    source_version=event.record.version,
                    minimal_payload=minimal_event_payload(event),
                )
                if created:
                    new_events += 1
                    logger.info(
                        "attendance change synchronized",
                        extra={
                            "integration_event_id": str(event_row_id),
                            "analysis_job_id": str(job_id),
                            "external_event_id": event.event_id,
                            "integration_id": str(integration_id),
                        },
                    )
                else:
                    duplicate_events += 1

            if page.events and (page.next_cursor is None or page.next_cursor == cursor):
                raise SystemExit("The company change feed did not advance its cursor.")
            if page.next_cursor is not None and page.next_cursor != cursor:
                workflow_repo.set_sync_cursor(integration_id, CURSOR_NAME, page.next_cursor)
                cursor = page.next_cursor
            if not page.has_more:
                break
        else:
            raise SystemExit(
                f"Stopped after {args.max_pages} pages; run the sync again to continue."
            )

        print(
            f"Cursor sync complete: {new_events} new events, {duplicate_events} duplicates, "
            f"{pages_read} page(s) read."
        )
        logger.info(
            "attendance cursor sync completed",
            extra={
                "new_events": new_events,
                "duplicate_events": duplicate_events,
                "pages_read": pages_read,
                "integration_id": str(integration_id),
            },
        )
    finally:
        provider.close()
        if workflow_repo is not None:
            workflow_repo.close()


if __name__ == "__main__":
    main()
