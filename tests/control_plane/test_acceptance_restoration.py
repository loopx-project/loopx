"""An exact reviewed rollback must recover a released stale Todo, without execution."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted, read_canonical_todo_fields_if_promoted,
)
from loopx.control_plane.goals.goal_frontier.acceptance import acceptance_gaps_from_held_goal_binding


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_restore_then_reacquire_preserves_acceptance_and_exact_retry(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://github.com/example/acceptance-restoration-fixture.git"],
                   cwd=project, check=True)
    subprocess.run(["git", "-c", "user.name=LoopX Test",
                    "-c", "user.email=loopx-test@example.invalid", "commit",
                    "--quiet", "--allow-empty", "-m", "fixture"], cwd=project, check=True)
    delivery = tmp_path / "delivery-worktree"
    subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(delivery)],
                   cwd=project, check=True)
    runtime, state, registry = project / "runtime", project / "state.md", project / "registry.json"
    goal, target = "acceptance-restoration", "todo_artifact"
    state.write_text("---\nstatus: active-read-only\nowner_mode: goal\nobjective: Validate a bounded artifact\n---\n# Goal\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(project), "state_file": state.name,
        "domain": "acceptance-restoration", "status": "active-read-only",
        "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
        "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 2},
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    wait = "resume_at:2020-01-01T00:00:00Z"
    todo = {"schema_version": "todo_item_v0", "todo_id": target, "role": "agent", "status": "open",
            "done": False, "text": "Deliver the artifact", "archive_state": "active",
            "source_section": "Agent Todo", "index": 1, "task_class": "advancement_task",
            "claimed_by": "agent-a", "resume_when": wait, "action_kind": "validate"}
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", todos=[todo])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    document = tmp_path / "acceptance.json"
    document.write_text(json.dumps({
        "scope": {"kind": "selected_work", "todo_ids": [target]}, "objective": "Deliver the artifact",
        "non_goals": [], "criteria": [{"id": "artifact", "description": "Artifact is verified",
                                        "validation_argv": [sys.executable, "-c", "pass"]}],
        "bindings": [{"todo_id": target, "criterion_ids": ["artifact"]}],
    }))

    def cli(*args, expected=0):
        proc = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                               "--format", "json", *args, "--goal-id", goal],
                              cwd=delivery, capture_output=True, text=True, timeout=60)
        assert proc.returncode == expected, proc.stdout + proc.stderr
        return json.loads(proc.stdout)

    def inspect():
        return cli("goal-acceptance", "inspect")

    initial = inspect()
    cli("goal-acceptance", "configure", "--document", str(document), "--expected-provider-revision",
        initial["provider_revision"], "--operation-id", "confirm-owner", "--execute")
    confirmed = inspect()["goal_acceptance_contract"]
    lease_args = ["--todo-id", target, "--owner", "agent-a", "--idempotency-key", "execution-one"]
    first = cli("task-lease", "acquire", *lease_args, "--expected-version", "0", "--ttl-seconds", "600")
    assert first["acquired"]
    turn_binding = ["--agent-id", "agent-a", "--todo-id", target,
                    "--turn-instance-id", "turn-restore-acceptance"]
    guard = cli("quota", "should-run", "--codex-app", *turn_binding, )
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == target
    cli("todo", "update", "--todo-id", target, "--agent-id", "agent-a", "--clear-resume-when",
        "--task-lease-idempotency-key", "execution-one", "--task-lease-expected-version", "1")
    assert inspect()["goal_acceptance_contract"]["tasks"][0]["state"] == "stale"
    cli("task-lease", "release", *lease_args, "--expected-version", "1")
    stale = inspect()
    def turn_holds():
        fields = read_canonical_todo_fields_if_promoted(runtime_root=runtime, goal_id=goal)
        source = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        return acceptance_gaps_from_held_goal_binding(fields["agent_todos"], source["todos"], agent_id="agent-a")
    assert len(turn_holds()) == 1
    held = cli("task-lease", "acquire", "--todo-id", target, "--owner", "agent-a",
               "--idempotency-key", "execution-two", "--expected-version", "1", expected=1)
    assert "stale" in json.dumps(held)
    restore = ["todo", "update", "--todo-id", target, "--agent-id", "agent-a",
               "--resume-when", wait, "--update-operation-id", "restore-original",
               "--update-expected-provider-revision", stale["provider_revision"]]
    unknown = restore.copy()
    unknown[unknown.index(wait)] = "resume_at:2021-01-01T00:00:00Z"
    refused = cli(*unknown, expected=1)
    assert "owner" in json.dumps(refused) and "rebind" in json.dumps(refused)
    assert inspect() == stale
    assert cli(*restore)["status"] == "applied"
    restored = inspect()
    assert turn_holds() == []  # Managed-Turn frontier no longer projects the stale hold.
    assert restored["goal_acceptance_contract"]["tasks"][0]["state"] == "ready"
    for field in ("revision", "digest", "criteria"):
        assert restored["goal_acceptance_contract"][field] == confirmed[field]
    lease = cli("task-lease", "inspect", "--todo-id", target)
    assert not lease["active"] and lease["lease"]["version"] == 1
    assert cli(*restore)["status"] == "replayed"
    assert inspect() == restored
    next_lease = cli("task-lease", "acquire", "--todo-id", target, "--owner", "agent-a",
                     "--idempotency-key", "execution-two", "--expected-version", "1", "--ttl-seconds", "600")
    assert next_lease["acquired"] and next_lease["lease"]["version"] == 2
    # Recovery replay remains historical after a fresh execution starts; it
    # cannot release or acquire another generation, complete work or spend quota.
    after_acquire = inspect()
    assert cli(*restore)["status"] == "replayed"
    assert inspect() == after_acquire
    assert cli("task-lease", "inspect", "--todo-id", target)["lease"] == next_lease["lease"]
    assert cli("todo", "list")["todos"][0]["status"] == "open"

    completed = cli("todo", "complete", *turn_binding, "--task-lease-idempotency-key", "execution-two",
                    "--task-lease-expected-version", "2", "--evidence", "fixture:restoration-check",
                    "--next-agent-todo", "Validate the next artifact", "--next-claimed-by", "agent-a",
                    "--next-action-kind", "validate")
    assert completed["ok"]
    refresh = cli("refresh-state", *turn_binding, "--classification", "validated_recovery",
                  "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
                  "--no-global-sync", "--suppress-external-sinks")
    assert refresh["ok"]
    assert refresh["delivery_workspace"]["workspace_kind"] == "independent_git_worktree"
    spend_args = ["quota", "spend-slot", *turn_binding, "--slots", "1", "--source", "heartbeat",
                  "--execute", ]
    first_spend = cli(*spend_args)
    assert first_spend["settlement_result"]["ok"]
    assert cli(*restore)["status"] == "replayed"
    repeated_spend = cli(*spend_args)
    assert repeated_spend["settlement_result"]["ok"]
    runs = runtime / "goals" / goal / "runs/index.jsonl"
    assert sum(json.loads(line).get("classification") == "quota_slot_spent"
               for line in runs.read_text().splitlines()) == 1
    assert next(todo for todo in cli("todo", "list")["todos"] if todo["todo_id"] == target)["status"] == "done"
