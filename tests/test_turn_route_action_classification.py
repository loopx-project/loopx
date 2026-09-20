"""Characterisation of how ``_typed_route`` classifies repair actions.

Repair routing is an explicit typed contract selected from ``EffectiveAction``.
Names do not acquire repair authority merely because they end in ``_repair`` or
``_repair_required``.
"""

from __future__ import annotations

from typing import Any

from loopx.control_plane.quota.effective_action import EffectiveAction
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.driver import (
    REPAIR_ACTIONS,
    REPLAN_ACTIONS,
    _typed_route,
)

EXPECTED_REGISTERED_REPAIR_ACTIONS = frozenset(
    {
        EffectiveAction.AGENT_WORKSPACE_REPAIR,
        EffectiveAction.BOUNDARY_PROJECTION_REPAIR,
        EffectiveAction.CAPABILITY_BRIDGE_REPAIR,
        EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR,
        EffectiveAction.CONTROL_PLANE_PROJECTION_REPAIR,
        EffectiveAction.CONTROL_PLANE_REPAIR,
        EffectiveAction.RUNTIME_USER_GATE_PROJECTION_REPAIR,
        EffectiveAction.STATE_PROJECTION_GAP_REPAIR,
        EffectiveAction.TODO_DECISION_SCOPE_PROJECTION_REPAIR,
    }
)
RETIRED_REPAIR_ACTIONS = (
    "capability_repair",
    "projection_repair",
    "self_repair",
    "state_projection_repair",
    "workspace_repair",
)


def _run_decision(**overrides: Any) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "ok": True,
        "goal_id": "g",
        "agent_id": "a",
        "agent_identity": {"agent_id": "a"},
        "decision": "run",
        "should_run": True,
        "effective_action": "normal_run",
        "state": "eligible",
        "recommended_action": "Advance.",
        "selected_todo": {"todo_id": "t001", "text": "Advance."},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "normal_run",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
            },
            "cli_channel": {"spend_after_validation": True},
        },
        "open_count": 0,
        "action_required": False,
    }
    decision.update(overrides)
    return decision


def _route_for(action: str) -> str:
    return _typed_route(
        build_turn_envelope(_run_decision(effective_action=action))
    ).value


def test_registered_repair_actions_are_explicit_and_typed() -> None:
    assert REPAIR_ACTIONS == EXPECTED_REGISTERED_REPAIR_ACTIONS
    for action in REPAIR_ACTIONS:
        assert isinstance(action, EffectiveAction)
        assert _route_for(action.value) == "repair_required", action


def test_repair_like_unknown_actions_do_not_gain_repair_authority() -> None:
    for action in RETIRED_REPAIR_ACTIONS + (
        "skip_repair",
        "restore_state_repair",
        "some_future_repair_required",
    ):
        assert _route_for(action) == "ready_for_host", action


def test_replan_actions_reach_the_replan_route() -> None:
    for action in sorted(REPLAN_ACTIONS):
        assert _route_for(action) == "replan_required", action


def test_remaining_registered_actions_reach_the_host_route() -> None:
    special = (
        {value for value in REPLAN_ACTIONS}
        | {EffectiveAction.GOVERNED_CAPABILITY_INTENT.value}
        | {action.value for action in REPAIR_ACTIONS}
    )
    for member in EffectiveAction:
        action = member.value
        if action in special:
            continue
        assert _route_for(action) == "ready_for_host", action
