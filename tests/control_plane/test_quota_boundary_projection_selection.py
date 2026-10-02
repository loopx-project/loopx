from __future__ import annotations

from typing import Any

import pytest

from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.scheduler.execution_context import (
    SchedulerRuntimeProfile, scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)

GOAL_ID = "boundary-selection-fixture"
AGENT_ID = "codex-main"


def test_workspace_and_boundary_guards_check_the_selected_todo_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research = quota_todo_item(
        todo_id="todo_research001",
        index=1,
        priority="P1",
        title="Research the current provider contract.",
        claimed_by=AGENT_ID,
        required_capabilities=["network"],
    )
    later_implementation = quota_todo_item(
        todo_id="todo_implementation001",
        index=2,
        priority="P1",
        title="Implement the later capability adapter.",
        claimed_by=AGENT_ID,
        required_write_scopes=["skills/**"],
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[research, later_implementation],
        recommended_action=research["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
            "write_scope": ["docs/**"],
        },
        claim_scope_agent_id=AGENT_ID,
    )
    workspace_guard_selected_todo: dict[str, Any] = {}

    def _record_workspace_guard_selection(
        *args: object,
        selected_todo: dict[str, Any] | None = None,
        **kwargs: object,
    ) -> dict[str, Any] | None:
        workspace_guard_selected_todo.update(selected_todo or {})
        if (selected_todo or {}).get("required_write_scopes"):
            return {
                "schema_version": "agent_workspace_guard_v1",
                "reason": "selected writing todo requires an independent worktree",
                "required_action": "move to the assigned worktree",
            }
        return None

    monkeypatch.setattr(
        "loopx.control_plane.quota.should_run.build_agent_workspace_guard",
        _record_workspace_guard_selection,
    )

    packet = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=["network"],
    )

    assert packet["selected_todo"]["todo_id"] == research["todo_id"]
    assert workspace_guard_selected_todo["todo_id"] == research["todo_id"]
    assert packet["effective_action"] == "normal_run"
    assert packet["normal_delivery_allowed"] is True
    assert packet["self_repair_allowed"] is False
    assert "workspace_guard" not in packet
    assert "boundary_projection_gap" not in packet


@pytest.mark.parametrize("other_agent_gate", [False, True])
def test_workspace_guard_keeps_alternative_todo_selection_available(
    monkeypatch: pytest.MonkeyPatch,
    other_agent_gate: bool,
) -> None:
    foreign_primary = quota_todo_item(
        todo_id="todo_foreign_primary001",
        index=1,
        priority="P0",
        title="Implement the primary repository change.",
        claimed_by=AGENT_ID,
        required_write_scopes=["src/**"],
    )
    matching_alternative = quota_todo_item(
        todo_id="todo_matching_alternative001",
        index=2,
        priority="P1",
        title="Implement the current repository change.",
        claimed_by=AGENT_ID,
        required_write_scopes=["src/**"],
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        quota_state="operator_gate" if other_agent_gate else "eligible",
        user_todo_items=[
            quota_todo_item(
                todo_id="todo_peer_gate001",
                role="user",
                task_class="user_gate",
                title="Approve the peer's independent action.",
                blocks_agent="codex-peer",
            )
        ] if other_agent_gate else [],
        agent_todo_items=[foreign_primary, matching_alternative],
        recommended_action=foreign_primary["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID, "codex-peer"],
            "write_scope": ["src/**"],
        },
        claim_scope_agent_id=AGENT_ID,
    )

    def _guard_foreign_primary_only(
        *args: object,
        selected_todo: dict[str, Any] | None = None,
        **kwargs: object,
    ) -> dict[str, Any] | None:
        if (selected_todo or {}).get("todo_id") == foreign_primary["todo_id"]:
            return {
                "schema_version": "agent_workspace_guard_v1",
                "reason": "selected Todo belongs to another repository",
                "required_action": "select a Todo matching the current worktree",
            }
        return None

    monkeypatch.setattr(
        "loopx.control_plane.quota.should_run.build_agent_workspace_guard",
        _guard_foreign_primary_only,
    )

    guarded = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id="turn-workspace-selection",
    )

    assert guarded["decision"] == "workspace_guard"
    assert guarded["normal_delivery_allowed"] is False
    assert guarded["action_portfolio"]["selection_policy"][
        "requires_explicit_turn_binding"
    ] is True
    assert guarded["interaction_contract"]["agent_channel"][
        "selection_required"
    ] is True

    selected = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        requested_action_todo_id=matching_alternative["todo_id"],
        turn_instance_id="turn-workspace-selection",
    )

    assert selected["normal_delivery_allowed"] is True
    assert selected["selected_todo"]["todo_id"] == matching_alternative["todo_id"]
    assert selected["selected_todo"]["selection_binding"] == (
        "pending_action_selection"
    )
    assert "workspace_guard" not in selected


@pytest.mark.parametrize("other_agent_gate", [False, True])
def test_deferred_selection_does_not_guard_an_unselected_default(
    monkeypatch: pytest.MonkeyPatch,
    other_agent_gate: bool,
) -> None:
    primary = quota_todo_item(
        todo_id="todo_primary001", priority="P0",
        title="Implement the primary repository change.", claimed_by=AGENT_ID,
        required_write_scopes=["src/**"],
    )
    alternative = quota_todo_item(
        todo_id="todo_alternative001", index=2, priority="P1",
        title="Implement the current repository change.", claimed_by=AGENT_ID,
        required_write_scopes=["src/**"],
    )
    prior = quota_todo_item(
        todo_id="todo_prior001", index=3, status="done",
        title="Complete the prior dependency.", claimed_by=AGENT_ID,
    )
    successor = quota_todo_item(
        todo_id="todo_successor001", index=4, status="deferred", priority="P0",
        title="Resume the ready successor.", claimed_by=AGENT_ID,
        resume_when="todo_done:todo_prior001",
    )
    status = quota_status_payload(
        goal_id=GOAL_ID, status="active",
        quota_state="operator_gate" if other_agent_gate else "eligible",
        agent_todo_items=[primary, alternative, prior, successor],
        user_todo_items=[quota_todo_item(
            todo_id="todo_peer_gate001", role="user", task_class="user_gate",
            title="Approve the peer's independent action.", blocks_agent="codex-peer",
        )] if other_agent_gate else [],
        recommended_action=primary["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID, "codex-peer"],
            "write_scope": ["src/**"],
        },
        claim_scope_agent_id=AGENT_ID,
    )
    guarded_ids: list[str | None] = []

    def guard(*args: object, selected_todo: dict[str, Any] | None = None,
              **kwargs: object) -> dict[str, Any] | None:
        guarded_ids.append((selected_todo or {}).get("todo_id"))
        if not selected_todo or selected_todo["todo_id"] == primary["todo_id"]:
            return {
                "schema_version": "agent_workspace_guard_v1",
                "reason": "default Todo belongs to another repository",
                "required_action": "move to its independent worktree",
            }
        return None

    monkeypatch.setattr(
        "loopx.control_plane.quota.should_run.build_agent_workspace_guard", guard,
    )
    packet = build_quota_should_run(
        status, goal_id=GOAL_ID, agent_id=AGENT_ID,
        requested_action_todo_id=alternative["todo_id"],
        turn_instance_id="turn-priority-selection",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE
        ),
    )
    assert packet["action_selection_qualification"]["reason"] == (
        "ready_deferred_successor_priority_preemption"
    )
    assert packet["normal_delivery_allowed"] is False
    assert "workspace_guard" not in packet
    assert guarded_ids == []


def test_boundary_projection_preserves_a_guarded_continuation_selection() -> None:
    queue_head = quota_todo_item(
        todo_id="todo_research001",
        index=1,
        priority="P1",
        title="Research the next provider contract.",
        claimed_by=AGENT_ID,
    )
    in_flight = quota_todo_item(
        todo_id="todo_implementation001",
        index=2,
        priority="P1",
        title="Continue the in-flight capability adapter.",
        claimed_by=AGENT_ID,
        required_write_scopes=["skills/**"],
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[queue_head, in_flight],
        recommended_action=queue_head["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
            "write_scope": ["docs/**"],
        },
        claim_scope_agent_id=AGENT_ID,
        latest_runs=[
            {
                "generated_at": "2026-08-23T00:00:00Z",
                "classification": "bounded_implementation_progress",
                "progress_scope": "agent_lane",
                "agent_id": AGENT_ID,
                "todo_id": in_flight["todo_id"],
                "delivery_outcome": "outcome_progress",
                "recommended_action": in_flight["text"],
            }
        ],
    )

    packet = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert packet["selected_todo"]["todo_id"] == in_flight["todo_id"]
    assert packet["agent_lane_next_action"]["selected_by"] == "in_flight_todo"
    assert packet["effective_action"] == "boundary_projection_repair"
    assert packet["normal_delivery_allowed"] is False
    assert packet["self_repair_allowed"] is True
    assert packet["action_portfolio"]["selection_policy"][
        "requires_explicit_turn_binding"
    ] is True
    assert packet["interaction_contract"]["agent_channel"][
        "selection_required"
    ] is True
    envelope = build_turn_envelope(packet)
    assert envelope["action"]["action_portfolio"] == packet["action_portfolio"]
    assert envelope["writeback"]["selection_required"] is True
    assert (
        packet["boundary_projection_gap"]["selected_todo"]["todo_id"]
        == (in_flight["todo_id"])
    )
    assert packet["boundary_projection_gap"]["missing_write_scopes"] == ["skills/**"]
