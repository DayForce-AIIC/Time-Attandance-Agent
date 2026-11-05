"""HTTP implementation of the vendor-neutral company attendance contract."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

import httpx

from app.integrations.provider import (
    AttendanceProviderError,
    AttendanceProviderUnavailable,
    AttendanceSourceChanged,
)
from app.integrations.schemas import (
    AttendanceChangePage,
    AttendanceRangeEvidence,
    AttendanceRecord,
    CompanyCorrectionRequest,
    CorrectionResult,
)


class HttpAttendanceProvider:
    """Call the company's configured versioned REST API with bounded timeouts."""

    def __init__(self, base_url: str, access_token: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds

    def fetch_attendance_record(
        self, record_ref: str, *, version: str | None = None
    ) -> AttendanceRecord | None:
        try:
            response = self._request(
                "GET",
                f"/v1/attendance/records/{quote(record_ref, safe='')}",
                params={
                    "version": version,
                    "include": "employee_name,punches,schedule,leave",
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            if exc.response.status_code == 409:
                raise AttendanceSourceChanged("company source version changed") from exc
            raise _status_error("company attendance API", exc)
        try:
            record = AttendanceRecord.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise AttendanceProviderError(
                "company attendance API returned invalid evidence"
            ) from exc
        if record.record_ref != record_ref:
            raise AttendanceProviderError("company attendance API returned a different record")
        if version is not None and record.version != version:
            raise AttendanceSourceChanged(
                "company attendance API returned a different source version"
            )
        return record

    def fetch_changed_events(self, cursor: str | None) -> AttendanceChangePage:
        try:
            response = self._request(
                "GET",
                "/v1/attendance/events",
                params={"cursor": cursor, "limit": "100"},
            )
        except httpx.HTTPStatusError as exc:
            raise _status_error("company change feed", exc)
        try:
            return AttendanceChangePage.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise AttendanceProviderError("company change feed returned an invalid page") from exc

    def fetch_attendance_range(
        self, employee_ref: str, start_date: date, end_date: date
    ) -> AttendanceRangeEvidence:
        try:
            response = self._request(
                "GET",
                f"/v1/attendance/employees/{quote(employee_ref, safe='')}/timecards",
                params={
                    "from": start_date.isoformat(),
                    "through": end_date.isoformat(),
                    "include": "employee_name,punches,schedule,leave",
                },
            )
        except httpx.HTTPStatusError as exc:
            raise _status_error("company attendance range API", exc)
        try:
            result = AttendanceRangeEvidence.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise AttendanceProviderError("company attendance range response is invalid") from exc
        if result.employee_ref != employee_ref:
            raise AttendanceProviderError("company attendance range returned another employee")
        if result.covered_from != start_date or result.covered_through != end_date:
            raise AttendanceProviderError("company attendance range does not match the request")
        return result

    def submit_correction(
        self,
        request: CompanyCorrectionRequest,
        *,
        idempotency_key: str,
    ) -> CorrectionResult:
        if not idempotency_key or len(idempotency_key) > 200:
            raise AttendanceProviderError("invalid correction idempotency key")
        try:
            response = self._request(
                "POST",
                "/v1/attendance/corrections",
                json_body=request.model_dump(mode="json"),
                headers={
                    "Idempotency-Key": idempotency_key,
                    "If-Match": f'"{request.expected_version}"',
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                raise AttendanceSourceChanged("company source version changed") from exc
            raise _status_error("company correction API", exc)
        try:
            result = CorrectionResult.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise AttendanceProviderError(
                "company correction API returned an invalid result"
            ) from exc
        if result.record_ref != request.record_ref:
            raise AttendanceProviderError("company correction API returned a different record")
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | None] | None = None,
        json_body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if headers is not None:
            request_headers.update(headers)
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=request_headers,
                params=params,
                json=json_body,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise AttendanceProviderUnavailable("company attendance API request timed out") from exc
        except httpx.RequestError as exc:
            raise AttendanceProviderUnavailable("company attendance API is unreachable") from exc
        response.raise_for_status()
        return response

    def close(self) -> None:
        """Requests use short-lived clients and hold no resources between calls."""


def _status_error(operation: str, error: httpx.HTTPStatusError) -> AttendanceProviderError:
    status_code = error.response.status_code
    message = f"{operation} returned HTTP {status_code}"
    if status_code in {408, 425, 429} or status_code >= 500:
        return AttendanceProviderUnavailable(message)
    return AttendanceProviderError(message)
