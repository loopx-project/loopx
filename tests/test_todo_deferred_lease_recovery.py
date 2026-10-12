"""Public CLI: resume a wait before acquiring execution, without rewriting history."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.markdown import render_todo_markdown


def fixture(tmp_path: Path, monkeypatch, provider: str, lease_state: str | None, *, mode="hard_lease", **fields):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Synthetic display\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "goal-recovery", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}), encoding="utf-8")
    todo = {"schema_version": "todo_item_v0", "source_section": "Agent Todo", "index": 1,
        "todo_id": "todo_recovery", "text": "Original acceptance", "role": "agent", "status": "deferred",
        "done": True, "archive_state": "active", "priority": "P2", "task_class": "advancement_task",
        "completion_validation_required": True, "claimed_by": "agent-a",
        "resume_when": "resume_at:2020-01-01T00:00:00Z", **fields}
    if todo.get("claimed_by") is None:
        todo.pop("claimed_by", None)
    projection = build_todo_runtime_shadow_projection(goal_id="goal-recovery", handoff_mode=mode, todos=[todo])
    if lease_state is not None:
        projection["leases"] = [{"schema_version": "task_lease_v0", "goal_id": "goal-recovery",
            "todo_id": "todo_recovery", "owner": "agent-a", "idempotency_key": "old-execution",
            "status": "released" if lease_state == "released" else "active", "version": 5, "lease_epoch": 3,
            "expires_at": "2099-01-01T00:00:00Z" if lease_state == "active" else "2020-01-01T00:00:00Z",
            "write_scopes": []}]
    initialize_canonical_authority(runtime, "goal-recovery", projection, state_path=state, provider=provider)
    state.unlink()

    def cli(*args: str, ok: bool = True):
        completed = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(runtime), *args,
            "--goal-id", "goal-recovery", "--todo-id", "todo_recovery"],
            capture_output=True, text=True, timeout=45)
        result = json.loads(completed.stdout)
        assert (completed.returncode == 0) is ok, (result, completed.stderr)
        return result

    def snapshot():
        result = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-recovery")
        assert result is not None
        lease = cli("task-lease", "inspect")["lease"]
        result["leases"] = [] if lease is None else [lease]
        return result

    return cli, snapshot


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode,lease_state", [
    ("hard_lease", None), ("hard_lease", "released"), ("hard_lease", "expired"),
    ("hard_lease", "active"), ("legacy", "released"),
    ("soft_claim", "released"), ("soft_claim", "expired"),
])
def test_deferred_bundled_edit_names_executable_lifecycle_recovery(tmp_path, monkeypatch, provider, mode, lease_state):
    cli, snapshot = fixture(tmp_path, monkeypatch, provider, lease_state, mode=mode)
    before = snapshot()
    # A fresh lease cannot be acquired while this Todo remains deferred.
    if mode != "soft_claim":
        acquire = cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "premature", ok=False)
        assert acquire["error_code"] == "todo_not_open"
        assert snapshot() == before
    bundled = ("todo", "update", "--agent-id", "agent-a", "--status", "open",
        "--clear-resume-when", "--note", "Reviewed note", "--evidence", "artifact:reviewed")
    for dry in [("--dry-run",), ()]:
        rejected = cli(*bundled, *dry, ok=False)
        recovery = rejected["recovery"]
        assert recovery["action"] == "resolve_lifecycle_edit"
        assert recovery["execution_authority_granted"] is False
        assert "acquire" not in recovery
        if lease_state == "active":
            assert "retry" not in recovery
        else:
            assert "--clear-resume-when" in recovery["retry"]["command"]
            assert "deferred" in render_todo_markdown(rejected)
            assert "Do not bundle notes, evidence" in recovery["reason"]
        assert snapshot() == before
    # A stale or partial execution proof cannot become administrative authority.
    for proof in [("--task-lease-idempotency-key", "old-execution"),
        ("--task-lease-idempotency-key", "old-execution", "--task-lease-expected-version", "5")]:
        stale = cli(*bundled, *proof, ok=False)
        assert "retry" not in stale.get("recovery", {})
        assert snapshot() == before
    reopen = ("todo", "update", "--agent-id", "agent-a", "--status", "open", "--clear-resume-when",
        "--reason", "Reviewed wait; original acceptance remains", "--update-operation-id", "resume-once",
        "--update-expected-provider-revision", before["provider_revision"])
    if lease_state == "active":
        assert cli(*reopen, ok=False)["error_code"] == "deferred_resume_active_lease"
        assert snapshot() == before
        return
    assert cli(*reopen, "--dry-run")["status"] == "planned"
    assert snapshot() == before
    result = cli(*reopen)
    assert result["deferred_resume_transition"]["execution_authority_granted"] is False
    opened = snapshot()
    task = opened["todos"][0]
    assert task["status"] == "open" and not task["done"] and "resume_when" not in task
    for field in ("text", "claimed_by", "priority", "task_class", "completion_validation_required"):
        assert task[field] == before["todos"][0][field]
    if lease_state is not None:
        assert opened["leases"][0]["status"] == "released"
        assert opened["leases"][0]["version"] == 5
        assert opened["leases"][0]["lease_epoch"] == 3
        assert opened["leases"][0]["idempotency_key"] == "old-execution"
    assert cli(*reopen)["status"] == "replayed"
    assert snapshot() == opened
    if mode == "soft_claim":
        assert "do not acquire a lease in soft_claim" in recovery["reason"]
        assert cli("todo", "update", "--agent-id", "agent-a", "--note", "Reviewed note")["ok"]
        edited = snapshot()
        assert edited["todos"][0]["note"] == "Reviewed note"
        assert edited["leases"] == opened["leases"]
        cli("todo", "update", "--agent-id", "agent-a", "--evidence", "artifact:reviewed", ok=False)
        assert snapshot() == edited
        return
    # Reopening alone still cannot authorize the pending copy/evidence edit.
    cli("todo", "update", "--agent-id", "agent-a", "--note", "Reviewed note", ok=False)
    assert snapshot() == opened
    lease = cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "fresh-execution",
        "--expected-version", "0" if lease_state is None else "5")["lease"]
    assert lease["lease_epoch"] == (1 if lease_state is None else 4)
    proof = ("--task-lease-idempotency-key", "fresh-execution", "--task-lease-expected-version", str(lease["version"]))
    assert cli("todo", "update", "--agent-id", "agent-a", "--note", "Reviewed note",
        "--evidence", "artifact:reviewed", *proof)["ok"]
    assert snapshot()["todos"][0]["note"] == "Reviewed note"
    cli("task-lease", "release", "--owner", "agent-a", "--idempotency-key", "fresh-execution",
        "--expected-version", str(lease["version"]))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fields,actor,code", [
    ({}, "agent-b", "update_owner_mismatch"), ({}, "unknown", "actor_not_registered"),
    ({"excluded_agents": ["agent-a"]}, "agent-a", "actor_excluded"),
    ({"bound_agent": "agent-b"}, "agent-a", "bound_agent_mismatch"),
])
def test_deferred_recovery_does_not_relax_actor_admission(tmp_path, monkeypatch, provider, fields, actor, code):
    cli, snapshot = fixture(tmp_path, monkeypatch, provider, "released", **fields)
    before = snapshot()
    for extra in [("--note", "Bundled"), ()]:
        rejected = cli("todo", "update", "--agent-id", actor, "--status", "open", "--clear-resume-when",
            "--reason", "Reviewed wait", *extra, ok=False)
        assert rejected["error_code"] == code
        assert "recovery" not in rejected
        assert snapshot() == before
