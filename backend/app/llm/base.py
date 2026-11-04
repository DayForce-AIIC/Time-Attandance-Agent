"""Provider-neutral prompt and response types."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class PromptBundle:
    """Complete current-turn prompt plus isolated examples for chat providers."""

    system: str
    user: str
    context: dict[str, object]
    schema: dict[str, object]
    exception_id: int
    few_shot: list[dict[str, str]] = field(default_factory=list)


@dataclass
class LLMResult:
    """Raw provider output and basic usage/latency metadata."""

    raw_text: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str


class LLMClient(Protocol):
    """Synchronous interface implemented by each LLM provider."""

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult: ...


class LLMTimeout(Exception):
    """Raised when an LLM provider exceeds its configured timeout."""


class LLMUnavailable(Exception):
    """Raised when an LLM provider cannot be reached or returns an error."""
