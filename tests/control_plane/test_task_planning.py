from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.goals.task_planning import build_task_planning_packet


@pytest.fixture
def bound_goal(tmp_path):
    state = tmp_path / "state.md"
    state.write_text("# Active Goal State\n\n## Agent Todos\n\n## User Todos\n")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "planning-goal",
                        "repo": str(tmp_path),
                        "state_file": str(state),
                        "status": "active",
                        "waiting_on": "protected approval",
                        "coordination": {"registered_agents": ["planner", "peer"]},
                    }
                ],
            }
        )
    )
    return dict(
        registry_path=registry,
        goal_id="planning-goal",
        agent_id="planner",
        text="Investigate the failure, then repair and validate it.\nKeep the API stable.",
        project=tmp_path,
        runtime_root_arg=str(tmp_path / "runtime"),
    )


def test_empty_frontier_plans_before_writes_without_starting_a_loop(bound_goal):
    registry = bound_goal["registry_path"]
    state = bound_goal["project"] / "state.md"
    before = registry.read_bytes(), state.read_bytes()
    packet = build_task_planning_packet(**bound_goal)
    assert packet["text"] == bound_goal["text"]
    assert [step["id"] for step in packet["ordered_steps"]] == [
        "plan_ranked_todos",
        "write_ordered_todos",
        "read_back_authored_todos",
    ]
    assert packet["planner"]["required_before_todo_write"] is True
    assert packet["execution_handoff"] == {
        "owner": "caller",
        "requires_quota_guard": True,
        "starts_host_loop": False,
        "spends_quota": False,
        "planning_is_advancement": False,
    }
    assert packet["goal_waiting_on"] == "protected approval"
    assert (registry.read_bytes(), state.read_bytes()) == before


def test_existing_plan_is_an_incremental_frontier_not_a_new_goal(bound_goal):
    state = bound_goal["project"] / "state.md"
    state.write_text("""# Active Goal State

## Agent Todos
- [ ] [P0] Reproduce the API failure and retain a regression.
  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task action_kind=test claimed_by=planner -->
- [ ] [P0] Another agent's task.
  <!-- loopx:todo todo_id=todo_peer status=open task_class=advancement_task action_kind=test claimed_by=peer -->

## User Todos
""")
    packet = build_task_planning_packet(**bound_goal)
    assert packet["runnable_todo_ids"] == ["todo_existing"]
    assert [step["id"] for step in packet["ordered_steps"]] == [
        "compare_planned_todos_with_frontier",
        "apply_todo_delta",
        "read_back_authored_todos",
    ]
    assert all(item["todo_id"] != "todo_peer" for item in packet["existing_todos"])


def test_unknown_identity_rejected_without_creating_it(bound_goal):
    with pytest.raises(ValueError, match="not registered"):
        build_task_planning_packet(**(bound_goal | {"agent_id": "unknown"}))


def test_full_frontier_and_live_blockers_ignore_display_order_and_terminal_items(bound_goal):
    state = bound_goal["project"] / "state.md"
    work = [
        f"- [ ] [P0] Task {i}.\n"
        f"  <!-- loopx:todo todo_id=todo_work_{i} status=open task_class=advancement_task action_kind=test claimed_by=planner -->"
        for i in range(40)
    ]
    gates = [
        f"- [{'x' if status == 'done' else ' '}] [P0] Gate {status}.\n"
        f"  <!-- loopx:todo todo_id=todo_gate_{status} status={status} task_class=user_gate action_kind=approve blocks_agent=planner -->"
        for status in ("open", "blocked", "done", "deferred")
    ]
    for ordered in (work, list(reversed(work))):
        state.write_text("# Active Goal State\n\n## Agent Todos\n" + "\n".join(ordered)
                         + "\n\n## User Todos\n" + "\n".join(gates) + "\n")
        packet = build_task_planning_packet(**bound_goal)
        assert set(packet["runnable_todo_ids"]) == {f"todo_work_{i}" for i in range(40)}
        assert set(packet["blocking_todo_ids"]) == {"todo_gate_open", "todo_gate_blocked"}


def test_public_cli_returns_a_read_only_checkpoint_and_rejects_execution(bound_goal):
    command = [
        "--format",
        "json",
        "--registry",
        str(bound_goal["registry_path"]),
        "--runtime-root",
        bound_goal["runtime_root_arg"],
        "todo",
        "plan",
        "--goal-id",
        bound_goal["goal_id"],
        "--agent-id",
        bound_goal["agent_id"],
        "--project",
        str(bound_goal["project"]),
        "--text",
        bound_goal["text"],
    ]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(command) == 0
    packet = json.loads(output.getvalue())
    assert packet["read_only"] and packet["dry_run"]
    assert packet["runnable_todo_ids"] == []
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(command + ["--execute"]) == 1
    assert "unsupported" in json.loads(output.getvalue())["error"]
    assert not list(Path(bound_goal["runtime_root_arg"]).rglob("*rollout*"))
