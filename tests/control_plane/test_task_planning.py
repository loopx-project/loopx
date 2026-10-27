from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import (
    advance_canonical_fixture_authority, initialize_canonical_source_fixture, read_fixture_todos,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

from loopx.cli import main
from loopx.control_plane.goals.start_contract import (
    build_goal_start_prompt,
    goal_planner_contract,
)
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
                "common_runtime_root": str(tmp_path / "runtime"),
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
    initialize_canonical_source_fixture(
        tmp_path / "runtime", "planning-goal", state_path=state, provider="sqlite",
    )
    return dict(
        registry_path=registry,
        goal_id="planning-goal",
        agent_id="planner",
        text="Investigate the failure, then repair and validate it.\nKeep the API stable.",
        project=tmp_path,
        runtime_root_arg=str(tmp_path / "runtime"),
    )


def _replace_frontier(bound_goal, operation_id):
    advance_canonical_fixture_authority(
        Path(bound_goal["runtime_root_arg"]), bound_goal["goal_id"],
        build_todo_runtime_shadow_projection(goal_id=bound_goal["goal_id"],
            todos=read_fixture_todos(bound_goal["project"] / "state.md"),
            handoff_mode="soft_claim", leases=[]), operation_id=operation_id,
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
    _replace_frontier(bound_goal, "fixture-existing-plan")
    packet = build_task_planning_packet(**bound_goal)
    assert packet["runnable_todo_ids"] == ["todo_existing"]
    assert [step["id"] for step in packet["ordered_steps"]] == [
        "compare_planned_todos_with_frontier",
        "apply_todo_delta",
        "read_back_authored_todos",
    ]
    assert all(item["todo_id"] != "todo_peer" for item in packet["existing_todos"])


@pytest.mark.parametrize("fine_grained", [False, True])
def test_planning_scope_is_sufficient_to_start_without_exhaustive_decomposition(bound_goal, fine_grained):
    registry = bound_goal["registry_path"]
    contents = json.loads(registry.read_text())
    if fine_grained:
        contents["goals"][0]["execution_profile"] = {"turn_granularity": "fine"}
        registry.write_text(json.dumps(contents))
    packet = build_task_planning_packet(**bound_goal)
    planner = packet["planner"]
    assert planner == goal_planner_contract(fine_grained=fine_grained)
    for clause in (
        "minimum sufficient plan: next step and decisive validation",
        "Diagnostics and feedback may come first to strengthen delivery",
        "replan uncertain later work",
        "no fixed-count filler or exhaustive decomposition",
    ):
        assert clause in planner["budget_policy"]
    assert planner["profiles"]["clear_bounded_problem"][
        "may_reuse_current_todo_when_it_already_represents_the_plan"] is True
    assert planner["required_before_todo_write"] is True
    assert packet["execution_handoff"]["requires_quota_guard"] is True
    assert packet["execution_handoff"]["planning_is_advancement"] is False
    assert "do not substitute them for the requested deliverable" in planner["first_delivery_guidance"]
    assert "existing acceptance, feedback and authority limits" in planner["first_delivery_guidance"]
    assert planner.get("maximum_runnable_todos_written_ahead") == (1 if fine_grained else None)


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
    for position, ordered in enumerate((work, list(reversed(work)))):
        state.write_text("# Active Goal State\n\n## Agent Todos\n" + "\n".join(ordered)
                         + "\n\n## User Todos\n" + "\n".join(gates) + "\n")
        _replace_frontier(bound_goal, f"fixture-frontier-order-{position}")
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
    quality = packet["planner"]["first_delivery_guidance"]
    # Agent-consumed obligations must reach both public planning entrypoints;
    # their presence is not a measurement of the resulting model quality.
    for obligation in (
        "strongest feasible approach",
        "deliberately weak placeholder",
        "quality, latency and full cost",
        "existing acceptance, feedback and authority limits",
    ):
        assert obligation in quality
    for fine_grained in (False, True):
        assert goal_planner_contract(fine_grained=fine_grained)[
            "first_delivery_guidance"
        ] == quality
        assert quality in build_goal_start_prompt(
            goal_text=bound_goal["text"],
            goal_id=bound_goal["goal_id"],
            agent_id=bound_goal["agent_id"],
            fine_grained=fine_grained,
        )
    assert packet["execution_handoff"]["spends_quota"] is False
    assert packet["execution_handoff"]["requires_quota_guard"] is True
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(command + ["--execute"]) == 1
    assert "unsupported" in json.loads(output.getvalue())["error"]
    assert not list(Path(bound_goal["runtime_root_arg"]).rglob("*rollout*"))
