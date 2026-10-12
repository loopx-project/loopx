"""Every Stage 2C mutation must still target the current production source."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "stage2c_mutant_locator_contract", ROOT / "examples/shared-goal-authority-e2e/mutants.py",
)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


@pytest.mark.parametrize("case", HARNESS.CASES, ids=lambda case: case.name)
def test_mutation_targets_current_production_source(case) -> None:
    # Exercise each real edit rather than maintaining a second locator list.
    # The full harness separately requires a green oracle and assertion kill.
    for relative_path, edit in case.edits:
        original = (ROOT / relative_path).read_text(encoding="utf-8")
        mutated = edit(original)
        assert mutated != original, f"{case.name}: mutation did not change {relative_path}"
