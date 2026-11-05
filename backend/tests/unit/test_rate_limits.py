"""Coverage for the routes protected by process-local rate limits."""

from app.config import Settings
from app.rate_limits import SlidingWindowRateLimiter, route_rate_limit
from fastapi import Request


def _request(method: str, path: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("198.51.100.20", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_all_expensive_and_state_changing_routes_have_limits() -> None:
    settings = Settings(_env_file=None)
    cases = [
        ("/api/v1/assistant/query", "assistant", settings.assistant_rate_limit_per_window),
        (
            "/api/v1/assistant/query/stream",
            "assistant",
            settings.assistant_rate_limit_per_window,
        ),
        (
            "/api/v1/exceptions/123/propose",
            "assistant",
            settings.assistant_rate_limit_per_window,
        ),
        (
            "/integrations/v1/events/attendance-changed",
            "webhook",
            settings.webhook_rate_limit_per_window,
        ),
        (
            "/api/v1/exceptions/123/decision",
            "writeback",
            settings.writeback_rate_limit_per_window,
        ),
        (
            "/api/v1/decisions/00000000-0000-0000-0000-000000000001/retry-writeback",
            "writeback",
            settings.writeback_rate_limit_per_window,
        ),
    ]

    for path, expected_group, expected_limit in cases:
        assert route_rate_limit(_request("POST", path), settings) == (
            expected_group,
            expected_limit,
        )


def test_read_and_unlisted_routes_are_not_limited() -> None:
    settings = Settings(_env_file=None)

    assert route_rate_limit(_request("GET", "/api/v1/exceptions"), settings) is None
    assert route_rate_limit(_request("POST", "/api/v1/metrics"), settings) is None


def test_sliding_window_returns_retry_delay_and_releases_expired_capacity() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=10)

    assert limiter.allow("assistant:client", 1, now=0) == (True, 0)
    assert limiter.allow("assistant:client", 1, now=2) == (False, 8)
    assert limiter.allow("assistant:client", 1, now=10) == (True, 0)
