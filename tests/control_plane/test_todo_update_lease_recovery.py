"""Real provider CLI recovery: diagnostic rejection never grants or changes a lease."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.markdown import render_todo_markdown


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["legacy", "hard_lease"])
def test_released_lease_recovery_through_public_cli(tmp_path: Path, monkeypatch, provider: str, mode: str) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Synthetic display\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "goal-recovery", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(goal_id="goal-recovery", handoff_mode=mode, todos=[{
        "schema_version": "todo_item_v0", "source_section": "Agent Todo", "index": 1,
        "todo_id": "todo_recovery", "text": "Original task", "role": "agent", "status": "open",
        "done": False, "archive_state": "active", "claimed_by": "agent-a", "task_class": "advancement_task",
    }])
    initialize_canonical_authority(runtime, "goal-recovery", projection, state_path=state, provider=provider)
    state.unlink()
    registry_bytes = registry.read_bytes()

    def cli(*args: str, ok: bool = True) -> dict:
        completed = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(runtime), *args,
            "--goal-id", "goal-recovery", "--todo-id", "todo_recovery"],
            capture_output=True, text=True, timeout=45)
        payload = json.loads(completed.stdout)
        assert (completed.returncode == 0) is ok, (payload, completed.stderr)
        return payload

    def snapshot() -> dict:
        result = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-recovery")
        assert result is not None
        return result

    first = cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "first-execution", "--ttl-seconds", "60")
    cli("task-lease", "release", "--owner", "agent-a", "--idempotency-key", "first-execution",
        "--expected-version", str(first["lease"]["version"]))
    before = snapshot()
    display_before = state.read_bytes() if state.exists() else None
    edit = ("todo", "update", "--agent-id", "agent-a", "--text", "Recovered edit")
    for dry in [("--dry-run",), ()]:
        rejected = cli(*edit, *dry, ok=False)
        assert rejected["error_code"] == "handoff_mode_requires_lease"
        assert rejected["handoff_mode"] == mode
        recovery = rejected["recovery"]
        assert recovery["action"] == "acquire_fresh_lease"
        assert recovery["lease_state"] == "released"
        assert recovery["owner_relation"] == "same_owner"
        assert recovery["acquire"]["command"] == "loopx task-lease acquire"
        assert recovery["execution_authority_granted"] is False
        rendered = render_todo_markdown(rejected)
        assert f"handoff_mode: `{mode}`" in rendered
        assert "loopx task-lease acquire" in rendered
        assert "first-execution" not in rendered
        assert "first-execution" not in json.dumps(rejected)
        assert snapshot() == before
        assert (state.read_bytes() if state.exists() else None) == display_before
    observed = cli("task-lease", "inspect")["lease"]
    assert observed["version"] == recovery["acquire"]["expected_version"]
    acquired = cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "new-execution",
        "--expected-version", str(observed["version"]), "--ttl-seconds", str(recovery["acquire"]["ttl_seconds"]))
    proof = ("--task-lease-idempotency-key", "new-execution", "--task-lease-expected-version", str(acquired["lease"]["version"]))
    before = snapshot()
    stale = cli(*edit, "--task-lease-idempotency-key", "first-execution",
        "--task-lease-expected-version", str(observed["version"]), ok=False)
    assert stale["error_code"] == "lease_cas_mismatch"
    assert stale["recovery"]["action"] == "inspect_current_proof"
    assert snapshot() == before
    assert cli(*edit, *proof)["ok"] is True
    assert snapshot()["todos"][0]["text"] == "Recovered edit"
    released = cli("task-lease", "release", "--owner", "agent-a", "--idempotency-key", "new-execution",
        "--expected-version", str(acquired["lease"]["version"]))
    assert released["lease"]["status"] == "released"
    assert cli("task-lease", "inspect")["lease"]["status"] == "released"
    assert registry.read_bytes() == registry_bytes
    # Successful publication may materialize a derived display; it is not an input.
    assert "Recovered edit" in state.read_text(encoding="utf-8")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("claimed,lease_state", [(False, None), (True, None), (True, "expired"), (True, "released"), (True, "active")])
def test_blocked_edit_recovers_by_separate_lifecycle_before_execution(
    tmp_path: Path, monkeypatch, provider: str, claimed: bool, lease_state: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Synthetic display\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "goal-recovery", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}), encoding="utf-8")
    todo = {
        "schema_version": "todo_item_v0", "source_section": "Agent Todo", "index": 1,
        "todo_id": "todo_recovery", "text": "Original acceptance", "role": "agent", "status": "blocked",
        "done": False, "archive_state": "active", "priority": "P2", "task_class": "advancement_task",
        "completion_validation_required": True,
        **({"claimed_by": "agent-a"} if claimed else {}),
    }
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-recovery", handoff_mode="hard_lease", todos=[todo],
    )
    if lease_state is not None:
        projection["leases"] = [{
            "schema_version": "task_lease_v0", "goal_id": "goal-recovery", "todo_id": "todo_recovery",
            "owner": "agent-a", "idempotency_key": "old-execution", "status": "released" if lease_state == "released" else "active",
            "version": 5, "lease_epoch": 3, "expires_at": "2099-01-01T00:00:00Z" if lease_state == "active" else "2020-01-01T00:00:00Z",
            "write_scopes": [],
        }]
    initialize_canonical_authority(runtime, "goal-recovery", projection, state_path=state, provider=provider)
    state.unlink()

    def cli(*args: str, ok: bool = True) -> dict:
        completed = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(runtime), *args,
            "--goal-id", "goal-recovery", "--todo-id", "todo_recovery"],
            capture_output=True, text=True, timeout=45)
        payload = json.loads(completed.stdout)
        assert (completed.returncode == 0) is ok, (payload, completed.stderr)
        return payload

    def snapshot() -> dict:
        result = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-recovery")
        assert result is not None
        return result

    before = snapshot()
    for dry in [("--dry-run",), ()]:
        rejected = cli("todo", "update", "--agent-id", "agent-a", "--status", "open", "--note", "Reviewed note", *dry, ok=False)
        # An active execution retained on an already blocked Todo is divergent;
        # it must not be reclassified as ordinary missing-proof recovery.
        expected_error = "handoff_mode_lease_claim_divergence" if lease_state == "active" else "handoff_mode_requires_lease"
        assert rejected["error_code"] == expected_error
        recovery = rejected["recovery"]
        assert recovery["action"] == "resolve_lifecycle_edit"
        assert recovery["execution_authority_granted"] is False
        if lease_state == "active":
            assert "retry" not in recovery
        else:
            assert "--clear-resume-when" in recovery["retry"]["command"]
            assert "--reason" in render_todo_markdown(rejected)
        assert "acquire" not in recovery
        assert snapshot() == before
    if claimed:
        foreign = cli("todo", "update", "--agent-id", "agent-b", "--status", "open", "--note", "Foreign edit", ok=False)
        assert foreign["error_code"] == "update_owner_mismatch"
        assert "recovery" not in foreign
        assert snapshot() == before
    unknown = cli("todo", "update", "--agent-id", "agent-unknown", "--status", "open", "--note", "Foreign edit", ok=False)
    assert unknown["error_code"] == "actor_not_registered"
    assert snapshot() == before
    stale = cli("todo", "update", "--agent-id", "agent-a", "--status", "open", "--note", "Reviewed note",
        "--task-lease-idempotency-key", "obsolete-execution", "--task-lease-expected-version", "1", ok=False)
    assert "--clear-resume-when" not in stale.get("recovery", {}).get("retry", {}).get("command", "")
    assert snapshot() == before
    if lease_state == "active":
        live = cli("todo", "update", "--agent-id", "agent-a", "--status", "open", "--clear-resume-when",
            "--reason", "Reviewed prerequisites", ok=False)
        assert live["error_code"] == "blocked_lifecycle_active_lease"
        assert snapshot() == before
        return

    reopen = ("todo", "update", "--agent-id", "agent-a", "--status", "open", "--clear-resume-when",
        "--reason", "Reviewed prerequisites; original acceptance remains", "--update-operation-id", "reopen-once")
    assert cli(*reopen, "--dry-run")["status"] == "planned"
    assert snapshot() == before
    applied = cli(*reopen)
    assert applied["blocked_lifecycle_transition"]["execution_authority_granted"] is False
    opened = snapshot()
    assert opened["todos"][0]["status"] == "open"
    for field in ("text", "priority", "task_class", "completion_validation_required"):
        assert opened["todos"][0][field] == before["todos"][0][field]
    assert opened["todos"][0].get("claimed_by") == before["todos"][0].get("claimed_by")
    assert cli(*reopen)["status"] == "replayed"
    assert snapshot() == opened
    if claimed:
        execution = cli("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "fresh-execution",
            "--expected-version", "0" if lease_state is None else "5")
    else:
        execution = cli("todo", "claim", "--agent-id", "agent-a", "--claimed-by", "agent-a",
            "--task-lease-idempotency-key", "fresh-execution")
    version = str(execution["lease"]["version"])
    assert cli("todo", "update", "--agent-id", "agent-a", "--note", "Reviewed note",
        "--task-lease-idempotency-key", "fresh-execution", "--task-lease-expected-version", version)["ok"]
    assert snapshot()["todos"][0]["note"] == "Reviewed note"
    cli("task-lease", "release", "--owner", "agent-a", "--idempotency-key", "fresh-execution", "--expected-version", version)
