"""Conversation reuse does not change Turn identity or recovery authority."""

from copy import deepcopy

import pytest

from loopx.control_plane.turn_driver.driver import (
    FailedTurnSessionRecoveryError,
    build_loopx_turn_plan,
    reconcile_failed_turn_session_request,
)
from tests.test_loopx_turn_driver import _envelope


def _plan(envelope, **options):
    return build_loopx_turn_plan(envelope, host="codex-cli",
        execution_mode="isolated-headless", turn_instance_id="session-policy", **options)


@pytest.mark.parametrize(("value", "encoded"), [
    (True, "True"), (1.0, "1.0"), (0.000001, "1e-06"),
    (1152921504606846977, "1152921504606846977"),
    ([True, "task"], "[True, 'task']"),
    ({"name": "task", "active": True}, "{'name': 'task', 'active': True}"),
    ("任务-\"quoted\"", "任务-\"quoted\""),
])
def test_historical_binding_codec_survives_plan_and_failed_recovery(value, encoded):
    envelope = _envelope()
    envelope["action"]["selected_todo"]["todo_id"] = encoded
    binding = {"schema_version": "loopx_turn_session_binding_v0",
        "goal_id": "fixture-goal", "agent_id": "codex-fixture", "todo_id": value}
    before = deepcopy((envelope, binding))
    plan = _plan(envelope, session_scope="todo", session_binding=binding)
    assert plan["ok"] and plan["session"]["action"] == "resume"
    request = {"turn_envelope": envelope, "session": plan["session"], "original": "retained"}
    assert reconcile_failed_turn_session_request(request, session_binding=binding) == request
    assert (envelope, binding) == before


@pytest.mark.parametrize("binding", [None, {}, {"schema_version": "future"},
    {"schema_version": "loopx_turn_session_binding_v0", "goal_id": "other"}])
@pytest.mark.parametrize("scope", ["todo", "agent"])
def test_fresh_ignores_old_binding_after_lineage_validation(binding, scope):
    plan = _plan(_envelope(), session_scope=scope, session_binding=binding,
        iteration_context_policy="fresh")
    assert plan["ok"] and plan["session"] == {
        "schema_version": "loopx_turn_session_binding_v0", "action": "start_new",
        "binding_status": "existing_binding_ignored" if binding else "not_found",
        "context_policy": {"schema_version": "loopx_iteration_context_policy_v0",
            "mode": "fresh", "scope": "iteration",
            **({"binding_scope": "agent"} if scope == "agent" else {})},
    }
    invalid = _envelope()
    invalid["agent_id"] = ""
    with pytest.raises(ValueError, match="identity.agent_id must be a non-empty string"):
        _plan(invalid, session_scope=scope, session_binding=binding,
            iteration_context_policy="fresh")


@pytest.mark.parametrize("binding", [None, {}, {"schema_version": "future"}])
@pytest.mark.parametrize("scope", ["todo", "agent"])
def test_wait_does_not_inspect_a_session(binding, scope):
    envelope = _envelope(should_run=False, quiet_noop_allowed=True)
    plan = _plan(envelope, session_scope=scope, session_binding=binding)
    assert plan["ok"] and plan["session"] == {
        "schema_version": "loopx_turn_session_binding_v0", "action": "none",
        "binding_status": "not_applicable",
        "context_policy": {"schema_version": "loopx_iteration_context_policy_v0",
            "mode": "resume", "scope": "iteration",
            **({"binding_scope": "agent"} if scope == "agent" else {})},
    }


@pytest.mark.parametrize("value", [None, False, 0, 0.0, "", [], {}])
def test_false_binding_identity_is_rejected_without_a_fresh_fallback(value):
    envelope = _envelope()
    binding = {"schema_version": "loopx_turn_session_binding_v0",
        "goal_id": "fixture-goal", "agent_id": "codex-fixture", "todo_id": value}
    plan = _plan(envelope, session_scope="todo", session_binding=binding)
    assert not plan["ok"] and plan["session"]["binding_status"] == "identity_mismatch"
    assert plan["error"] == "session binding does not match the current goal_id, agent_id, todo_id"
    request = {"turn_envelope": envelope, "session": {"action": "start_new"}}
    with pytest.raises(FailedTurnSessionRecoveryError) as caught:
        reconcile_failed_turn_session_request(request, session_binding=binding)
    assert caught.value.reason == "session_binding_identity_mismatch"
    assert str(caught.value) == "failed-Turn recovery " + plan["error"]


def test_agent_scope_reuses_conversation_without_changing_original_turn_identity():
    envelope = _envelope()
    binding = {"schema_version": "loopx_turn_session_binding_v0",
        "goal_id": "fixture-goal", "agent_id": "codex-fixture", "todo_id": "previous-task"}
    fresh = _plan(envelope, session_scope="agent")
    resumed = _plan(envelope, session_scope="agent", session_binding=binding)
    assert resumed["ok"] and resumed["session"]["action"] == "resume"
    assert resumed["transaction"]["settlement_plan"]["identity"] == fresh["transaction"]["settlement_plan"]["identity"]
    request = {"turn_envelope": envelope, "session": fresh["session"],
        "transaction": fresh["transaction"]}
    recovered = reconcile_failed_turn_session_request(request, session_binding=binding)
    assert recovered["session"]["binding_status"] == "failed_turn_recovery"
    assert recovered["transaction"] == fresh["transaction"]
    assert request["session"]["action"] == "start_new"
