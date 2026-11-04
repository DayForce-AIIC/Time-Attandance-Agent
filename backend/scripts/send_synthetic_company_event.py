"""Queue synthetic attendance events for all implemented policy rules."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from app.config import get_settings
from app.integrations.factory import make_attendance_provider
from app.integrations.routes import make_webhook_signature
from app.integrations.synthetic_scenarios import (
    SyntheticEventScenario,
    synthetic_scenario_records,
)
from app.policies import FROZEN_RULE_IDS

DEFAULT_RECORD_REF = "tcday_2026-11-03_8F39A"
DEFAULT_EMPLOYEE_REF = "emp_8F39A"
DEFAULT_VERSION = "17"


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
        description=(
            "Queue synthetic attendance events for every implemented attendance rule. "
            "Keep the analysis worker running while the queued events are processed."
        )
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--event-id",
        help="Optional event ID for --single, or a batch ID prefix for the full scenario set.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=FROZEN_RULE_IDS,
        help="Send only this policy scenario; may be repeated. The default sends all 15.",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Send one source record instead of the full set of synthetic policy scenarios.",
    )
    parser.add_argument("--record-ref", help="Source timecard reference for --single.")
    parser.add_argument("--employee-ref", help="Employee reference for --single.")
    parser.add_argument("--version", help="Source version for --single.")
    args = parser.parse_args()

    if args.single and args.scenario:
        parser.error("--single and --scenario cannot be used together")
    if not args.single and any((args.record_ref, args.employee_ref, args.version)):
        parser.error("--record-ref, --employee-ref, and --version require --single")
    if not args.single and settings.attendance_provider != "mock":
        raise SystemExit("The all-rules batch is available only with ATTENDANCE_PROVIDER=mock.")

    provider = make_attendance_provider(settings)
    try:
        if args.single:
            scenario = SyntheticEventScenario(
                rule_id="CUSTOM",
                employee_ref=args.employee_ref or DEFAULT_EMPLOYEE_REF,
                record_ref=args.record_ref or DEFAULT_RECORD_REF,
                version=args.version or DEFAULT_VERSION,
            )
            scenarios = [scenario]
        else:
            _, all_scenarios = synthetic_scenario_records()
            selected_rules = set(args.scenario or FROZEN_RULE_IDS)
            scenarios = [
                scenario for scenario in all_scenarios if scenario.rule_id in selected_rules
            ]
            if selected_rules != {scenario.rule_id for scenario in scenarios}:
                raise SystemExit("One or more requested policy scenarios are unavailable.")

        # Check the whole batch before posting anything so a missing mock fixture
        # cannot leave the user with a partially queued set of scenarios.
        source_records = {}
        for scenario in scenarios:
            record = provider.fetch_attendance_record(scenario.record_ref, version=scenario.version)
            if record is None or record.employee_ref != scenario.employee_ref:
                raise SystemExit(
                    f"Synthetic source record is unavailable for {scenario.rule_id}: "
                    f"{scenario.record_ref}"
                )
            source_records[scenario.rule_id] = record

        if args.single:
            event_prefix = args.event_id or f"demo-{uuid4()}"
        else:
            event_prefix = args.event_id or f"demo-{uuid4().hex[:16]}"
            if len(event_prefix) > 150:
                parser.error("batch --event-id prefix must be 150 characters or fewer")

        accepted_events = []
        for scenario in scenarios:
            event_id = event_prefix if args.single else f"{event_prefix}-{scenario.rule_id.lower()}"
            accepted = _send_event(
                base_url=args.url,
                event_id=event_id,
                scenario=scenario,
                webhook_secret=settings.integration_webhook_secret.get_secret_value(),
            )
            record = source_records[scenario.rule_id]
            accepted_events.append(
                {
                    "policy_rule": scenario.rule_id,
                    "event": accepted,
                    "source_record": {
                        "record_ref": record.record_ref,
                        "employee_ref": record.employee_ref,
                        "version": record.version,
                        "work_date": record.work_date.isoformat(),
                        "punch_count": len(record.punches),
                    },
                }
            )

        print(
            json.dumps(
                {
                    "queued_count": len(accepted_events),
                    "queued_events": accepted_events,
                    "next_step": "Keep make worker running; analysis and case creation are asynchronous.",
                },
                indent=2,
            )
        )
    finally:
        provider.close()


def _send_event(
    *,
    base_url: str,
    event_id: str,
    scenario: SyntheticEventScenario,
    webhook_secret: str,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    timestamp_header = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "event_id": event_id,
        "event_type": "attendance.record.changed",
        "occurred_at": timestamp_header,
        "record": {
            "employee_ref": scenario.employee_ref,
            "record_type": "timecard_day",
            "record_ref": scenario.record_ref,
            "version": scenario.version,
            "updated_at": timestamp_header,
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = make_webhook_signature(raw_body, timestamp_header, webhook_secret)
    response = httpx.post(
        f"{base_url.rstrip('/')}/integrations/v1/events/attendance-changed",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Company-Event-Id": event_id,
            "X-Company-Timestamp": timestamp_header,
            "X-Company-Signature": signature,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    main()
