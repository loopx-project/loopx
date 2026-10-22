"""Ordinary reads require migration; explicit cold import retains source access."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import (
    legacy_coordination_writer_fence_path,
)
from test_cold_source_import_cli import canonical_todos, workspace


def _cli(tmp_path: Path, runtime: Path, receiver: Path, env: dict, *args: str):
    child = subprocess.run(
        [sys.executable, "-c",
         "import loopx,runpy,sys; print(loopx.__file__,file=sys.stderr); "
         "runpy.run_module('loopx.cli',run_name='__main__')",
         "--registry", str(tmp_path / "project/.loopx/registry.json"),
         "--runtime-root", str(runtime), "--format", "json", *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert str(receiver / "loopx/__init__.py") in child.stderr
    assert "Traceback" not in child.stderr, child.stderr
    return child.returncode, json.loads(child.stdout)


def _reason_codes(value):
    if isinstance(value, dict):
        return {
            code for key, child in value.items()
            for code in ([child] if key in {"code", "reason_code", "error_code"}
                         and isinstance(child, str) else _reason_codes(child))
        }
    if isinstance(value, list):
        return set().union(*(_reason_codes(child) for child in value))
    return set()


@pytest.mark.parametrize("args", [
    ("todo", "list", "--goal-id", "cold"),
    ("status", "--goal-id", "cold"),
    ("task-lease", "inspect", "--goal-id", "cold", "--todo-id", "todo_current"),
    ("quota", "should-run", "--goal-id", "cold", "--agent-id", "agent-a", "--codex-app"),
])
def test_ordinary_cli_refuses_unmigrated_goal_without_importing_it(
    tmp_path, monkeypatch, args,
):
    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    registry_path = tmp_path / "project/.loopx/registry.json"
    registry = json.loads(registry_path.read_text())
    registry["goals"][0].update(
        domain="software", adapter={"kind": "read_only_project_map_v0"},
    )
    registry_path.write_text(json.dumps(registry))
    original = state.read_bytes()
    _, result = _cli(tmp_path, runtime, receiver, env, *args)
    assert "canonical_authority_required" in _reason_codes(result), result
    assert state.read_bytes() == original
    assert not legacy_coordination_writer_fence_path(
        runtime_root=runtime, goal_id="cold",
    ).exists()
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_explicit_import_retains_full_source_and_enables_ordinary_reads(
    tmp_path, monkeypatch, provider,
):
    cli, state, backup, body, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    original = state.read_bytes()
    prepared = cli(
        "prepare-import", "--backup-manifest", backup["manifest_path"],
        "--provider", provider, "--target-handoff-mode", "hard_lease",
    )["cold_import"]
    assert prepared["status"] == "prepared", prepared
    assert state.read_bytes() == original
    applied = cli(
        "apply-import", "--plan-sha256", prepared["plan_sha256"],
        "--writers-stopped", "--execute",
    )["cold_import"]
    assert applied["status"] == "applied", applied
    assert applied["execution_authority_granted"] is False
    assert state.read_bytes() == original
    snapshot = canonical_todos(runtime, receiver, env, tmp_path)
    records = {row["todo_id"]: row for row in snapshot["todos"]}
    assert set(records) == {"todo_current", "todo_archived"}
    assert records["todo_current"]["text"] == body
    assert records["todo_archived"]["evidence"] == "original"
    code, listed = _cli(
        tmp_path, runtime, receiver, env,
        "todo", "list", "--goal-id", "cold", "--todo-id", "todo_current",
    )
    assert code == 0 and listed["ok"], listed
    assert listed["todo"]["text"] == body
    assert listed["authority_read"]["legacy_fallback_used"] is False
    replay = cli(
        "recover-import", "--plan-sha256", prepared["plan_sha256"], "--execute",
    )["cold_import"]
    assert replay["status"] == "replayed", replay
    assert replay["cursor"] == applied["cursor"]
    assert canonical_todos(runtime, receiver, env, tmp_path) == snapshot
