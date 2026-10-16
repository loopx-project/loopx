"""Public handover -> recipient edit/completion, including projection recovery."""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.local_authority import CanonicalAuthorityRequired
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.task_lease import transfer_task_lease

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_claim_transfer_recipient_and_projection_recovery(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "claim-transfer", "todo_transfer"
    state.write_text("# Synthetic handover\n\nNarrative survives projection.\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": target, "role": "agent", "status": "open", "done": False,
        "text": "Hand over and finish the same canonical work", "archive_state": "active", "source_section": "Agent Todo",
        "index": 1, "task_class": "advancement_task", "claimed_by": "agent-a",
    }])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    cli_repo = Path(os.environ.get("LOOPX_LEASE_REPLAY_REPO", REPO))

    def cli(command, *args, expected_exit=0):
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            *command, "--goal-id", goal, "--todo-id", target, *args], cwd=cli_repo,
            capture_output=True, text=True, timeout=60, check=False)
        assert child.returncode == expected_exit, child.stdout + child.stderr
        return json.loads(child.stdout)

    transfer = ["--owner", "agent-a", "--idempotency-key", "source-a", "--expected-version", "1",
                "--new-owner", "agent-b", "--new-idempotency-key", "receiver-b", "--ttl-seconds", "600"]
    try:
        acquired = cli(["task-lease", "acquire"], "--owner", "agent-a", "--idempotency-key", "source-a",
                       "--expected-version", "0", "--ttl-seconds", "600", "--write-scope", "src/**")
        assert acquired["lease"]["version"] == 1
        # The old lease-only command keeps its exact authority boundary.
        old = cli(["task-lease", "transfer"], *transfer, expected_exit=1)
        assert old["error_code"] == "owner_conflicts_with_claim"
        # Make only the display unavailable, after canonical authority exists.
        original_text = state.read_text()
        state.unlink()
        state.mkdir()
        result = cli(["task-lease", "transfer"], *transfer, "--transfer-claim")
        assert result["ok"] and result["transferred"] and result["transfer_claim"]
        assert result["claimed_by"] == "agent-b" and result["todo_changed"]
        assert result["lease"]["version"] == result["lease"]["lease_epoch"] == 2
        assert result["source_authority"] == provider + "_v0"
        assert result["projection_delivery"] == "pending"
        assert result["projection_outbox"]["retry_business_mutation"] is False
        assert cli(["todo", "list"])["todo"]["claimed_by"] == "agent-b"
        state.rmdir()
        state.write_text(original_text)
        replay = cli(["task-lease", "transfer"], *transfer, "--transfer-claim")
        assert replay["status"] == "replayed"
        assert replay["original_receipt"] == result["original_receipt"]
        assert replay["provider_revision"] == result["provider_revision"]
        assert replay["projection_delivery"] in {"delivered", "current"}
        assert "Narrative survives projection." in state.read_text()
        assert "claimed_by=agent-b" in state.read_text()
        stale = cli(["todo", "update"], "--agent-id", "agent-a", "--note", "Old executor",
                    "--task-lease-idempotency-key", "source-a", "--task-lease-expected-version", "1", expected_exit=1)
        assert not stale["ok"]
        updated = cli(["todo", "update"], "--agent-id", "agent-b", "--note", "Recipient continued the work",
                      "--task-lease-idempotency-key", "receiver-b", "--task-lease-expected-version", "2")
        assert updated["ok"]
        readback = cli(["task-lease", "inspect"])
        assert readback["active"] and readback["lease"]["owner"] == "agent-b"
        assert readback["lease"]["write_scopes"] == ["src/**"]
        complete = cli(["todo", "complete"], "--agent-id", "agent-b", "--task-lease-idempotency-key", "receiver-b",
                       "--task-lease-expected-version", "2", "--evidence", "validation://claim-transfer", "--no-follow-up")
        assert complete["ok"]
        current = cli(["todo", "list"])
        assert current["todo"]["status"] == "done"
        current_lease = cli(["task-lease", "inspect"])
        assert current_lease["lease"]["status"] == "released" and not current_lease["active"]
        historical = cli(["task-lease", "transfer"], *transfer, "--transfer-claim")
        assert historical["original_receipt"] == result["original_receipt"]
        assert historical["lease"]["status"] == "active"
        assert cli(["todo", "list"])["todo"]["status"] == "done"
        assert cli(["task-lease", "inspect"])["lease"] == current_lease["lease"]
        assert not (runtime / "goals" / goal / "task-leases" / f"{target}.json").exists()
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
                       cwd=cli_repo, capture_output=True, text=True, timeout=30, check=True)


@pytest.mark.parametrize("handoff_mode", ["legacy", "soft_claim", "hard_lease"])
@pytest.mark.parametrize("output_format", ["json", "markdown", None])
def test_claim_transfer_never_emulates_a_legacy_two_write_handover(tmp_path, handoff_mode, output_format):
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text(f"---\nhandoff_mode: {handoff_mode}\n---\n"
                     "# Retained source\n\n## Agent Todo\n- [ ] Historical work <!-- todo_id=todo_example -->\n")
    lease_path = runtime / "goals/example/task-leases/todo_example.json"
    lease_path.parent.mkdir(parents=True)
    now = datetime.now(UTC).replace(microsecond=0)
    lease_path.write_text(json.dumps({
        "schema_version": "task_lease_v0", "goal_id": "example", "todo_id": "todo_example",
        "owner": "agent-a", "idempotency_key": "source-a", "version": 1, "lease_epoch": 1,
        "status": "active", "write_scopes": ["src/**"],
        "acquire_ttl_seconds": 600, "acquired_at": now.isoformat(), "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }))
    original_state, original_lease = state.read_bytes(), lease_path.read_bytes()
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{
            "id": "example",
            "repo": str(tmp_path),
            "state_file": "state.md",
            "coordination": {
                "registered_agents": ["agent-a", "agent-b"],
                "agent_model": "peer_v1",
            },
        }],
    }))
    with pytest.raises(CanonicalAuthorityRequired, match="Canonical authority is required") as caught:
        transfer_task_lease(registry_path=registry, runtime_root=runtime, goal_id="example",
            todo_id="todo_example", owner="agent-a", idempotency_key="source-a", expected_version=1,
            new_owner="agent-b", new_idempotency_key="receiver-b", transfer_claim=True)
    assert caught.value.code == "canonical_authority_required"
    assert caught.value.payload["operation"] == "task_lease_transfer"
    assert caught.value.payload["write_admitted"] is False
    child = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        *(["--format", output_format] if output_format is not None else []),
        "task-lease", "transfer", "--goal-id", "example", "--todo-id", "todo_example",
        "--owner", "agent-a", "--idempotency-key", "source-a", "--expected-version", "1",
        "--new-owner", "agent-b", "--new-idempotency-key", "receiver-b", "--transfer-claim",
    ], cwd=REPO, capture_output=True, text=True, timeout=60, check=False)
    assert child.returncode == 1, child.stdout + child.stderr
    assert "Traceback" not in child.stderr
    if output_format == "json":
        rejected = json.loads(child.stdout)
        assert rejected["error_code"] == "canonical_authority_required"
        assert rejected["write_admitted"] is False
        assert rejected["recovery"] == {"action": "migrate_goal_authority", "backup_required": True}
    else:
        assert "canonical_authority_required" in child.stdout
        assert "migrate_goal_authority" in child.stdout
        assert "backup_required: `True`" in child.stdout
    assert state.read_bytes() == original_state
    assert lease_path.read_bytes() == original_lease
