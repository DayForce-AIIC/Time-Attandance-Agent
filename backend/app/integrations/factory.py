"""Build a configured company attendance provider."""

from urllib.parse import urlparse

from app.config import Settings
from app.integrations.http_provider import HttpAttendanceProvider
from app.integrations.mock_provider import MockAttendanceProvider
from app.integrations.provider import AttendanceProvider


def make_attendance_provider(settings: Settings) -> AttendanceProvider:
    """Create the selected mock or HTTP provider using server-only credentials."""
    if settings.attendance_provider == "mock":
        return MockAttendanceProvider()

    base_url = (settings.attendance_api_base_url or "").strip().rstrip("/")
    token = settings.attendance_api_token
    if not base_url or token is None or not token.get_secret_value().strip():
        raise ValueError(
            "ATTENDANCE_PROVIDER=http requires ATTENDANCE_API_BASE_URL and ATTENDANCE_API_TOKEN"
        )
    parsed = urlparse(base_url)
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ATTENDANCE_API_BASE_URL must be a plain HTTP(S) base URL")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        raise ValueError("ATTENDANCE_API_BASE_URL must use HTTPS outside localhost")
    return HttpAttendanceProvider(
        base_url,
        token.get_secret_value(),
        settings.attendance_api_timeout_seconds,
    )
