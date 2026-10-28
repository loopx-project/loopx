"""Frontier wire reduction must not rewrite historical signed Turn plans."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
    quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.agents.agent_scope_frontier import (
    AgentScopeFrontierAction,
    build_agent_scope_frontier_payload,
)
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.journal_store import (
    load_loopx_turn_plan_from_journal,
    load_turn_journal,
    turn_journal_path,
    write_turn_journal_checkpoint,
)
from loopx.control_plane.turn_driver.turn_journal_runtime import (
    interpret_turn_journal_projection,
)


# Captured before the v1 writer migration. These characterize historical bytes;
# the field-preservation and mutation assertions below define the invariant.
V0_SIGNATURE_HASHES = {
    True: "sha256:c6d7977aef90126a9cff9525be407c69dd62152e61fb9cec15687cd54ef0fe5d",
    False: "sha256:8f04ecb05ca2156bd59123ad8df6301122552fc62ce79dd7fadce1772f10a5fc",
}


def _legacy_decision(*, action_present=True):
    frontier = {
        "schema_version": "agent_scope_frontier_v0",
        "effective_action": "successor_replan_required",
        "blocks_delivery": True,
        "quiet_noop_allowed": False,
        "requires_replan": True,
        "recommended_action": "Resolve the ready successor.",
        "spend_policy": "spend after validated successor writeback",
    }
    if action_present:
        frontier["action"] = "successor_replan_required"
    return {
        "ok": True,
        "goal_id": "frontier-fixture",
        "agent_identity": {"agent_id": "frontier-agent"},
        "decision": "run",
        "should_run": True,
        "effective_action": "successor_replan_required",
        "state": "eligible",
        "recommended_action": "Resolve the ready successor.",
        "selected_todo": {"todo_id": "todo_successor", "status": "open"},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "successor_replan_required",
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "primary_action": "Resolve the ready successor.",
            },
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "cli_channel": {},
        },
        "agent_scope_frontier": frontier,
    }


def _signature_hash(document):
    return (
        "sha256:"
        + sha256(
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )


@pytest.mark.parametrize("action_present", [True, False])
def test_v0_frontier_retains_signed_fields(action_present):
    source = _legacy_decision(action_present=action_present)
    before = deepcopy(source)
    envelope = build_turn_envelope(source)
    assert source == before
    assert (
        envelope["contract_capsule"]["agent_scope_frontier"]
        == source["agent_scope_frontier"]
    )
    signature = turn_envelope_action_signature_document(envelope)
    assert signature == quota_action_signature_document(source)
    assert _signature_hash(signature) == envelope["action_signature"]["source_hash"]
    assert _signature_hash(signature) == V0_SIGNATURE_HASHES[action_present]

    for key in source["agent_scope_frontier"]:
        changed = deepcopy(envelope)
        del changed["contract_capsule"]["agent_scope_frontier"][key]
        assert (
            _signature_hash(turn_envelope_action_signature_document(changed))
            != V0_SIGNATURE_HASHES[action_present]
        )


def test_v1_reduces_only_the_frontier_slot_and_keeps_root_action():
    legacy = _legacy_decision()
    source = deepcopy(legacy)
    source["agent_scope_frontier"] = build_agent_scope_frontier_payload(
        agent_id="frontier-agent",
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        requires_replan=True,
        candidate_counts={},
        reason="ready successor",
        recommended_action="Resolve the ready successor.",
        spend_policy="spend after validated successor writeback",
    )
    legacy_envelope = build_turn_envelope(legacy)
    envelope = build_turn_envelope(source)
    capsule = envelope["contract_capsule"]["agent_scope_frontier"]
    expected = dict(legacy["agent_scope_frontier"])
    del expected["effective_action"]
    expected.update(schema_version="agent_scope_frontier_v1", agent_id="frontier-agent",
                    candidate_counts={}, reason="ready successor")
    assert capsule == expected
    assert (
        envelope["effective_action"]
        == legacy_envelope["effective_action"]
        == "successor_replan_required"
    )
    signature = turn_envelope_action_signature_document(envelope)
    legacy_signature = turn_envelope_action_signature_document(legacy_envelope)
    assert signature == quota_action_signature_document(source)
    assert envelope["action_signature"]["matches"] is True
    assert _signature_hash(signature) != _signature_hash(legacy_signature)
    # The wire reduction is versioned, signed, and confined to this capsule.
    legacy_signature["contract_capsule"]["agent_scope_frontier"] = expected
    assert signature == legacy_signature


@pytest.mark.parametrize("action_present", [True, False])
def test_persisted_v0_plan_resumes_without_rewriting_frontier(tmp_path, action_present):
    envelope = build_turn_envelope(_legacy_decision(action_present=action_present))
    plan = build_loopx_turn_plan(
        envelope, host="generic-cli", execution_mode="isolated-headless"
    )
    assert plan["route"]["kind"] == "replan_required"
    turn_key = plan["transaction"]["turn_key"]
    path = turn_journal_path(tmp_path, goal_id="frontier-fixture", turn_key=turn_key)
    journal = {
        "schema_version": "loopx_turn_journal_v0",
        "goal_id": "frontier-fixture",
        "turn_key": turn_key,
        "status": "in_progress",
        "completed_phases": [],
        "plan": plan,
    }
    write_turn_journal_checkpoint(path, journal)
    # Host execution and typed-result validation commit as one checkpoint.
    journal["completed_phases"] = ["host_execute", "typed_result"]
    write_turn_journal_checkpoint(path, journal)
    original_bytes = path.read_bytes()
    resumed = load_loopx_turn_plan_from_journal(
        tmp_path, goal_id="frontier-fixture", turn_key=turn_key
    )
    assert resumed == plan
    assert (
        _signature_hash(
            turn_envelope_action_signature_document(resumed["turn_envelope"])
        )
        == V0_SIGNATURE_HASHES[action_present]
    )
    inspection = interpret_turn_journal_projection(
        load_turn_journal(path),
        goal_id="frontier-fixture",
        agent_id="frontier-agent",
        turn_key=turn_key,
    )
    assert inspection["journal_consistent"] is True
    assert inspection["recovery_decision"]["can_continue"] is True
    assert inspection["recovery_decision"]["resume_from"] == "validation"
    assert inspection["recovery_decision"]["reinvoke_host"] is False
    assert path.read_bytes() == original_bytes
