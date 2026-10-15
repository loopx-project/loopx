"""The preflight accepts only cadence values supported by the owning CLI."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

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


def _render_config_flags(run_sh: str) -> set[str]:
    """Collect every option run.sh hands to render_config.py, including argv arrays."""
    lines = run_sh.splitlines()
    start = next(i for i, line in enumerate(lines) if 'scripts/render_config.py"' in line)
    command = []
    for line in lines[start:]:
        command.append(line)
        if not line.rstrip().endswith("\\"):
            break
    text = "\n".join(command)
    flags = set(re.findall(r"(--[a-z][a-z-]*)", text))
    for array in re.findall(r'"\$\{(\w+)\[@\]\}"', text):
        flags.update(re.findall(rf"{array}=\((--[a-z][a-z-]*)", run_sh))
    return flags


def test_run_sh_passes_only_options_the_renderer_accepts():
    lhtb = Path(__file__).resolve().parents[1] / "LHTB"
    help_result = subprocess.run(
        [sys.executable, "-m", "benchmark.LHTB.scripts.render_config", "--help"],
        cwd=lhtb.parents[1], capture_output=True, text=True, check=True, timeout=30,
    )
    accepted = set(re.findall(r"(--[a-z][a-z-]*)", help_result.stdout))
    passed = _render_config_flags((lhtb / "run.sh").read_text())
    assert passed and passed <= accepted, passed - accepted


def test_run_sh_rejects_the_retired_planning_cap(tmp_path):
    run_sh = Path(__file__).resolve().parents[1] / "LHTB" / "run.sh"
    (tmp_path / "upstream" / "tasks").mkdir(parents=True)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    for name in ("harbor", "python"):
        (venv_bin / name).write_text("#!/bin/sh\nexit 0\n")
        (venv_bin / name).chmod(0o755)
    result = subprocess.run(
        ["bash", str(run_sh), "preflight"],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "LHTB_ROOT": str(tmp_path),
             "OPENAI_BASE_URL": "http://192.0.2.1:8000/v1", "OPENAI_API_KEY": "x",
             "LOOPX_PLANNING_TIMEOUT_SEC": "300"},
    )
    assert result.returncode == 1
    assert "Remove LOOPX_PLANNING_TIMEOUT_SEC" in result.stderr
