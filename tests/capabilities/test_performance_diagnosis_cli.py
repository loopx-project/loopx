"""Preserve CLI compatibility while loading only the requested command."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def run_cli(
    module: str, args: list[str], *, environment: dict[str, str] | None = None,
    forbidden_modules: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    # A common program name makes argparse output directly comparable.
    script = f"""
import sys
sys.argv[0] = 'loopx'
from {module} import main
code = main()
for module in {forbidden_modules!r}:
    assert module not in sys.modules, 'unexpected import: ' + module
raise SystemExit(code)
"""
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        cwd=ROOT, env={**os.environ, **(environment or {}), "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, timeout=45,
    )


@pytest.mark.parametrize("args,code", [
    (["performance-diagnosis", "--help"], 0),
    (["performance-diagnosis", "plan", "--help"], 0),
    (["performance-diagnosis", "inspect", "--help"], 0),
    (["--format=json", "performance-diagnosis", "plan", "--help"], 0),
    (["--registry=missing.json", "--runtime-root=missing", "--cli-bin=loopx", "performance-diagnosis", "inspect", "--help"], 0),
    (["performance-diagnosis"], 2),
    (["performance-diagnosis", "unknown"], 2),
    (["performance-diagnosis", "plan"], 2),
    (["performance-diagnosis", "plan", "--tool", "node-cpu"], 2),
    (["performance-diagnosis", "inspect"], 2),
    (["performance-diagnosis", "inspect", "--profile-json", "missing.json", "--top", "invalid"], 2),
    (["performance-diagnosis", "inspect", "--profile-json", "missing.json", "--unknown"], 2),
    (["performance-diagnosis", "inspect", "--profile-json", "missing.json", "--format", "invalid"], 2),
    (["performance-diagnosis", "inspect", "--profile", "missing.json"], 2),
    (["--format", "invalid", "performance-diagnosis", "inspect", "--help"], 2),
    (["--registry"], 2),
])
def test_parser_matches_full_cli(args: list[str], code: int) -> None:
    full = run_cli("loopx.cli", args)
    entry = run_cli("loopx.entrypoint", args)
    assert full.returncode == entry.returncode == code
    assert (entry.stdout, entry.stderr) == (full.stdout, full.stderr)


@pytest.mark.parametrize("action", ["plan", "inspect"])
@pytest.mark.parametrize("global_format,local_format", [("json", None), ("markdown", None), ("markdown", "json")])
def test_real_dispatch_matches_full_cli(tmp_path: Path, action, global_format, local_format) -> None:
    source = tmp_path / "input.json"
    if action == "plan":
        source.write_text(json.dumps(["node", "work.js"]), encoding="utf-8")
        options = ["--tool=node-cpu", f"--command-json={source}", f"--output-directory={tmp_path / 'capture'}"]
    else:
        source.write_text(json.dumps({
            "startTime": 0, "endTime": 1000,
            "nodes": [{"id": 1, "callFrame": {"functionName": "work"}}],
            "samples": [1], "timeDeltas": [1000],
        }), encoding="utf-8")
        options = [f"--profile-json={source}", "--top=1"]
    args = ["--registry", str(tmp_path / "absent-registry.json"),
            "--runtime-root", str(tmp_path / "absent-runtime"), "--cli-bin", "loopx",
            "--format", global_format, "performance-diagnosis", action, *options]
    if local_format:
        args += ["--format", local_format]
    full = run_cli("loopx.cli", args)
    entry = run_cli("loopx.entrypoint", args, forbidden_modules=("loopx.cli", "loopx.chat"))
    assert full.returncode == entry.returncode == 0, full.stderr + entry.stderr
    assert (entry.stdout, entry.stderr) == (full.stdout, full.stderr)
    assert not (tmp_path / "absent-registry.json").exists()
    assert not (tmp_path / "capture").exists()
    payload = json.loads(entry.stdout) if (local_format or global_format) == "json" else None
    if payload is not None:
        claim = "execution_performed" if action == "plan" else "root_cause_proven"
        assert payload[claim] is False


@pytest.mark.parametrize("content", [None, "{invalid", "{}"])
def test_input_errors_match_full_cli(tmp_path: Path, content) -> None:
    source = tmp_path / "profile.json"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    args = ["performance-diagnosis", "inspect", "--profile-json", str(source), "--format=json"]
    full = run_cli("loopx.cli", args)
    entry = run_cli("loopx.entrypoint", args)
    assert full.returncode == entry.returncode == 1
    assert (entry.stdout, entry.stderr) == (full.stdout, full.stderr)
    assert json.loads(entry.stdout)["ok"] is False


def test_missing_node_has_same_actionable_failure(tmp_path: Path) -> None:
    source = tmp_path / "argv.json"
    source.write_text('["node", "work.js"]', encoding="utf-8")
    args = ["performance-diagnosis", "plan", "--tool=node-cpu", "--command-json", str(source),
            "--output-directory", str(tmp_path / "capture"), "--format=json"]
    environment = {"PATH": str(tmp_path / "empty-path"), "TMPDIR": str(tmp_path)}
    full = run_cli("loopx.cli", args, environment=environment)
    entry = run_cli("loopx.entrypoint", args, environment=environment)
    assert full.returncode == entry.returncode == 1
    assert (entry.stdout, entry.stderr) == (full.stdout, full.stderr)
    payload = json.loads(entry.stdout)
    assert payload["ok"] is False
    assert "Node.js" in payload["error"]


@pytest.mark.parametrize("action", [[], ["plan"], ["inspect"]])
def test_help_needs_no_runtime_or_optional_stack(tmp_path: Path, action) -> None:
    script = f"""
import importlib.abc
import sys

forbidden = ('loopx.cli', 'loopx.control_plane.effect_runtime',
             'loopx.chat', 'loopx.capabilities.auto_research')
class BlockOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('help imported ' + fullname)
sys.meta_path.insert(0, BlockOptionalImports())
def audit(event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'socket.connect', 'socket.getaddrinfo'):
        raise AssertionError('help attempted ' + event)
sys.addaudithook(audit)
from loopx.entrypoint import main
raise SystemExit(main(['performance-diagnosis', *{action!r}, '--help']))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=15,
        env={**os.environ, "PATH": str(tmp_path / "no-executables"), "LOOPX_USAGE_PING": "0"},
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
