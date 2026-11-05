"""Configured quality targets for the real-model evaluation mode."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from eval.run_eval import evaluate_gates


def _metrics(
    *,
    citation_rate: float,
    adversarial_rate: float,
    latency_p95_ms: int | None,
    fallback_rate: float,
    oracle_pass_rate: float,
) -> dict[str, object]:
    return {
        "unsafe_escape_count": 0,
        "correct_citation_rate": citation_rate,
        "adversarial_safe_rate": adversarial_rate,
        "latency_p95_ms": latency_p95_ms,
        "fallback_rate": fallback_rate,
        "oracle_pass_rate": oracle_pass_rate,
    }


def test_ollama_baseline_mode_enforces_quality_safety_and_latency_targets() -> None:
    gates = evaluate_gates(
        "ollama",
        _metrics(
            citation_rate=0.97,
            adversarial_rate=0.5,
            latency_p95_ms=120_001,
            fallback_rate=0.21,
            oracle_pass_rate=0.97,
        ),
        [],
    )

    assert [gate["passed"] for gate in gates] == [True, False, False, False, False, False]


def test_ollama_baseline_mode_accepts_metrics_at_target_boundaries() -> None:
    gates = evaluate_gates(
        "ollama",
        _metrics(
            citation_rate=0.98,
            adversarial_rate=1.0,
            latency_p95_ms=120_000,
            fallback_rate=0.20,
            oracle_pass_rate=0.98,
        ),
        [],
    )

    assert all(gate["passed"] for gate in gates)
