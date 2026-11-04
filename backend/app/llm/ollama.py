"""Ollama chat-completions provider."""

from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

from app.config import Settings
from app.llm.base import LLMResult, LLMTimeout, LLMUnavailable, PromptBundle


class OllamaClient:
    """Synchronous Ollama client with injectable transport for isolated use."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.llm_timeout_seconds
        self._transport = transport
        self._client = client

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult:
        """Submit one non-streaming request and normalize Ollama's response."""
        del attempt
        messages: list[dict[str, str]] = [{"role": "system", "content": bundle.system}]
        messages.extend(bundle.few_shot)
        user_content = bundle.user
        if feedback:
            user_content = f"{user_content}\n\nRETRY FEEDBACK:\n{feedback}"
        messages.append({"role": "user", "content": user_content})
        payload = {
            "model": self.model,
            "stream": False,
            "format": bundle.schema,
            "messages": messages,
            "options": {"temperature": 0, "seed": 42, "num_ctx": 4096},
        }

        started = perf_counter()
        try:
            if self._client is not None:
                response = self._client.post(
                    f"{self.url}/api/chat", json=payload, timeout=self.timeout
                )
            else:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    response = client.post(f"{self.url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            raw_text = data["message"]["content"]
            if not isinstance(raw_text, str):
                raise TypeError("Ollama message content must be text")
        except httpx.TimeoutException as exc:
            raise LLMTimeout("Ollama request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailable(f"Ollama returned HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise LLMUnavailable("Ollama request failed") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMUnavailable("Ollama returned an invalid response") from exc

        latency_ms = round((perf_counter() - started) * 1000)
        return LLMResult(
            raw_text=raw_text,
            latency_ms=latency_ms,
            prompt_tokens=_optional_int(data.get("prompt_eval_count")),
            completion_tokens=_optional_int(data.get("eval_count")),
            model=self.model,
        )


def _optional_int(value: Any) -> int | None:
    """Keep usage metadata optional and ignore malformed provider values."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None
