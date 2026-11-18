"""Host adapters retain the imported release across real child processes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import textwrap
import venv

import pytest

from loopx import __version__


REPO = Path(__file__).resolve().parents[1]
CLAUDE = REPO / "loopx" / "claude_goal_mode"
CALLERS = (
    "goal_mcp",
    "kunlun_controller",
    "kunlun_cli",
    "scripts/goalmode_cmd.py",
    "scripts/connect.py",
    "hooks/goal_policy.py",
    "statusline/goal_status.py",
)


@pytest.fixture(scope="module")
def mcp_only_python(tmp_path_factory):
    runtime = tmp_path_factory.mktemp("mcp-only-python")
    venv.EnvBuilder(with_pip=False).create(runtime)
    interpreter = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    probe = subprocess.run(
        [str(interpreter), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    # Reuse the test environment's MCP dependencies without processing its
    # editable-install .pth files. The fixture must have no importable LoopX.
    packages = Path(probe.stdout.strip())
    (packages / "mcp-test-dependencies.pth").write_text(
        sysconfig.get_path("purelib") + "\n", encoding="utf-8",
    )
    environment = {**os.environ, "PYTHONPATH": ""}
    probe = subprocess.run(
        [str(interpreter), "-P", "-c",
         "from mcp.server.fastmcp import FastMCP; "
         "import importlib.util; assert importlib.util.find_spec('loopx') is None"],
        cwd=runtime, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    return interpreter


def _host_invocation(interpreter: Path, project: Path, environment: dict[str, str], caller: str):
    # The host imports the intended source release explicitly, as the managed
    # MCP launcher does. This insertion deliberately does not reach children.
    code = textwrap.dedent("""\
        import importlib.util, json, pathlib, subprocess, sys
        from mcp.server.fastmcp import FastMCP
        source, caller = sys.argv[1:3]
        sys.path.insert(0, source)
        if caller == "goal_mcp":
            from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
            control = GoalModeMCPControlPlane(
                GoalModeMCPConfig("provenance", "claude_code", "claude_code"), lambda: {})
            print(control.run_cli(["version"]))
        elif caller == "kunlun_controller":
            from loopx.kunluncode_goal_mode.control_plane import LoopXControlPlane
            control = LoopXControlPlane(pathlib.Path.cwd(), {
                "registry": str(pathlib.Path.cwd() / "unused-registry.json"),
                "goal_id": "provenance", "agent_id": "worker"})
            print(json.dumps(control._run_json(["version"])))
        elif caller == "kunlun_cli":
            from loopx.kunluncode_goal_mode.cli import _loopx_prefix
            result = subprocess.run([*_loopx_prefix(), "--format", "json", "version"],
                capture_output=True, text=True, timeout=30)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
        else:
            adapter = pathlib.Path(source) / "loopx" / "claude_goal_mode"
            sys.path.insert(0, str(adapter / "hooks"))
            spec = importlib.util.spec_from_file_location("host_script", adapter / caller)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            prefix = module.gh_prefix if caller.endswith("goalmode_cmd.py") else module._gh_prefix
            result = subprocess.run([*prefix(), "--format", "json", "version"],
                capture_output=True, text=True, timeout=30)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
        """)
    return subprocess.run(
        [str(interpreter), "-P", "-c", code, str(REPO), caller],
        cwd=project, env=environment, capture_output=True, text=True, timeout=45,
    )


@pytest.mark.parametrize("caller,ambient", [
    *((caller, "no_loopx") for caller in CALLERS),
    ("goal_mcp", "competing_cli"),
    ("goal_mcp", "shadow_packages"),
])
def test_host_children_keep_source_release(mcp_only_python, tmp_path, caller, ambient):
    project = tmp_path / "foreign-project"
    project.mkdir()
    environment = {**os.environ, "PYTHONPATH": "", "PATH": ""}
    invoked = tmp_path / "foreign-cli-invoked"
    if ambient == "competing_cli":
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        trap = bin_dir / "foreign_cli.py"
        trap.write_text(
            f"from pathlib import Path\nPath({str(invoked)!r}).write_text('invoked')\n"
            "print('{\"ok\": true, \"schema_version\": \"foreign_release\"}')\n",
            encoding="utf-8",
        )
        executable = bin_dir / ("loopx.cmd" if os.name == "nt" else "loopx")
        if os.name == "nt":
            executable.write_text(f'@"{sys.executable}" "{trap}" %*\n', encoding="utf-8")
        else:
            executable.write_text(f"#!{sys.executable}\n" + trap.read_text(), encoding="utf-8")
            executable.chmod(0o755)
        environment["PATH"] = str(bin_dir)
    elif ambient == "shadow_packages":
        inherited = tmp_path / "inherited-source"
        for root in (project, inherited):
            package = root / "loopx"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "raise RuntimeError('foreign LoopX package imported')\n", encoding="utf-8",
            )
        environment["PYTHONPATH"] = str(inherited)

    result = _host_invocation(mcp_only_python, project, environment, caller)

    assert not invoked.exists(), "adapter invoked the competing PATH executable"
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "loopx_version_v0"
    assert payload["version"] == __version__


def test_explicit_native_controller_command_override_is_preserved(tmp_path):
    from loopx.kunluncode_goal_mode.control_plane import LoopXControlPlane

    launcher = tmp_path / "explicit.py"
    launcher.write_text("print('{\"ok\": true, \"route\": \"explicit\"}')\n", encoding="utf-8")
    control = LoopXControlPlane(tmp_path, {
        "registry": str(tmp_path / "registry.json"), "goal_id": "g", "agent_id": "a",
    }, command_prefix=[sys.executable, str(launcher)])

    assert control._run_json(["version"]) == {"ok": True, "route": "explicit"}
