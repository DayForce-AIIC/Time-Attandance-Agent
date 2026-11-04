"""Business clock abstraction used to make the demo deterministic."""

from datetime import datetime

from fastapi import Request

from app.config import Settings, get_settings


class Clock:
    """Return the configured demo time or the current naive local time."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def now(self) -> datetime:
        """Get the business time as a timezone-naive datetime."""
        return self._settings.demo_now or datetime.now()


def get_clock(request: Request) -> Clock:
    """FastAPI dependency that uses the settings owned by the app factory."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    return Clock(settings)
