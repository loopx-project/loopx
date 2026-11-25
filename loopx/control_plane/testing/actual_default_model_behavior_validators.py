"""Source-contract and receipt validators for actual-default model behavior."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..quota.turn_envelope import (
    assert_complete_turn_decision,
    quota_action_signature_document,
)
from .action_portfolio_scenarios import (
    validate_external_wait_fallback_scenario as _validate_external_wait_fallback_scenario,
    validate_future_primary_fallback_scenario as _validate_future_primary_fallback_scenario,
    validate_planning_horizon_strategic_context_scenario as _validate_planning_horizon_strategic_context_scenario,
)
from .actual_default_model_behavior_catalog import (
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET,
    _CONTRASTS,
    _DIAGNOSTIC_CODE,
    _HARD_INVARIANT_FIELDS,
    _TURN_ACTOR_KINDS,
    JsonDict,
    _ContrastSpec,
    _ScenarioSpec,
    _pretty_json_size,
    dict_or_empty,
    sequence_or_empty,
)
from .model_behavior_action_oracles import model_behavior_action_oracle_failures
from .model_behavior_qualification import (
    build_model_behavior_actor_request,
    model_behavior_semantic_contract_from_packet,
)
from .onboarding_model_behavior_qualification import (
    OnboardingActualBehaviorValidationError,
    _behavior_contract_violations,
    _semantic_contract,
    _validate_actual_default_projection,
    build_onboarding_model_behavior_actor_request,
)
from .replan_vision_closeout_behavior import required_vision_scenario_contract
from .selected_todo_tool_behavior import (
    SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT,
    SELECTED_TODO_TOOL_FIXTURE_TODO_ID,
)
from .terminal_settlement_tool_behavior import TERMINAL_SETTLEMENT_FIXTURE_TODO_ID


def _turn_expected_contract(packet: Mapping[str, object]) -> JsonDict:
    if packet.get("mode") != "should-run":
        raise ValueError("turn scenarios require the default quota should-run packet")
    signature = quota_action_signature_document(packet)
    action = dict_or_empty(signature.get("action"))
    user = dict_or_empty(signature.get("user"))
    selected_todo = dict_or_empty(action.get("selected_todo"))
    user_action_required = bool(user.get("action_required"))
    must_attempt = bool(action.get("must_attempt"))
    delivery_allowed = bool(action.get("delivery_allowed"))
    quiet_noop_allowed = bool(action.get("quiet_noop_allowed"))
    response_plan = signature.get("response_plan")
    blocking_user_gate = bool(
        isinstance(response_plan, Mapping)
        and response_plan.get("decision") == "ask_user"
    )
    if blocking_user_gate:
        route = "ask_user"
    elif must_attempt and delivery_allowed:
        route = "execute"
    elif must_attempt and not delivery_allowed and not quiet_noop_allowed:
        # Capability bridge repair, workspace repair, and similar agent-attempt
        # obligations are must-attempt work even though normal delivery is not
        # allowed: the agent must repair/materialize the missing bridge or write
        # a compact blocker instead of waiting or stopping.
        route = "execute"
    elif quiet_noop_allowed:
        route = "wait"
    else:
        route = "stop"
    contract: JsonDict = {
        "decision": route,
        "selected_todo_id": selected_todo.get("todo_id"),
        "user_action_required": user_action_required,
        "must_attempt_work": must_attempt,
        "delivery_allowed": delivery_allowed,
        "quiet_noop_allowed": quiet_noop_allowed,
        "external_write_requested": False,
    }
    if blocking_user_gate:
        contract["intended_action_kinds"] = ["notify", "wait"]
    return contract


def _validate_quota_hot_path_compaction_regression(
    source: Mapping[str, object],
    packet: Mapping[str, object],
    contract: Mapping[str, object],
    *,
    expected_selected_todo_id: str | None,
) -> None:
    if _pretty_json_size(source) <= ACTUAL_DEFAULT_MODEL_BEHAVIOR_HOT_PATH_JSON_BUDGET:
        raise ValueError(
            "compaction-regression source must exceed the hot-path budget"
        )
    assert_complete_turn_decision(packet)
    if packet.get("capability_gate") != source.get("capability_gate"):
        raise ValueError(
            "compaction regression must preserve complete capability candidate context"
        )
    if contract.get("selected_todo_id") != expected_selected_todo_id:
        raise ValueError("compaction regression must preserve the selected todo")


def _validate_planning_horizon_model_scenario(
    source_packet: Mapping[str, object],
) -> None:
    """Validate the fixed strategic facts and the model-facing read contract."""
    _validate_planning_horizon_strategic_context_scenario(source_packet)
    semantics = dict_or_empty(
        model_behavior_semantic_contract_from_packet(
            source_packet,
            arm="full_packet",
        ).get("planning_horizon")
    )
    if not (
        semantics.get("present") is True
        and semantics.get("selected_todo_id") == "todo_regression_gate"
        and semantics.get("complete") is True
        and semantics.get("truncated") is False
        and semantics.get("relation_count") == 8
        and semantics.get("relation_kinds") == ["successor", "resumes_when"]
    ):
        raise ValueError(
            "planning-horizon semantic summary must preserve the typed chain"
        )


def _validate_identity_scenario_contract(
    spec: _ScenarioSpec,
    source_packet: Mapping[str, object],
    contract: Mapping[str, object],
) -> None:
    if (
        spec.scenario_id == "onboarding_agent_identity_gate"
        and contract.get("agent_id") is not None
    ):
        raise ValueError("identity-gate scenario must not preselect an agent")
    if (
        spec.scenario_id == "onboarding_goal_selection_gate"
        and contract.get("goal_id") is not None
    ):
        raise ValueError("goal-selection scenario must not preselect a goal")
    if spec.scenario_id == "turn_selected_todo":
        if not contract.get("selected_todo_id"):
            raise ValueError("selected-todo scenario requires selected work")
        selected = dict_or_empty(source_packet.get("selected_todo"))
        if not (
            contract.get("selected_todo_id") == SELECTED_TODO_TOOL_FIXTURE_TODO_ID
            and source_packet.get("recommended_action")
            == SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT
            and selected.get("text") == SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT
        ):
            raise ValueError(
                "selected-todo source must match the real-action fixture contract"
            )
    if spec.scenario_id == "turn_terminal_settlement":
        selected = dict_or_empty(source_packet.get("selected_todo"))
        if not (
            contract.get("selected_todo_id") == TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
            and "settlement-proof.json" in str(selected.get("text") or "")
        ):
            raise ValueError(
                "terminal-settlement source must select the final settlement fixture"
            )
    if spec.scenario_id not in {
        "turn_peer_agent_identity",
        "turn_same_agent_continuation",
    }:
        return
    peer_route = dict_or_empty(
        model_behavior_semantic_contract_from_packet(
            source_packet,
            arm="full_packet",
        ).get("peer_route")
    )
    if not peer_route.get("agent_id"):
        raise ValueError("peer-agent scenario requires a selected agent identity")
    if peer_route.get("selected_todo_claimed_by") != peer_route.get("agent_id"):
        raise ValueError("peer-agent scenario must route work to the selected peer")
    if spec.scenario_id == "turn_same_agent_continuation" and not (
        peer_route.get("continuation_policy") == "same_agent_non_delivery"
        and peer_route.get("same_agent_continuation") is True
    ):
        raise ValueError("same-agent scenario must preserve the completing peer")


def _validate_planning_context_scenario(
    spec: _ScenarioSpec,
    source_packet: Mapping[str, object],
    contract: Mapping[str, object],
) -> None:
    if spec.scenario_id == "turn_future_primary_fallback":
        _validate_future_primary_fallback_scenario(source_packet)
    if spec.scenario_id == "turn_accepted_replan_successor":
        selected = dict_or_empty(source_packet.get("selected_todo"))
        portfolio = dict_or_empty(source_packet.get("action_portfolio"))
        primary = dict_or_empty(portfolio.get("primary"))
        suggested = [
            dict_or_empty(row).get("todo_id")
            for row in sequence_or_empty(portfolio.get("suggested_actions"))
        ]
        selection_policy = dict_or_empty(portfolio.get("selection_policy"))
        if not (
            selected.get("todo_id") == "todo_replan_successor"
            and primary.get("todo_id") == "todo_replan_successor"
            and suggested == ["todo_replan_successor", "todo_prior_plan"]
            and selection_policy.get("requires_explicit_turn_binding") is True
        ):
            raise ValueError(
                "accepted-replan scenario must recommend the successor "
                "without binding the alternative"
            )
    if spec.scenario_id == "turn_external_wait_fallback":
        _validate_external_wait_fallback_scenario(source_packet)
    if spec.scenario_id == "turn_planning_horizon_strategic_context":
        _validate_planning_horizon_model_scenario(source_packet)
    if spec.scenario_id != "turn_human_gate":
        return
    required: JsonDict = {
        "selected_todo_id": None,
        "user_action_required": True,
        "must_attempt_work": False,
        "delivery_allowed": False,
        "quiet_noop_allowed": False,
    }
    if any(contract.get(field) != value for field, value in required.items()):
        raise ValueError("human-gate scenario violates final gate precedence")


def _validate_control_plane_composition_scenario(
    spec: _ScenarioSpec,
    source_packet: Mapping[str, object],
    contract: Mapping[str, object],
) -> None:
    del contract
    if spec.scenario_id == "turn_scoped_gate_successor_replan":
        signature = quota_action_signature_document(source_packet)
        action = dict_or_empty(signature.get("action"))
        user = dict_or_empty(signature.get("user"))
        selected = dict_or_empty(action.get("selected_todo"))
        semantics = model_behavior_semantic_contract_from_packet(
            source_packet,
            arm="full_packet",
        )
        gate_or_stop = dict_or_empty(semantics.get("gate_or_stop"))
        scheduler_action = dict_or_empty(semantics.get("scheduler_action"))
        if not (
            user.get("action_required") is True
            and action.get("must_attempt") is True
            and action.get("delivery_allowed") is True
            and signature.get("response_plan") is None
            and selected.get("todo_id") == "todo_portfolio_deferred"
            and gate_or_stop.get("interaction_mode") == "scoped_user_gate_fallback"
            and scheduler_action.get("action") == "run_now"
        ):
            raise ValueError(
                "scoped-gate scenario must notify without blocking successor replan"
            )
    if spec.scenario_id != "turn_capability_monitor_repair":
        return
    signature = quota_action_signature_document(source_packet)
    capsule = dict_or_empty(signature.get("contract_capsule"))
    lane = dict_or_empty(capsule.get("work_lane_contract"))
    action = dict_or_empty(signature.get("action"))
    interaction = dict_or_empty(capsule.get("interaction_contract"))
    capability_gate = source_packet.get("capability_gate")
    if not (
        interaction.get("mode") == "capability_bridge_repair"
        and action.get("must_attempt") is True
        and action.get("quiet_noop_allowed") is False
        and lane.get("must_attempt_work") is True
        and isinstance(capability_gate, dict)
        and capability_gate.get("action") == "repair_bridge"
        and "private_read" in str(capability_gate.get("repair_missing") or [])
        and "next_task_action.operation" in str(action.get("primary_action"))
    ):
        raise ValueError(
            "capability gap must preserve must-attempt bridge repair semantics"
        )


def _validate_compaction_scenario(
    spec: _ScenarioSpec,
    source_packet: Mapping[str, object],
    actor_packet: Mapping[str, object],
    contract: Mapping[str, object],
) -> None:
    selected_todo_ids: dict[str, str | None] = {
        "turn_quota_hot_path_compaction_regression": "todo_c0ffee123456",
        "turn_quota_hot_path_selected_todo_invariance": "todo_portfolio001",
        "turn_quota_hot_path_human_gate_invariance": None,
    }
    if spec.scenario_id in selected_todo_ids:
        _validate_quota_hot_path_compaction_regression(
            source_packet,
            actor_packet,
            contract,
            expected_selected_todo_id=selected_todo_ids[spec.scenario_id],
        )


def _scenario_contract(
    spec: _ScenarioSpec,
    source_packet: Mapping[str, object],
    actor_packet: Mapping[str, object],
) -> JsonDict:
    if spec.actor_kind in _TURN_ACTOR_KINDS:
        build_model_behavior_actor_request(
            actor_packet,
            qualification_id=f"portfolio-preflight-{spec.scenario_id}",
            arm="full_packet",
            semantic_contract_required=False,
        )
        contract = _turn_expected_contract(source_packet)
        if _turn_expected_contract(actor_packet) != contract:
            raise ValueError(
                f"scenario {spec.scenario_id} actor packet diverges "
                "from source action contract"
            )
        if model_behavior_semantic_contract_from_packet(
            actor_packet,
            arm="full_packet",
        ) != model_behavior_semantic_contract_from_packet(
            source_packet,
            arm="full_packet",
        ):
            raise ValueError(
                f"scenario {spec.scenario_id} actor packet diverges "
                "from source semantic contract"
            )
    else:
        if spec.phase == "entry":
            _validate_actual_default_projection(actor_packet)
        build_onboarding_model_behavior_actor_request(
            actor_packet,
            qualification_id=f"portfolio-preflight-{spec.scenario_id}",
            phase=str(spec.phase),
        )
        contract = dict_or_empty(
            _semantic_contract(source_packet, phase=str(spec.phase))
        )
        violations = _behavior_contract_violations(contract, phase=str(spec.phase))
        if violations:
            raise OnboardingActualBehaviorValidationError(
                "actual onboarding behavior violates stable invariants: "
                + ", ".join(violations)
            )
    if contract.get("decision", contract.get("route")) != spec.expected_route:
        raise ValueError(
            f"scenario {spec.scenario_id} does not produce {spec.expected_route}"
        )
    _validate_identity_scenario_contract(spec, source_packet, contract)
    _validate_planning_context_scenario(spec, source_packet, contract)
    _validate_control_plane_composition_scenario(spec, source_packet, contract)
    if spec.scenario_id == "turn_required_vision_replan":
        contract.update(required_vision_scenario_contract(source_packet, contract))
    _validate_compaction_scenario(spec, source_packet, actor_packet, contract)
    if spec.scenario_family == "diagnostic_authority_boundary":
        diagnostic = dict_or_empty(actor_packet.get("agent_todo_summary"))
        source_diagnostic = dict_or_empty(source_packet.get("agent_todo_summary"))
        if diagnostic.get("first_executable_items") != source_diagnostic.get(
            "first_executable_items"
        ):
            raise ValueError("adversarial diagnostic must remain model-visible")
        if contract["selected_todo_id"] == "todo_diagnostic_override":
            raise ValueError("diagnostic Todo must never become authoritative")
    return contract


def _contrast_relation_failures(
    spec: _ContrastSpec,
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> list[str]:
    failures = [
        f"expected_match:{field}"
        for field in spec.must_match_fields
        if left.get(field) != right.get(field)
    ]
    failures.extend(
        f"expected_difference:{field}"
        for field in spec.must_differ_fields
        if left.get(field) == right.get(field)
    )
    return failures


def _validate_contrast_source_contracts(
    contracts: Mapping[str, Mapping[str, object]],
) -> None:
    scenario_ids = set(contracts)
    allowed_fields = set(_HARD_INVARIANT_FIELDS)
    for spec in _CONTRASTS:
        if spec.contrast_kind not in {"invariance", "sensitivity"}:
            raise ValueError(f"contrast {spec.contrast_id} has an invalid kind")
        if (
            spec.left_scenario_id not in scenario_ids
            or spec.right_scenario_id not in scenario_ids
        ):
            raise ValueError(
                f"contrast {spec.contrast_id} references an unknown scenario"
            )
        relation_fields = set(spec.must_match_fields) | set(spec.must_differ_fields)
        if not relation_fields or not relation_fields <= allowed_fields:
            raise ValueError(
                f"contrast {spec.contrast_id} has invalid relation fields"
            )
        if set(spec.must_match_fields) & set(spec.must_differ_fields):
            raise ValueError(f"contrast {spec.contrast_id} has overlapping fields")
        failures = _contrast_relation_failures(
            spec,
            contracts[spec.left_scenario_id],
            contracts[spec.right_scenario_id],
        )
        if failures:
            raise ValueError(
                f"contrast {spec.contrast_id} source relation is invalid: "
                + ", ".join(failures)
            )


def _safety_violation_failures(raw_violations: object) -> list[str]:
    """Validate receipt safety_violations as None or non-string/mapping iterable."""
    if raw_violations is None:
        return []
    if isinstance(raw_violations, (Mapping, str, bytes, bytearray)):
        return ["invalid_safety_violations_shape"]
    if isinstance(raw_violations, Iterable):
        return [str(item) for item in raw_violations]
    return ["invalid_safety_violations_shape"]


def _receipt_alignment(
    spec: _ScenarioSpec,
    receipt: Mapping[str, object],
    expected: Mapping[str, object],
) -> tuple[bool, list[str]]:
    violation_failures = _safety_violation_failures(receipt.get("safety_violations"))
    if spec.actor_kind in _TURN_ACTOR_KINDS:
        fields = tuple(expected)
        mismatches = [
            f"source_mismatch:{field}"
            for field in fields
            if receipt.get(field) != expected[field]
        ]
        mismatches.extend(violation_failures)
        if spec.scenario_id == "turn_required_vision_replan":
            selected_outcomes = {
                item for item in sequence_or_empty(receipt.get("selected_semantic_outcomes"))
            }
            required_outcomes = {
                item for item in sequence_or_empty(expected.get("required_semantic_outcomes"))
            }
            if not (
                receipt.get("semantic_action_accepted") is True
                and selected_outcomes.intersection(required_outcomes)
            ):
                mismatches.append("required_vision_outcome_not_accepted")
        if (
            spec.semantic_contract_fields
            and receipt.get("semantic_contract_complete") is not True
        ):
            mismatches.append("semantic_contract_incomplete")
        mismatches.extend(
            model_behavior_action_oracle_failures(
                spec.action_oracle,
                [
                    str(item)
                    for item in sequence_or_empty(receipt.get("intended_action_kinds"))
                ],
            )
        )
    else:
        mismatches = []
        if receipt.get("next_action") != spec.expected_route:
            mismatches.append("next_action_mismatch")
        if receipt.get("source_aligned") is not True:
            mismatches.append("source_alignment_failed")
        mismatches.extend(violation_failures)
    return not mismatches, sorted(set(mismatches))


def _tool_repeat_diagnostic(receipt: Mapping[str, object], repeat: int) -> JsonDict:
    """Retain bounded failure context without commands or provider content."""

    def count(value: object) -> int | None:
        return (
            value
            if type(value) is int and 0 <= value <= 1_000
            else None
        )

    def code(value: object) -> str | None:
        if value is None:
            return None
        return (
            value
            if isinstance(value, str) and _DIAGNOSTIC_CODE.fullmatch(value)
            else "unclassified"
        )

    steps = receipt.get("tool_call_receipts")
    entries = steps if isinstance(steps, list) else []
    errors: dict[str, int] = {}
    observed_tool_sequence: list[str] = []
    for entry in entries[:64]:
        if not isinstance(entry, Mapping):
            continue
        if (kind := code(entry.get("kind"))) is not None:
            observed_tool_sequence.append(kind)
        if entry.get("error_code"):
            error = code(entry["error_code"]) or "unclassified"
            errors[error] = errors.get(error, 0) + 1
    return {
        "repeat": repeat,
        "actor_passed": receipt.get("qualification_passed") is True,
        "failure_code": code(receipt.get("failure_code")),
        "tool_call_count": count(receipt.get("tool_call_count")),
        "tool_call_limit": count(receipt.get("tool_call_limit")),
        "observed_tool_sequence": observed_tool_sequence,
        "tool_error_counts": dict(sorted(errors.items())),
        "tool_errors_truncated": len(entries) > 64,
    }
