"""Typed application settings loaded from the environment or an optional .env file."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
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
