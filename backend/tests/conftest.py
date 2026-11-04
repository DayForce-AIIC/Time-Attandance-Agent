"""Shared deterministic fixtures for backend contract and unit tests."""

from datetime import datetime
from pathlib import Path

import pytest
from app.clock import Clock
from app.config import Settings
from app.db import get_conn, init_db
from app.deps import get_book, get_clock
from app.detection import run_detection
from app.main import create_app
from app.policies import load_policies
from app.seed import seed
from fastapi.testclient import TestClient


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Use an isolated database, mock provider, and fixed business clock."""
    return Settings(
        db_path=tmp_path / "shiftguard-test.db",
        auto_seed=False,
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
def fault():
    """Return a helper that changes the active app's dynamic fault mode."""

    def set_mode(application, mode) -> None:
        application.state.fault_mode = mode

    return set_mode
