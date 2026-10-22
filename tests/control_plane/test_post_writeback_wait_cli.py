"""A later dependency wait cannot replace an admitted Turn's durable outcome."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from test_quota_settlement_cli import (
    AGENT_ID,
    ALTERNATIVE_TODO_ID,
    GOAL_ID,
    TODO_ID,
    _configure_read_only_todo,
    _configure_selectable_alternative,
    _initialize_git_checkout,
    _spend_run_count,
    _write_fixture,
)

DEPENDENCY_ID = "todo_later_dependency"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("kind,cycle", [
    ("monitor_changed", False), ("todo_done", False), ("todo_done", True),
])
def test_committed_progress_before_wait_recovers_only_original_spend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    kind: str,
    cycle: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    # Compare complete Todo readbacks at one observation time. Elapsed wall
    # time changes the derived wait projection without mutating the Todo.
    monkeypatch.setattr(
        "loopx.control_plane.todos.resume_condition.now_utc_iso",
        lambda: "2026-09-20T00:00:00Z",
    )
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    _configure_selectable_alternative(project)
    dependency_owner = (
        AGENT_ID if kind == "monitor_changed" else "codex-dependency-peer"
    )
    workspace = project
    if kind == "todo_done":
        configuration = json.loads(registry.read_text())
        configuration["goals"][0]["coordination"]["registered_agents"].append(
            dependency_owner
        )
        registry.write_text(json.dumps(configuration))
        _initialize_git_checkout(project)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=LoopX Test",
                "-c",
                "user.email=loopx-test@example.invalid",
                "commit",
                "--quiet",
                "--allow-empty",
                "-s",
                "-m",
                "dependency fixture",
            ],
            cwd=project,
            check=True,
        )
        workspace = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", "--quiet", "--detach", str(workspace)],
            cwd=project,
            check=True,
        )
    dependency = (
        "task_class=continuous_monitor target_key=later-dependency cadence=6h "
        "next_due_at=2099-01-01T00:00:00Z expires_at=2099-01-08T00:00:00Z "
        "material_change_generation=0"
        if kind == "monitor_changed"
        else "task_class=advancement_task priority=2"
    )
    state.write_text(
        state.read_text()
        + (
            "\n- [ ] [P2] Await independent dependency evidence.\n"
            f"  <!-- loopx:todo todo_id={DEPENDENCY_ID} status=open "
            f"claimed_by={dependency_owner} {dependency} -->\n"
        )
    )

    def run(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(workspace)
            rc = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    *args,
                ]
            )
            return rc, json.loads(capsys.readouterr().out)

    rc, listed = run("todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=listed["todos"],
            handoff_mode="soft_claim",
            leases=[],
        ),
        state_path=state,
        provider=provider,
    )
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        f"progress-before-wait-{kind}-{provider}",
    )
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
    )
    rc, admitted = run(*guard_args)
    assert rc == 0 and admitted["normal_delivery_allowed"], admitted
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    vision_path = tmp_path / "vision.json"
    vision_path.write_text(
        json.dumps(
            {
                "schema_version": "goal_vision_replan_contract_v0",
                "state": "vision_patch_proposed",
                "vision_patch": {
                    "acceptance_summary": "The accepted bounded segment preserves its original receipts."
                },
                "path_delta": {
                    "outcome": "continue",
                    "prior_assumption": "The original route is viable.",
                    "observed_reality": "Independent validation accepts a bounded segment.",
                    "retained": ["Original task acceptance"],
                    "evidence_refs": ["evidence:bounded-segment"],
                },
            }
        )
    )
    refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--classification",
        "verified_segment_before_wait",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--agent-vision-json",
        str(vision_path),
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    rc, written = run(*refresh_args)
    assert rc == 0 and written["settlement_progress"]["state"] == "spend_required", (
        written
    )
    original = read_heartbeat_settlement(
        runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=binding[-1],
    )
    assert original is not None and original.writeback.failure is None
    rc, waiting = run(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--resume-when",
        f"{kind}:{DEPENDENCY_ID}",
        "--successor-todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert rc == 0, waiting
    if cycle:
        rc, changed = run(
            "todo", "update", "--goal-id", GOAL_ID, "--todo-id", DEPENDENCY_ID,
            "--agent-id", dependency_owner, "--resume-when", f"todo_done:{TODO_ID}",
            "--successor-todo-id", ALTERNATIVE_TODO_ID,
        )
        assert rc == 0, changed
    rc, before = run("todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, before
    if cycle:
        waiting_todo = next(row for row in before["todos"] if row["todo_id"] == TODO_ID)
        assert waiting_todo["resume_condition"]["invalid_state"] == "dependency_cycle"
        assert waiting_todo["resume_ready"] is False
    rc, recovery = run(*guard_args)
    assert rc == 0, recovery
    assert recovery["settlement_progress"]["state"] == "spend_required", recovery.get(
        "unsettled_host_turn_recovery"
    )
    assert recovery["settlement_owed"]["effect_id"] == identity["effect_id"]
    assert recovery["normal_delivery_allowed"] is False
    assert (
        recovery["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    )
    actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert actions == [recovery["settlement_owed"]["command"]]
    assert "refresh-state" not in actions[0]
    assert "selected_todo" not in recovery
    envelope = build_turn_envelope(recovery)
    assert envelope["action"]["delivery_allowed"] is False
    assert envelope["writeback"]["next_cli_actions"] == actions
    assert _spend_run_count(runtime) == 0

    wrong_identity = shlex.split(actions[0])[1:]
    wrong_identity[wrong_identity.index("--turn-instance-id") + 1] = "unadmitted-turn"
    rc, rejected = run(*wrong_identity)
    assert rc != 0 and rejected["ok"] is False, rejected
    assert _spend_run_count(runtime) == 0

    # Execute the actual projected route. The spend owner retains all ordinary
    # workspace, checkpoint, declaration and exact-receipt checks.
    rc, spent = run(*shlex.split(actions[0])[1:])
    assert rc == 0 and spent["appended"] is True, spent
    rc, repeated = run(*shlex.split(actions[0])[1:])
    assert rc == 0 and repeated["idempotent_replay"] is True, repeated
    assert _spend_run_count(runtime) == 1
    rc, settled = run(*guard_args)
    assert rc == 0 and settled["effective_action"] == "heartbeat_settled_skip", settled
    assert settled["should_run"] is False
    final = read_heartbeat_settlement(
        runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=binding[-1],
    )
    assert final is not None and final.settlement.failure is None
    assert final.writeback_run == original.writeback_run
    rc, after = run("todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0 and after["todos"] == before["todos"], after
    rc, next_turn = run(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--codex-app",
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "independent-next-turn",
    )
    assert rc == 0 and next_turn["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID, (
        next_turn
    )
