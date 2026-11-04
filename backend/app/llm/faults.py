"""Deterministic fault modes for exercising proposal validation and fallback."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Callable

from app.llm.base import LLMClient, LLMResult, LLMTimeout, LLMUnavailable, PromptBundle
from app.schemas import FaultMode


class FaultInjector:
    """Wrap an LLM client and apply the currently selected simulation fault."""

    def __init__(self, inner: LLMClient, get_mode: Callable[[], FaultMode]) -> None:
        self.inner = inner
        self.get_mode = get_mode
        self.model = getattr(inner, "model", "unknown")

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult:
        mode = FaultMode(self.get_mode())
        if mode == FaultMode.TIMEOUT:
            raise LLMTimeout("Simulated LLM timeout")
        if mode == FaultMode.GARBAGE:
            return LLMResult(
                raw_text="Sure! Here is the fix: set it to 5pm",
                latency_ms=0,
                prompt_tokens=None,
                completion_tokens=None,
                model=self.model,
            )

        result = self.inner.generate(bundle, attempt, feedback)
        if mode == FaultMode.NONE:
            return result
        if mode == FaultMode.BAD_THEN_GOOD:
            return _mutate(result, bundle, unsafe=True) if attempt == 1 else result
        if mode == FaultMode.ALWAYS_BAD:
            return _mutate(result, bundle, unsafe=True)
        if mode == FaultMode.BAD_CITATION:
            return _mutate(result, bundle, citations=["POL-999"])
        if mode == FaultMode.NAIVE_LATEST:
            return _mutate(result, bundle, naive_latest=True)
        return result


def _mutate(
    result: LLMResult,
    bundle: PromptBundle,
    *,
    unsafe: bool = False,
    citations: list[str] | None = None,
    naive_latest: bool = False,
) -> LLMResult:
    """Parse and modify the proposal while preserving unrelated result fields."""
    try:
        proposal = json.loads(result.raw_text)
        correction = proposal["proposed_correction"]
        if not isinstance(proposal, dict) or not isinstance(correction, dict):
            raise TypeError("proposal and correction must be objects")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LLMUnavailable("Cannot inject a fault into malformed provider JSON") from exc

    if citations is not None:
        proposal["policy_ids_cited"] = citations
    if unsafe:
        if correction.get("ts") is not None:
            correction["ts"] = _latest_plus_six_hours(bundle)
        else:
            correction.clear()
            correction.update({"action": "ADD_PUNCH", "kind": "OUT", "ts": "2099-01-01T00:00"})
    if naive_latest:
        latest = _window_latest(bundle)
        if latest is not None and correction.get("ts") is not None:
            correction["ts"] = latest
    return LLMResult(
        raw_text=json.dumps(proposal, separators=(",", ":")),
        latency_ms=result.latency_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        model=result.model,
    )


def _window_latest(bundle: PromptBundle) -> str | None:
    constraints = bundle.context.get("constraints")
    window = constraints.get("ts_window") if isinstance(constraints, dict) else None
    latest = window.get("latest") if isinstance(window, dict) else None
    return str(latest) if latest is not None else None


def _latest_plus_six_hours(bundle: PromptBundle) -> str:
    latest = _window_latest(bundle)
    if latest is None:
        return "2099-01-01T00:00"
    value = datetime.strptime(latest, "%Y-%m-%dT%H:%M") + timedelta(hours=6)
    return value.strftime("%Y-%m-%dT%H:%M")
