"""The fixture oracle keeps ordinary lane and original terminal intent distinct."""
import pytest

from tests.control_plane.host_checkpoint_fixture import checkpoint_decision


@pytest.mark.parametrize("intent", ["ordinary", "stored", "deferred", "other-turn", "goal-wide"])
def test_fixture_checks_the_original_direction_scope(intent):
    identity = {"effect_id": "original-effect", "turn_instance_id": "original-turn"}
    todo = {"status": "done", "completion_continuation": "active_goal"}
    if intent == "stored":
        todo["no_followup"] = True
    elif intent in {"deferred", "other-turn"}:
        todo.update(completion_requested_continuation="no_followup",
                    completion_turn_key="other-turn" if intent == "other-turn" else identity["effect_id"])
    terminal = intent in {"stored", "deferred", "goal-wide"}
    context = {"read_context_id": "fresh-read", "settlement_identity": identity,
               "basis": {"todo": None if intent == "goal-wide" else todo, "agent_vision": {},
                         "frontier": {"scope": "goal" if terminal else "agent_lane"}}}
    assert checkpoint_decision(context)["read_context_id"] == "fresh-read"
    context["basis"]["frontier"]["scope"] = "agent_lane" if terminal else "goal"
    with pytest.raises(AssertionError):
        checkpoint_decision(context)
