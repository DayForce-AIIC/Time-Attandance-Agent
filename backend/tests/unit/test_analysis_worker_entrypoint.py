"""Startup and heartbeat lifecycle checks for the durable worker process."""

import sys
from unittest.mock import MagicMock
from uuid import uuid4

from app.config import Settings
from scripts import analysis_worker as worker_script


def test_worker_validates_settings_and_cleans_up_heartbeat(monkeypatch) -> None:
    organization_id = uuid4()
    integration_id = uuid4()
    settings = Settings(
        _env_file=None,
        environment="staging",
        auth_mode="supabase",
        auth_organization_id=organization_id,
        attendance_integration_id=integration_id,
        auto_seed=False,
    )
    lifecycle: list[str] = []
    provider = MagicMock()
    repository = MagicMock()
    repository.get_integration.return_value = {"status": "active"}

    class RepoFactory:
        @classmethod
        def from_settings(cls, _settings):
            lifecycle.append("repository")
            return repository

    class Worker:
        def __init__(self, *_args, **_kwargs):
            pass

        def process_one(self) -> bool:
            return False

    monkeypatch.setattr(sys, "argv", ["analysis_worker.py", "--once"])
    monkeypatch.setattr(worker_script, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker_script,
        "validate_production_config",
        lambda _settings: lifecycle.append("validated"),
    )
    monkeypatch.setattr(worker_script, "configure_logging", lambda: None)
    monkeypatch.setattr(
        worker_script,
        "make_attendance_provider",
        lambda _settings: lifecycle.append("provider") or provider,
    )
    monkeypatch.setattr(worker_script, "PostgresWorkflowRepository", RepoFactory)
    monkeypatch.setattr(worker_script, "make_llm_client", lambda *_args: object())
    monkeypatch.setattr(worker_script, "AttendanceAnalysisWorker", Worker)

    worker_script.main()

    assert lifecycle == ["validated", "provider", "repository"]
    repository.record_service_heartbeat.assert_called_once_with(
        organization_id, "analysis_worker", repository.record_service_heartbeat.call_args.args[2]
    )
    repository.remove_service_heartbeat.assert_called_once_with(
        organization_id, "analysis_worker", repository.remove_service_heartbeat.call_args.args[2]
    )
    repository.close.assert_called_once()
    provider.close.assert_called_once()


def test_worker_refuses_production_before_opening_provider(monkeypatch) -> None:
    settings = Settings(_env_file=None, environment="production")
    provider_factory = MagicMock()
    monkeypatch.setattr(sys, "argv", ["analysis_worker.py", "--once"])
    monkeypatch.setattr(worker_script, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_script, "make_attendance_provider", provider_factory)

    try:
        worker_script.main()
    except ValueError as error:
        assert "Unsafe production configuration" in str(error)
    else:
        raise AssertionError("worker should reject the current production storage configuration")

    provider_factory.assert_not_called()
