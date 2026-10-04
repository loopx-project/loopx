#!/usr/bin/env python3
"""Run the real agent-facing CLI output budget matrix from canary plans."""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEST_PATH = REPO_ROOT / "tests" / "control_plane" / "test_cli_output_budget.py"
DIFFERENTIAL_SMOKE = (
    REPO_ROOT
    / "examples"
    / "control_plane"
    / "cli-output-base-head-differential-smoke.py"
)


def _run_budget_checks() -> None:
    tests = runpy.run_path(str(TEST_PATH))
    tests["test_manifest_covers_the_declared_agent_facing_surface_set"]()
    tests["test_brief_budget_retains_full_commands_on_real_long_paths"]()
    # The differential probe runs all default and mode-variant assertions on
    # its candidate observations, including growth/duplication. Repeating the
    # candidate matrix here adds real CLI work without another contract.


def main() -> int:
    if not TEST_PATH.is_file():
        raise RuntimeError(
            f"missing CLI output budget test: {TEST_PATH.relative_to(REPO_ROOT)}"
        )
    _run_budget_checks()
    completed = subprocess.run(
        [sys.executable, str(DIFFERENTIAL_SMOKE.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "agent-facing CLI base/head differential failed\n"
            f"stdout:\n{completed.stdout[-3000:]}\n"
            f"stderr:\n{completed.stderr[-3000:]}"
        )
    print("cli-output-budget-regression-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
