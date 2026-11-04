"""Safe operational alert logging with an optional generic JSON webhook sink."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("shiftguard.operations")


class OperationalAlertReporter:
    """Count repeated upstream failures and emit rate-limited safe alerts."""

    def __init__(self, settings: Settings) -> None:
        self._webhook_url = (
            settings.ops_alert_webhook_url.get_secret_value()
            if settings.ops_alert_webhook_url is not None
            else None
        )
        self._timeout = settings.ops_alert_timeout_seconds
        self._threshold = settings.ops_alert_failure_threshold
        self._window_seconds = settings.ops_alert_window_seconds
        self._cooldown_seconds = settings.ops_alert_cooldown_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._last_alert: dict[str, float] = {}
        self._lock = threading.Lock()

    def report_success(self, component: str) -> None:
        """Clear a component's active failure window after a successful operation."""
        with self._lock:
            failures = self._failures.pop(component, None)
            previous = len(failures) if failures is not None else 0
            had_alert = self._last_alert.pop(component, None) is not None
        if had_alert:
            logger.info(
                "upstream component recovered",
                extra={"component": component, "failures_before_recovery": previous},
            )

    def report_failure(
        self,
        component: str,
        error_code: str,
        *,
        trace_id: str | None = None,
        event_id: str | None = None,
        job_id: str | None = None,
        case_id: str | None = None,
        decision_id: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        """Log a safe failure and alert after repeated failures in the time window."""
        now = time.monotonic()
        with self._lock:
            failures = self._failures[component]
            while failures and now - failures[0] > self._window_seconds:
                failures.popleft()
            failures.append(now)
            count = len(failures)
            last_alert = self._last_alert.get(component)
            should_alert = count >= self._threshold and (
                last_alert is None or now - last_alert >= self._cooldown_seconds
            )
            if should_alert:
                self._last_alert[component] = now

        fields = {
            "component": component,
            "error_code": error_code,
            "failure_count": count,
            "failure_window_seconds": self._window_seconds,
            "trace_id": trace_id,
            "integration_event_id": event_id,
            "analysis_job_id": job_id,
            "exception_case_id": case_id,
            "decision_id": decision_id,
            "external_operation_id": operation_id,
        }
        logger.warning("upstream failure recorded", extra=fields)
        if not should_alert:
            return

        payload: dict[str, Any] = {
            "service": "shiftguard",
            "severity": "error",
            "component": component,
            "error_code": error_code,
            "occurrences": count,
            "window_seconds": self._window_seconds,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "integration_event_id": event_id,
            "analysis_job_id": job_id,
            "exception_case_id": case_id,
            "decision_id": decision_id,
            "external_operation_id": operation_id,
        }
        logger.error("operational alert threshold reached", extra=fields)
        if not self._webhook_url:
            return
        try:
            response = httpx.post(
                self._webhook_url,
                json=payload,
                timeout=self._timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(
                "operational alert delivery failed",
                extra={
                    "component": component,
                    "error_code": "alert_sink_unavailable",
                    "http_error_type": type(exc).__name__,
                },
            )
