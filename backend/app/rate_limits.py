"""Per-process sliding-window guards for high-cost and externally exposed routes."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.config import Settings


class SlidingWindowRateLimiter:
    """Bound request bursts per route and client identity within one API process."""

    def __init__(self, *, window_seconds: int, max_keys: int = 4096) -> None:
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
        """Return whether the request is allowed and the retry delay if limited."""
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            if len(self._requests) >= self.max_keys and key not in self._requests:
                for old_key in list(self._requests):
                    requests = self._requests[old_key]
                    while requests and requests[0] <= cutoff:
                        requests.popleft()
                    if not requests:
                        del self._requests[old_key]
                if len(self._requests) >= self.max_keys and key not in self._requests:
                    group = key.split(":", 1)[0]
                    key = f"{group}:rate-limit-overflow"

            requests = self._requests[key]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= limit:
                retry_after = max(1, math.ceil(self.window_seconds - (current - requests[0])))
                return False, retry_after
            requests.append(current)
            return True, 0


def route_rate_limit(request: Request, settings: Settings) -> tuple[str, int] | None:
    """Select limits only for calls that trigger costly or state-changing work."""
    if request.method != "POST":
        return None
    path = request.url.path
    if path == "/api/v1/assistant/query":
        return "assistant", settings.assistant_rate_limit_per_window
    if path == "/integrations/v1/events/attendance-changed":
        return "webhook", settings.webhook_rate_limit_per_window
    if path.endswith("/decision") or path.endswith("/retry-writeback"):
        return "writeback", settings.writeback_rate_limit_per_window
    return None


def client_rate_limit_key(request: Request, group: str) -> str:
    """Use a credential digest for signed-in calls and the socket peer otherwise."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        identity = hashlib.sha256(authorization[7:].encode("utf-8")).hexdigest()[:24]
    else:
        identity = request.client.host if request.client is not None else "unknown"
    return f"{group}:{identity}"
