from __future__ import annotations

from loopx.control_plane.quota.should_run import (
    build_quota_should_run as bounded_build_quota_should_run,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.testing.quota_should_run_parity import (
    build_quota_should_run_parity,
)
from loopx.quota import build_quota_should_run as facade_build_quota_should_run

GOAL_ID = "quota-parity-fixture"


def test_absent_boundary_keeps_delivery_and_capability_decisions() -> None:
    """No declared scopes must not crash or invent a capability grant."""
    from loopx.control_plane.scheduler.execution_context import (
        GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    for capabilities, expected in [([], "run"), (["network"], "repair_bridge")]:
        payload = quota_status_payload(
            goal_id=GOAL_ID, status="active", recommended_action="Check the route.",
            agent_todo_items=[{
                "todo_id": "todo_missing_boundary", "role": "agent", "status": "open",
                "task_class": "advancement_task", "priority": "P1",
                "text": "Check the route.", "required_capabilities": capabilities,
            }],
        )
        goal = payload["run_history"]["goals"][0]
        goal.pop("adapter_kind")
        goal.pop("adapter_status")
        payload["attention_queue"]["items"][0]["project_asset"].pop("stop_condition")
        packet = bounded_build_quota_should_run(
            payload, goal_id=GOAL_ID, available_capabilities=["shell"],
            scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
        )
        assert packet["goal_boundary"] is None
        assert packet["decision"] == expected
        assert not (packet.get("delivery_workspace") or {}).get("allowed_write_scopes")


def test_facade_builds_through_bounded_should_run_module() -> None:
    todo_text = "[P1] Advance the bounded slice."
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )

    facade_packet = facade_build_quota_should_run(payload, goal_id=GOAL_ID)
    bounded_packet = bounded_build_quota_should_run(payload, goal_id=GOAL_ID)

    assert facade_packet == bounded_packet
    assert bounded_packet["decision"] == "run"


def test_advancement_run_parity_surface() -> None:
    todo_text = "[P1] Advance the bounded slice."
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )

    parity = build_quota_should_run_parity(payload, goal_id=GOAL_ID)

    assert parity["decision"] == "run"
    assert parity["should_run"] is True
    assert parity["effective_action"] == "normal_run"
    assert parity["lane"] == "advancement_task"
    assert parity["obligation"] == "advance_one_bounded_segment"
    assert parity["must_attempt_work"] is True
    assert parity["interaction_mode"] == "bounded_delivery"
    assert parity["capability_gate_action"] is None


def test_capability_gate_repair_parity_surface() -> None:
    todo_text = "[P1] Network-only slice."
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
                "required_capabilities": ["network"],
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )

    parity = build_quota_should_run_parity(
        payload,
        goal_id=GOAL_ID,
        available_capabilities=["shell"],
    )

    assert parity["decision"] == "repair_bridge"
    assert parity["should_run"] is True
    assert parity["effective_action"] == "capability_bridge_repair"
    assert parity["capability_gate_action"] == "repair_bridge"
    assert parity["lane"] == "advancement_task"
