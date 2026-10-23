"""Literal v0 read semantics; these expectations are independent of the writer."""
from copy import deepcopy

import pytest

from loopx.control_plane.work_items.interaction_contract import (
    protocol_action_packet_fields, render_protocol_action_packet_summary,
)


@pytest.mark.parametrize("payload,expected", [
    ({}, "actor=agent user_action_required=false agent_action_required=false quiet_noop_allowed=true llm=no_api agent_action=quiet no-op; no material transition"),
    ({"execution_obligation": {"must_attempt_work": True}},
     "actor=agent user_action_required=false agent_action_required=true quiet_noop_allowed=false llm=no_api agent_action=advance scope-bounded work with validation"),
    ({"requires_user_action": True},
     "actor=user user_action_required=true agent_action_required=false quiet_noop_allowed=false llm=no_api user_action=wait for user/owner action after surfacing the blocker or gate"),
    ({"requires_user_action": True, "scoped_user_gate_fallback": {}},
     "actor=agent_with_user_gate user_action_required=true agent_action_required=true quiet_noop_allowed=false llm=no_api agent_action=surface the scoped user gate, then advance one non-gated fallback"),
    ({"requires_user_action": True, "execution_obligation": {"must_attempt_work": True, "delivery_allowed": True}, "gate_prompt": "review evidence"},
     "actor=agent_with_user_gate user_action_required=true agent_action_required=true quiet_noop_allowed=false llm=no_api user_action_pending=true user_action=review evidence agent_action=advance scope-bounded work with validation"),
    ({"execution_obligation": {"must_attempt_work": True, "kind": "outcome_floor_recovery"}},
     "actor=agent user_action_required=false agent_action_required=true quiet_noop_allowed=false llm=no_api agent_action=produce the required outcome-floor evidence artifact or write the concrete bl..."),
    ({"agent_work_mode": "monitor_only", "requires_user_action": True, "execution_obligation": {"must_attempt_work": True}, "work_lane_contract": {"action": "inspect observation"}},
     "actor=agent user_action_required=false agent_action_required=true quiet_noop_allowed=false llm=no_api agent_action=inspect observation"),
    ({"interaction_contract": {"agent_channel": {"must_attempt": False, "delivery_allowed": True, "quiet_noop_allowed": False}}, "execution_obligation": {"must_attempt_work": True}},
     "actor=agent user_action_required=false agent_action_required=false quiet_noop_allowed=false llm=no_api agent_action=quiet no-op; no material transition"),
    ({"interaction_contract": {"agent_channel": {"must_attempt": 1, "delivery_allowed": True, "quiet_noop_allowed": False}}},
     "actor=agent user_action_required=false agent_action_required=false quiet_noop_allowed=true llm=no_api agent_action=quiet no-op; no material transition"),
    ({"execution_obligation": {"must_attempt_work": [], "delivery_allowed": {}}, "work_lane_contract": {"lane": 7}, "automation_liveness": {"automation_action": True, "pause_allowed": False}, "scheduler_hint": {"action": 0}},
     "actor=agent user_action_required=false agent_action_required=false quiet_noop_allowed=true lane=7 automation=true pause_allowed=false llm=no_api agent_action=quiet no-op; no material transition"),
    ({"execution_obligation": {"must_attempt_work": "false"}, "recommended_action": False},
     "actor=agent user_action_required=false agent_action_required=true quiet_noop_allowed=false llm=no_api agent_action=False"),
])
def test_original_ordered_fields(payload, expected):
    before = deepcopy(payload)
    assert render_protocol_action_packet_summary(protocol_action_packet_fields(payload)) == expected
    assert payload == before


def test_original_unicode_codepoints_and_python_whitespace():
    payload = {"execution_obligation": {"must_attempt_work": True},
               "recommended_action": "\x1c" + "😀" * 81 + "\u0085"}
    fields = protocol_action_packet_fields(payload)
    assert fields["agent_action"] == "😀" * 77 + "..."
    # U+FEFF is not Python whitespace; historical text must not silently change.
    payload["recommended_action"] = "\ufeff a  b \ufeff"
    assert protocol_action_packet_fields(payload)["agent_action"] == "\ufeff a b \ufeff"


def test_original_non_scalar_values_and_boolean_rendering():
    payload = {"execution_obligation": {"must_attempt_work": True},
               "recommended_action": [True, None, "text"],
               "work_lane_contract": {"lane": [True, None]}}
    fields = protocol_action_packet_fields(payload)
    assert fields["agent_action"] == "[True, None, 'text']"
    assert fields["lane"] == [True, None]
    assert render_protocol_action_packet_summary({"a": True, "b": None, "c": [False]}) == "a=true b=None c=[False]"


def test_whitespace_owner_action_does_not_acquire_a_default():
    fields = protocol_action_packet_fields({"requires_user_action": True,
        "capability_gate": {"action": "ask_owner", "owner_action": "   "}})
    assert "user_action" not in fields


def test_original_scalar_types_survive_the_python_read_api():
    fields = protocol_action_packet_fields({"work_lane_contract": {"lane": 1.0},
        "automation_liveness": {"automation_action": [1.0, False]}})
    assert type(fields["lane"]) is float
    assert "lane=1.0 automation=[1.0, False]" in render_protocol_action_packet_summary(fields)
