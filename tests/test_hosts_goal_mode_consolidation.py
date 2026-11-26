"""Contracts protecting the consolidation of host adapters under loopx/hosts/."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.hosts import read_host_asset
from loopx.hosts.kiro_cli import mcp_server_script
from loopx.hosts.opencode import plugin_source as opencode_plugin_source
from loopx.hosts.opencode import runtime_source as opencode_runtime_source
from loopx.hosts.opencode2 import worker_source as opencode2_worker_source
from loopx.hosts.pi import extension_source as pi_extension_source
from loopx.hosts.pi import runtime_source as pi_runtime_source

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_retired_goal_mode_packages_live_under_hosts() -> None:
    for retired in (
        "kiro_cli_goal_mode",
        "opencode_goal_mode",
        "opencode2_goal_mode",
        "pi_goal_mode",
    ):
        assert not (REPO_ROOT / "loopx" / retired).exists(), retired

    for host_dir in ("kiro_cli", "opencode", "opencode2", "pi"):
        assert (REPO_ROOT / "loopx" / "hosts" / host_dir / "__init__.py").is_file()
        assert (REPO_ROOT / "loopx" / "hosts" / host_dir / "README.md").is_file()


def test_shared_host_asset_loader_reads_consolidated_assets_and_fails_closed() -> None:
    assert "LoopXGoalPlugin" in opencode_plugin_source()
    assert "createLoopxGoalPlugin" in opencode_runtime_source()
    assert "STATE_SCHEMA_VERSION" in opencode2_worker_source()
    assert "loopx_goal_activate" in pi_extension_source()
    assert "createGoalLoop" in pi_runtime_source()

    with pytest.raises(RuntimeError, match="host asset package is unavailable"):
        read_host_asset(None, "loopx-goal.ts")


def test_kiro_mcp_server_resolves_package_parent_without_pythonpath(
    tmp_path: Path,
) -> None:
    script = mcp_server_script()
    assert script == REPO_ROOT / "loopx" / "hosts" / "kiro_cli" / "mcp_server.py"
    assert script.is_file()

    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH"
    }
    probe = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('kiro_mcp_probe', {str(script)!r})\n"
        "assert spec is not None and spec.loader is not None\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "assert mod.PACKAGE_PARENT.resolve() == __import__('pathlib').Path("
        f"{str(REPO_ROOT)!r}).resolve()\n"
        "assert mod.KIRO_CLI_RUNTIME_PROFILE == 'kiro_cli'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
