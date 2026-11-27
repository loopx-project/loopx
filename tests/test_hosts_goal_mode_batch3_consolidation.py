"""Contracts protecting Batch 3 host consolidation under loopx/hosts/."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from loopx.hosts import agy, dsh
from loopx.hosts.agy.launchd_watchdog import build_antigravity_launchd_plist
from tests.test_dsh_goal_mode import _signed_request

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_batch3_retired_goal_mode_packages_live_under_hosts() -> None:
    for retired in ("agy_goal_mode", "dsh_goal_mode"):
        assert not (REPO_ROOT / "loopx" / retired).exists(), retired

    for rel in (
        "agy/__init__.py",
        "agy/README.md",
        "agy/host_runtime.py",
        "agy/launchd_watchdog.py",
        "dsh/__init__.py",
        "dsh/__main__.py",
        "dsh/README.md",
        "dsh/host_failure_map.py",
        "dsh/turn_host_adapter.py",
    ):
        assert (REPO_ROOT / "loopx" / "hosts" / rel).is_file(), rel

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "loopx.agy_goal_mode" not in package_data
    assert "loopx.dsh_goal_mode" not in package_data
    assert package_data["loopx.hosts.agy"] == ["README.md"]
    assert package_data["loopx.hosts.dsh"] == ["README.md"]


def test_agy_consolidated_entrypoints_and_cli_execution(tmp_path: Path) -> None:
    cmd = agy.build_antigravity_launchd_command(
        goal_id="g1",
        agent_id="a1",
        conversation_id="c1",
        resolve_local_executable=False,
    )
    assert cmd[3:6] == ["-m", "loopx.hosts.agy.host_runtime", "install-launchd"]

    hooks = agy.build_antigravity_hooks_template(
        goal_id="g1",
        agent_id="a1",
        conversation_id="c1",
        resolve_local_executable=False,
    )
    assert "loopx.hosts.agy.host_runtime" in hooks["loopx"]["Stop"][0]["command"]
    assert "agy_goal_mode" not in hooks["loopx"]["Stop"][0]["command"]

    plist = build_antigravity_launchd_plist(
        goal_id="g1",
        agent_id="a1",
        conversation_id="c1",
        state_dir=str(tmp_path / "watchdog"),
        log_dir=str(tmp_path / "watchdog-logs"),
        resolve_local_executable=False,
    )
    assert plist["ProgramArguments"][3:6] == [
        "-m",
        "loopx.hosts.agy.host_runtime",
        "heartbeat-tick",
    ]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.hosts.agy.host_runtime",
            "install-launchd",
            "--goal-id",
            "g1",
            "--agent-id",
            "a1",
            "--conversation-id",
            "c1",
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
            "--state-dir",
            str(tmp_path / "watchdog"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True


def test_dsh_consolidated_module_compat_launcher_and_core_staging(
    tmp_path: Path,
) -> None:
    assert dsh.ADAPTER_MODULE == "loopx.hosts.dsh"
    runner = REPO_ROOT / "tests" / "dsh_goal_mode_fake_runner.py"
    request_json = json.dumps(_signed_request())

    module_run = subprocess.run(
        [sys.executable, "-m", "loopx.hosts.dsh", "--dsh-runner", str(runner)],
        input=request_json,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert module_run.returncode == 0, module_run.stderr

    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    launcher = REPO_ROOT / "scripts" / "dsh_turn_host_adapter.py"
    compat_run = subprocess.run(
        [sys.executable, str(launcher), "--dsh-runner", str(runner)],
        input=request_json,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compat_run.returncode == 0, compat_run.stderr
    assert json.loads(module_run.stdout) == json.loads(compat_run.stdout)

    spec = importlib.util.spec_from_file_location(
        "batch3_core_prepare", REPO_ROOT / "packages" / "loopx-core" / "prepare.py"
    )
    assert spec is not None and spec.loader is not None
    prepare_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare_mod)

    staged = prepare_mod.prepare_core_source(tmp_path / "core-stage")
    assert not (staged / "loopx" / "agy_goal_mode").exists()
    assert not (staged / "loopx" / "dsh_goal_mode").exists()
    for rel in (
        "loopx/hosts/agy/__init__.py",
        "loopx/hosts/agy/README.md",
        "loopx/hosts/agy/host_runtime.py",
        "loopx/hosts/agy/launchd_watchdog.py",
        "loopx/hosts/dsh/__init__.py",
        "loopx/hosts/dsh/__main__.py",
        "loopx/hosts/dsh/README.md",
        "loopx/hosts/dsh/host_failure_map.py",
        "loopx/hosts/dsh/turn_host_adapter.py",
    ):
        assert (staged / rel).is_file(), rel
