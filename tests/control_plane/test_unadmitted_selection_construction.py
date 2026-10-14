"""Typed rejection builds recovery before any settlement capability exists."""
from __future__ import annotations

from copy import deepcopy
import shlex

import pytest

from loopx.cli_commands.quota_action_selection import (
    _requested_quota_action_selection_preflight,
)
from loopx.control_plane.work_items import interaction_contract
from loopx.control_plane.effect_program import interpret_quota_should_run_packet
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.scheduler.execution_context import (
    resolve_scheduler_execution_context,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.work_items.action_selection_contract import (
    action_selection_needs_recovery,
    action_selection_context,
    bind_action_selection_recovery_command,
)


def _source(state: str) -> dict:
    return {
        "goal_id": "selection-fixture", "agent_identity": {"agent_id": "fixture-agent"},
        "ok": True, "should_run": True, "effective_action": "autonomous_replan",
        "recommended_action": "Handle the preempting replan",
        "execution_obligation": {"must_attempt_work": True, "delivery_allowed": True},
        "action_selection_qualification": {
            "schema_version": "action_selection_qualification_v0", "state": state,
            "requested_todo_id": "todo_pending", "reason": "current_delivery_gate",
            "recovery_action": "reenter_guard_without_selection",
        },
    }


@pytest.mark.parametrize("state", ["deferred", "rejected"])
@pytest.mark.parametrize(
    ("scheduler_context", "expected_profile"),
    [
        pytest.param(None, None, id="omitted"),
        pytest.param(resolve_scheduler_execution_context(None), None, id="unresolved"),
        pytest.param(
            scheduler_execution_context_for_runtime_profile("generic_cli"),
            "generic_cli",
            id="declared",
        ),
    ],
)
def test_refused_selection_recovery_preserves_optional_scheduler_context(
    state, scheduler_context, expected_profile,
):
    contract = interaction_contract.build_interaction_contract(
        _source(state), scheduler_execution_context=scheduler_context,
        turn_instance_id="selection-turn", runtime_root="fixture runtime",
        registry_path="fixture registry.json", available_capabilities=["shell"],
    )
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["cli_channel"]["spend_allowed_now"] is False
    assert contract["cli_channel"]["spend_after_validation"] is False
    [command] = contract["cli_channel"]["next_cli_actions"]
    assert contract["agent_channel"]["primary_action"] == command
    argv = shlex.split(command)
    for flag, expected in {
        "--registry": "fixture registry.json", "--runtime-root": "fixture runtime",
        "--goal-id": "selection-fixture", "--agent-id": "fixture-agent",
        "--turn-instance-id": "selection-turn", "--available-capability": "shell",
    }.items():
        assert argv[argv.index(flag) + 1] == expected
    assert "--todo-id" not in argv and "--replan-obligation-id" not in argv
    if expected_profile is None:
        assert "--runtime-profile" not in argv
    else:
        assert argv[argv.index("--runtime-profile") + 1] == expected_profile


@pytest.mark.parametrize("state", ["deferred", "rejected"])
@pytest.mark.parametrize("grant", ["work", "spend", "bypass", "settlement_plan", "should_run", "actionable"])
def test_unadmitted_packet_cannot_reintroduce_authority_at_typed_readers(state, grant):
    source = _source(state)
    source["interaction_contract"] = interaction_contract.build_interaction_contract(source)
    source["ok"] = source["should_run"] = False
    source["execution_obligation"] = {"must_attempt_work": False}
    contract = source["interaction_contract"]
    if grant == "work":
        contract["agent_channel"].update(must_attempt=True, delivery_allowed=True, quiet_noop_allowed=False)
    elif grant == "spend":
        contract["cli_channel"]["spend_after_validation"] = True
    elif grant == "bypass":
        source["safe_bypass_allowed"] = True
    elif grant == "should_run":
        source["should_run"] = True
    elif grant == "actionable":
        source["actionable_by_codex"] = True
    else:
        contract["cli_channel"]["settlement_plan"] = {}
    with pytest.raises(EffectRuntimeRejected, match="unadmitted action selection"):
        interpret_quota_should_run_packet(source)
    with pytest.raises(EffectRuntimeRejected, match="unadmitted action selection"):
        build_turn_envelope(source)
    with pytest.raises(EffectRuntimeRejected, match="unadmitted action selection"):
        effect_runtime_result("quota.scoped_override.project", {
            "override": {}, "interaction_contract": contract,
            "selection_context": action_selection_context(source),
        })


@pytest.mark.parametrize("state", ["deferred", "rejected"])
def test_recovery_never_constructs_settlement_or_executable_primary_action(monkeypatch, state):
    def unexpected(*args, **kwargs):
        pytest.fail("unadmitted selection constructed an executable projection")

    monkeypatch.setattr(interaction_contract, "_turn_scoped_cli_settlement_context", unexpected)
    monkeypatch.setattr(interaction_contract, "build_primary_action_projection", unexpected)
    source = _source(state)
    contract = interaction_contract.build_interaction_contract(
        source, turn_instance_id="selection-turn", runtime_root="/runtime with spaces",
        available_capabilities=["shell"],
    )
    source["interaction_contract"] = contract
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["agent_channel"]["delivery_allowed"] is False
    cli = contract["cli_channel"]
    assert cli["spend_after_validation"] is False
    assert cli["selection_required"] is False
    for field in ("settlement_plan", "replan_settlement_contract", "selection_command", "selection_policy_ref"):
        assert field not in cli
    bind_action_selection_recovery_command(
        source, registry_path="/registry with spaces.json", runtime_root="/runtime with spaces",
        goal_id="selection-fixture", agent_id="fixture-agent", turn_instance_id="selection-turn",
        scheduler_args=" --codex-app", available_capabilities=["shell"],
    )
    [command] = cli["next_cli_actions"]
    assert contract["agent_channel"]["primary_action"] == command
    argv = shlex.split(command)
    for flag, value in {"--registry": "/registry with spaces.json", "--runtime-root": "/runtime with spaces",
                        "--goal-id": "selection-fixture", "--agent-id": "fixture-agent",
                        "--turn-instance-id": "selection-turn", "--available-capability": "shell"}.items():
        assert argv[argv.index(flag) + 1] == value
    assert "--codex-app" in argv
    assert "--todo-id" not in argv and "--replan-obligation-id" not in argv


@pytest.mark.parametrize("state", ["deferred", "rejected"])
def test_preflight_returns_one_result_without_mutating_source(state):
    source = _source(state)
    before = deepcopy(source)
    result = _requested_quota_action_selection_preflight(
        source, requested_todo_id="todo_pending", receipt_bound_todo_id=None,
        receipt_bound_replan_obligation_id=None,
    )
    assert source == before
    assert result["error_code"] == f"quota_action_selection_{state}"
    assert result["should_run"] is False
    assert result["execution_obligation"]["kind"] == "quota_skip"
    assert result["execution_obligation"]["must_attempt_work"] is False
    for flag in ("normal_delivery_allowed", "recovery_delivery_allowed", "self_repair_allowed",
                 "capability_repair_allowed", "workspace_repair_allowed", "actionable_by_codex"):
        assert result[flag] is False
    assert result["spend_after_validation"] is False


@pytest.mark.parametrize("bound_field", ["selected_todo", "autonomous_replan_obligation"])
def test_committed_binding_is_owned_by_receipt_reconciliation(bound_field):
    source = _source("deferred")
    source[bound_field] = {"selection_binding": "heartbeat_receipt"}
    assert action_selection_needs_recovery(source) is False


def test_qualified_selection_is_not_recovery():
    assert action_selection_needs_recovery(_source("qualified")) is False


@pytest.mark.parametrize("must_attempt,delivery", [(False, True), (True, False), (True, None)])
def test_pending_delivery_requires_explicit_canonical_permission(must_attempt, delivery):
    from loopx.control_plane.quota.error_codes import QuotaActionSelectionConflictError

    source = _source("qualified")
    source.update(normal_delivery_allowed=True, selected_todo={
        "todo_id": "todo_pending", "selection_binding": "pending_action_selection",
    })
    agent = {"must_attempt": must_attempt}
    if delivery is not None:
        agent["delivery_allowed"] = delivery
    source["interaction_contract"] = {"agent_channel": agent}
    with pytest.raises(QuotaActionSelectionConflictError):
        _requested_quota_action_selection_preflight(
            source, requested_todo_id="todo_pending", receipt_bound_todo_id=None,
            receipt_bound_replan_obligation_id=None,
        )


@pytest.mark.parametrize("legacy", [None, {}, {"must_attempt_work": False}, {"must_attempt_work": True}])
def test_compatibility_obligation_does_not_override_canonical_admission(legacy):
    source = _source("qualified")
    source["selected_todo"] = {"todo_id": "todo_pending"}
    if legacy is None:
        source.pop("execution_obligation")
    else:
        source["execution_obligation"] = legacy
    source["interaction_contract"] = {"agent_channel": {"must_attempt": True, "delivery_allowed": True}}
    assert _requested_quota_action_selection_preflight(
        source, requested_todo_id="todo_pending", receipt_bound_todo_id=None,
        receipt_bound_replan_obligation_id=None,
    ) is None


@pytest.mark.parametrize("legacy", [None, {}, {"must_attempt_work": False}, {"must_attempt_work": True}])
def test_closed_canonical_packet_ignores_compatibility_authority_at_readers(legacy):
    source = _source("rejected")
    source["interaction_contract"] = interaction_contract.build_interaction_contract(source)
    source["ok"] = source["should_run"] = False
    if legacy is None:
        source.pop("execution_obligation")
    else:
        source["execution_obligation"] = legacy
    interpret_quota_should_run_packet(source)
    envelope = build_turn_envelope(source)
    assert envelope["writeback"]["spend_allowed_now"] is False
    assert envelope["writeback"]["spend_after_validation"] is False
    effect_runtime_result("quota.scoped_override.project", {
        "override": {}, "interaction_contract": source["interaction_contract"],
        "selection_context": action_selection_context(source),
    })


@pytest.mark.parametrize("field", ["must_attempt_work", "delivery_allowed"])
def test_null_legacy_permission_does_not_grant_work_or_delivery(field):
    source = _source("qualified")
    source["execution_obligation"][field] = None
    contract = interaction_contract.build_interaction_contract(source)
    canonical = "must_attempt" if field == "must_attempt_work" else "delivery_allowed"
    assert contract["agent_channel"][canonical] is False


@pytest.mark.parametrize("field", ["must_attempt_work", "delivery_allowed"])
@pytest.mark.parametrize("value", ["false", "true", 1, [], {}])
def test_malformed_legacy_boolean_cannot_become_canonical_permission(field, value):
    source = _source("qualified")
    source["execution_obligation"][field] = value
    with pytest.raises(ValueError, match="must be a boolean"):
        interaction_contract.build_interaction_contract(source)


def test_one_recovery_decision_drives_both_interaction_channels(monkeypatch):
    from loopx.control_plane.work_items import action_selection_contract as selection

    calls = []
    original = selection.action_selection_needs_recovery

    def observed(payload, **facts):
        calls.append(facts)
        return original(payload, **facts)

    monkeypatch.setattr(selection, "action_selection_needs_recovery", observed)
    contract = interaction_contract.build_interaction_contract(_source("rejected"))
    assert len(calls) == 1
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["cli_channel"]["spend_after_validation"] is False
    assert "settlement_plan" not in contract["cli_channel"]


@pytest.mark.parametrize("delivery_allowed", [False, True, None])
def test_pending_workspace_repair_requires_explicit_delivery_refusal(delivery_allowed):
    from loopx.control_plane.quota.error_codes import QuotaActionSelectionConflictError

    source = _source("qualified")
    source.update(
        effective_action="agent_workspace_repair", workspace_repair_allowed=True,
        selected_todo={"todo_id": "todo_pending", "selection_binding": "pending_action_selection"},
        execution_obligation={"kind": "agent_workspace_repair", "must_attempt_work": True},
    )
    agent = {"must_attempt": True}
    if delivery_allowed is not None:
        agent["delivery_allowed"] = delivery_allowed
    source["interaction_contract"] = {"agent_channel": agent}
    kwargs = dict(requested_todo_id="todo_pending", receipt_bound_todo_id=None,
                  receipt_bound_replan_obligation_id=None)
    if delivery_allowed is False:
        assert _requested_quota_action_selection_preflight(source, **kwargs) is None
    else:
        with pytest.raises(QuotaActionSelectionConflictError):
            _requested_quota_action_selection_preflight(source, **kwargs)


def _scoped_gate_status():
    """Reuse the real scoped user-gate fallback producer fixture."""
    import sys

    sys.path.insert(0, "tests/control_plane")
    from test_user_gate_lane_progress import APP_CONTEXT, AGENT_ID, GOAL_ID, _status_payload

    return _status_payload(gate_action_kind="approve_product_first_screen"), {
        "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "scheduler_execution_context": APP_CONTEXT,
    }


def test_scoped_fallback_cannot_execute_under_an_unadmitted_selection():
    """A runnable scoped fallback does not survive a refused selection.

    The fallback readback grants safe_bypass_allowed, and the shipped heartbeat
    task body reads that under should_run=false as permission for one bounded
    step plus a spend. The selection refusal has to close that authority too,
    or the same packet says both "no work" and "do one step and spend".
    """
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
    from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
    from loopx.quota import build_quota_should_run

    status, kwargs = _scoped_gate_status()
    payload = build_quota_should_run(
        status, requested_action_todo_id="todo_not_projected", **kwargs,
    )

    assert payload["effective_action"] == "quota_skip"
    assert payload["state"] == "quota_action_selection_rejected"
    for flag in (
        "ok", "should_run", "actionable_by_codex", "safe_bypass_allowed",
        "spend_allowed_now", "spend_after_validation", "normal_delivery_allowed",
        "recovery_delivery_allowed", "self_repair_allowed",
        "capability_repair_allowed", "workspace_repair_allowed",
    ):
        assert payload[flag] is False, flag
    assert payload["safe_bypass_kind"] is None
    assert payload["safe_bypass_policy"] is None
    assert "scoped_user_gate_fallback" not in payload

    obligation = payload["execution_obligation"]
    assert obligation["kind"] == "quota_skip"
    assert obligation["must_attempt_work"] is False
    assert obligation["delivery_allowed"] is False
    assert "no quota spend" in obligation["spend_policy"]

    interaction = payload["interaction_contract"]
    assert interaction["mode"] != "scoped_user_gate_fallback"
    assert interaction["agent_channel"]["must_attempt"] is False
    assert interaction["agent_channel"]["delivery_allowed"] is False
    assert interaction["cli_channel"]["spend_allowed_now"] is False
    assert interaction["cli_channel"]["spend_after_validation"] is False

    assert "protocol_action_packet" not in payload

    envelope = build_turn_envelope(payload)
    assert envelope["writeback"]["spend_allowed_now"] is False
    assert envelope["writeback"]["spend_after_validation"] is False
    capsule = envelope["contract_capsule"]
    assert "protocol_action_packet" not in capsule
    assert capsule["interaction_contract"]["mode"] != "scoped_user_gate_fallback"
    assert capsule["execution_obligation"]["must_attempt_work"] is False
    assert extract_turn_authority({"turn_envelope": envelope})["write_scope"] == []

    guidance = render_quota_should_run_markdown(payload)
    assert "safe_bypass" not in guidance
    assert "spend only after validated writeback" not in guidance


def test_scoped_fallback_still_runs_without_a_refused_selection():
    """The refusal closes the grant; it does not retire the fallback path."""
    from loopx.quota import build_quota_should_run

    status, kwargs = _scoped_gate_status()
    payload = build_quota_should_run(status, **kwargs)

    assert payload["should_run"] is True
    assert payload["safe_bypass_allowed"] is True
    assert payload["safe_bypass_kind"] == "scoped_user_gate_fallback"
    assert payload["interaction_contract"]["mode"] == "scoped_user_gate_fallback"


@pytest.mark.parametrize("state", ["deferred", "rejected"])
def test_recovery_fields_close_the_safe_bypass_grant(state):
    """Both refusal states share one owner, so both close the same authority."""
    from loopx.control_plane.work_items.action_selection_contract import (
        build_action_selection_recovery_fields,
    )

    source = _source(state)
    source.update(
        safe_bypass_allowed=True, safe_bypass_kind="scoped_user_gate_fallback",
        safe_bypass_policy="advance the fallback; spend only after validated writeback",
    )
    recovery = build_action_selection_recovery_fields(source)
    assert recovery["safe_bypass_allowed"] is False
    assert recovery["safe_bypass_kind"] is None
    assert recovery["safe_bypass_policy"] is None
