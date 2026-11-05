"""Typed application settings loaded from the environment or an optional .env file."""

import ipaddress
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the API, database, and LLM provider."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Path("./shiftguard.db")
    environment: Literal["development", "staging", "production"] = "development"
    auth_mode: Literal["demo", "supabase"] = "demo"
    supabase_database_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    postgres_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    auth_organization_id: UUID | None = None
    cors_origins: str = "http://localhost:5173"
    attendance_provider: Literal["mock", "http"] = "mock"
    attendance_api_base_url: str | None = None
    attendance_api_token: SecretStr | None = None
    attendance_api_timeout_seconds: float = Field(default=10, gt=0, le=60)
    attendance_integration_id: UUID | None = None
    integration_webhook_secret: SecretStr | None = None
    integration_webhook_max_skew_seconds: int = Field(default=300, ge=30, le=3600)
    integration_webhook_max_body_bytes: int = Field(default=65536, ge=1024, le=1048576)
    analysis_max_attempts: int = Field(default=4, ge=1, le=10)
    analysis_retry_base_seconds: int = Field(default=15, ge=1, le=3600)
    analysis_job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    analysis_worker_poll_seconds: float = Field(default=2, gt=0, le=60)
    analysis_worker_heartbeat_seconds: int = Field(default=30, ge=5, le=3600)
    analysis_worker_heartbeat_timeout_seconds: int = Field(default=300, ge=30, le=86400)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    assistant_rate_limit_per_window: int = Field(default=20, ge=1, le=10000)
    webhook_rate_limit_per_window: int = Field(default=120, ge=1, le=10000)
    writeback_rate_limit_per_window: int = Field(default=20, ge=1, le=10000)
    ops_alert_webhook_url: SecretStr | None = None
    ops_alert_timeout_seconds: float = Field(default=3, gt=0, le=30)
    ops_alert_failure_threshold: int = Field(default=3, ge=1, le=100)
    ops_alert_window_seconds: int = Field(default=300, ge=1, le=86400)
    ops_alert_cooldown_seconds: int = Field(default=900, ge=1, le=86400)
    demo_now: datetime | None = datetime(2025, 3, 13, 20, 0)
    llm_provider: Literal["mock", "ollama"] = "mock"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    enable_demo_controls: bool = True
    auto_seed: bool = True
    default_manager_id: str = "m-001"
    manual_minutes_per_exception: float = Field(default=6.0, ge=0)
    review_minutes_per_exception: float = Field(default=1.0, ge=0)

    # Demo-mode API key guard (audit finding: no auth in demo mode).
    # Set to any non-empty secret to require  Authorization: Bearer <key>
    # or  X-Api-Key: <key>  on every request when auth_mode == "demo".
    # Leave empty to disable the check for purely local, single-developer use.
    demo_api_key: str = ""

    @field_validator("demo_now")
    @classmethod
    def demo_clock_must_be_naive(cls, value: datetime | None) -> datetime | None:
        """Keep business-clock timestamps site-local and timezone-naive."""
        if value is not None and value.utcoffset() is not None:
            raise ValueError("DEMO_NOW must be a timezone-naive local timestamp")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()


def validate_production_config(settings: Settings) -> None:
    """Fail closed when production configuration still permits demo behavior.

    The current application still opens its SQLite demo store for core API
    resources. That store is not suitable for a production deployment, so even
    a fully configured Supabase and company connector cannot enable production
    until the application storage has been migrated to a shared database.
    """
    if settings.environment != "production":
        return

    problems: list[str] = []
    if settings.auth_mode != "supabase":
        problems.append("AUTH_MODE must be supabase")
    else:
        if not _is_remote_https_url(settings.supabase_url):
            problems.append("SUPABASE_URL must be a non-local HTTPS URL")
        if not _is_tls_postgres_url(settings.supabase_database_url):
            problems.append(
                "SUPABASE_DATABASE_URL must be a remote PostgreSQL URL with TLS enabled"
            )
        if settings.auth_organization_id is None:
            problems.append("AUTH_ORGANIZATION_ID must be configured")

    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if not origins or any(not _is_remote_https_url(origin, origin_only=True) for origin in origins):
        problems.append("CORS_ORIGINS must contain only explicit, non-local HTTPS origins")

    if settings.attendance_provider != "http":
        problems.append("ATTENDANCE_PROVIDER must be http")
    if not _is_remote_https_url(settings.attendance_api_base_url):
        problems.append("ATTENDANCE_API_BASE_URL must be a non-local HTTPS URL")
    if (
        settings.attendance_api_token is None
        or not settings.attendance_api_token.get_secret_value().strip()
    ):
        problems.append("ATTENDANCE_API_TOKEN must be configured")
    if settings.attendance_integration_id is None:
        problems.append("ATTENDANCE_INTEGRATION_ID must be configured")
    if (
        settings.integration_webhook_secret is None
        or not settings.integration_webhook_secret.get_secret_value().strip()
    ):
        problems.append("INTEGRATION_WEBHOOK_SECRET must be configured")

    if settings.llm_provider != "ollama":
        problems.append("LLM_PROVIDER must be ollama")
    if not _is_usable_ollama_url(settings.ollama_url) or not settings.ollama_model.strip():
        problems.append("OLLAMA_URL and OLLAMA_MODEL must be configured")

    if settings.enable_demo_controls:
        problems.append("ENABLE_DEMO_CONTROLS must be false")
    if settings.auto_seed:
        problems.append("AUTO_SEED must be false")
    if settings.demo_now is not None:
        problems.append("DEMO_NOW must be unset")

    # create_app() currently initializes and uses the SQLite application store
    # even in Supabase mode. Do not let a filesystem path masquerade as a
    # production-ready shared store.
    problems.append(
        "the application still uses SQLite for core API resources; migrate that store to shared PostgreSQL before production"
    )

    if problems:
        raise ValueError("Unsafe production configuration: " + "; ".join(problems))


def _is_remote_https_url(value: str | None, *, origin_only: bool = False) -> bool:
    if not value or not value.strip():
        return False
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        _ = parsed.port
        if parsed.scheme.lower() != "https" or not hostname or parsed.username or parsed.password:
            return False
        if origin_only and (parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            return False
        host = hostname.casefold().rstrip(".")
        if any(character.isspace() for character in host):
            return False
        if "*" in host or host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return not (address.is_loopback or address.is_unspecified)
    except ValueError:
        return False


def _is_tls_postgres_url(value: SecretStr | None) -> bool:
    if value is None or not value.get_secret_value().strip():
        return False
    try:
        parsed = urlsplit(value.get_secret_value().strip())
        hostname = parsed.hostname
        _ = parsed.port
        query = parse_qs(parsed.query)
        sslmode = query.get("sslmode", [""])[0].lower()
        if (
            parsed.scheme.lower() not in {"postgres", "postgresql"}
            or not hostname
            or not parsed.username
            or not parsed.password
            or parsed.path in {"", "/"}
        ):
            return False
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            return False
        host = hostname.casefold().rstrip(".")
        if any(character.isspace() for character in host):
            return False
        if "*" in host or host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return not (address.is_loopback or address.is_unspecified)
    except ValueError:
        return False


def _is_usable_ollama_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        _ = parsed.port  # Validate port syntax and range when one is supplied.
        if not hostname:
            return False
        if any(character.isspace() for character in hostname):
            return False
        return (
            parsed.scheme.lower() in {"http", "https"}
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and "*" not in hostname
        )
    except ValueError:
        return False
