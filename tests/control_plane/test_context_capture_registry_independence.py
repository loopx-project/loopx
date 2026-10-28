"""Explicit capture recovery has no dependency on unrelated Goal routing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx import paths
from test_codex_context_lifecycle import project, setup


@pytest.fixture
def conflicting_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOPX_REGISTRY", raising=False)
    for root in (paths.DEFAULT_RUNTIME_ROOT, paths.LEGACY_RUNTIME_ROOT):
        marker = root / "owned-state" / "marker.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"preserved":true}\n')
    assert paths.default_runtime_route()["status"] == "conflict"
    roots = (paths.DEFAULT_RUNTIME_ROOT, paths.LEGACY_RUNTIME_ROOT)

    def snapshot():
        return {str(p.relative_to(root.parent)): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}

    before = snapshot()
    yield snapshot
    assert snapshot() == before, "a captured read must not select, repair or mutate either runtime"


def run_cli(module, cwd, *args):
    return subprocess.run([sys.executable, "-m", module, "--format", "json", *args], cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, encoding="utf-8", timeout=30)


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
def test_explicit_capture_and_selected_details_ignore_conflicting_defaults(tmp_path, conflicting_defaults, module):
    original = {"goal_id": "fixture", "requirements": "Preserve the final stop.", "counter": 9007199254740993}
    raw = json.dumps(original, ensure_ascii=False).encode()
    capture = tmp_path / "original.json"
    capture.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    command = ("quota", "context-read", "--capture-file", str(capture), "--capture-sha256", digest)
    result = run_cli(module, tmp_path, *command)
    assert result.returncode == 0, result.stdout + result.stderr
    page = json.loads(result.stdout)
    assert page["ok"] and page["eof"] and page["sha256"] == digest
    assert json.loads(page["content"]) == original
    result = run_cli(module, tmp_path, *command, "--select", "/counter", "--select", "/requirements")
    assert result.returncode == 0, result.stdout + result.stderr
    selected = json.loads(json.loads(result.stdout)["content"])["selections"]
    assert [row["value"] for row in selected] == [9007199254740993, original["requirements"]]
    wrong = run_cli(module, tmp_path, *command[:-1], "0" * 64)
    assert wrong.returncode == 1 and not json.loads(wrong.stdout)["ok"]
    assert "digest mismatch" in json.loads(wrong.stdout)["reason"].lower()
    partial = run_cli(module, tmp_path, "quota", "context-read", "--capture-file", str(capture))
    assert partial.returncode == 1 and "exact SHA-256" in json.loads(partial.stdout)["reason"]
    partial = run_cli(module, tmp_path, "quota", "context-read", "--capture-sha256", digest)
    assert partial.returncode == 1 and "exact SHA-256" in json.loads(partial.stdout)["reason"]
    assert capture.read_bytes() == raw


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
def test_explicit_native_location_recovers_without_goal_routing(tmp_path, monkeypatch, conflicting_defaults, module):
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    setup(native, monkeypatch)
    original, _ = project(native)
    result = run_cli(module, native, "quota", "context-read", "--codex-context-dir", str(native),
        "--goal-id", "fixture", "--agent-id", "agent")
    assert result.returncode == 0, result.stdout + result.stderr
    page = json.loads(result.stdout)
    assert page["eof"] and json.loads(page["content"]) == original
    for goal, agent in [("other", "agent"), ("fixture", "other")]:
        refused = run_cli(module, native, "quota", "context-read", "--codex-context-dir", str(native),
            "--goal-id", goal, "--agent-id", agent)
        assert refused.returncode == 1 and "no original capture for this Goal/Agent" in json.loads(refused.stdout)["reason"]
    monkeypatch.setenv("CODEX_THREAD_ID", "other-session")
    refused = run_cli(module, native, "quota", "context-read", "--codex-context-dir", str(native),
        "--goal-id", "fixture", "--agent-id", "agent")
    assert refused.returncode == 1 and "same native session" in json.loads(refused.stdout)["reason"]


@pytest.mark.parametrize("command", [("quota", "context-read"),
    ("quota", "should-run", "--goal-id", "fixture"), ("quota", "spend-slot", "--goal-id", "fixture")])
def test_implicit_source_and_authority_commands_preserve_route_conflict(tmp_path, conflicting_defaults, command):
    if command[1] != "context-read":
        # Capture flags alone cannot classify a business command as read-only.
        command = (*command, "--capture-file", str(tmp_path / "unrelated.json"), "--capture-sha256", "0" * 64)
    result = run_cli("loopx.entrypoint", tmp_path, *command)
    assert result.returncode != 0
    assert "Both default LoopX runtime roots contain state" in result.stdout + result.stderr
