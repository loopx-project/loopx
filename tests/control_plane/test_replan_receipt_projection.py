"""A semantic review never changes the original causal settlement binding."""
from copy import deepcopy

import pytest

from loopx.control_plane.effect_program import SettlementIdentity
from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
from loopx.control_plane.work_items.interaction_contract import _turn_scoped_cli_settlement_context

GOAL, AGENT, TURN, TODO = "receipt-review-fixture", "review-agent", "original-turn", "todo_original"
OBLIGATION = "replan-1111111111111111"


def _payload():
    identity = SettlementIdentity(goal_id=GOAL, agent_id=AGENT, todo_id=TODO, turn_instance_id=TURN)
    return {"goal_id": GOAL, "agent_identity": {"agent_id": AGENT},
        "selected_todo": None,
        "replan_action_packet": {"obligation_id": OBLIGATION, "settlement_only": False},
        "heartbeat_receipt": {"status": "replayed", "turn_instance_id": TURN,
            "semantic_replan_obligation_id": OBLIGATION, "settlement_identity": identity.as_dict()}}, identity


def _project(payload):
    return _turn_scoped_cli_settlement_context(payload, available_capabilities=None,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile("generic_cli"),
        turn_instance_id=TURN)


def test_fresh_vision_review_keeps_terminal_todos_original_receipt_binding():
    payload, identity = _payload()
    before = deepcopy(payload)
    plan, _ = _project(payload)
    assert plan["identity"] == identity.as_dict()
    for step in plan["ordered_steps"]:
        if command := step.get("command_template"):
            assert "--todo-id " + TODO in command
            assert "--replan-obligation-id" not in command
    assert payload == before


@pytest.mark.parametrize("field,value", [
    ("goal_id", "another-goal"), ("agent_id", "another-agent"),
    ("turn_instance_id", "another-turn"), ("semantic_replan_obligation_id", "replan-2222222222222222"),
])
def test_review_projection_rejects_a_conflicting_receipt(field, value):
    payload, _ = _payload()
    if field == "semantic_replan_obligation_id":
        payload["heartbeat_receipt"][field] = value
    else:
        payload["heartbeat_receipt"]["settlement_identity"][field] = value
    with pytest.raises((ValueError, RuntimeError)):
        _project(payload)


def test_unbound_first_review_still_has_a_direct_replan_plan():
    payload, _ = _payload()
    del payload["heartbeat_receipt"]
    plan, _ = _project(payload)
    assert plan["identity"]["replan_obligation_id"] == OBLIGATION
    assert plan["identity"].get("todo_id") is None
