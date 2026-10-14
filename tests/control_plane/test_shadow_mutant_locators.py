"""Every Stage 2C mutation must still target the current production source."""
from __future__ import annotations

import ast
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


@pytest.mark.parametrize("name", ["fence_outside_lock", "source_binding_outside_lock"])
def test_lock_mutations_preserve_executable_dry_run_and_locked_body(name: str) -> None:
    case = next(case for case in HARNESS.CASES if case.name == name)
    relative_path, edit = case.edits[0]
    mutated = edit((ROOT / relative_path).read_text(encoding="utf-8"))
    compile(mutated, relative_path, "exec")
    function = next(node for node in ast.parse(mutated).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "legacy_todo_write_transaction")
    guard, locks = function.body[-2:]
    assert isinstance(guard, ast.If) and ast.unparse(guard.test) == "not dry_run"
    assert isinstance(locks, ast.With) and len(locks.items) == 2
    assert len(locks.body) == 1 and isinstance(locks.body[0], ast.Expr)
    assert isinstance(locks.body[0].value, ast.Yield)
