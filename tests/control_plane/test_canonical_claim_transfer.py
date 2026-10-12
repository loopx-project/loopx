"""Public handover -> recipient edit/completion, including projection recovery."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.task_lease import TaskLeaseError, transfer_task_lease

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


def test_claim_transfer_never_emulates_a_legacy_two_write_handover(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}))
    with pytest.raises(TaskLeaseError, match="requires canonical authority") as caught:
        transfer_task_lease(registry_path=registry, runtime_root=tmp_path / "runtime", goal_id="example",
            todo_id="todo_example", owner="agent-a", idempotency_key="source-a", expected_version=1,
            new_owner="agent-b", new_idempotency_key="receiver-b", transfer_claim=True)
    assert caught.value.code == "claim_transfer_requires_canonical_authority"
    assert not (tmp_path / "runtime").exists()
