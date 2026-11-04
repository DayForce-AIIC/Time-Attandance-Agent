"""Shared deterministic fixtures for backend contract and unit tests."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from app.clock import Clock
from app.config import Settings
from app.db import get_conn, init_db
from app.deps import get_book, get_clock, get_llm
from app.detection import run_detection
from app.llm.base import LLMResult, PromptBundle
from app.main import create_app
from app.policies import load_policies
from app.schemas import AssistantModelAnswer
from app.seed import seed
from fastapi.testclient import TestClient


class AssistantTestClient:
    """Deterministic stand-in for a reachable Ollama model in API tests."""

    model = "test-ollama"

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult:
        del attempt, feedback
        payload = json.loads(bundle.user)
        question = str(payload["question"]).casefold()
        source_ids = list(bundle.context.get("source_ids", []))
        if "likely to be absent" in question:
            answer = "ShiftGuard does not predict individual absences."
            source_ids = []
        elif "reconcile wfh" in question:
            answer = "The prototype does not yet store WFH, leave, half-day, or full-day statuses."
            source_ids = []
        elif not source_ids:
            answer = "I could not find a matching attendance case or policy passage."
        else:
            answer = str(payload["evidence_basis"])

        response = AssistantModelAnswer(
            in_scope=True,
            uses_evidence=bool(source_ids),
            answer=answer,
            source_ids=source_ids,
        )
        return LLMResult(
            raw_text=response.model_dump_json(),
            latency_ms=1,
            prompt_tokens=None,
            completion_tokens=None,
            model=self.model,
        )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Use an isolated database, mock provider, and fixed business clock."""
    return Settings(
        db_path=tmp_path / "shiftguard-test.db",
        auth_mode="demo",
        auto_seed=False,
        attendance_provider="mock",
        llm_provider="mock",
        demo_now=datetime(2025, 3, 13, 20, 0),
    )


@pytest.fixture
def book():
    """Return the validated built-in policy book."""
    return load_policies()


@pytest.fixture
def conn(settings: Settings):
    """Initialize a clean per-test database connection."""
    connection = get_conn(settings.db_path)
    init_db(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def seeded_conn(conn, settings: Settings, book):
    """Seed the fixed demo dataset and run deterministic detection."""
    seed(conn)
    run_detection(conn, settings.demo_now, book)
    return conn


@pytest.fixture
def client(settings: Settings, book):
    """Create a TestClient over a seeded API database."""
    application = create_app(settings)
    application.dependency_overrides[get_clock] = lambda: Clock(settings)
    application.dependency_overrides[get_book] = lambda: book
    connection = get_conn(settings.db_path)
    seed(connection)
    run_detection(connection, settings.demo_now, book)
    connection.close()
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def ollama_client(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Exercise assistant routes as if Ollama is reachable, without a live server."""
    client.app.state.settings.llm_provider = "ollama"
    client.app.dependency_overrides[get_llm] = lambda: AssistantTestClient()
    monkeypatch.setattr("app.api.ollama_model_is_available", lambda _settings: True)
    return client


@pytest.fixture
def fault():
    """Return a helper that changes the active app's dynamic fault mode."""

    def set_mode(application, mode) -> None:
        application.state.fault_mode = mode

    return set_mode
