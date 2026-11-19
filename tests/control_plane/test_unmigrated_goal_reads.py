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
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
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


def test_global_todos_retains_healthy_lanes_with_a_stopped_unmigrated_goal(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = runtime / "registry.global.json"
    goals = []
    state_by_goal = {}
    for goal_id in ("ready", "stopped"):
        project = tmp_path / goal_id
        project.mkdir()
        state = project / "ACTIVE_GOAL_STATE.md"
        state.write_text(
            "---\nstatus: active\nowner_mode: goal\n---\n\n"
            "## Agent Todo\n\n- [ ] Verify the registered work.\n"
            f"  <!-- loopx:todo todo_id=todo_{goal_id} role=agent "
            "task_class=advancement_task status=open -->\n"
        )
        goal = {
            "id": goal_id, "repo": str(project), "state_file": state.name, "status": "active",
            "domain": "software", "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
            "coordination": {"registered_agents": ["agent-a"]},
        }
        if goal_id == "stopped":
            goal["activation"] = {
                "schema_version": "loopx_goal_activation_v1", "state": "stopped",
                "updated_at": "2026-01-01T00:00:00Z", "actor_kind": "owner", "reason": "Stop this fixture.",
            }
        else:
            initialize_canonical_source_fixture(runtime, goal_id, state_path=state, provider="sqlite")
        goals.append(goal)
        state_by_goal[goal_id] = state
    registry.write_text(json.dumps({
        "schema_version": "0.1", "registry_role": "global-local",
        "common_runtime_root": str(runtime), "goals": goals,
    }))
    original = registry.read_bytes(), state_by_goal["stopped"].read_bytes()

    code, status = run_json_cli_result("status", registry_path=registry)
    assert code == 1 and status["ok"] is False
    assert [(e["goal_id"], e["code"]) for e in status["contract"]["error_diagnostics"]] == [
        ("stopped", "canonical_authority_required"),
    ]
    assert any(row["goal_id"] == "loopx-contract" for row in status["attention_queue"]["items"])

    code, observed = run_json_cli_result("global-todos", registry_path=registry)
    assert code == 0 and observed["ok"] is True, observed
    assert {(todo["goal_id"], todo["todo_id"]) for todo in observed["todos"]} == {("ready", "todo_ready")}
    assert [(row["goal_id"], row["reason_code"]) for row in observed["source_warnings"]] == [
        ("stopped", "quota_unavailable"),
    ]
    envelope = observed["projection_envelope"]
    assert envelope["complete"] is False
    assert envelope["coverage"]["expected_count"] == 2
    assert envelope["coverage"]["included_count"] == 1
    quota = next(row for row in envelope["sources"] if row["source_id"] == "goal_quota")
    assert (quota["read_status"], quota["item_count"], quota["unreadable_count"]) == ("read", 1, 1)
    assert (registry.read_bytes(), state_by_goal["stopped"].read_bytes()) == original
    assert not legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="stopped").exists()


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
