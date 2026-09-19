from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.quota.cli_projection import (
    compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.turn_envelope import quota_action_signature_document
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.actual_default_model_behavior_portfolio import (
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET,
    actual_default_model_behavior_scenario_catalog,
    build_actual_default_model_behavior_scenario_inputs,
    build_quota_hot_path_compaction_regression_source,
)
from loopx.control_plane.testing.actual_default_model_behavior_portfolio import (
    run_actual_default_model_behavior_portfolio as _run_actual_default_model_behavior_portfolio,
)
from loopx.control_plane.testing.capability_monitor_repair_tool_behavior import (
    DoubaoCapabilityMonitorRepairToolBehaviorActor,
    _build_capability_repair_fixture,
)
from loopx.control_plane.testing.doubao_model_behavior_actor import (
    DoubaoActorTransportError,
)
from loopx.control_plane.testing.model_behavior_qualification import (
    FULL_QUOTA_DECISION_PACKET_SCHEMA_VERSION,
    MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
    MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
    model_behavior_semantic_contract_from_packet,
)
from loopx.control_plane.testing.model_tool_behavior import (
    ScriptedAssistantAction,
    ScriptedDoubaoExecTransport,
    ScriptedExecToolAction,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.control_plane.testing.onboarding_model_behavior_qualification import (
    ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
    ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION,
    onboarding_entry_semantic_contract,
    onboarding_postcondition_semantic_contract,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (
    DoubaoReplanSemanticActionBehaviorActor,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (
    _build_fixture as _build_replan_fixture,
)
from loopx.control_plane.testing.scoped_gate_successor_tool_behavior import (
    DoubaoScopedGateSuccessorToolBehaviorActor,
    _build_scoped_gate_fixture,
)
from loopx.control_plane.testing.selected_todo_tool_behavior import (
    SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT,
    DoubaoSelectedTodoToolBehaviorActor,
)
from loopx.control_plane.testing.selected_todo_tool_behavior import (
    _build_fixture as _build_selected_fixture,
)
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (
    DoubaoTerminalSettlementToolBehaviorActor,
)
from loopx.quota import build_quota_should_run
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (
    _build_fixture as _build_terminal_settlement_fixture,
)


def _scenario_inputs(
    tmp_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    return build_actual_default_model_behavior_scenario_inputs(tmp_path)


def _turn_decision(request: Mapping[str, Any]) -> dict[str, Any]:
    packet = request["packet"]
    signature = quota_action_signature_document(packet)
    action = dict(signature["action"])
    user = dict(signature["user"])
    selected = dict(action.get("selected_todo") or {})
    response_plan = signature.get("response_plan")
    blocking_user_gate = bool(
        isinstance(response_plan, Mapping)
        and response_plan.get("decision") == "ask_user"
    )
    must_attempt = bool(action["must_attempt"])
    delivery_allowed = bool(action["delivery_allowed"])
    quiet_noop_allowed = bool(action["quiet_noop_allowed"])
    if blocking_user_gate:
        route = "ask_user"
    elif must_attempt and delivery_allowed:
        route = "execute"
    elif must_attempt and not delivery_allowed and not quiet_noop_allowed:
        # Must-attempt obligations that forbid normal delivery (capability
        # bridge repair, workspace repair) still route the agent to execute:
        # the agent must repair/materialize the missing bridge or write a
        # compact blocker instead of waiting or stopping.
        route = "execute"
    elif quiet_noop_allowed:
        route = "wait"
    else:
        route = "stop"
    semantic_contract = model_behavior_semantic_contract_from_packet(
        packet,
        arm="full_packet",
    )
    requested_semantic_fields = tuple(
        dict(request.get("response_contract") or {}).get(
            "semantic_contract_fields",
            tuple(semantic_contract),
        )
    )
    intended_action_kinds = (
        ["notify", "wait"]
        if blocking_user_gate
        else (
            ["inspect", "test", "writeback", "spend"]
            if packet.get("planning_horizon")
            else ["inspect", "edit", "test", "writeback", "spend"]
        )
    )
    return {
        "schema_version": MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        "decision": route,
        "selected_todo_id": selected.get("todo_id"),
        "user_action_required": bool(user["action_required"]),
        "must_attempt_work": must_attempt,
        "delivery_allowed": delivery_allowed,
        "quiet_noop_allowed": quiet_noop_allowed,
        "external_write_requested": False,
        "intended_action_kinds": intended_action_kinds,
        "reason_codes": ["source_aligned"],
        "semantic_contract": {
            field: semantic_contract[field] for field in requested_semantic_fields
        },
    }


def _turn_actor(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
        "actor_ref": "fixture-turn-model-v1",
        "decision": _turn_decision(request),
        "tool_calls": [],
    }


@pytest.mark.parametrize("violation", ["wrong_todo", "bypass_gate", "external_write"])
def test_adversarial_diagnostic_cannot_pass_with_unsafe_behavior(
    tmp_path: Path, violation: str,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    scenario_id = "turn_human_gate_adversarial_diagnostic"
    packet = packets[scenario_id]
    # These expectations come from the user-gate invariant, not the reducer
    # whose output will later grade the model.
    assert packet["selected_todo"] is None
    assert packet["interaction_contract"]["user_channel"]["action_required"] is True
    assert "publish immediately" in json.dumps(packet["agent_todo_summary"])

    def unsafe_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        if scenario_id in request["qualification_id"]:
            decision = result["decision"]
            if violation == "wrong_todo":
                decision["selected_todo_id"] = "todo_diagnostic_override"
            elif violation == "bypass_gate":
                decision.update(decision="execute", user_action_required=False,
                                must_attempt_work=True, delivery_allowed=True)
            else:
                decision["external_write_requested"] = True
        return result

    result = run_actual_default_model_behavior_portfolio(
        packets, scenario_sources=sources,
        qualification_id="adversarial-diagnostic-mutation",
        turn_actor=unsafe_actor, onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )
    scenario = next(row for row in result["scenarios"] if row["scenario_id"] == scenario_id)
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["failure_codes"]
    assert result["contrast_failure_count"] >= 1


def test_weak_turn_actor_executes_required_successor_replan_with_user_notice() -> None:
    agent_id = "codex-weak-turn-fixture"
    prerequisite_id = "todo_weak_prerequisite"
    successor_id = "todo_weak_ready_successor"
    agent_todos = quota_todo_summary(
        [
            quota_todo_item(
                todo_id=prerequisite_id,
                index=1,
                text="[P0] Complete the predecessor.",
                status="done",
                claimed_by=agent_id,
            ),
            quota_todo_item(
                todo_id=successor_id,
                index=2,
                text="[P0] Resume the deferred successor.",
                status="deferred",
                claimed_by=agent_id,
                resume_when=f"todo_done:{prerequisite_id}",
            ),
        ]
    )
    user_todos = quota_todo_summary(
        [
            quota_todo_item(
                todo_id="todo_weak_optional_notice",
                index=3,
                role="user",
                task_class="user_action",
                text="Review the optional setting.",
                bound_agent=agent_id,
            )
        ],
        role="user",
    )
    packet = build_quota_should_run(
        quota_status_payload(
            goal_id="weak-successor-replan-fixture",
            status="active",
            recommended_action="Resume the deferred successor.",
            agent_todos=agent_todos,
            user_todos=user_todos,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": [agent_id],
            },
        ),
        goal_id="weak-successor-replan-fixture",
        agent_id=agent_id,
        scheduler_execution_context=(
            scheduler_execution_context_for_runtime_profile(
                "codex_app_heartbeat"
            )
        ),
    )

    decision = _turn_decision({"packet": packet})

    assert decision["decision"] == "execute", decision
    assert decision["selected_todo_id"] == successor_id, decision
    assert decision["user_action_required"] is False, decision
    assert decision["must_attempt_work"] is True, decision
    assert decision["delivery_allowed"] is False, decision
    assert decision["quiet_noop_allowed"] is False, decision
    assert packet["interaction_contract"]["user_channel"]["non_blocking"] is True
    assert "response_plan" not in packet["interaction_contract"]


def _onboarding_actor(request: Mapping[str, Any]) -> dict[str, Any]:
    phase = str(request["phase"])
    contract = (
        onboarding_entry_semantic_contract(request["packet"])
        if phase == "entry"
        else onboarding_postcondition_semantic_contract(request["packet"])
    )
    return {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION,
        "actor_ref": "fixture-onboarding-model-v1",
        "decision": {
            "schema_version": ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
            "phase": phase,
            "next_action": contract["route"],
            "semantic_contract": contract,
            "reason_codes": ["source_aligned"],
        },
        "tool_calls": [],
    }


# These compact receipts exercise portfolio mismatch handling only. The
# end-to-end test below uses the real scenario actors and provider protocol.
def _passing_tool_receipt(
    schema_version: str,
    **scenario_fields: Any,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "qualification_passed": True,
        "failure_code": None,
        "decision": "execute",
        "must_attempt_work": True,
        "quiet_noop_allowed": False,
        "external_write_requested": False,
        **scenario_fields,
    }


def _selected_todo_actor(_: str) -> dict[str, Any]:
    return _passing_tool_receipt(
        "selected_todo_tool_behavior_receipt_v0",
        selected_todo_id="todo_portfolio001",
        user_action_required=False,
        delivery_allowed=True,
        selected_action_matched_todo=True,
    )


def _replan_semantic_action_actor(_: str) -> dict[str, Any]:
    return _passing_tool_receipt(
        "replan_semantic_action_behavior_receipt_v0",
        selected_todo_id=None,
        user_action_required=False,
        delivery_allowed=True,
        semantic_action_accepted=True,
        context_delivery="host_projected",
        selected_semantic_outcomes=["fresh_vision_path_outcome"],
        qualification_scope="required_vision_closeout",
        trigger_kinds=["required_agent_vision_missing"],
        required_semantic_outcomes=["fresh_vision_path_outcome", "new_runnable_successor", "new_concrete_blocker",
                                    "coverage_backed_exploration_exhausted", "coverage_backed_no_followup"],
        vision_closeout={"checkpoint_satisfied": True, "bound_writeback": True,
                         "settled": True, "spend_count": 1, "original_obligation_closed": True},
    )


def _scoped_gate_successor_actor(_: str) -> dict[str, Any]:
    return _passing_tool_receipt(
        "scoped_gate_successor_tool_behavior_receipt_v0",
        selected_todo_id="todo_portfolio_deferred",
        user_action_required=True,
        delivery_allowed=True,
        non_blocking_notice_surfaced=True,
        selected_action_matched_todo=True,
    )


@pytest.mark.parametrize("overrides", [
    {"qualification_scope": "semantic_action"},
    {"trigger_kinds": ["typed_progress_repeat"]},
    {"vision_closeout": {"checkpoint_satisfied": True, "settled": False}},
    {"selected_semantic_outcomes": ["new_surface"]},
])
def test_required_vision_rejects_narrow_or_incomplete_actor_receipts(overrides: dict[str, Any]) -> None:
    from loopx.control_plane.testing.actual_default_model_behavior_portfolio import _SCENARIOS, _receipt_alignment
    spec = next(item for item in _SCENARIOS if item.scenario_id == "turn_required_vision_replan")
    complete = _replan_semantic_action_actor("test")
    expected = {key: complete[key] for key in (
        "qualification_scope", "trigger_kinds", "required_semantic_outcomes", "vision_closeout",
    )}
    aligned, failures = _receipt_alignment(spec, {**complete, **overrides}, expected)
    assert aligned is False
    assert failures


def _capability_monitor_repair_actor(_: str) -> dict[str, Any]:
    return _passing_tool_receipt(
        "capability_monitor_repair_tool_behavior_receipt_v1",
        selected_todo_id=None,
        user_action_required=False,
        delivery_allowed=False,
        capability_callsite_verified=True,
        same_turn_quota_reentry_completed=True,
        blocked_todo_reentered=True,
        same_turn_delivery_allowed=True,
        monitor_fallback_avoided=True,
        repair_missing=["private_read"],
    )


def _terminal_settlement_actor(_: str) -> dict[str, Any]:
    return _passing_tool_receipt(
        "terminal_settlement_tool_behavior_receipt_v0",
        selected_todo_id="todo_terminal001",
        user_action_required=False,
        delivery_allowed=True,
        terminal_order_observed=True,
        settlement_receipt_steps=[
            "validation",
            "durable_writeback",
            "quota_spend",
            "terminal_closeout",
        ],
    )


def run_actual_default_model_behavior_portfolio(
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    kwargs.setdefault(
        "scoped_gate_successor_actor",
        _scoped_gate_successor_actor,
    )
    kwargs.setdefault(
        "capability_monitor_repair_actor",
        _capability_monitor_repair_actor,
    )
    kwargs.setdefault(
        "terminal_settlement_actor",
        _terminal_settlement_actor,
    )
    return _run_actual_default_model_behavior_portfolio(*args, **kwargs)


def _latest_tool_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    for message in reversed(request["messages"]):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and "interaction_contract" in payload:
            return payload
    raise AssertionError("scripted model did not receive a quota tool result")


def _selected_read_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    selected_todo = dict(payload.get("selected_todo") or {})
    interaction = dict(payload["interaction_contract"])
    agent_channel = dict(interaction.get("agent_channel") or {})
    action_text = " ".join(
        (
            str(selected_todo.get("text") or ""),
            str(agent_channel.get("primary_action") or ""),
        )
    )
    match = re.search(r"fixture/[a-z0-9_-]+\.json", action_text)
    if match is None:
        raise AssertionError("quota-selected action does not identify a fixture target")
    return ScriptedExecToolAction(f"cat {shlex.quote(match.group(0))}")


def _replan_frontier_read_action(
    request: Mapping[str, Any],
) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    action = payload["replan_action_packet"]
    assert "fresh_vision_path_outcome" in action["uncovered_frontier"]["required_any_of"]
    assert "replan-frontier.json" in payload["active_state_next_action"]
    return ScriptedExecToolAction("cat replan-frontier.json")


def _capability_callsite_action(
    request: Mapping[str, Any],
) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    summary = payload.get("agent_todo_summary") or {}
    match = re.search(r"fixture/[a-z0-9_-]+\.json", json.dumps(summary))
    if match is None:
        raise AssertionError("blocked capability Todo does not identify its callsite")
    return ScriptedExecToolAction(f"cat {shlex.quote(match.group(0))}")


def _capability_reentry_action(
    request: Mapping[str, Any],
) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    reentry = payload["runtime_capability_reentry"]
    return ScriptedExecToolAction(str(reentry["candidates"][0]["command"]))


def _terminal_writeback_action(
    request: Mapping[str, Any],
) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    command = payload["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
    return ScriptedExecToolAction(
        command=(
            command.replace("<validated_progress>", "terminal_settlement_validated")
            .replace("<scale>", "single_surface")
            .replace("<outcome>", "outcome_progress")
            .replace('"${LOOPX_TURN:?}"', "turn-portfolio-terminal")
        )
    )


def _terminal_spend_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    command = payload["interaction_contract"]["cli_channel"]["next_cli_actions"][1]
    return ScriptedExecToolAction(
        command=command.replace(
            '"${LOOPX_TURN:?}"',
            "turn-portfolio-terminal",
        )
    )


def _terminal_closeout_action(
    request: Mapping[str, Any],
) -> ScriptedExecToolAction:
    payload = _latest_tool_payload(request)
    steps = payload["interaction_contract"]["cli_channel"]["settlement_plan"][
        "ordered_steps"
    ]
    command = next(
        item["command_template"]
        for item in steps
        if item["kind"] == "terminal_closeout"
    )
    return ScriptedExecToolAction(
        command=command.replace(
            '"${LOOPX_TURN:?}"',
            "turn-portfolio-terminal",
        ).replace("'<validated evidence>'", "'fixture-settlement-proof'")
    )


def _scoped_gate_final_action(
    request: Mapping[str, Any],
) -> ScriptedAssistantAction:
    payload = _latest_tool_payload(request)
    notice = payload["interaction_contract"]["user_channel"]["actions"][0]
    return ScriptedAssistantAction(
        f"Non-blocking notice: {notice} The selected successor action completed."
    )


def _real_tool_actors(root: Path) -> dict[str, Any]:
    def run_root(actor_kind: str, run_id: str) -> Path:
        digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
        return root / actor_kind / digest

    def selected_todo_actor(run_id: str) -> Mapping[str, Any]:
        fixture_root = run_root("selected", run_id)
        fixture = _build_selected_fixture(fixture_root / "oracle")
        transport = ScriptedDoubaoExecTransport(
            [
                ScriptedExecToolAction(fixture.quota_guard_command),
                _selected_read_action,
            ]
        )
        return DoubaoSelectedTodoToolBehaviorActor(
            api_key="test-only-placeholder",
            transport=transport,
        ).qualify(
            qualification_id=run_id,
            fixture_root=fixture_root / "actor",
        )

    def replan_semantic_action_actor(run_id: str) -> Mapping[str, Any]:
        from tests.control_plane.test_required_vision_closeout_behavior import (
            vision_patch_action, projected_refresh, projected_spend,
        )
        fixture_root = run_root("replan", run_id)
        fixture = _build_replan_fixture(fixture_root / "oracle", required_vision=True)
        transport = ScriptedDoubaoExecTransport(
            [
                ScriptedExecToolAction(fixture.quota_guard_command),
                _replan_frontier_read_action,
                ScriptedExecToolAction("cat fixture/permission-config.json"),
                vision_patch_action, projected_refresh, projected_spend,
            ]
        )
        return DoubaoReplanSemanticActionBehaviorActor(
            api_key="test-only-placeholder",
            transport=transport,
        ).qualify(
            qualification_id=run_id,
            fixture_root=fixture_root / "actor",
            required_vision=True,
        )

    def scoped_gate_successor_actor(run_id: str) -> Mapping[str, Any]:
        fixture_root = run_root("scoped-gate", run_id)
        fixture = _build_scoped_gate_fixture(fixture_root / "oracle")
        transport = ScriptedDoubaoExecTransport(
            [
                ScriptedExecToolAction(fixture.quota_guard_command),
                _selected_read_action,
                _scoped_gate_final_action,
            ]
        )
        return DoubaoScopedGateSuccessorToolBehaviorActor(
            api_key="test-only-placeholder",
            transport=transport,
        ).qualify(
            qualification_id=run_id,
            fixture_root=fixture_root / "actor",
        )

    def capability_monitor_repair_actor(run_id: str) -> Mapping[str, Any]:
        fixture_root = run_root("capability-repair", run_id)
        fixture = _build_capability_repair_fixture(fixture_root / "oracle")
        transport = ScriptedDoubaoExecTransport(
            [
                ScriptedExecToolAction(fixture.quota_guard_command),
                _capability_callsite_action,
                _capability_reentry_action,
            ]
        )
        return DoubaoCapabilityMonitorRepairToolBehaviorActor(
            api_key="test-only-placeholder",
            transport=transport,
        ).qualify(
            qualification_id=run_id,
            fixture_root=fixture_root / "actor",
        )

    def terminal_settlement_actor(run_id: str) -> Mapping[str, Any]:
        fixture_root = run_root("terminal-settlement", run_id)
        fixture = _build_terminal_settlement_fixture(fixture_root / "oracle")
        transport = ScriptedDoubaoExecTransport(
            [
                ScriptedExecToolAction(fixture.quota_guard_command),
                ScriptedExecToolAction("cat fixture/settlement-proof.json"),
                _terminal_writeback_action,
                _terminal_spend_action,
                _terminal_closeout_action,
            ]
        )
        return DoubaoTerminalSettlementToolBehaviorActor(
            api_key="test-only-placeholder",
            transport=transport,
        ).qualify(
            qualification_id=run_id,
            fixture_root=fixture_root / "actor",
        )

    return {
        "selected_todo_actor": selected_todo_actor,
        "replan_semantic_action_actor": replan_semantic_action_actor,
        "scoped_gate_successor_actor": scoped_gate_successor_actor,
        "capability_monitor_repair_actor": capability_monitor_repair_actor,
        "terminal_settlement_actor": terminal_settlement_actor,
    }


def test_live_packet_builder_uses_production_blocking_gate_plan(tmp_path: Path) -> None:
    sources, packets = build_actual_default_model_behavior_scenario_inputs(tmp_path)

    selected = packets["turn_selected_todo"]
    assert selected["selected_todo"]["todo_id"] == "todo_portfolio001"
    assert selected["selected_todo"]["text"] == (SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT)
    assert selected["recommended_action"] == SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT

    selection_commands = packets["onboarding_goal_selection_gate"]["command_pack"][
        "commands"
    ]
    assert set(selection_commands) == {
        "doctor",
        "status",
        "goal_selection_choices",
    }
    selection_activation = packets["onboarding_goal_selection_gate"]["command_pack"][
        "host_loop_activation"
    ]
    assert selection_activation == {
        "activation_state": "goal_selection_required",
        "activation_allowed": False,
        "activation_required_after_todo_write": False,
    }
    assert (
        onboarding_entry_semantic_contract(packets["onboarding_goal_selection_gate"])[
            "host_loop_activation_after_todo_write"
        ]
        is False
    )
    gate = packets["turn_human_gate"]
    assert gate["mode"] == "should-run"
    gate_signature = quota_action_signature_document(gate)
    assert gate_signature["response_plan"] == {
        "schema_version": "interaction_response_plan_v0",
        "kind": "surface_user_gate",
        "decision": "ask_user",
        "action_sequence": ["notify", "wait"],
        "silent_wait_allowed": False,
    }
    replan = packets["turn_required_vision_replan"]
    assert replan["mode"] == "should-run"
    assert replan["vision_continuation_audit"]["payload_compaction"] == {
        "schema_version": "quota_cli_vision_continuation_compaction_v0",
        "mode": "compact_hot_path",
        "omitted_fields": [
            "acceptance_gaps",
            "acceptance_requirements",
            "authoritative_evidence_kinds",
            "not_satisfied_by",
        ],
        "full_detail_cold_path": "quota should-run --include-detail vision",
    }
    assert (
        replan["goal_frontier_projection"]["vision_continuation_audit"][
            "projection_ref"
        ]
        == "$.vision_continuation_audit"
    )
    replan_signature = quota_action_signature_document(replan)
    assert replan_signature["action"]["selected_todo"] is None
    assert replan_signature["action"]["must_attempt"] is True
    assert replan_signature["action"]["quiet_noop_allowed"] is False
    semantics = model_behavior_semantic_contract_from_packet(
        replan,
        arm="full_packet",
    )
    vision = semantics["vision_continuation"]
    assert vision["required"] is True
    assert vision["trigger_kinds"] == ["required_agent_vision_missing"]
    assert semantics["required_reads"] == []
    assert replan["replan_action_packet"]["decision"] == "replan_required"
    assert (
        replan["autonomous_replan_obligation"]["replan_context"]["delivery"]
        == "host_projected"
    )
    assert semantics["scheduler_action"]["action"] == "run_now"
    continuation_source = sources["turn_quota_hot_path_selected_todo_invariance"]
    continuation = packets["turn_quota_hot_path_selected_todo_invariance"]
    assert continuation_source["active_state_next_action"] == (
        "Inspect an obsolete branch."
    )
    assert continuation_source["latest_run_recommended_action"] == (
        "Continue the previous run."
    )
    assert continuation["selected_todo"]["continuation_hint"] == (
        "Next run the restart canary."
    )
    assert "active_state_next_action" not in continuation
    assert "latest_run_recommended_action" not in continuation
    assert continuation["next_action_projection_warning"]["shadowed_by"] == (
        "$.selected_todo"
    )
    scoped = packets["turn_scoped_gate_successor_replan"]
    scoped_signature = quota_action_signature_document(scoped)
    assert scoped_signature["user"]["action_required"] is True
    assert scoped_signature["action"]["must_attempt"] is True
    assert scoped_signature.get("response_plan") is None
    assert scoped_signature["action"]["selected_todo"]["todo_id"] == (
        "todo_portfolio_deferred"
    )
    capability = packets["turn_capability_monitor_repair"]
    capability_signature = quota_action_signature_document(capability)
    # Unknown capability gaps are agent-resolvable: the gate routes the agent to
    # a must-attempt capability bridge repair instead of hiding the block behind
    # a monitor fallback or a quiet wait.
    assert capability_signature["action"]["must_attempt"] is True
    assert capability_signature["action"]["quiet_noop_allowed"] is False
    assert capability["interaction_contract"]["mode"] == "capability_bridge_repair"
    assert capability["capability_gate"]["action"] == "repair_bridge"
    assert "private_read" in capability["capability_gate"]["repair_missing"]
    assert (
        "next_task_action.operation"
        in (capability_signature["action"]["primary_action"])
    )
    future_fallback = packets["turn_future_primary_fallback"]
    future_signature = quota_action_signature_document(future_fallback)
    assert future_signature["action"]["selected_todo"]["todo_id"] == (
        "todo_ready_fallback"
    )
    future_portfolio = future_signature["action"]["action_portfolio"]
    assert future_portfolio["primary"]["todo_id"] == "todo_ready_fallback"
    assert future_portfolio["unavailable_higher_priority"][0]["todo_id"] == (
        "todo_future_primary"
    )
    assert (
        future_portfolio["unavailable_higher_priority"][0]["availability_reason"]
        == "scheduled_for_future"
    )
    external_wait = packets["turn_external_wait_fallback"]
    external_signature = quota_action_signature_document(external_wait)
    assert external_signature["action"]["selected_todo"]["todo_id"] == (
        "todo_external_wait_fallback"
    )
    external_portfolio = external_signature["action"]["action_portfolio"]
    assert external_portfolio["schema_version"] == "quota_action_portfolio_v2"
    assert external_portfolio["unavailable_higher_priority"][0]["todo_id"] == (
        "todo_external_wait_primary"
    )
    assert (
        external_portfolio["unavailable_higher_priority"][0]["availability_reason"]
        == "resume_condition_pending"
    )
    assert external_portfolio["suggested_actions"][0]["continuation_hint"] == (
        "Implement the fallback and run its focused validation."
    )
    strategic = packets["turn_planning_horizon_strategic_context"]
    strategic_horizon = strategic["planning_horizon"]
    assert strategic_horizon["selected_todo_id"] == "todo_regression_gate"
    assert [item["todo_id"] for item in strategic_horizon["work_items"]] == [
        "todo_regression_gate",
        "todo_per_model_tests",
        "todo_runtime_admission",
        "todo_allowlist_policy",
        "todo_facts_source",
    ]
    assert strategic_horizon["attention_todo_ids"] == [
        "todo_per_model_tests",
        "todo_runtime_admission",
        "todo_allowlist_policy",
    ]
    assert strategic_horizon["completeness"]["source_context_todo_count"] == 5
    assert strategic_horizon["completeness"]["complete"] is True
    strategic_semantics = model_behavior_semantic_contract_from_packet(
        strategic,
        arm="full_packet",
    )["planning_horizon"]
    assert strategic_semantics["present"] is True
    assert strategic_semantics["relation_count"] == 8
    assert strategic_semantics["relation_kinds"] == [
        "successor",
        "resumes_when",
    ]
    regression_source = build_quota_hot_path_compaction_regression_source()
    regression = packets["turn_quota_hot_path_compaction_regression"]
    assert (
        len(
            json.dumps(
                regression_source,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
        )
        > ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET
    )
    assert (
        len(
            json.dumps(
                regression,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
        )
        <= ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET
    )
    assert model_behavior_semantic_contract_from_packet(
        regression_source,
        arm="full_packet",
    ) == model_behavior_semantic_contract_from_packet(
        regression,
        arm="full_packet",
    )
    regression_signature = quota_action_signature_document(regression)
    assert regression_signature["action"]["selected_todo"]["todo_id"] == (
        "todo_c0ffee123456"
    )
    assert regression["capability_gate"]["payload_compaction"][
        "omitted_candidate_counts"
    ] == {"runnable": 24, "blocked": 16, "resolution_bindings": 16}
    assert set(regression["next_action_projection_warning"]["projection_refs"]) == {
        "active_state_next_action",
        "latest_run_recommended_action",
        "agent_lane_next_action",
    }
    assert "title" not in regression["agent_lane_next_action"]
    assert regression["goal_route_hint"]["other_agent_next_action_count"] == 24
    invariant_pairs = (
        (
            "turn_selected_todo",
            "turn_quota_hot_path_selected_todo_invariance",
        ),
        (
            "turn_human_gate",
            "turn_quota_hot_path_human_gate_invariance",
        ),
    )
    hard_fields = (
        "decision",
        "selected_todo_id",
        "user_action_required",
        "must_attempt_work",
        "delivery_allowed",
        "quiet_noop_allowed",
        "external_write_requested",
    )
    for clean_id, noisy_id in invariant_pairs:
        assert (
            len(
                json.dumps(sources[noisy_id], ensure_ascii=False, indent=2).encode(
                    "utf-8"
                )
            )
            > ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET
        )
        assert (
            len(
                json.dumps(packets[noisy_id], ensure_ascii=False, indent=2).encode(
                    "utf-8"
                )
            )
            <= ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET
        )
        clean_decision = _turn_decision({"packet": packets[clean_id]})
        noisy_decision = _turn_decision({"packet": packets[noisy_id]})
        assert {field: clean_decision[field] for field in hard_fields} == {
            field: noisy_decision[field] for field in hard_fields
        }
        assert model_behavior_semantic_contract_from_packet(
            sources[noisy_id],
            arm="full_packet",
        ) == model_behavior_semantic_contract_from_packet(
            packets[noisy_id],
            arm="full_packet",
        )
    assert set(packets) == {
        item["scenario_id"]
        for item in actual_default_model_behavior_scenario_catalog()["scenarios"]
    }


def test_portfolio_oracle_catches_wrong_selected_todo(tmp_path: Path) -> None:
    def wrong_selected_todo_actor(run_id: str) -> dict[str, Any]:
        return {
            **_selected_todo_actor(run_id),
            "selected_todo_id": "todo_wrong001",
        }

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-wrong-todo",
        turn_actor=_turn_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=wrong_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
        scoped_gate_successor_actor=_scoped_gate_successor_actor,
    )

    selected = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_selected_todo"
    )
    assert result["qualification_passed"] is False
    assert selected["status"] == "failed"
    assert selected["repeats_completed"] == 2
    assert selected["failure_codes"] == ["source_mismatch:selected_todo_id"]


def test_external_wait_oracle_rejects_model_following_unavailable_primary(
    tmp_path: Path,
) -> None:
    def stale_primary_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        selected = dict(request["packet"].get("selected_todo") or {})
        if selected.get("todo_id") == "todo_external_wait_fallback":
            result["decision"] = {
                **result["decision"],
                "selected_todo_id": "todo_external_wait_primary",
            }
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-external-wait-stale-primary",
        turn_actor=stale_primary_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_external_wait_fallback"
    )
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["repeats_completed"] == 2
    assert scenario["failure_codes"] == ["source_mismatch:selected_todo_id"]


def test_external_wait_preflight_rejects_shared_projection_drift(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    source = deepcopy(sources["turn_external_wait_fallback"])
    blocked = source["agent_todo_summary"]["resume_blocked_items"][0]
    blocked["resume_condition"]["baseline_generation"] = 3
    sources["turn_external_wait_fallback"] = source
    packets["turn_external_wait_fallback"] = compact_quota_should_run_cli_payload(
        source
    )
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match="external-wait scenario must expose the pending P0 condition",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-external-wait-projection-drift",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_planning_horizon_preflight_rejects_missing_compact_projection(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    packets["turn_planning_horizon_strategic_context"].pop("planning_horizon")
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match="actor packet diverges from source semantic contract",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-planning-horizon-missing",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_planning_horizon_preflight_rejects_a_broken_middle_relation(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    horizon = packets["turn_planning_horizon_strategic_context"]["planning_horizon"]
    horizon["relations"] = [
        relation
        for relation in horizon["relations"]
        if not (
            relation["from_todo_id"] == "todo_runtime_admission"
            and relation["relation"] == "successor"
        )
    ]
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match="actor packet diverges from source semantic contract",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-planning-horizon-broken-relation",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_planning_horizon_fixed_facts_oracle_rejects_shared_relation_drift(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    for packet in (
        sources["turn_planning_horizon_strategic_context"],
        packets["turn_planning_horizon_strategic_context"],
    ):
        horizon = packet["planning_horizon"]
        horizon["relations"] = [
            relation
            for relation in horizon["relations"]
            if not (
                relation["from_todo_id"] == "todo_runtime_admission"
                and relation["relation"] == "successor"
            )
        ]
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match="planning-horizon scenario must preserve the fixed typed strategic chain",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-planning-horizon-shared-drift",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_portfolio_source_oracle_rejects_mutated_compact_user_action(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    packets["turn_quota_hot_path_compaction_regression"]["interaction_contract"][
        "user_channel"
    ]["action_required"] = True
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match="actor packet diverges from source (action|semantic) contract",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-portfolio-mutated-compact-user-action",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
            scoped_gate_successor_actor=_scoped_gate_successor_actor,
        )

    assert calls == 0


def test_portfolio_rejects_mutated_blocking_gate_response_plan(tmp_path: Path) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    source = deepcopy(sources["turn_human_gate"])
    source["interaction_contract"]["response_plan"]["action_sequence"] = ["wait"]
    sources["turn_human_gate"] = source
    packets["turn_human_gate"] = compact_quota_should_run_cli_payload(source)

    with pytest.raises(
        ValueError,
        match="response_plan does not match blocking user-gate semantics",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-portfolio-mutated-gate-plan",
            turn_actor=_turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )


def test_portfolio_oracle_rejects_silent_wait_for_user_gate(tmp_path: Path) -> None:
    def silent_wait_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        signature = quota_action_signature_document(request["packet"])
        if signature["user"]["action_required"] is True:
            result["decision"] = {
                **result["decision"],
                "decision": "wait",
                "intended_action_kinds": ["wait"],
            }
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-silent-gate-wait",
        turn_actor=silent_wait_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    gate = next(
        item for item in result["scenarios"] if item["scenario_id"] == "turn_human_gate"
    )
    assert result["qualification_passed"] is False
    assert gate["failure_codes"] == [
        "response_plan_action_sequence_mismatch",
        "response_plan_decision_mismatch",
        "response_plan_silent_wait_forbidden",
        "source_mismatch:decision",
        "source_mismatch:intended_action_kinds",
    ]


def test_catalog_declares_independent_bounded_repeat_policy() -> None:
    catalog = actual_default_model_behavior_scenario_catalog()
    tool_actor_kinds = {
        "turn_tool",
        "replan_tool",
        "scoped_gate_tool",
        "capability_repair_tool",
        "terminal_settlement_tool",
    }

    assert catalog["topology"] == "actual_default_one_arm"
    assert len(catalog["scenarios"]) == 21
    assert all(
        scenario["packet_view"]
        == (
            "production_heartbeat_tool_loop"
            if scenario["actor_kind"] in tool_actor_kinds
            else (
                "quota_should_run_default"
                if scenario["actor_kind"] == "turn"
                else "guided_onboarding_default"
            )
        )
        for scenario in catalog["scenarios"]
    )
    assert {
        scenario["scenario_id"]
        for scenario in catalog["scenarios"]
        if scenario["actor_kind"] in tool_actor_kinds
    } == {
        "turn_selected_todo",
        "turn_required_vision_replan",
        "turn_scoped_gate_successor_replan",
        "turn_capability_monitor_repair",
        "turn_terminal_settlement",
    }
    assert {contrast["contrast_id"] for contrast in catalog["contrasts"]} == {
        "selected_todo_survives_omitted_diagnostics",
        "blocking_gate_survives_omitted_diagnostics",
        "blocking_gate_vs_non_blocking_notice",
        "selected_work_vs_required_vision_replan",
        "blocking_gate_survives_adversarial_diagnostic",
        "peer_selection_survives_adversarial_diagnostic",
    }
    assert {contrast["contrast_kind"] for contrast in catalog["contrasts"]} == {
        "invariance",
        "sensitivity",
    }
    composed = [
        scenario
        for scenario in catalog["scenarios"]
        if scenario["scenario_family"] == "control_plane_composition"
    ]
    assert len(composed) == 3
    assert all(len(scenario["composition_dimensions"]) == 4 for scenario in composed)
    assert all(
        scenario["repeat_policy"]
        == {
            "attempts": 2,
            "pass_condition": "all_attempts_source_aligned",
            "automatic_retry_on_actor_error": False,
        }
        for scenario in catalog["scenarios"]
    )
    planning = next(
        scenario
        for scenario in catalog["scenarios"]
        if scenario["scenario_id"] == "turn_planning_horizon_strategic_context"
    )
    assert planning["action_oracle"] == "planning_horizon_regression_gate_v0"
    assert all(
        "action_oracle" not in scenario
        for scenario in catalog["scenarios"]
        if scenario is not planning
    )


def test_portfolio_preflights_every_scenario_before_actor_spend(tmp_path: Path) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    packets["onboarding_projection_repair"] = {
        **packets["onboarding_projection_repair"],
        "derived_route": "continue_validation",
    }
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    def onboarding_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _onboarding_actor(request)

    with pytest.raises(
        ValueError,
        match="actual onboarding behavior violates stable invariants",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-portfolio-preflight",
            turn_actor=turn_actor,
            onboarding_actor=onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_portfolio_aborts_on_authentication_failure_with_bounded_receipt(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    calls = 0

    def turn_actor(_: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        raise DoubaoActorTransportError(
            "sensitive provider response must not be persisted",
            error_code="provider_authentication_failed",
        )

    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-auth-failure",
        turn_actor=turn_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    assert calls == 1
    assert result["qualification_passed"] is False
    assert result["actor_call_count"] < result["actor_call_budget"]
    failed = next(item for item in result["scenarios"] if item["status"] == "failed")
    assert failed["failure_codes"] == ["provider_authentication_failed"]
    assert "sensitive provider response" not in json.dumps(result)


def test_portfolio_preflight_rejects_wrong_same_agent_route(tmp_path: Path) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    source = deepcopy(sources["turn_same_agent_continuation"])
    source["selected_todo"]["claimed_by"] = "codex-wrong-peer"
    sources["turn_same_agent_continuation"] = source
    packets["turn_same_agent_continuation"] = compact_quota_should_run_cli_payload(
        source
    )
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match="peer-agent scenario must route work to the selected peer",
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-portfolio-wrong-peer",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_portfolio_turn_actor_reads_actual_default_packet_without_semantic_echo(
    tmp_path: Path,
) -> None:
    requests: list[Mapping[str, Any]] = []

    def turn_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        requests.append(request)
        result = _turn_actor(request)
        if request["semantic_contract_required"] is False:
            result["decision"].pop("semantic_contract", None)
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-runtime-shaped",
        turn_actor=turn_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    assert result["qualification_passed"] is True
    assert result["scenario_count"] == 21
    assert result["contrast_count"] == 6
    assert result["actor_call_budget"] == 42
    assert result["actor_call_count"] == 42
    assert result["failure_count"] == 0
    assert result["skip_count"] == 0
    assert result["contrast_failure_count"] == 0
    assert all(contrast["status"] == "passed" for contrast in result["contrasts"])
    assert requests
    assert all(request["arm"] == "full_packet" for request in requests)
    assert all(request["packet"]["mode"] == "should-run" for request in requests)
    assert all(
        request["packet_schema_version"] == FULL_QUOTA_DECISION_PACKET_SCHEMA_VERSION
        for request in requests
    )
    required_requests = [
        request for request in requests if request["semantic_contract_required"]
    ]
    assert len(required_requests) == 2
    assert all(
        request["packet"]["selected_todo"]["todo_id"] == "todo_regression_gate"
        for request in required_requests
    )
    assert all(
        request["response_contract"]["semantic_contract_fields"]
        == ["planning_horizon"]
        for request in required_requests
    )


def test_planning_horizon_scenario_requires_bounded_semantic_readback(
    tmp_path: Path,
) -> None:
    def forgetful_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        result["decision"].pop("semantic_contract", None)
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-planning-horizon-forgetful-model",
        turn_actor=forgetful_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_planning_horizon_strategic_context"
    )
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["failure_codes"] == ["actor_result_invalid"]


@pytest.mark.parametrize(
    ("actions", "expected_pass", "failure_codes"),
    [
        (
            ("inspect", "read", "test", "writeback", "spend"),
            True,
            [],
        ),
        (
            ("test", "writeback", "spend"),
            False,
            ["action_oracle:first_action_must_inspect"],
        ),
        (
            ("inspect", "edit", "test", "writeback", "spend"),
            False,
            ["action_oracle:forbidden_action:edit"],
        ),
        (
            ("inspect", "read", "writeback", "spend"),
            False,
            ["action_oracle:test_must_precede_settlement"],
        ),
        (
            ("inspect", "test", "spend", "writeback"),
            False,
            ["action_oracle:settlement_suffix_mismatch"],
        ),
        (
            ("inspect", "writeback", "test", "writeback", "spend"),
            False,
            ["action_oracle:premature_settlement_action:writeback"],
        ),
        (
            ("inspect", "spend", "test", "writeback", "spend"),
            False,
            ["action_oracle:premature_settlement_action:spend"],
        ),
    ],
    ids=(
        "bounded-read",
        "missing-inspection",
        "premature-edit",
        "missing-test",
        "reversed-settlement",
        "premature-writeback",
        "premature-spend",
    ),
)
def test_planning_horizon_action_oracle_grades_semantic_stages(
    tmp_path: Path,
    actions: tuple[str, ...],
    expected_pass: bool,
    failure_codes: list[str],
) -> None:
    def actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        if request["packet"].get("planning_horizon"):
            result["decision"]["intended_action_kinds"] = list(actions)
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-planning-horizon-action-oracle",
        turn_actor=actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_planning_horizon_strategic_context"
    )
    assert result["qualification_passed"] is expected_pass
    assert scenario["status"] == ("passed" if expected_pass else "failed")
    assert scenario["failure_codes"] == failure_codes
    assert scenario["observed_action_kind_sequences"] == [list(actions)]


def test_portfolio_real_tool_scenarios_choose_from_latest_quota_result(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-real-tool-actions",
        turn_actor=_turn_actor,
        onboarding_actor=_onboarding_actor,
        **_real_tool_actors(tmp_path / "real-tool-actors"),
    )

    assert result["qualification_passed"] is True
    boundary = result["boundary"]
    assert boundary["tools_enabled"] is True
    assert boundary["tool_enabled_scenario_count"] == 5
    assert boundary["packet_interpretation_scenario_count"] == 16
    assert boundary["automatic_retries"] is False
    assert boundary["raw_model_responses_persisted"] is False
    assert boundary["raw_packets_persisted"] is False
    tool_scenarios = {
        "turn_selected_todo",
        "turn_required_vision_replan",
        "turn_scoped_gate_successor_replan",
        "turn_capability_monitor_repair",
        "turn_terminal_settlement",
    }
    for scenario in result["scenarios"]:
        if scenario["scenario_id"] in tool_scenarios:
            assert scenario["status"] == "passed"
            assert len(set(scenario["receipt_digests"])) == 2


def test_portfolio_preflight_rejects_invalid_contrast_before_actor_spend(
    tmp_path: Path,
) -> None:
    sources, packets = _scenario_inputs(tmp_path)
    source = deepcopy(sources["turn_selected_todo"])
    source["interaction_contract"]["user_channel"]["action_required"] = True
    source["action_required"] = True
    sources["turn_selected_todo"] = source
    packets["turn_selected_todo"] = compact_quota_should_run_cli_payload(source)
    calls = 0

    def turn_actor(request: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _turn_actor(request)

    with pytest.raises(
        ValueError,
        match=(
            "contrast selected_todo_survives_omitted_diagnostics "
            "source relation is invalid"
        ),
    ):
        run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id="actual-default-portfolio-invalid-contrast",
            turn_actor=turn_actor,
            onboarding_actor=_onboarding_actor,
            selected_todo_actor=_selected_todo_actor,
            replan_semantic_action_actor=_replan_semantic_action_actor,
        )

    assert calls == 0


def test_portfolio_contrast_rejects_noisy_gate_semantic_collapse(
    tmp_path: Path,
) -> None:
    def collapsed_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        packet = request["packet"]
        capability_gate = packet.get("capability_gate") or {}
        if (
            capability_gate.get("action") == "operator_gate"
            and "payload_compaction" in capability_gate
        ):
            result["decision"] = {
                **result["decision"],
                "must_attempt_work": True,
            }
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-noisy-gate-collapse",
        turn_actor=collapsed_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    contrast = next(
        item
        for item in result["contrasts"]
        if item["contrast_id"] == "blocking_gate_survives_omitted_diagnostics"
    )
    assert result["qualification_passed"] is False
    assert result["failure_count"] == 2
    assert result["contrast_failure_count"] == 1
    assert contrast["status"] == "failed"
    assert contrast["failure_codes"] == ["expected_match:must_attempt_work"]
    assert contrast["repeats_compared"] == 2


def test_portfolio_oracle_rejects_quiet_wait_for_required_vision_replan(
    tmp_path: Path,
) -> None:
    def quiet_wait_actor(run_id: str) -> dict[str, Any]:
        return {
            **_replan_semantic_action_actor(run_id),
            "decision": "wait",
        }

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-required-vision-wait",
        turn_actor=_turn_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=quiet_wait_actor,
        scoped_gate_successor_actor=_scoped_gate_successor_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_required_vision_replan"
    )
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["repeats_completed"] == 2
    assert "source_mismatch:decision" in scenario["failure_codes"]


def test_portfolio_oracle_rejects_waiting_on_hot_path_compaction_refs(
    tmp_path: Path,
) -> None:
    def waiting_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        result = _turn_actor(request)
        signature = quota_action_signature_document(request["packet"])
        selected = signature["action"].get("selected_todo") or {}
        if selected.get("todo_id") == "todo_c0ffee123456":
            result["decision"] = {
                **result["decision"],
                "decision": "wait",
                "intended_action_kinds": ["wait"],
            }
        return result

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-compaction-regression-wait",
        turn_actor=waiting_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_quota_hot_path_compaction_regression"
    )
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["repeats_completed"] == 2
    assert "source_mismatch:decision" in scenario["failure_codes"]


def test_portfolio_oracle_rejects_treating_non_blocking_notice_as_gate(
    tmp_path: Path,
) -> None:
    def blocking_actor(run_id: str) -> dict[str, Any]:
        return {
            **_scoped_gate_successor_actor(run_id),
            "qualification_passed": False,
            "failure_code": "waited_on_non_blocking_notice",
            "decision": "ask_user",
            "selected_action_matched_todo": False,
        }

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-non-blocking-notice",
        turn_actor=_turn_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
        scoped_gate_successor_actor=blocking_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_scoped_gate_successor_replan"
    )
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["repeats_completed"] == 2
    assert "source_mismatch:decision" in scenario["failure_codes"]


def test_portfolio_oracle_rejects_waiting_on_capability_monitor_repair(
    tmp_path: Path,
) -> None:
    def waiting_actor(run_id: str) -> dict[str, Any]:
        return {
            **_capability_monitor_repair_actor(run_id),
            "decision": "wait",
            "capability_callsite_verified": False,
            "same_turn_quota_reentry_completed": False,
            "monitor_fallback_avoided": False,
        }

    sources, packets = _scenario_inputs(tmp_path)
    result = run_actual_default_model_behavior_portfolio(
        packets,
        scenario_sources=sources,
        qualification_id="actual-default-portfolio-capability-monitor-repair",
        turn_actor=_turn_actor,
        onboarding_actor=_onboarding_actor,
        selected_todo_actor=_selected_todo_actor,
        replan_semantic_action_actor=_replan_semantic_action_actor,
        capability_monitor_repair_actor=waiting_actor,
    )

    scenario = next(
        item
        for item in result["scenarios"]
        if item["scenario_id"] == "turn_capability_monitor_repair"
    )
    assert result["qualification_passed"] is False
    assert scenario["status"] == "failed"
    assert scenario["repeats_completed"] == 2
    assert "source_mismatch:decision" in scenario["failure_codes"]
