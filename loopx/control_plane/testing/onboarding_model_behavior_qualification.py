from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from typing import Any, Protocol

from ..runtime.public_safety import LOCAL_PATH_SURFACE_PATTERN
from .model_behavior_qualification import (
    _ACTOR_RESULT_FIELDS,
    _reason_codes,
    _reject_private_or_secret_material,
    _token,
)


ONBOARDING_MODEL_BEHAVIOR_REQUEST_SCHEMA_VERSION = (
    "onboarding_model_behavior_actor_request_v0"
)
ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION = (
    "onboarding_model_behavior_actor_result_v0"
)
ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION = (
    "onboarding_model_behavior_decision_v0"
)
ONBOARDING_MODEL_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "onboarding_model_behavior_receipt_v0"
)
ONBOARDING_ACTUAL_BEHAVIOR_QUALIFICATION_SCHEMA_VERSION = (
    "onboarding_actual_behavior_qualification_v0"
)
ONBOARDING_POSTCONDITION_SCHEMA_VERSION = "onboarding_postcondition_observation_v0"
START_GOAL_PACKET_SCHEMA_VERSION = "loopx_start_goal_guided_v0"

ONBOARDING_MODEL_BEHAVIOR_PHASES = ("entry", "postcondition")
ONBOARDING_REQUIRED_CONNECT_COMMAND_IDS = (
    "goal_start_connect_if_needed",
    "goal_start_refresh_state",
    "goal_start_host_loop_activation",
    "goal_start_quota_should_run",
)

_ENTRY_CONTRACT_FIELDS = (
    "route",
    "goal_id",
    "agent_id",
    "action_command_ids",
    "host_loop_activation_available",
    "host_loop_activation_after_todo_write",
    "requested_host_surface",
    "host_surface",
    "activation_method",
    "visible_goal_command_available",
    "writes_now",
    "spends_quota_now",
)
_POSTCONDITION_CONTRACT_FIELDS = (
    "route",
    "state_projection_gap",
    "executable_todo_present",
    "selected_action_kind",
    "normal_delivery_allowed",
    "user_action_required",
)
_DECISION_FIELDS = {
    "schema_version",
    "phase",
    "next_action",
    "semantic_contract",
    "reason_codes",
}
_ENTRY_ROUTES = {
    "connect_if_needed",
    "select_agent_identity",
    "select_goal",
    "stop",
}
_POSTCONDITION_ROUTES = {
    "continue_validation",
    "repair_projection",
    "ask_user",
    "stop",
}


class OnboardingModelBehaviorActor(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class OnboardingActualBehaviorValidationError(ValueError):
    """The actual onboarding input violates a stable behavior invariant."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_actual_default_projection(packet: Mapping[str, Any]) -> None:
    if packet.get("command_pack_detail_included") is True:
        raise OnboardingActualBehaviorValidationError(
            "regular onboarding model qualification excludes explicit "
            "command-pack detail; validate cold-path restoration deterministically"
        )


def _model_safe_packet_value(value: Any) -> Any:
    """Redact local path surfaces while preserving the shipped packet shape."""
    if isinstance(value, Mapping):
        return {str(key): _model_safe_packet_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_model_safe_packet_value(item) for item in value]
    if isinstance(value, str):
        return LOCAL_PATH_SURFACE_PATTERN.sub("<LOCAL_PATH>", value)
    return value


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _optional_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    return _mapping(value, field=field)


def _nullable_token(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _token(value, field=field)


def _entry_route(packet: Mapping[str, Any]) -> str:
    transaction = _mapping(packet.get("guided_transaction"), field="guided_transaction")
    blocked_by = str(transaction.get("blocked_by") or "")
    if blocked_by == "agent_identity_selection" or transaction.get(
        "identity_selection_gate"
    ):
        return "select_agent_identity"
    if blocked_by == "goal_selection" or transaction.get("goal_selection_gate"):
        return "select_goal"
    step_ids = [
        str(step.get("id") or "")
        for step in transaction.get("ordered_steps") or []
        if isinstance(step, Mapping)
    ]
    if "connect_if_needed" in step_ids:
        return "connect_if_needed"
    return "stop"


def _command_pack_value(packet: Mapping[str, Any], field: str) -> Any:
    value = packet.get(field)
    if value is not None:
        return value
    command_pack = packet.get("command_pack")
    if isinstance(command_pack, Mapping):
        return command_pack.get(field)
    return None


def onboarding_entry_semantic_contract(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    if packet.get("schema_version") != START_GOAL_PACKET_SCHEMA_VERSION:
        raise ValueError("entry packet must use loopx_start_goal_guided_v0")
    transaction = _mapping(packet.get("guided_transaction"), field="guided_transaction")
    commands = _optional_mapping(
        _command_pack_value(packet, "commands"), field="commands"
    )
    activation = _optional_mapping(
        _command_pack_value(packet, "host_loop_activation"),
        field="host_loop_activation",
    )
    host_mutation = _optional_mapping(
        activation.get("host_mutation"), field="host_loop_activation.host_mutation"
    )
    command_ids = [
        command_id
        for command_id in ONBOARDING_REQUIRED_CONNECT_COMMAND_IDS
        if isinstance(commands.get(command_id), str) and commands[command_id].strip()
    ]
    return {
        "route": _entry_route(packet),
        "goal_id": _nullable_token(packet.get("goal_id"), field="goal_id"),
        "agent_id": _nullable_token(
            packet.get("agent_id") or activation.get("agent_id"),
            field="agent_id",
        ),
        "action_command_ids": command_ids,
        "host_loop_activation_available": bool(activation),
        "host_loop_activation_after_todo_write": bool(
            activation.get("activation_required_after_todo_write")
        ),
        "requested_host_surface": _nullable_token(
            packet.get("host_surface"), field="requested_host_surface"
        ),
        "host_surface": _nullable_token(
            activation.get("host_surface"), field="host_surface"
        ),
        "activation_method": _nullable_token(
            activation.get("activation_method"), field="activation_method"
        ),
        "visible_goal_command_available": (
            host_mutation.get("host_command") == "/goal <task_body>"
        ),
        "writes_now": bool(transaction.get("writes_now")),
        "spends_quota_now": bool(transaction.get("spends_quota_now")),
    }


def onboarding_entry_contract_violations(
    contract: Mapping[str, Any],
) -> list[str]:
    """Validate stable onboarding behavior without deriving it from the packet."""
    violations: list[str] = []
    route = str(contract.get("route") or "")
    if route not in _ENTRY_ROUTES:
        violations.append("entry_route_invalid")
    if contract.get("writes_now") is not False:
        violations.append("entry_must_not_write")
    if contract.get("spends_quota_now") is not False:
        violations.append("entry_must_not_spend_quota")
    if route == "connect_if_needed":
        if contract.get("goal_id") is None:
            violations.append("connect_route_requires_goal_id")
        if contract.get("agent_id") is None:
            violations.append("connect_route_requires_agent_id")
        if (
            tuple(contract.get("action_command_ids") or ())
            != ONBOARDING_REQUIRED_CONNECT_COMMAND_IDS
        ):
            violations.append("connect_route_missing_required_commands")
        if contract.get("host_loop_activation_available") is not True:
            violations.append("connect_route_requires_host_loop_activation")
        if contract.get("host_loop_activation_after_todo_write") is not True:
            violations.append("host_loop_must_activate_after_todo_write")
        if contract.get("requested_host_surface") in {"codex-ide-plugin", "codex-ide"}:
            if contract.get("host_surface") != "codex_ide_visible_goal_mode":
                violations.append("codex_ide_requires_ide_goal_surface")
            if contract.get("activation_method") != "set_visible_goal":
                violations.append("codex_ide_requires_visible_goal_activation")
            if contract.get("visible_goal_command_available") is not True:
                violations.append("codex_ide_requires_goal_command")
    return violations


def build_onboarding_postcondition_observation(
    *,
    check_warning_codes: Sequence[str],
    executable_todo_count: int,
    selected_action_kind: str | None,
    normal_delivery_allowed: bool,
    user_action_required: bool,
    next_action_actionable: bool,
) -> dict[str, Any]:
    if executable_todo_count < 0:
        raise ValueError("executable_todo_count must be non-negative")
    warning_codes = [
        _token(code, field="check_warning_codes[]") for code in check_warning_codes
    ]
    selected_kind = _nullable_token(
        selected_action_kind,
        field="selected_action_kind",
    )
    projection_gap = "state_projection_gap" in warning_codes or (
        next_action_actionable
        and executable_todo_count == 0
        and not user_action_required
    )
    if user_action_required:
        route = "ask_user"
    elif projection_gap:
        route = "repair_projection"
    elif executable_todo_count > 0 and normal_delivery_allowed:
        route = "continue_validation"
    else:
        route = "stop"
    return {
        "schema_version": ONBOARDING_POSTCONDITION_SCHEMA_VERSION,
        "check_warning_codes": warning_codes,
        "executable_todo_count": executable_todo_count,
        "selected_action_kind": selected_kind,
        "normal_delivery_allowed": normal_delivery_allowed,
        "user_action_required": user_action_required,
        "next_action_actionable": next_action_actionable,
        "derived_route": route,
        "state_projection_gap": projection_gap,
    }


def onboarding_postcondition_semantic_contract(
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    if observation.get("schema_version") != ONBOARDING_POSTCONDITION_SCHEMA_VERSION:
        raise ValueError(
            "postcondition packet must use onboarding_postcondition_observation_v0"
        )
    count = observation.get("executable_todo_count")
    if not isinstance(count, int) or count < 0:
        raise ValueError("postcondition executable_todo_count must be non-negative")
    route = str(observation.get("derived_route") or "")
    if route not in _POSTCONDITION_ROUTES:
        raise ValueError("postcondition derived_route is invalid")
    return {
        "route": route,
        "state_projection_gap": bool(observation.get("state_projection_gap")),
        "executable_todo_present": count > 0,
        "selected_action_kind": _nullable_token(
            observation.get("selected_action_kind"),
            field="selected_action_kind",
        ),
        "normal_delivery_allowed": bool(observation.get("normal_delivery_allowed")),
        "user_action_required": bool(observation.get("user_action_required")),
    }


def onboarding_postcondition_contract_violations(
    contract: Mapping[str, Any],
) -> list[str]:
    """Check route invariants independently from the observation reducer."""
    violations: list[str] = []
    route = str(contract.get("route") or "")
    if route not in _POSTCONDITION_ROUTES:
        return ["postcondition_route_invalid"]
    if route == "continue_validation":
        if contract.get("state_projection_gap") is not False:
            violations.append("continue_route_forbids_projection_gap")
        if contract.get("executable_todo_present") is not True:
            violations.append("continue_route_requires_executable_todo")
        if contract.get("normal_delivery_allowed") is not True:
            violations.append("continue_route_requires_delivery")
        if contract.get("user_action_required") is not False:
            violations.append("continue_route_forbids_user_gate")
    elif route == "repair_projection":
        if contract.get("state_projection_gap") is not True:
            violations.append("repair_route_requires_projection_gap")
        if contract.get("executable_todo_present") is not False:
            violations.append("repair_route_forbids_executable_todo")
        if contract.get("normal_delivery_allowed") is not False:
            violations.append("repair_route_forbids_normal_delivery")
    elif route == "ask_user":
        if contract.get("user_action_required") is not True:
            violations.append("ask_user_route_requires_user_gate")
    elif contract.get("normal_delivery_allowed") is True:
        violations.append("stop_route_forbids_normal_delivery")
    return violations


def _semantic_contract(
    packet: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    if phase == "entry":
        return onboarding_entry_semantic_contract(packet)
    if phase == "postcondition":
        return onboarding_postcondition_semantic_contract(packet)
    raise ValueError("phase must be entry or postcondition")


def _behavior_contract_violations(
    contract: Mapping[str, Any],
    *,
    phase: str,
) -> list[str]:
    if phase == "entry":
        return onboarding_entry_contract_violations(contract)
    return onboarding_postcondition_contract_violations(contract)


def build_onboarding_model_behavior_actor_request(
    packet: Mapping[str, Any],
    *,
    qualification_id: str,
    phase: str,
) -> dict[str, Any]:
    if phase not in ONBOARDING_MODEL_BEHAVIOR_PHASES:
        raise ValueError("phase must be entry or postcondition")
    normalized_packet = _model_safe_packet_value(packet)
    if not isinstance(normalized_packet, dict):
        raise ValueError("packet must be an object")
    contract = _semantic_contract(normalized_packet, phase=phase)
    violations = _behavior_contract_violations(contract, phase=phase)
    if violations:
        raise OnboardingActualBehaviorValidationError(
            "actual onboarding behavior violates stable invariants: "
            + ", ".join(violations)
        )
    _reject_private_or_secret_material(normalized_packet)
    return {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_REQUEST_SCHEMA_VERSION,
        "qualification_id": _token(qualification_id, field="qualification_id"),
        "phase": phase,
        "packet": normalized_packet,
        "sandbox": {
            "schema_version": "model_behavior_no_write_sandbox_v0",
            "tools_enabled": False,
            "filesystem_writes_allowed": False,
            "external_writes_allowed": False,
            "provider_network_only": True,
        },
        "response_contract": {
            "schema_version": ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
            "format": "json_object",
            "reject_unknown_fields": True,
            "semantic_contract_fields": list(
                _ENTRY_CONTRACT_FIELDS
                if phase == "entry"
                else _POSTCONDITION_CONTRACT_FIELDS
            ),
        },
    }


def normalize_onboarding_model_behavior_actor_request(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("actor request must be an object")
    if raw.get("schema_version") != ONBOARDING_MODEL_BEHAVIOR_REQUEST_SCHEMA_VERSION:
        raise ValueError(
            "actor request must use onboarding_model_behavior_actor_request_v0"
        )
    packet = _mapping(raw.get("packet"), field="packet")
    canonical = build_onboarding_model_behavior_actor_request(
        packet,
        qualification_id=str(raw.get("qualification_id") or ""),
        phase=str(raw.get("phase") or ""),
    )
    if dict(raw) != canonical:
        raise ValueError("actor request does not match the canonical no-write contract")
    return canonical


def normalize_onboarding_model_behavior_actor_result(
    raw: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("actor result must be an object")
    unknown = sorted(set(raw) - _ACTOR_RESULT_FIELDS)
    if unknown:
        raise ValueError(f"unknown actor result field(s): {', '.join(unknown)}")
    if raw.get("schema_version") != ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION:
        raise ValueError(
            "actor result must use onboarding_model_behavior_actor_result_v0"
        )
    if raw.get("tool_calls") != []:
        raise ValueError("onboarding qualification forbids all tool calls")
    decision = _mapping(raw.get("decision"), field="decision")
    unknown_decision = sorted(set(decision) - _DECISION_FIELDS)
    if unknown_decision:
        raise ValueError(
            f"unknown onboarding decision field(s): {', '.join(unknown_decision)}"
        )
    if (
        decision.get("schema_version")
        != ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION
    ):
        raise ValueError("decision must use onboarding_model_behavior_decision_v0")
    if decision.get("phase") != phase:
        raise ValueError("decision phase must match the actor request")
    next_action = str(decision.get("next_action") or "")
    allowed_actions = _ENTRY_ROUTES if phase == "entry" else _POSTCONDITION_ROUTES
    if next_action not in allowed_actions:
        raise ValueError("decision next_action is invalid for this phase")
    contract = _mapping(decision.get("semantic_contract"), field="semantic_contract")
    contract_fields = (
        _ENTRY_CONTRACT_FIELDS if phase == "entry" else _POSTCONDITION_CONTRACT_FIELDS
    )
    if set(contract) != set(contract_fields):
        raise ValueError("decision semantic_contract fields do not match the phase")
    _reject_private_or_secret_material(contract, path="decision.semantic_contract")
    return {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION,
        "actor_ref": _token(raw.get("actor_ref"), field="actor_ref"),
        "decision": {
            "schema_version": ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
            "phase": phase,
            "next_action": next_action,
            "semantic_contract": {
                field: json.loads(_canonical_json(contract[field]))
                for field in contract_fields
            },
            "reason_codes": _reason_codes(decision.get("reason_codes")),
        },
        "tool_calls": [],
    }


def run_onboarding_model_behavior_phase(
    packet: Mapping[str, Any],
    *,
    qualification_id: str,
    phase: str,
    actor: OnboardingModelBehaviorActor,
) -> dict[str, Any]:
    request = build_onboarding_model_behavior_actor_request(
        packet,
        qualification_id=qualification_id,
        phase=phase,
    )
    result = normalize_onboarding_model_behavior_actor_result(
        actor(request),
        phase=phase,
    )
    decision = dict(result["decision"])
    expected = _semantic_contract(request["packet"], phase=phase)
    actual = dict(decision["semantic_contract"])
    alignment = {field: actual[field] == expected[field] for field in expected}
    violations = [
        f"semantic_contract_mismatch:{field}"
        for field, matches in alignment.items()
        if not matches
    ]
    if decision["next_action"] != expected["route"]:
        violations.append("next_action_mismatch")
    return {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        "qualification_id": request["qualification_id"],
        "phase": phase,
        "actor_ref": result["actor_ref"],
        "packet_digest": _digest(request["packet"]),
        "decision_digest": _digest(decision),
        "next_action": decision["next_action"],
        "source_aligned": not violations,
        "behavior_contract_valid": True,
        "semantic_contract_complete": all(alignment.values()),
        "semantic_contract_alignment": alignment,
        "semantic_contract_digests": {
            field: _digest(actual[field]) for field in actual
        },
        "safety_violations": violations,
        "boundary": {
            "tools_enabled": False,
            "tool_call_count": 0,
            "filesystem_writes_allowed": False,
            "external_writes_allowed": False,
            "raw_packet_persisted": False,
            "raw_model_response_persisted": False,
        },
    }


def _receipt_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "next_action": receipt["next_action"],
        "source_aligned": receipt["source_aligned"],
        "behavior_contract_valid": receipt["behavior_contract_valid"],
        "safety_violations": list(receipt["safety_violations"]),
        "receipt_digest": _digest(dict(receipt)),
    }


def run_onboarding_actual_behavior_qualification(
    actual_packet: Mapping[str, Any],
    *,
    qualification_id: str,
    actor: OnboardingModelBehaviorActor,
    transition_runner: Callable[[], Mapping[str, Any]],
    repair_observation: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_actual_default_projection(actual_packet)
    entry_contract = onboarding_entry_semantic_contract(actual_packet)
    entry_violations = onboarding_entry_contract_violations(entry_contract)
    if entry_violations:
        raise OnboardingActualBehaviorValidationError(
            "actual onboarding behavior violates stable invariants: "
            + ", ".join(entry_violations)
        )
    if entry_contract["route"] != "connect_if_needed":
        raise OnboardingActualBehaviorValidationError(
            "closed-loop qualification requires the connect_if_needed route"
        )

    entry_receipt = run_onboarding_model_behavior_phase(
        actual_packet,
        qualification_id=qualification_id,
        phase="entry",
        actor=actor,
    )
    if entry_receipt["source_aligned"] is not True:
        return {
            "schema_version": ONBOARDING_ACTUAL_BEHAVIOR_QUALIFICATION_SCHEMA_VERSION,
            "qualification_id": qualification_id,
            "closed_loop_complete": False,
            "qualification_passed": False,
            "automatic_release_promotion_allowed": False,
            "entry": _receipt_summary(entry_receipt),
            "healthy_postcondition": None,
            "repair_calibration": None,
            "failure_code": "entry_source_alignment_failed",
        }

    healthy_observation = dict(transition_runner())
    healthy_receipt = run_onboarding_model_behavior_phase(
        healthy_observation,
        qualification_id=qualification_id,
        phase="postcondition",
        actor=actor,
    )
    repair_receipt = run_onboarding_model_behavior_phase(
        repair_observation,
        qualification_id=qualification_id,
        phase="postcondition",
        actor=actor,
    )
    healthy_expected = healthy_receipt["next_action"] == "continue_validation"
    repair_expected = repair_receipt["next_action"] == "repair_projection"
    passed = bool(
        healthy_expected
        and repair_expected
        and healthy_receipt["source_aligned"]
        and repair_receipt["source_aligned"]
    )
    if not healthy_expected:
        failure_code = "actual_postcondition_not_healthy"
    elif not repair_expected:
        failure_code = "projection_gap_repair_calibration_failed"
    elif not passed:
        failure_code = "model_source_alignment_failed"
    else:
        failure_code = None
    return {
        "schema_version": ONBOARDING_ACTUAL_BEHAVIOR_QUALIFICATION_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "closed_loop_complete": True,
        "qualification_passed": passed,
        "automatic_release_promotion_allowed": False,
        "entry": _receipt_summary(entry_receipt),
        "healthy_postcondition": _receipt_summary(healthy_receipt),
        "repair_calibration": _receipt_summary(repair_receipt),
        "failure_code": failure_code,
    }
