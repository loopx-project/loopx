"""The preflight accepts only cadence values supported by the owning CLI."""
from __future__ import annotations

import subprocess
import sys

import pytest

from benchmark.LHTB.scripts.preflight import cli_supports_replan_count
from benchmark.runtime.codex import DEFAULT_REPLAN_AFTER_TURNS


@pytest.mark.parametrize("help_text,option,count,expected", [
    ("--execution-replan-after-turns {1,2,3,4,5,6}", "--execution-replan-after-turns", 6, True),
    ("--execution-replan-after-turns {1,2,3,4,5,6}", "--execution-replan-after-turns", 3, True),
    ("--execution-replan-after-turns {1,2,3,4,5}", "--execution-replan-after-turns", 6, False),
    ("--execution-replan-after-todos {1,2,3,4,5}", "--execution-replan-after-todos", 3, True),
    ("--execution-replan-after-todos {1,2,3,4,5}", "--execution-replan-after-todos", 6, False),
    ("--execution-replan-after-turns\n  {1,2,3,4,5,6}", "--execution-replan-after-turns", 6, True),
    ("--execution-replan-after-turns-extra {1,2,3,4,5,6}", "--execution-replan-after-turns", 6, False),
    ("--execution-replan-after-turns {1,2,3,4,5,6}", "--execution-replan-after-todos", 3, False),
])
def test_resolved_cadence_uses_its_own_cli_choices(help_text, option, count, expected):
    assert cli_supports_replan_count(help_text, option, count) is expected


def test_current_cli_accepts_the_benchmark_default():
    help_result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "configure-goal", "--help"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert cli_supports_replan_count(
        help_result.stdout, "--execution-replan-after-turns", DEFAULT_REPLAN_AFTER_TURNS,
    )
