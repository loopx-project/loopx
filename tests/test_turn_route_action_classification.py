"""Characterisation of how ``_typed_route`` classifies repair actions.

Repair routing is an explicit typed contract selected from ``EffectiveAction``.
Names do not acquire repair authority merely because they end in ``_repair`` or
``_repair_required``.
"""

from __future__ import annotations

from typing import Any

import pytest

from loopx.control_plane.quota.effective_action import EffectiveAction
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.turn_contract_generated import LoopXTurnRoute
from loopx.control_plane.turn_driver.driver import (
    _typed_route,
)

REPLAN_ACTIONS = {"autonomous_replan", "autonomous_replan_required", "successor_replan_required"}

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
    for action in EXPECTED_REGISTERED_REPAIR_ACTIONS:
        assert isinstance(action, EffectiveAction)
        assert _route_for(action.value) == "repair_required", action


def test_repair_like_unknown_actions_do_not_gain_repair_authority() -> None:
    for action in RETIRED_REPAIR_ACTIONS + (
        "skip_repair",
        "restore_state_repair",
        "some_future_repair_required",
    ):
        assert _route_for(action) == "ready_for_host", action


@pytest.mark.parametrize("delivery_allowed", [True, False])
def test_replan_actions_reach_the_replan_route(delivery_allowed: bool) -> None:
    for action in sorted(REPLAN_ACTIONS):
        decision = _run_decision(effective_action=action)
        decision["interaction_contract"]["agent_channel"]["delivery_allowed"] = delivery_allowed
        envelope = build_turn_envelope(decision)
        assert envelope["action"]["delivery_allowed"] is delivery_allowed
        assert _typed_route(envelope) is LoopXTurnRoute.REPLAN_REQUIRED, action


@pytest.mark.parametrize("control", ["should_run", "must_attempt"])
@pytest.mark.parametrize("action", ["autonomous_replan_required", "successor_replan_required"])
def test_replan_still_requires_an_admitted_attempt(control: str, action: str) -> None:
    decision = _run_decision(effective_action=action)
    channel = decision["interaction_contract"]["agent_channel"]
    channel["delivery_allowed"] = False
    if control == "should_run":
        decision["should_run"] = False
    else:
        channel["must_attempt"] = False
    assert _typed_route(build_turn_envelope(decision)) is LoopXTurnRoute.BLOCKED


@pytest.mark.parametrize("action", ["normal_run", "control_plane_repair", "future_repair"])
def test_replan_does_not_admit_other_delivery_routes(action: str) -> None:
    decision = _run_decision(effective_action=action)
    decision["interaction_contract"]["agent_channel"]["delivery_allowed"] = False
    assert _typed_route(build_turn_envelope(decision)) is LoopXTurnRoute.BLOCKED


def test_replan_still_requires_the_action_signature() -> None:
    decision = _run_decision(effective_action="autonomous_replan_required")
    decision["interaction_contract"]["agent_channel"]["delivery_allowed"] = False
    envelope = build_turn_envelope(decision)
    envelope["action_signature"]["matches"] = False
    assert _typed_route(envelope) is LoopXTurnRoute.CONTRACT_ERROR


def test_remaining_registered_actions_reach_the_host_route() -> None:
    special = (
        {value for value in REPLAN_ACTIONS}
        | {EffectiveAction.GOVERNED_CAPABILITY_INTENT.value}
        | {action.value for action in EXPECTED_REGISTERED_REPAIR_ACTIONS}
    )
    for member in EffectiveAction:
        action = member.value
        if action in special:
            continue
        assert _route_for(action) == "ready_for_host", action


def test_unregistered_actions_keep_reaching_the_host_route() -> None:
    """Admission is unchanged: an unknown value still runs, it is not refused."""

    for action in ("totally_unknown_action", "future_channel_due", "skip_repair"):
        assert _route_for(action) == "ready_for_host", action

@pytest.mark.parametrize("command", [None, "", False, 0, [], {}])
def test_capability_route_rejects_empty_commands(command: Any) -> None:
    envelope = build_turn_envelope(_run_decision())
    envelope["effective_action"] = "governed_capability_intent"
    envelope["action"]["capability_intent"] = {
        "schema_version": "pending_capability_intent_projection_v0",
        "goal_id": "g", "agent_id": "a", "command": command,
    }
    assert _typed_route(envelope) is LoopXTurnRoute.CONTRACT_ERROR


@pytest.mark.parametrize("effective_action", ["normal_run", "control_plane_repair", "autonomous_replan_required", "governed_capability_intent", "future_repair"])
def test_route_refusal_order_preserves_attempt_and_user_wait_gates(effective_action: str) -> None:
    envelope = build_turn_envelope(_run_decision())
    envelope["effective_action"] = effective_action
    envelope["user"]["action_required"] = True
    envelope["action"].update(must_attempt=False, quiet_noop_allowed=True)
    # A user notice does not override an admitted attempt's refusal in this
    # owner. Loop controller applies its own user precedence after validation.
    assert _typed_route(envelope) is LoopXTurnRoute.BLOCKED
    envelope["should_run"] = False
    assert _typed_route(envelope) is LoopXTurnRoute.USER_ACTION_REQUIRED
    envelope["user"]["action_required"] = False
    assert _typed_route(envelope) is LoopXTurnRoute.WAIT
    envelope["action_signature"]["matches"] = False
    assert _typed_route(envelope) is LoopXTurnRoute.CONTRACT_ERROR


@pytest.mark.parametrize("value", [1.0, 1e-7, True, [1, None], {"a": False}])
def test_historical_route_text_keeps_python_scalar_codec(value: Any) -> None:
    envelope = build_turn_envelope(_run_decision())
    envelope["action_signature"].update(source_hash=value, envelope_hash=str(value))
    envelope["effective_action"] = value
    assert _typed_route(envelope) is LoopXTurnRoute.READY_FOR_HOST
