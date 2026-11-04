"""Typed application settings loaded from the environment or an optional .env file."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the API, database, and LLM provider."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Path("./shiftguard.db")
    demo_now: datetime | None = datetime(2025, 3, 13, 20, 0)
    llm_provider: Literal["mock", "ollama"] = "mock"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    enable_demo_controls: bool = True
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
