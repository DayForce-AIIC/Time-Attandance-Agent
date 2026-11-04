"""Construct the configured LLM provider and wrap it in simulation faults."""

from collections.abc import Callable

from app.config import Settings
from app.llm.base import LLMClient
from app.llm.faults import FaultInjector
from app.llm.mock import MockClient
from app.llm.ollama import OllamaClient
from app.schemas import FaultMode


def make_llm_client(
    settings: Settings, get_mode: Callable[[], FaultMode]
) -> LLMClient:
    """Build the provider selected by settings, with fault injection enabled."""
    if settings.llm_provider == "mock":
        provider: LLMClient = MockClient()
    elif settings.llm_provider == "ollama":
        provider = OllamaClient(settings)
    else:  # Guard against unvalidated settings-like objects.
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
    return FaultInjector(provider, get_mode)
