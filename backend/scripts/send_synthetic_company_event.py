"""Send a signed synthetic event to a running ShiftGuard API and fetch its evidence."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from app.config import get_settings
from app.integrations.factory import make_attendance_provider
from app.integrations.routes import make_webhook_signature


def main() -> None:
    settings = get_settings()
    if settings.auth_mode != "supabase":
        raise SystemExit(
            "Set AUTH_MODE=supabase and configure the Supabase database before sending events."
        )
    if (
        settings.attendance_integration_id is None
        or settings.integration_webhook_secret is None
        or not settings.integration_webhook_secret.get_secret_value().strip()
    ):
        raise SystemExit("Set ATTENDANCE_INTEGRATION_ID and INTEGRATION_WEBHOOK_SECRET first.")

    parser = argparse.ArgumentParser(
        description="Post one signed synthetic timecard-day event and fetch the pinned source record."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--event-id", default=f"demo-{uuid4()}")
    parser.add_argument("--record-ref", default="tcday_2026-11-03_8F39A")
    parser.add_argument("--employee-ref", default="emp_8F39A")
    parser.add_argument("--version", default="17")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    timestamp_header = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "event_id": args.event_id,
        "event_type": "attendance.record.changed",
        "occurred_at": timestamp_header,
        "record": {
            "employee_ref": args.employee_ref,
            "record_type": "timecard_day",
            "record_ref": args.record_ref,
            "version": args.version,
            "updated_at": timestamp_header,
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = make_webhook_signature(
        raw_body,
        timestamp_header,
        settings.integration_webhook_secret.get_secret_value(),
    )
    response = httpx.post(
        f"{args.url.rstrip('/')}/integrations/v1/events/attendance-changed",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Company-Event-Id": args.event_id,
            "X-Company-Timestamp": timestamp_header,
            "X-Company-Signature": signature,
        },
        timeout=15,
    )
    response.raise_for_status()
    print(json.dumps({"accepted_event": response.json()}, indent=2))

    provider = make_attendance_provider(settings)
    try:
        record = provider.fetch_attendance_record(args.record_ref, version=args.version)
        if record is None:
            raise SystemExit(
                "The referenced record or source version was not found by the provider."
            )
        print(json.dumps({"fetched_evidence": record.model_dump(mode="json")}, indent=2))
    finally:
        provider.close()


if __name__ == "__main__":
    main()
