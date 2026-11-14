"""Legacy missing fields do not authorize an explicitly absent/invalid guard."""
import pytest

from loopx.control_plane.effect_program import SettlementIdentity
from test_replan_receipt_projection import AGENT, GOAL, TURN, OBLIGATION, _payload, _project


@pytest.mark.parametrize("guard", [None, "", "malformed"])
@pytest.mark.parametrize("binding", ["todo", "replan"])
def test_explicit_invalid_guard_cannot_enter_legacy_settlement_compatibility(guard, binding):
    payload, _ = _payload()
    payload["replan_action_packet"]["settlement_only"] = True
    payload["heartbeat_receipt"]["semantic_replan_obligation_id"] = guard
    if binding == "replan":
        payload["heartbeat_receipt"]["settlement_identity"] = SettlementIdentity(
            goal_id=GOAL, agent_id=AGENT, todo_id=None, replan_obligation_id=OBLIGATION,
            turn_instance_id=TURN).as_dict()
    with pytest.raises(ValueError, match="semantic guard"):
        _project(payload)
