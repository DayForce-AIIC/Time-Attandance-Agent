"""Regression test for the committed deterministic evaluation fixtures."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = ROOT / "eval" / "generate_cases.py"
SPEC = importlib.util.spec_from_file_location("shiftguard_eval_generator", GENERATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def test_case_generation_is_deterministic() -> None:
    first = generator.serialize_cases(generator.build_cases())
    second = generator.serialize_cases(generator.build_cases())
    assert first == second
    assert len(first.splitlines()) == 48
