"""Execution-boundary edits select the existing public-entry fence regression."""
from __future__ import annotations

import sys

import pytest

from loopx.canary.premerge import build_premerge_validation_gate


@pytest.mark.parametrize("tier", ["quick", "standard"])
@pytest.mark.parametrize("path", [
    "loopx/control_plane/turn_driver/executor.py",
    "loopx/control_plane/turn_driver/lane_fence.py",
    "loopx/cli_commands/turn_run_once.py",
    "tests/test_turn_lane_fence.py",
])
def test_execution_boundary_edits_run_the_public_entry_regression(path, tier):
    plan = build_premerge_validation_gate(changed_files=[path], tier=tier, execute=False)
    checks = [row for row in plan["direct_checks"] if row["id"] == "turn_lane_entrypoint"]
    assert len(checks) == 1
    assert checks[0]["argv"] == [sys.executable, "-m", "pytest", "-q", "tests/test_turn_lane_fence.py"]
    assert checks[0]["status"] == "ready"
    assert plan["executes_checks"] is False


def test_context_only_edits_do_not_select_unrelated_execution_checks():
    plan = build_premerge_validation_gate(
        changed_files=["loopx/control_plane/quota/context_delivery.ts"], execute=False,
    )
    assert not any(row["id"] == "turn_lane_entrypoint" for row in plan["direct_checks"])
