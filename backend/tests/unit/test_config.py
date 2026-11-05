"""Configuration guards for development and production startup."""

from uuid import uuid4

import pytest
from app.config import Settings, validate_production_config
from pydantic import SecretStr


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "auth_mode": "supabase",
        "supabase_url": "https://shiftguard.supabase.co",
        "supabase_database_url": SecretStr(
            "postgresql://postgres:password@db.shiftguard.supabase.co/app?sslmode=require"
        ),
        "supabase_jwt_secret": SecretStr("jwt-test-secret"),
        "auth_organization_id": uuid4(),
        "cors_origins": "https://shiftguard.example.com",
        "attendance_provider": "http",
        "attendance_api_base_url": "https://attendance.example.com",
        "attendance_api_token": SecretStr("attendance-test-token"),
        "attendance_integration_id": uuid4(),
        "integration_webhook_secret": SecretStr("webhook-test-secret"),
        "llm_provider": "ollama",
        "ollama_url": "http://ollama.internal:11434",
        "ollama_model": "qwen2.5:3b-instruct",
        "enable_demo_controls": False,
        "auto_seed": False,
        "demo_now": None,
        "db_path": "/var/lib/shiftguard/empty-demo.sqlite3",
    }
    values.update(updates)
    return Settings(**values)


@pytest.mark.parametrize("environment", ["development", "staging"])
def test_nonproduction_environments_do_not_use_production_gate(environment: str) -> None:
    validate_production_config(Settings(_env_file=None, environment=environment))


def test_production_config_reports_missing_requirements_without_secret_values() -> None:
    settings = Settings(_env_file=None, environment="production", auth_mode="supabase")

    with pytest.raises(ValueError) as error:
        validate_production_config(settings)

    message = str(error.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_DATABASE_URL" in message
    assert "ATTENDANCE_API_TOKEN" in message
    assert "ENABLE_DEMO_CONTROLS must be false" in message
    assert "SQLite for core API resources" in message
    assert "jwt-test-secret" not in message
    assert "attendance-test-token" not in message


def test_secure_service_settings_still_fail_closed_until_shared_app_store_exists() -> None:
    settings = _production_settings()

    with pytest.raises(ValueError, match="SQLite for core API resources"):
        validate_production_config(settings)


def test_supabase_jwks_auth_does_not_require_a_legacy_jwt_secret() -> None:
    settings = _production_settings(supabase_jwt_secret=None)

    with pytest.raises(ValueError) as error:
        validate_production_config(settings)

    assert "SUPABASE_JWT_SECRET" not in str(error.value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("supabase_url", "http://shiftguard.supabase.co", "SUPABASE_URL"),
        ("supabase_url", "https://localhost:54321", "SUPABASE_URL"),
        ("cors_origins", "https://portal.example.com/path", "CORS_ORIGINS"),
        ("cors_origins", "https://portal.example.com, *", "CORS_ORIGINS"),
        ("cors_origins", "https://*.example.com", "CORS_ORIGINS"),
        (
            "supabase_database_url",
            SecretStr("postgresql://postgres:password@localhost/app?sslmode=require"),
            "SUPABASE_DATABASE_URL",
        ),
        (
            "supabase_database_url",
            SecretStr("postgresql://postgres:password@db.example.com/app?sslmode=prefer"),
            "SUPABASE_DATABASE_URL",
        ),
        ("attendance_provider", "mock", "ATTENDANCE_PROVIDER"),
        ("llm_provider", "mock", "LLM_PROVIDER"),
        ("ollama_url", "ftp://ollama.internal:11434", "OLLAMA_URL"),
        ("enable_demo_controls", True, "ENABLE_DEMO_CONTROLS"),
        ("auto_seed", True, "AUTO_SEED"),
        ("demo_now", "2025-03-13T20:00", "DEMO_NOW"),
    ],
)
def test_production_gate_rejects_unsafe_service_settings(
    field: str, value: object, message: str
) -> None:
    settings = _production_settings(**{field: value})

    with pytest.raises(ValueError, match=message):
        validate_production_config(settings)
