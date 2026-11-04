"""Attendance provider interface and provider-specific error types."""

from datetime import date, datetime
from typing import Protocol

from app.integrations.schemas import (
    AttendanceChangePage,
    AttendanceRangeEvidence,
    AttendanceRecord,
    CompanyCorrectionRequest,
    CorrectionResult,
)


class AttendanceProviderError(RuntimeError):
    """Base error for unavailable or invalid company attendance API operations."""


class AttendanceProviderUnavailable(AttendanceProviderError):
    """A temporary network or upstream service failure that can be retried."""


class AttendanceRecordNotFound(AttendanceProviderError):
    """The referenced source record no longer exists."""


class AttendanceSourceChanged(AttendanceProviderError):
    """The company rejected an operation because its source version changed."""


class AttendanceProvider(Protocol):
    """Operations required to read evidence and submit an approved correction."""

    def fetch_attendance_record(
        self, record_ref: str, *, version: str | None = None
    ) -> AttendanceRecord | None: ...

    def fetch_changed_events(self, cursor: str | None) -> AttendanceChangePage: ...

    def fetch_attendance_range(
        self, employee_ref: str, start_date: date, end_date: date
    ) -> AttendanceRangeEvidence: ...

    def submit_correction(
        self,
        request: CompanyCorrectionRequest,
        *,
        idempotency_key: str,
    ) -> CorrectionResult: ...

    def close(self) -> None: ...


def normalized_now() -> datetime:
    """Return an aware timestamp for mock-provider results."""
    from datetime import timezone

    return datetime.now(timezone.utc)
