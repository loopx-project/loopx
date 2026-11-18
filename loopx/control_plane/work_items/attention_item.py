from __future__ import annotations

from typing import Any, Callable, Optional


ProjectAssetBuilder = Callable[..., dict[str, Any]]
DreamingLaneBadgeBuilder = Callable[[Optional[dict[str, Any]]], Optional[dict[str, Any]]]


def attention_item(
    *,
    goal_id: str,
    status: str,
    waiting_on: str,
    severity: str,
    recommended_action: str,
    source: str,
    build_project_asset: ProjectAssetBuilder,
    compact_dreaming_lane_badge: DreamingLaneBadgeBuilder,
    operator_question: str | None = None,
    agent_command: str | None = None,
    controller_stage: str | None = None,
    missing_gates: list[str] | None = None,
    next_handoff_condition: str | None = None,
    lifecycle_phase: str | None = None,
    lifecycle_flags: list[str] | None = None,
    user_todos: dict[str, Any] | None = None,
    agent_todos: dict[str, Any] | None = None,
    todo_state_file: str | None = None,
    dreaming_proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_asset = build_project_asset(
        status=status,
        waiting_on=waiting_on,
        recommended_action=recommended_action,
        operator_question=operator_question,
        agent_command=agent_command,
        missing_gates=missing_gates,
        next_handoff_condition=next_handoff_condition,
    )
    item: dict[str, Any] = {
        "goal_id": goal_id,
        "status": status,
        "waiting_on": waiting_on,
        "severity": severity,
        "recommended_action": recommended_action,
        "project_asset": project_asset,
        "source": source,
    }
    if operator_question:
        item["operator_question"] = operator_question
    if agent_command:
        item["agent_command"] = agent_command
    if controller_stage:
        item["controller_stage"] = controller_stage
    if missing_gates:
        item["missing_gates"] = missing_gates
    if next_handoff_condition:
        item["next_handoff_condition"] = next_handoff_condition
    if lifecycle_phase:
        item["lifecycle_phase"] = lifecycle_phase
    if lifecycle_flags:
        item["lifecycle_flags"] = lifecycle_flags
    if user_todos:
        item["user_todos"] = user_todos
    if agent_todos:
        item["agent_todos"] = agent_todos
    if todo_state_file:
        item["todo_state_file"] = todo_state_file
    if dreaming_proposal:
        dreaming_lane_badge = compact_dreaming_lane_badge(dreaming_proposal)
        item["dreaming_proposal"] = dreaming_proposal
        project_asset["dreaming_proposal"] = dreaming_proposal
        if dreaming_lane_badge:
            item["dreaming_lane_badge"] = dreaming_lane_badge
            project_asset["dreaming_lane_badge"] = dreaming_lane_badge
    return item
