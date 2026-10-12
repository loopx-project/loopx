"""Reviewed policy migration must leave ordinary owner work usable, with history."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection


@pytest.fixture(params=["file", "sqlite"])
def migrated_owner(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text("# Synthetic display\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "copy-goal", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id="copy-goal", handoff_mode="hard_lease", todos=[{
        "schema_version": "todo_item_v0", "source_section": "Agent Todo", "index": 1,
        "todo_id": "todo_copy", "role": "agent", "status": "open", "done": False,
        "archive_state": "active", "claimed_by": "agent-a", "task_class": "advancement_task",
        "text": "Original full acceptance", "note": "Original note", "evidence": "Original evidence",
        "required_write_scopes": ["src/**"], "required_capabilities": ["shell"],
    }])
    initialize_canonical_authority(runtime, "copy-goal", projection, state_path=state, provider=request.param)
    state.unlink()  # Canonical reads and writes recover the derived display.

    def cli(*args, ok=True):
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(runtime), *map(str, args),
            "--goal-id", "copy-goal"], capture_output=True, text=True, timeout=60)
        result = json.loads(child.stdout)
        assert (child.returncode == 0) is ok, (result, child.stderr)
        return result

    def snapshot():
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="copy-goal", include_leases=True)

    acquired = cli("task-lease", "acquire", "--todo-id", "todo_copy", "--owner", "agent-a",
        "--idempotency-key", "previous-execution", "--ttl-seconds", "60", "--write-scope", "src/**")
    released = cli("task-lease", "release", "--todo-id", "todo_copy", "--owner", "agent-a",
        "--idempotency-key", "previous-execution", "--expected-version", acquired["lease"]["version"])
    plan = tmp_path / "migration.json"
    preview = cli("handoff-mode", "plan-migration", "--mode", "soft_claim", "--plan", plan)
    migration = ("handoff-mode", "migrate", "--plan", plan, "--plan-sha256", preview["plan_sha256"], "--execute")
    before = snapshot()
    applied = cli(*migration)
    assert applied["status"] == "applied" and Path(applied["backup_path"]).is_file()
    assert snapshot()["todos"] == before["todos"] and snapshot()["leases"] == before["leases"]
    return registry, state, cli, snapshot, released["lease"], migration


def test_migrated_owner_cli_and_app_continue_without_rewriting_history(migrated_owner, monkeypatch):
    registry, state, cli, snapshot, lease, migration = migrated_owner
    registry_bytes = registry.read_bytes()
    original = snapshot()
    edit = ("todo", "update", "--todo-id", "todo_copy", "--agent-id", "agent-a", "--note", "CLI observation")
    for proof in [("--task-lease-idempotency-key", "previous-execution"),
                  ("--task-lease-idempotency-key", "previous-execution", "--task-lease-expected-version", str(lease["version"]))]:
        rejected = cli(*edit, *proof, ok=False)
        assert rejected["error_code"] == "handoff_mode_requires_lease"
        assert "without either task-lease proof flag" in rejected["recovery"]["reason"]
        assert "acquire" not in rejected["recovery"]
        assert snapshot() == original
    assert cli(*edit, "--dry-run")["status"] == "planned"
    assert snapshot() == original
    attempt = (*edit, "--update-operation-id", "continued-copy",
               "--update-expected-provider-revision", original["provider_revision"])
    assert cli(*attempt)["status"] == "applied"
    after_cli = snapshot()
    assert cli(*attempt)["status"] == "replayed" and snapshot() == after_cli

    service = ChatActionService(store=ChatActionStore(registry.parent / "actions"), registry_path=registry)
    proposal = service.preview({"action_kind": "todo.update", "summary": "Review an observation",
        "context": {}, "idempotency_key": "app-copy", "normalized_parameters": {
            "goal_id": "copy-goal", "todo_id": "todo_copy", "agent_id": "agent-a", "note": "App observation"}})
    assert proposal["status"] == "preview_ready" and snapshot() == after_cli
    with monkeypatch.context() as fault:
        def lost_response(*args, **kwargs):
            raise ConnectionError("Synthetic action receipt loss")
        fault.setattr(service.store, "apply", lost_response)
        with pytest.raises(ConnectionError):
            service.apply(proposal["proposal_id"])
    assert snapshot()["todos"][0]["note"] == "App observation"
    cli("todo", "update", "--todo-id", "todo_copy", "--agent-id", "agent-a", "--note", "Later observation")
    current = snapshot()
    state.unlink()
    recovered = service.apply(proposal["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["canonical_status"] == "replayed"
    assert recovered["receipt"]["projection_verified"] is True
    assert snapshot() == current and "Later observation" in unquote(state.read_text())
    assert snapshot()["leases"] == original["leases"]
    preserved = {k: v for k, v in original["todos"][0].items() if k not in {"note", "updated_at", "last_actor_agent_id"}}
    assert {k: snapshot()["todos"][0][k] for k in preserved} == preserved
    assert cli(*migration)["status"] == "replayed" and snapshot() == current
    assert registry.read_bytes() == registry_bytes
    # Returning to hard lease restores its execution requirement; soft copy
    # admission did not consume, replace or silently upgrade the retained grant.
    plan = registry.parent / "hard-again.json"
    preview = cli("handoff-mode", "plan-migration", "--mode", "hard_lease", "--plan", plan)
    cli("handoff-mode", "migrate", "--plan", plan, "--plan-sha256", preview["plan_sha256"], "--execute")
    hard = snapshot()
    assert cli(*edit, ok=False)["error_code"] == "handoff_mode_requires_lease"
    assert snapshot() == hard
