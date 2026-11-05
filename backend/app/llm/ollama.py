"""Ollama chat-completions provider."""

from __future__ import annotations

import json
from contextlib import nullcontext
from time import perf_counter
from typing import Any, Callable

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

    def generate_stream(
        self,
        bundle: PromptBundle,
        attempt: int,
        on_delta: Callable[[str], None],
        feedback: str | None = None,
    ) -> LLMResult:
        """Stream Ollama's JSON response while retaining it for normal validation."""
        del attempt
        messages: list[dict[str, str]] = [{"role": "system", "content": bundle.system}]
        messages.extend(bundle.few_shot)
        user_content = bundle.user
        if feedback:
            user_content = f"{user_content}\n\nRETRY FEEDBACK:\n{feedback}"
        messages.append({"role": "user", "content": user_content})
        payload = {
            "model": self.model,
            "stream": True,
            "format": bundle.schema,
            "messages": messages,
            "options": {"temperature": 0, "seed": 42, "num_ctx": 4096},
        }

        started = perf_counter()
        chunks: list[str] = []
        final_data: dict[str, Any] = {}
        client_context = (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(timeout=self.timeout, transport=self._transport)
        )
        try:
            with client_context as client:
                with client.stream(
                    "POST", f"{self.url}/api/chat", json=payload, timeout=self.timeout
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            raise TypeError("Ollama stream events must be JSON objects")
                        final_data = data
                        if isinstance(data.get("error"), str):
                            raise LLMUnavailable("Ollama returned an error while streaming")
                        message = data.get("message")
                        content = message.get("content") if isinstance(message, dict) else None
                        if isinstance(content, str) and content:
                            chunks.append(content)
                            on_delta(content)
        except httpx.TimeoutException as exc:
            raise LLMTimeout("Ollama request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailable(f"Ollama returned HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise LLMUnavailable("Ollama request failed") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMUnavailable("Ollama returned an invalid streaming response") from exc

        return LLMResult(
            raw_text="".join(chunks),
            latency_ms=round((perf_counter() - started) * 1000),
            prompt_tokens=_optional_int(final_data.get("prompt_eval_count")),
            completion_tokens=_optional_int(final_data.get("eval_count")),
            model=self.model,
        )


def ollama_model_is_available(settings: Settings) -> bool:
    """Return whether Ollama is reachable and has the configured model installed."""
    try:
        response = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=1.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return False

    configured = _normalize_model_name(settings.ollama_model)
    available = {
        _normalize_model_name(str(model[key]))
        for model in payload["models"]
        if isinstance(model, dict)
        for key in ("name", "model")
        if isinstance(model.get(key), str)
    }
    return configured in available


def _normalize_model_name(name: str) -> str:
    """Treat Ollama's omitted tag as its default ``latest`` tag."""
    normalized = name.strip()
    if normalized and ":" not in normalized.rsplit("/", 1)[-1]:
        normalized = f"{normalized}:latest"
    return normalized


def _optional_int(value: Any) -> int | None:
    """Keep usage metadata optional and ignore malformed provider values."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None
