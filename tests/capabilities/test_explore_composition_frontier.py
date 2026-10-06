from __future__ import annotations

import shlex
from pathlib import Path

from loopx.capabilities.explore.composition_frontier import (
    build_explore_composition_frontier,
    project_live_explore_composition_frontier,
)
from loopx.capabilities.explore.result_log import (
    append_explore_result_events,
    build_explore_edge_event,
    build_explore_node_event,
    build_explore_result_projection,
    explore_result_log_path,
)
from loopx.control_plane.work_items.progress_observation import (
    build_replan_action_packet,
    build_replan_context,
)

GOAL_ID = "composition-frontier-fixture"
AGENT_ID = "codex-composition-fixture"
EXPERIMENT_ID = "experiment_joint_boundary"


def _events() -> list[dict[str, object]]:
    return [
        build_explore_node_event(
            goal_id=GOAL_ID,
            node_id="surface_a",
            title="Closed surface A",
            node_kind="hypothesis",
            status="resolved",
            evidence_refs=["evidence-a"],
        ),
        build_explore_node_event(
            goal_id=GOAL_ID,
            node_id="surface_b",
            title="Closed surface B",
            node_kind="hypothesis",
            status="dead_end",
            evidence_refs=["evidence-b"],
        ),
        build_explore_node_event(
            goal_id=GOAL_ID,
            node_id=EXPERIMENT_ID,
            title="Combine A and B at the shared boundary",
            node_kind="experiment",
            status="open",
        ),
        build_explore_edge_event(
            goal_id=GOAL_ID,
            from_node=EXPERIMENT_ID,
            to_node="surface_a",
            edge_type="depends_on",
        ),
        build_explore_edge_event(
            goal_id=GOAL_ID,
            from_node=EXPERIMENT_ID,
            to_node="surface_b",
            edge_type="depends_on",
        ),
    ]


def _projection() -> dict[str, object]:
    return build_explore_result_projection(_events(), goal_id=GOAL_ID)


def test_explicit_joint_experiment_projects_one_bounded_gap() -> None:
    frontier = build_explore_composition_frontier(
        _projection(),
        agent_id=AGENT_ID,
        enabled=True,
    )

    assert frontier["state"] == "pending"
    assert frontier["candidate_count"] == 1
    assert frontier["pending_count"] == 1
    assert frontier["scheduled_count"] == 0
    assert frontier["selected_gap"] == frontier["gaps"][0]
    assert frontier["selected_gap"]["experiment_node_ref"] == EXPERIMENT_ID
    assert frontier["selected_gap"]["input_node_refs"] == [
        "surface_a",
        "surface_b",
    ]


def test_frontier_does_not_infer_pairs_and_exact_todo_binding_schedules_gap() -> None:
    no_experiment = build_explore_composition_frontier(
        build_explore_result_projection(_events()[:2], goal_id=GOAL_ID),
        agent_id=AGENT_ID,
        enabled=True,
    )
    assert no_experiment["candidate_count"] == 0
    assert no_experiment["selected_gap"] is None

    scheduled = build_explore_composition_frontier(
        _projection(),
        todos=[
            {
                "todo_id": "todo_joint",
                "status": "claimed",
                "done": False,
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
                "explore_result_node_refs": [EXPERIMENT_ID],
            }
        ],
        agent_id=AGENT_ID,
        enabled=True,
    )
    assert scheduled["state"] == "satisfied"
    assert scheduled["pending_count"] == 0
    assert scheduled["scheduled_count"] == 1
    assert scheduled["selected_gap"] is None


def test_live_projection_is_goal_opt_in_and_reads_only_public_explore_log(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    append_explore_result_events(
        explore_result_log_path(runtime_root, GOAL_ID),
        _events(),
        expected_goal_id=GOAL_ID,
    )
    status_payload = {
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "spawn_policy": {
                        "explore_harness": {"enabled": True, "profile": "generic"}
                    },
                }
            ]
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "agent_todos": {"items": []},
                    "project_asset": {"agent_todos": {"items": []}},
                }
            ]
        },
    }

    frontier = project_live_explore_composition_frontier(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        status_payload=status_payload,
    )
    assert frontier is not None
    assert frontier["enabled"] is True
    assert frontier["selected_gap"]["experiment_node_ref"] == EXPERIMENT_ID

    status_payload["run_history"]["goals"][0]["spawn_policy"] = {}
    disabled = project_live_explore_composition_frontier(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        status_payload=status_payload,
    )
    assert disabled is None


def test_replan_successor_binds_obligation_and_joint_experiment() -> None:
    frontier = build_explore_composition_frontier(
        _projection(),
        agent_id=AGENT_ID,
        enabled=True,
    )
    obligation = {
        "obligation_id": "replan-composition-fixture",
        "agent_id": AGENT_ID,
        "todo_actions": [
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": "Select a bounded successor.",
            }
        ],
    }
    context = build_replan_context(
        obligation,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        newest_first_runs=[],
    )
    packet = build_replan_action_packet(
        {**obligation, "replan_context": context},
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        bounded_research_frontier=frontier,
    )

    command = shlex.split(packet["writeback_contract"]["successor_command"])
    assert command[0:3] == ["loopx", "todo", "add"]
    assert command[command.index("--replan-obligation-id") + 1] == (
        "replan-composition-fixture"
    )
    assert command[command.index("--explore-result-node-ref") + 1] == (
        EXPERIMENT_ID
    )
    assert command[command.index("--action-kind") + 1] == "joint_probe"
    assert command[command.index("--task-domain") + 1] == "research"
    assert command[command.index("--target-key") + 1] == EXPERIMENT_ID
    assert packet["bounded_frontier"]["required_outcome"] == (
        "joint_experiment_result"
    )
