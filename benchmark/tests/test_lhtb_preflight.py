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


@pytest.mark.parametrize("legacy_limit", [None, 300])
def test_config_renderer_removes_only_legacy_null_planning_limit(tmp_path, monkeypatch, legacy_limit):
    import yaml
    from benchmark.LHTB.scripts.render_config import main

    template = tmp_path / "template.yaml"
    template.write_text(yaml.safe_dump({"datasets": [{"task_names": [f"task-{i}" for i in range(46)]}],
        "agents": [{"kwargs": {"planning_timeout_sec": legacy_limit}}]}))
    output = tmp_path / "candidate.yaml"
    monkeypatch.setattr(sys, "argv", ["render_config", "--template", str(template),
        "--output", str(output), "--job-name", "fixture", "--jobs-dir", "runs",
        "--concurrency", "1", "--model", "fixture", "--effort", "high", "--timeout", "1000"])
    if legacy_limit is not None:
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2 and not output.exists()
    else:
        assert main() == 0
        kwargs = yaml.safe_load(output.read_text())["agents"][0]["kwargs"]
        assert "planning_timeout_sec" not in kwargs
        assert kwargs["task_entry"] == "loopx-planned"
        assert kwargs["scheduler_timeout_sec"] == 5080
