"""Tests for HttpAttendanceProvider.

No live vendor API is required. Every test uses unittest.mock.patch to
replace the module-level ``httpx.request`` call that ``HttpAttendanceProvider._request``
makes internally, so the tests run completely offline.

Coverage
--------
fetch_attendance_record
  - 200 OK → valid AttendanceRecord returned
  - 200 OK → Bearer token sent in Authorization header
  - 200 OK with wrong record_ref → AttendanceProviderError
  - 200 OK with mismatched version when version pinned → AttendanceSourceChanged
  - 200 OK with any version when version not pinned → accepted
  - 404 → returns None
  - 409 → AttendanceSourceChanged
  - 500 → AttendanceProviderUnavailable (retryable server error)
  - 429 → AttendanceProviderUnavailable (rate-limited)
  - 408 → AttendanceProviderUnavailable (request timeout status)
  - 400 → AttendanceProviderError (permanent client error, NOT retryable)
  - malformed JSON body → AttendanceProviderError
  - httpx.TimeoutException → AttendanceProviderUnavailable
  - httpx.RequestError (connection refused, etc.) → AttendanceProviderUnavailable

fetch_changed_events
  - 200 OK with events → AttendanceChangePage returned with correct content
  - 200 OK with empty events list → empty page returned
  - 500 → AttendanceProviderUnavailable
  - malformed JSON body → AttendanceProviderError

fetch_attendance_range
  - 200 OK → valid AttendanceRangeEvidence returned
  - wrong employee_ref in response → AttendanceProviderError
  - date range mismatch → AttendanceProviderError
  - 500 → AttendanceProviderUnavailable
  - malformed JSON body → AttendanceProviderError

submit_correction
  - 200 OK → CorrectionResult returned
  - 200 OK → Idempotency-Key header sent
  - 200 OK → If-Match header sent with expected version
  - 409 → AttendanceSourceChanged
  - 500 → AttendanceProviderUnavailable
  - wrong record_ref in result → AttendanceProviderError
  - empty idempotency_key → AttendanceProviderError (pre-flight, no HTTP call)
  - idempotency_key > 200 chars → AttendanceProviderError (pre-flight, no HTTP call)
  - malformed JSON body → AttendanceProviderError

factory / configuration
  - attendance_provider="mock" → MockAttendanceProvider
  - attendance_provider="http" without base_url → ValueError
  - attendance_provider="http" without token → ValueError
  - attendance_provider="http" with http:// non-localhost → ValueError (must use HTTPS)
  - attendance_provider="http" with https:// valid URL → HttpAttendanceProvider
  - attendance_provider="http" with http://localhost → HttpAttendanceProvider (localhost exempt)
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.integrations.http_provider import HttpAttendanceProvider
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

# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------

BASE_URL = "https://attendance.example.com"
TOKEN = "test-bearer-token"
NOW = datetime(2025, 3, 13, 20, 0, 0, tzinfo=timezone.utc)
WORK_DATE = date(2025, 3, 13)
RECORD_REF = "tcday_2025-03-13_EMP001"
EMPLOYEE_REF = "EMP-001"
VERSION = "v3"

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _make_provider() -> HttpAttendanceProvider:
    return HttpAttendanceProvider(BASE_URL, TOKEN, timeout_seconds=5.0)


def _ok_response(data: Any) -> MagicMock:
    """Return a mock httpx.Response with status 200 and the given JSON payload."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status.return_value = None  # no-op — 200 is fine
    return resp


def _error_response(status_code: int) -> MagicMock:
    """Return a mock httpx.Response that raises HTTPStatusError on raise_for_status."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    request_obj = MagicMock(spec=httpx.Request)
    exc = httpx.HTTPStatusError("error", request=request_obj, response=resp)
    resp.raise_for_status.side_effect = exc
    return resp


def _malformed_response() -> MagicMock:
    """Return a mock httpx.Response whose .json() raises ValueError (bad JSON)."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.side_effect = ValueError("bad json")
    resp.raise_for_status.return_value = None
    return resp


@contextmanager
def _mock_request(response: MagicMock):
    """Patch ``httpx.request`` (the module-level function used by _request()) to return *response*."""
    with patch("httpx.request", return_value=response) as mock:
        yield mock


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def _record_payload(
    *,
    record_ref: str = RECORD_REF,
    employee_ref: str = EMPLOYEE_REF,
    version: str = VERSION,
) -> dict:
    return {
        "record_ref": record_ref,
        "employee_ref": employee_ref,
        "version": version,
        "updated_at": NOW.isoformat(),
        "work_date": WORK_DATE.isoformat(),
        "timezone": "America/Los_Angeles",
        "jurisdiction": "CA",
        "ot_eligible": True,
        "punches": [],
        "leave": [],
        "schedule": None,
    }


def _range_payload(
    *,
    employee_ref: str = EMPLOYEE_REF,
    covered_from: date = WORK_DATE,
    covered_through: date = WORK_DATE,
    records: list | None = None,
) -> dict:
    return {
        "employee_ref": employee_ref,
        "covered_from": covered_from.isoformat(),
        "covered_through": covered_through.isoformat(),
        "coverage_complete": True,
        "records": records if records is not None else [_record_payload()],
    }


def _events_payload(*, has_more: bool = False, next_cursor: str | None = None) -> dict:
    return {
        "events": [
            {
                "event_id": "evt-001",
                "event_type": "attendance.record.changed",
                "occurred_at": NOW.isoformat(),
                "record": {
                    "employee_ref": EMPLOYEE_REF,
                    "record_type": "timecard_day",
                    "record_ref": RECORD_REF,
                    "version": VERSION,
                    "updated_at": NOW.isoformat(),
                },
            }
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def _correction_result_payload(*, record_ref: str = RECORD_REF) -> dict:
    return {
        "operation_id": "op-001",
        "status": "applied",
        "record_ref": record_ref,
        "new_version": "v4",
        "applied_at": NOW.isoformat(),
    }


def _make_correction_request() -> CompanyCorrectionRequest:
    return CompanyCorrectionRequest(
        decision_ref="dec-001",
        record_ref=RECORD_REF,
        employee_ref=EMPLOYEE_REF,
        expected_version=VERSION,
        correction={"action": "approve"},
        approved_by="HR-Manager",
        approved_at=NOW,
    )


# ===========================================================================
# fetch_attendance_record
# ===========================================================================


def test_fetch_record_200_returns_valid_record():
    p = _make_provider()
    with _mock_request(_ok_response(_record_payload())):
        record = p.fetch_attendance_record(RECORD_REF)
    assert isinstance(record, AttendanceRecord)
    assert record.record_ref == RECORD_REF
    assert record.employee_ref == EMPLOYEE_REF
    assert record.version == VERSION


def test_fetch_record_200_sends_bearer_token():
    p = _make_provider()
    with _mock_request(_ok_response(_record_payload())) as mock:
        p.fetch_attendance_record(RECORD_REF)
    _, kwargs = mock.call_args
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_fetch_record_404_returns_none():
    p = _make_provider()
    with _mock_request(_error_response(404)):
        result = p.fetch_attendance_record(RECORD_REF)
    assert result is None


def test_fetch_record_409_raises_source_changed():
    p = _make_provider()
    with _mock_request(_error_response(409)):
        with pytest.raises(AttendanceSourceChanged):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_500_raises_provider_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(500)):
        with pytest.raises(AttendanceProviderUnavailable):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_429_raises_provider_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(429)):
        with pytest.raises(AttendanceProviderUnavailable):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_408_raises_provider_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(408)):
        with pytest.raises(AttendanceProviderUnavailable):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_400_raises_provider_error_not_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(400)):
        with pytest.raises(AttendanceProviderError) as exc_info:
            p.fetch_attendance_record(RECORD_REF)
    # 400 is a permanent client error — must NOT be the retryable subclass
    assert not isinstance(exc_info.value, AttendanceProviderUnavailable)


def test_fetch_record_wrong_record_ref_raises_provider_error():
    p = _make_provider()
    payload = _record_payload(record_ref="COMPLETELY-DIFFERENT-REF")
    with _mock_request(_ok_response(payload)):
        with pytest.raises(AttendanceProviderError, match="different record"):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_version_mismatch_raises_source_changed():
    p = _make_provider()
    payload = _record_payload(version="v99")
    with _mock_request(_ok_response(payload)):
        with pytest.raises(AttendanceSourceChanged):
            p.fetch_attendance_record(RECORD_REF, version="v1")


def test_fetch_record_no_version_pin_accepts_any_version():
    p = _make_provider()
    payload = _record_payload(version="v99")
    with _mock_request(_ok_response(payload)):
        record = p.fetch_attendance_record(RECORD_REF)  # no version kwarg
    assert record is not None
    assert record.version == "v99"


def test_fetch_record_malformed_json_raises_provider_error():
    p = _make_provider()
    with _mock_request(_malformed_response()):
        with pytest.raises(AttendanceProviderError):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_timeout_raises_provider_unavailable():
    p = _make_provider()
    with patch("httpx.request", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(AttendanceProviderUnavailable, match="timed out"):
            p.fetch_attendance_record(RECORD_REF)


def test_fetch_record_connection_error_raises_provider_unavailable():
    p = _make_provider()
    with patch("httpx.request", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(AttendanceProviderUnavailable, match="unreachable"):
            p.fetch_attendance_record(RECORD_REF)


# ===========================================================================
# fetch_changed_events
# ===========================================================================


def test_fetch_changed_events_returns_page_with_events():
    p = _make_provider()
    payload = _events_payload(has_more=True, next_cursor="cursor-abc")
    with _mock_request(_ok_response(payload)):
        page = p.fetch_changed_events(cursor=None)
    assert isinstance(page, AttendanceChangePage)
    assert len(page.events) == 1
    assert page.next_cursor == "cursor-abc"
    assert page.has_more is True


def test_fetch_changed_events_empty_page():
    p = _make_provider()
    payload = {"events": [], "next_cursor": None, "has_more": False}
    with _mock_request(_ok_response(payload)):
        page = p.fetch_changed_events(cursor="some-cursor")
    assert page.events == []
    assert page.has_more is False


def test_fetch_changed_events_500_raises_provider_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(500)):
        with pytest.raises(AttendanceProviderUnavailable):
            p.fetch_changed_events(cursor=None)


def test_fetch_changed_events_malformed_json_raises_provider_error():
    p = _make_provider()
    with _mock_request(_malformed_response()):
        with pytest.raises(AttendanceProviderError):
            p.fetch_changed_events(cursor=None)


# ===========================================================================
# fetch_attendance_range
# ===========================================================================


def test_fetch_range_200_returns_valid_evidence():
    p = _make_provider()
    with _mock_request(_ok_response(_range_payload())):
        evidence = p.fetch_attendance_range(EMPLOYEE_REF, WORK_DATE, WORK_DATE)
    assert isinstance(evidence, AttendanceRangeEvidence)
    assert evidence.employee_ref == EMPLOYEE_REF
    assert evidence.coverage_complete is True
    assert len(evidence.records) == 1


def test_fetch_range_wrong_employee_raises_provider_error():
    p = _make_provider()
    payload = _range_payload(employee_ref="WRONG-EMP")
    with _mock_request(_ok_response(payload)):
        with pytest.raises(AttendanceProviderError, match="another employee"):
            p.fetch_attendance_range(EMPLOYEE_REF, WORK_DATE, WORK_DATE)


def test_fetch_range_wrong_dates_raises_provider_error():
    p = _make_provider()
    other_date = date(2025, 3, 20)
    payload = _range_payload(covered_from=other_date, covered_through=other_date)
    with _mock_request(_ok_response(payload)):
        with pytest.raises(AttendanceProviderError, match="does not match"):
            p.fetch_attendance_range(EMPLOYEE_REF, WORK_DATE, WORK_DATE)


def test_fetch_range_500_raises_provider_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(500)):
        with pytest.raises(AttendanceProviderUnavailable):
            p.fetch_attendance_range(EMPLOYEE_REF, WORK_DATE, WORK_DATE)


def test_fetch_range_malformed_json_raises_provider_error():
    p = _make_provider()
    with _mock_request(_malformed_response()):
        with pytest.raises(AttendanceProviderError):
            p.fetch_attendance_range(EMPLOYEE_REF, WORK_DATE, WORK_DATE)


# ===========================================================================
# submit_correction
# ===========================================================================


def test_submit_correction_200_returns_result():
    p = _make_provider()
    with _mock_request(_ok_response(_correction_result_payload())):
        result = p.submit_correction(_make_correction_request(), idempotency_key="idem-key-001")
    assert isinstance(result, CorrectionResult)
    assert result.record_ref == RECORD_REF
    assert result.status == "applied"


def test_submit_correction_sends_idempotency_key_header():
    p = _make_provider()
    with _mock_request(_ok_response(_correction_result_payload())) as mock:
        p.submit_correction(_make_correction_request(), idempotency_key="my-key-999")
    _, kwargs = mock.call_args
    assert kwargs["headers"]["Idempotency-Key"] == "my-key-999"


def test_submit_correction_sends_if_match_header():
    p = _make_provider()
    with _mock_request(_ok_response(_correction_result_payload())) as mock:
        p.submit_correction(_make_correction_request(), idempotency_key="k")
    _, kwargs = mock.call_args
    assert kwargs["headers"]["If-Match"] == f'"{VERSION}"'


def test_submit_correction_409_raises_source_changed():
    p = _make_provider()
    with _mock_request(_error_response(409)):
        with pytest.raises(AttendanceSourceChanged):
            p.submit_correction(_make_correction_request(), idempotency_key="k")


def test_submit_correction_500_raises_provider_unavailable():
    p = _make_provider()
    with _mock_request(_error_response(500)):
        with pytest.raises(AttendanceProviderUnavailable):
            p.submit_correction(_make_correction_request(), idempotency_key="k")


def test_submit_correction_wrong_record_ref_in_response_raises_provider_error():
    p = _make_provider()
    payload = _correction_result_payload(record_ref="WRONG-REF")
    with _mock_request(_ok_response(payload)):
        with pytest.raises(AttendanceProviderError, match="different record"):
            p.submit_correction(_make_correction_request(), idempotency_key="k")


def test_submit_correction_empty_idempotency_key_raises_provider_error():
    p = _make_provider()
    # pre-flight check: no HTTP call should be made
    with patch("httpx.request") as mock:
        with pytest.raises(AttendanceProviderError, match="idempotency"):
            p.submit_correction(_make_correction_request(), idempotency_key="")
        mock.assert_not_called()


def test_submit_correction_too_long_idempotency_key_raises_provider_error():
    p = _make_provider()
    with patch("httpx.request") as mock:
        with pytest.raises(AttendanceProviderError, match="idempotency"):
            p.submit_correction(_make_correction_request(), idempotency_key="x" * 201)
        mock.assert_not_called()


def test_submit_correction_malformed_json_raises_provider_error():
    p = _make_provider()
    with _mock_request(_malformed_response()):
        with pytest.raises(AttendanceProviderError):
            p.submit_correction(_make_correction_request(), idempotency_key="k")


# ===========================================================================
# factory / configuration
# ---------------------------------------------------------------------------
# These tests import app.integrations.factory and app.core.config.  They
# build a minimal Settings object using keyword arguments (no .env file
# needed) and then use model_copy(update=...) to override specific fields.
# ===========================================================================


def _base_settings():
    """Return a valid Settings with attendance_provider=mock (no .env needed)."""
    from app.config import Settings

    return Settings(
        attendance_provider="mock",
    )


def test_factory_returns_mock_provider_when_configured():
    from app.integrations.factory import make_attendance_provider
    from app.integrations.mock_provider import MockAttendanceProvider

    provider = make_attendance_provider(_base_settings())
    try:
        assert isinstance(provider, MockAttendanceProvider)
    finally:
        provider.close()


def test_factory_raises_when_http_provider_missing_base_url():
    from app.integrations.factory import make_attendance_provider
    from pydantic import SecretStr

    settings = _base_settings().model_copy(
        update={
            "attendance_provider": "http",
            "attendance_api_token": SecretStr("tok"),
            "attendance_api_base_url": None,
        }
    )
    # factory checks base_url and token together; missing base_url → ValueError
    with pytest.raises(ValueError, match="ATTENDANCE_API_BASE_URL|requires"):
        make_attendance_provider(settings)


def test_factory_raises_when_http_provider_missing_token():
    from app.integrations.factory import make_attendance_provider

    settings = _base_settings().model_copy(
        update={
            "attendance_provider": "http",
            "attendance_api_base_url": "https://attendance.example.com",
            "attendance_api_token": None,
        }
    )
    # factory checks base_url and token together; missing token → ValueError
    with pytest.raises(ValueError, match="ATTENDANCE_API_TOKEN|requires"):
        make_attendance_provider(settings)


def test_factory_raises_when_http_url_is_plain_http_non_localhost():
    from app.integrations.factory import make_attendance_provider
    from pydantic import SecretStr

    settings = _base_settings().model_copy(
        update={
            "attendance_provider": "http",
            "attendance_api_base_url": "http://attendance.example.com",
            "attendance_api_token": SecretStr("tok"),
        }
    )
    # non-localhost http:// must be rejected
    with pytest.raises(ValueError, match="HTTPS"):
        make_attendance_provider(settings)


def test_factory_accepts_https_base_url():
    from app.integrations.factory import make_attendance_provider
    from pydantic import SecretStr

    settings = _base_settings().model_copy(
        update={
            "attendance_provider": "http",
            "attendance_api_base_url": "https://attendance.example.com",
            "attendance_api_token": SecretStr("tok"),
        }
    )
    provider = make_attendance_provider(settings)
    try:
        assert isinstance(provider, HttpAttendanceProvider)
    finally:
        provider.close()


def test_factory_accepts_http_localhost():
    from app.integrations.factory import make_attendance_provider
    from pydantic import SecretStr

    settings = _base_settings().model_copy(
        update={
            "attendance_provider": "http",
            "attendance_api_base_url": "http://localhost:8080",
            "attendance_api_token": SecretStr("tok"),
        }
    )
    provider = make_attendance_provider(settings)
    try:
        assert isinstance(provider, HttpAttendanceProvider)
    finally:
        provider.close()
