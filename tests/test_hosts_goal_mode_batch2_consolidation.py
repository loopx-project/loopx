"""Contracts protecting Batch 2 host consolidation under loopx/hosts/."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from loopx.hosts.kunluncode import cli as kunlun_cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_batch2_retired_goal_mode_packages_live_under_hosts() -> None:
    for retired in (
        "claude_goal_mode",
        "kunluncode_goal_mode",
        "zcode_goal_mode",
    ):
        assert not (REPO_ROOT / "loopx" / retired).exists(), retired

    for host_dir in ("claude", "kunluncode", "zcode"):
        assert (REPO_ROOT / "loopx" / "hosts" / host_dir / "__init__.py").is_file()
        assert (REPO_ROOT / "loopx" / "hosts" / host_dir / "README.md").is_file()

    for asset in (
        "claude/.claude-plugin/plugin.json",
        "claude/commands/loopx.md",
        "claude/hooks/hooks.json",
        "claude/settings.example.json",
        "zcode/contract.json",
        "zcode/contract.ts",
        "zcode/images/native-error-mobile.png",
        "zcode/images/native-quota-desktop.png",
    ):
        assert (REPO_ROOT / "loopx" / "hosts" / asset).is_file(), asset


def test_kunluncode_source_root_resolves_repository_root() -> None:
    assert kunlun_cli._source_root() == REPO_ROOT


def test_claude_direct_scripts_resolve_repo_and_plugin_root_without_pythonpath(
    tmp_path: Path,
) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH"
    }
    expected_plugin = REPO_ROOT / "loopx" / "hosts" / "claude"

    for rel, check_plugin in (
        ("hooks/goal_state.py", False),
        ("mcp/loopx_mcp.py", False),
        ("scripts/install.py", True),
        ("scripts/connect.py", True),
    ):
        script = expected_plugin / rel
        assert script.is_file(), script
        checks = [
            "import importlib.util",
            "from pathlib import Path",
            f"spec = importlib.util.spec_from_file_location('claude_probe', {str(script)!r})",
            "assert spec is not None and spec.loader is not None",
            "mod = importlib.util.module_from_spec(spec)",
            "spec.loader.exec_module(mod)",
            f"assert mod.REPO_ROOT.resolve() == Path({str(REPO_ROOT)!r}).resolve()",
        ]
        if check_plugin:
            checks.append(
                f"assert mod.PLUGIN_ROOT.resolve() == Path({str(expected_plugin)!r}).resolve()"
            )
        completed = subprocess.run(
            [sys.executable, "-c", "\n".join(checks)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"{rel}: {completed.stderr}{completed.stdout}"
