"""Fault-injector behavior tests for every configured mode."""

import json

import pytest

from app.llm.base import LLMResult, LLMTimeout
from app.llm.faults import FaultInjector
from app.schemas import FaultMode


class FixedClient:
    model = "fixed-model"

    def generate(self, bundle, attempt, feedback=None):
        del bundle, attempt, feedback
        return LLMResult(
            raw_text=json.dumps(
                {
                    "exception_id": 1,
                    "explanation": "PUNCH-01 supports a manager-reviewed timecard change.",
                    "proposed_correction": {
                        "action": "ADD_PUNCH",
                        "kind": "OUT",
                        "ts": "2025-03-13T18:00",
                    },
                    "policy_ids_cited": ["PUNCH-01"],
                    "confidence": 0.9,
                }
            ),
            latency_ms=7,
            prompt_tokens=5,
            completion_tokens=6,
            model=self.model,
        )


@pytest.fixture
def bundle():
    from app.llm.base import PromptBundle

    return PromptBundle(
        "system",
        "user",
        {"constraints": {"ts_window": {"earliest": "2025-03-13T09:00", "latest": "2025-03-13T18:00"}}},
        {},
        1,
    )


@pytest.mark.parametrize("mode", [FaultMode.NONE, FaultMode.BAD_THEN_GOOD, FaultMode.ALWAYS_BAD, FaultMode.GARBAGE, FaultMode.TIMEOUT, FaultMode.BAD_CITATION, FaultMode.NAIVE_LATEST])
def test_each_fault_mode_behaves_as_specified(mode, bundle):
    client = FaultInjector(FixedClient(), lambda: mode)
    if mode == FaultMode.TIMEOUT:
        with pytest.raises(LLMTimeout):
            client.generate(bundle, 1)
        return
    result = client.generate(bundle, 1)
    if mode == FaultMode.GARBAGE:
        assert result.raw_text == "Sure! Here is the fix: set it to 5pm"
        return
    payload = json.loads(result.raw_text)
    if mode in {FaultMode.BAD_THEN_GOOD, FaultMode.ALWAYS_BAD}:
        assert payload["proposed_correction"]["ts"] == "2025-03-14T00:00"
    elif mode == FaultMode.BAD_CITATION:
        assert payload["policy_ids_cited"] == ["POL-999"]
    elif mode == FaultMode.NAIVE_LATEST:
        assert payload["proposed_correction"]["ts"] == "2025-03-13T18:00"
    else:
        assert payload["proposed_correction"]["ts"] == "2025-03-13T18:00"


def test_bad_then_good_only_mutates_first_attempt(bundle):
    client = FaultInjector(FixedClient(), lambda: FaultMode.BAD_THEN_GOOD)
    assert json.loads(client.generate(bundle, 2).raw_text)["proposed_correction"]["ts"] == "2025-03-13T18:00"
