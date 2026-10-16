"""Acquire must grant current execution proof from the selected authority."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("retained", [False, True])
def test_user_action_cannot_grant_execution_and_retained_lease_can_be_released(
    tmp_path, monkeypatch, provider, retained,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "user-action-lease", "todo_user_action"
    state.write_text("# Synthetic bound User action\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    now = datetime.now(timezone.utc)
    leases = [{"schema_version": "task_lease_v0", "goal_id": goal, "todo_id": target,
        "owner": "agent-b", "idempotency_key": "retained-execution", "status": "active",
        "version": 3, "lease_epoch": 2, "write_scopes": [], "acquire_ttl_seconds": 600,
        "acquired_at": now.isoformat(), "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat()}] if retained else []
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease",
        leases=leases, todos=[{"schema_version": "todo_item_v0", "todo_id": target,
            "role": "user", "task_class": "user_action", "status": "open", "done": False,
            "text": "Review the synthetic observation", "bound_agent": "agent-a",
            "archive_state": "active", "source_section": "User Todo", "index": 1}])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)

    def cli(*args, expected_exit=0):
        child = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args,
            "--goal-id", goal, "--todo-id", target], cwd=REPO,
            capture_output=True, text=True, timeout=60, check=False)
        assert child.returncode == expected_exit, child.stdout + child.stderr
        return json.loads(child.stdout)

    def current():
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal, include_leases=True)

    before, display = current(), state.read_bytes()
    rejected = cli("task-lease", "acquire", "--owner", "outsider", "--idempotency-key", "outsider", expected_exit=1)
    assert rejected["error_code"] == "owner_not_registered"
    for owner in ("agent-a", "agent-b"):
        rejected = cli("task-lease", "acquire", "--owner", owner,
            "--idempotency-key", "new-" + owner, "--expected-version", "3" if retained else "0", expected_exit=1)
        assert rejected["error_code"] == "todo_not_executable"
        assert current() == before and state.read_bytes() == display
    inspected = cli("task-lease", "inspect")
    assert inspected["active"] is False
    if retained:
        assert inspected["executor_constraint"] == {"effective": False, "reason": "todo_not_executable"}
        proof = ("--owner", "agent-b", "--idempotency-key", "retained-execution", "--expected-version", "3")
        for operation, extra in (("renew", ()), ("transfer", ("--new-owner", "agent-a", "--new-idempotency-key", "transfer-a"))):
            rejected = cli("task-lease", operation, *proof, *extra, expected_exit=1)
            assert rejected["error_code"] == "todo_not_executable"
            assert current() == before and state.read_bytes() == display
        released = cli("task-lease", "release", *proof)
        assert released["lease"]["status"] == "released"
        after_release = current()
        replay = cli("task-lease", "release", *proof)
        assert replay["idempotent"] is True and current() == after_release
        assert replay["original_receipt"] == released["original_receipt"]
    before_copy = current()
    rejected = cli("todo", "update", "--agent-id", "agent-b", "--note", "Foreign copy", expected_exit=1)
    assert rejected["reason_code"] == "bound_agent_mismatch" and current() == before_copy
    assert cli("todo", "update", "--agent-id", "agent-a", "--note", "Bound administrative copy",
        "--update-operation-id", "bound-copy", "--update-expected-provider-revision",
        before_copy["provider_revision"])["ok"] is True
    completion = ("todo", "complete", "--agent-id", "agent-a", "--decision-outcome", "cancel",
        "--evidence", "The synthetic observation was withdrawn")
    assert cli(*completion, "--dry-run")["ok"] is True
    assert cli(*completion)["ok"] is True
    assert cli("todo", "list")["todo"]["status"] == "done"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_acquire_renew_complete_and_retired_retry(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "lease-admission", "todo_admission"
    state.write_text("# Synthetic admission\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": target, "role": "agent", "status": "open", "done": False,
        "text": "Acquire and finish canonical work", "archive_state": "active", "source_section": "Agent Todo",
        "index": 1, "task_class": "advancement_task", "claimed_by": "agent-a",
    }])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    state.unlink()
    cli_repo = Path(os.environ.get("LOOPX_LEASE_REPLAY_REPO", REPO))

    def cli(command, *args, expected_exit=0):
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            *command, "--goal-id", goal, "--todo-id", target, *args], cwd=cli_repo,
            capture_output=True, text=True, timeout=60, check=False)
        assert child.returncode == expected_exit, child.stdout + child.stderr
        return json.loads(child.stdout)

    proof = ["--owner", "agent-a", "--idempotency-key", "admission-a"]
    acquire = [*proof, "--expected-version", "0", "--ttl-seconds", "600", "--write-scope", "src/**"]
    try:
        first = cli(["task-lease", "acquire"], *acquire)
        assert first["ok"] and first["acquired"]
        assert first["source_authority"] == provider + "_v0"
        assert first["lease"]["version"] == first["lease"]["lease_epoch"] == 1
        assert first["lease"]["write_scopes"] == ["src/**"]
        replay = cli(["task-lease", "acquire"], *acquire)
        assert replay["idempotent"] and not replay["acquired"]
        assert replay["lease"] == first["lease"]
        assert replay["original_receipt"] == first["original_receipt"]
        renewed = cli(["task-lease", "renew"], *proof, "--expected-version", "1", "--ttl-seconds", "600")
        current = cli(["task-lease", "acquire"], *acquire)
        assert current["lease"] == renewed["lease"]
        assert current["original_receipt"] == first["original_receipt"]
        retired = cli(["task-lease", "release"], *proof, "--expected-version", "2")
        assert retired["released"]
        rejected = cli(["task-lease", "acquire"], *acquire, expected_exit=1)
        assert rejected["error_code"] == "idempotency_key_reuse"
        inspected = cli(["task-lease", "inspect"])
        assert not inspected["active"] and inspected["lease"] == retired["lease"]
        assert not (runtime / "goals" / goal / "task-leases" / f"{target}.json").exists()
        assert not state.exists()
        next_proof = ["--owner", "agent-a", "--idempotency-key", "admission-next", "--expected-version", "2"]
        next_execution = cli(["task-lease", "acquire"], *next_proof, "--ttl-seconds", "600")
        assert next_execution["lease"]["version"] == 3 and next_execution["lease"]["lease_epoch"] == 2
        complete_args = ["--agent-id", "agent-a", "--task-lease-idempotency-key", "admission-next",
                         "--task-lease-expected-version", "3", "--evidence", "validation://lease-admission", "--no-follow-up"]
        preview = cli(["todo", "complete"], *complete_args, "--dry-run")
        assert preview["ok"]
        assert not state.exists()
        completed = cli(["todo", "complete"], *complete_args)
        assert completed["ok"]
        assert state.exists()
        readback = cli(["todo", "list"])
        assert readback["todo"]["status"] == "done"
        terminal = cli(["task-lease", "inspect"])
        assert terminal["lease"]["status"] == "released" and not terminal["active"]
        assert (cli(["task-lease", "acquire"], *next_proof, "--ttl-seconds", "600", expected_exit=1))["error_code"] == "todo_not_open"
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
                       cwd=cli_repo, capture_output=True, text=True, timeout=30, check=True)
