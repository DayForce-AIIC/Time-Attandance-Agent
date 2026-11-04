"""Regression checks for durable worker IDs and synthetic source stability."""

from app.analysis_worker import _model_correlation_id
from app.integrations.mock_provider import MockAttendanceProvider


def test_model_correlation_id_is_repeatable_and_javascript_safe():
    case_key = "f" * 64

    value = _model_correlation_id(case_key)

    assert value == _model_correlation_id(case_key)
    assert 0 < value <= 9_007_199_254_740_991


def test_mock_provider_keeps_unchanged_source_evidence_stable_between_processes():
    first = MockAttendanceProvider()
    second = MockAttendanceProvider()
    try:
        first_record = first.fetch_attendance_record("tcday_2026-11-03_8F39A")
        second_record = second.fetch_attendance_record("tcday_2026-11-03_8F39A")

        assert first_record is not None
        assert second_record is not None
        assert first_record.model_dump(mode="json") == second_record.model_dump(mode="json")
    finally:
        first.close()
        second.close()
