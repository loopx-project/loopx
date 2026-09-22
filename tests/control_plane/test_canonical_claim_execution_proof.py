"""The public claim command must never grant execution from historical proof."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("retirement", ["release", "transfer"])
def test_claim_replay_uses_current_execution(tmp_path, monkeypatch, provider, retirement):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "claim-execution", "todo_claim_execution"
    state.write_text("# Claim execution\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": target, "role": "agent", "status": "open", "done": False,
        "text": "Adopt work and obtain current execution", "archive_state": "active", "source_section": "Agent Todo",
        "index": 1, "task_class": "advancement_task", "required_write_scopes": ["src/**"],
    }])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    state.unlink()  # Recovery must not rebuild authority from missing Markdown.

    def cli(command, *args, expected_exit=0):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            *command, "--goal-id", goal, "--todo-id", target, *args], cwd=REPO,
            capture_output=True, text=True, timeout=90, check=False)
        assert process.returncode == expected_exit, process.stdout + process.stderr
        return json.loads(process.stdout)

    request = ["--claimed-by", "agent-a", "--agent-id", "agent-a", "--claim-operation-id", "claim-original",
               "--task-lease-idempotency-key", "execution-a", "--task-lease-expected-version", "0"]
    lease_args = ["--owner", "agent-a", "--idempotency-key", "execution-a"]
    try:
        preview = cli(["todo", "claim"], *request, "--dry-run")
        assert preview["status"] == "planned"
        assert not cli(["task-lease", "inspect"])["active"]
        first = cli(["todo", "claim"], *request)
        assert first["status"] == "applied"
        assert first["lease"]["version"] == first["lease"]["lease_epoch"] == 1
        assert first["lease"]["write_scopes"] == ["src/**"]
        renewed = cli(["task-lease", "renew"], *lease_args, "--expected-version", "1", "--ttl-seconds", "600")
        replay = cli(["todo", "claim"], *request)
        assert replay["status"] == "replayed"
        assert replay["changed"] is False
        assert replay["lease"] == renewed["lease"]
        assert replay["lease"]["version"] == 2
        assert replay["original_receipt"] == first["original_receipt"]
        assert replay["provider_revision"] == first["provider_revision"]
        assert replay["current_provider_revision"] != first["provider_revision"]
        transition = ["--expected-version", "2"]
        if retirement == "transfer":
            transition += ["--new-owner", "agent-b", "--new-idempotency-key", "execution-b", "--transfer-claim"]
        retired = cli(["task-lease", retirement], *lease_args, *transition)
        assert retired["ok"]
        state.unlink(missing_ok=True)  # Maintenance may refresh display; remove it before recovery.
        before = cli(["task-lease", "inspect"])
        rejected = cli(["todo", "claim"], *request, expected_exit=1)
        assert rejected["ok"] is False
        assert rejected["error_code"] == ("idempotency_key_reuse" if retirement == "release" else "owner_conflicts_with_claim")
        assert not rejected.get("lease"), "a rejected old execution cannot return an active lease"
        assert cli(["task-lease", "inspect"]) == before
        assert not state.exists(), "rejected recovery cannot revive a legacy writer"
        readback = cli(["todo", "list"])
        assert first["source_authority"] == provider + "_v0"
        assert readback["todos"][0]["claimed_by"] == ("agent-b" if retirement == "transfer" else "agent-a")
        assert not (runtime / "goals" / goal / "task-leases" / f"{target}.json").exists()
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
            cwd=REPO, capture_output=True, text=True, timeout=30, check=True)
