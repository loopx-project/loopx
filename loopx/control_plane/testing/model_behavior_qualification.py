from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, Protocol

from ..quota.turn_envelope import (
    PLANNING_HORIZON_DETAIL_REFS_REF,
    TURN_ENVELOPE_SCHEMA_VERSION,
    quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from ..runtime.public_safety import (
    LOCAL_PATH_SURFACE_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
)
from ..work_items.interaction_contract import INTERACTION_RESPONSE_PLAN_SCHEMA_VERSION
from .model_behavior_packet_safety import scheduler_transport_validation_view


MODEL_BEHAVIOR_QUALIFICATION_SCHEMA_VERSION = "model_behavior_qualification_v0"
MODEL_BEHAVIOR_ACTOR_REQUEST_SCHEMA_VERSION = "model_behavior_actor_request_v0"
MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION = "model_behavior_actor_result_v0"
MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION = "model_behavior_decision_v0"
MODEL_BEHAVIOR_DECISION_RECEIPT_SCHEMA_VERSION = "model_behavior_decision_receipt_v0"
MODEL_BEHAVIOR_ARM_TERMINAL_RECEIPT_SCHEMA_VERSION = (
    "model_behavior_arm_terminal_receipt_v0"
)
MODEL_BEHAVIOR_PAIR_RESULT_SCHEMA_VERSION = "model_behavior_pair_result_v0"
FULL_QUOTA_DECISION_PACKET_SCHEMA_VERSION = "loopx_quota_should_run_full_v0"

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$")
_TODO_ID_PATTERN = re.compile(r"^todo_[A-Za-z0-9_-]{3,80}$")
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)(?:^|_)(?:api_?key|access_?key|secret|password|credential|auth_?token)(?:$|_)"
)
_DECISION_FIELDS = {
    "schema_version",
    "decision",
    "selected_todo_id",
    "user_action_required",
    "must_attempt_work",
    "delivery_allowed",
    "quiet_noop_allowed",
    "external_write_requested",
    "intended_action_kinds",
    "reason_codes",
    "semantic_contract",
}
_ACTOR_RESULT_FIELDS = {
    "schema_version",
    "actor_ref",
    "decision",
    "tool_calls",
}
_DECISION_VALUES = {"execute", "wait", "ask_user", "stop"}
_ACTION_KIND_VALUES = {
    "read",
    "inspect",
    "edit",
    "test",
    "writeback",
    "spend",
    "notify",
    "wait",
    "stop",
}
_HARD_INVARIANT_FIELDS = (
    "decision",
    "selected_todo_id",
    "user_action_required",
    "must_attempt_work",
    "delivery_allowed",
    "quiet_noop_allowed",
    "external_write_requested",
)
_BEHAVIOR_SIGNAL_FIELDS = ("intended_action_kinds",)

MODEL_BEHAVIOR_SEMANTIC_CONTRACT_FIELDS = (
    "concrete_user_question",
    "required_reads",
    "gate_or_stop",
    "peer_route",
    "write_scope",
    "spend_rule",
    "scheduler_action",
    "vision_continuation",
    "planning_horizon",
    "actionable_warnings",
)

MODEL_BEHAVIOR_HARD_INVARIANT_FIELDS = _HARD_INVARIANT_FIELDS
MODEL_BEHAVIOR_SIGNAL_FIELDS = _BEHAVIOR_SIGNAL_FIELDS


def _semantic_contract_field_names(
    *,
    required: bool,
    requested: Sequence[str] | None,
) -> tuple[str, ...]:
    if not required:
        if requested:
            raise ValueError(
                "semantic_contract_fields require semantic_contract_required=true"
            )
        return ()
    if requested is None:
        return MODEL_BEHAVIOR_SEMANTIC_CONTRACT_FIELDS
    fields: list[str] = []
    for value in requested:
        field = str(value or "").strip()
        if field not in MODEL_BEHAVIOR_SEMANTIC_CONTRACT_FIELDS:
            raise ValueError(f"unknown semantic contract field: {field}")
        if field in fields:
            raise ValueError(f"duplicate semantic contract field: {field}")
        fields.append(field)
    if not fields:
        raise ValueError("required semantic_contract_fields must not be empty")
    return tuple(fields)


class ModelBehaviorActor(Protocol):
    """Provider adapter that returns parsed JSON without executing tools."""

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ModelBehaviorPairValidationError(ValueError):
    """The paired packets failed deterministic validation before actor execution."""


class ModelBehaviorArmExecutionError(RuntimeError):
    """One actor arm failed with only a compact terminal receipt retained."""

    def __init__(self, receipt: Mapping[str, Any]) -> None:
        self.receipt = dict(receipt)
        super().__init__(
            "model behavior arm "
            f"{self.receipt['arm']} failed: {self.receipt['error_code']}"
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _actor_failure_code(exc: Exception) -> str:
    explicit_code = getattr(exc, "error_code", None)
    if explicit_code is not None:
        try:
            return _token(explicit_code, field="actor_error_code")
        except ValueError:
            return "actor_execution_failed"
    if isinstance(exc, (ValueError, RuntimeError)):
        return "actor_result_invalid"
    return "actor_execution_failed"


def _arm_failure_receipt(
    *,
    qualification_id: str,
    arm: str,
    exc: Exception,
    completed_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": MODEL_BEHAVIOR_ARM_TERMINAL_RECEIPT_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "arm": arm,
        "status": "failed",
        "error_code": _actor_failure_code(exc),
        "completed_arm_receipt_digests": {
            completed_arm: _digest(dict(receipt))
            for completed_arm, receipt in completed_receipts.items()
        },
        "boundary": {
            "tools_enabled": False,
            "filesystem_writes_allowed": False,
            "external_writes_allowed": False,
            "raw_packet_persisted": False,
            "raw_model_response_persisted": False,
        },
    }


def _token(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a compact public-safe token")
    return text


def _reason_codes(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("decision.reason_codes must be a non-empty list")
    codes: list[str] = []
    for item in value:
        code = _token(item, field="decision.reason_codes[]")
        if code not in codes:
            codes.append(code)
        if len(codes) > 12:
            raise ValueError("decision.reason_codes must contain at most 12 values")
    return codes


def _intended_action_kinds(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("decision.intended_action_kinds must be a non-empty list")
    kinds: list[str] = []
    for item in value:
        kind = str(item or "").strip()
        if kind not in _ACTION_KIND_VALUES:
            raise ValueError(
                "decision.intended_action_kinds contains an unknown action kind"
            )
        kinds.append(kind)
        if len(kinds) > 12:
            raise ValueError(
                "decision.intended_action_kinds must contain at most 12 values"
            )
    return kinds


def _reject_private_or_secret_material(value: Any, *, path: str = "packet") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if _SECRET_FIELD_PATTERN.search(key):
                raise ValueError(f"{path}.{key} is a credential-shaped field")
            _reject_private_or_secret_material(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_private_or_secret_material(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    if LOCAL_PATH_SURFACE_PATTERN.search(value):
        raise ValueError(f"{path} contains a local absolute path")
    if SECRET_LIKE_SURFACE_PATTERN.search(value):
        raise ValueError(f"{path} contains a credential-like value")


def _packet_schema(packet: Mapping[str, Any], *, arm: str) -> str:
    if arm == "full_packet":
        if (
            packet.get("mode") != "should-run"
            or not isinstance(packet.get("interaction_contract"), Mapping)
            or not packet.get("goal_id")
        ):
            raise ValueError("full_packet must be a LoopX quota should-run decision")
        return FULL_QUOTA_DECISION_PACKET_SCHEMA_VERSION
    if arm == "candidate_packet":
        if packet.get("schema_version") != TURN_ENVELOPE_SCHEMA_VERSION:
            raise ValueError(
                "candidate_packet must use the current LoopX TurnEnvelope schema"
            )
        return str(TURN_ENVELOPE_SCHEMA_VERSION)
    raise ValueError("arm must be full_packet or candidate_packet")


def _packet_action_signature(
    packet: Mapping[str, Any],
    *,
    arm: str,
) -> dict[str, Any]:
    _packet_schema(packet, arm=arm)
    return (
        quota_action_signature_document(packet)
        if arm == "full_packet"
        else turn_envelope_action_signature_document(packet)
    )


def _expected_blocking_user_gate_response_plan(
    signature: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Independently derive the blocking-gate plan from channel invariants."""

    user = dict(signature.get("user") or {})
    action = dict(signature.get("action") or {})
    if not (
        user.get("action_required") is True
        and action.get("must_attempt") is False
        and action.get("delivery_allowed") is False
        and action.get("quiet_noop_allowed") is False
    ):
        return None
    return {
        "schema_version": INTERACTION_RESPONSE_PLAN_SCHEMA_VERSION,
        "kind": "surface_user_gate",
        "decision": "ask_user",
        "action_sequence": ["notify", "wait"],
        "silent_wait_allowed": False,
    }


def _validated_packet_response_plan(
    packet: Mapping[str, Any],
    *,
    arm: str,
) -> dict[str, Any] | None:
    signature = _packet_action_signature(packet, arm=arm)
    expected = _expected_blocking_user_gate_response_plan(signature)
    actual = signature.get("response_plan")
    actual_plan = dict(actual) if isinstance(actual, Mapping) else None
    if expected is not None and actual_plan != expected:
        raise ValueError(
            "packet response_plan does not match blocking user-gate semantics"
        )
    if (
        expected is None
        and actual_plan
        and actual_plan.get("kind") == "surface_user_gate"
    ):
        raise ValueError("packet response_plan projects a non-blocking user gate")
    return actual_plan


def build_model_behavior_actor_request(
    packet: Mapping[str, Any],
    *,
    qualification_id: str,
    arm: str,
    semantic_contract_required: bool = False,
    semantic_contract_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build one provider-neutral, no-write model actor request."""

    normalized_packet = dict(packet)
    required_semantic_fields = _semantic_contract_field_names(
        required=semantic_contract_required,
        requested=semantic_contract_fields,
    )
    packet_schema_version = _packet_schema(normalized_packet, arm=arm)
    _validated_packet_response_plan(normalized_packet, arm=arm)
    _reject_private_or_secret_material(scheduler_transport_validation_view(normalized_packet, arm=arm))
    return {
        "schema_version": MODEL_BEHAVIOR_ACTOR_REQUEST_SCHEMA_VERSION,
        "qualification_id": _token(qualification_id, field="qualification_id"),
        "arm": arm,
        "packet_schema_version": packet_schema_version,
        "semantic_contract_required": semantic_contract_required,
        "packet": normalized_packet,
        "sandbox": {
            "schema_version": "model_behavior_no_write_sandbox_v0",
            "tools_enabled": False,
            "filesystem_writes_allowed": False,
            "external_writes_allowed": False,
            "provider_network_only": True,
        },
        "actor_instruction": {
            "schema_version": "model_behavior_actor_instruction_v0",
            "source_of_truth": "packet",
            "task": "decide the next LoopX agent behavior from the packet",
            "constraints": [
                "do not call tools",
                "do not execute any action",
                "return only the response-contract JSON object",
                "preserve user gates, work obligations, and write boundaries",
            ],
        },
        "response_contract": {
            "schema_version": MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
            "format": "json_object",
            "reject_unknown_fields": True,
            "decision_values": sorted(_DECISION_VALUES),
            "intended_action_kind_values": sorted(_ACTION_KIND_VALUES),
            "reason_code_limit": 12,
            "semantic_contract_required": semantic_contract_required,
            "semantic_contract_fields": list(required_semantic_fields),
        },
    }


def normalize_model_behavior_actor_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild and verify a canonical actor request before provider transport."""

    if not isinstance(raw, Mapping):
        raise ValueError("actor request must be an object")
    if raw.get("schema_version") != MODEL_BEHAVIOR_ACTOR_REQUEST_SCHEMA_VERSION:
        raise ValueError("actor request must use model_behavior_actor_request_v0")
    packet = raw.get("packet")
    if not isinstance(packet, Mapping):
        raise ValueError("actor request packet must be an object")
    response_contract = raw.get("response_contract")
    requested_fields = (
        response_contract.get("semantic_contract_fields")
        if isinstance(response_contract, Mapping)
        and isinstance(response_contract.get("semantic_contract_fields"), list)
        else None
    )
    canonical = build_model_behavior_actor_request(
        packet,
        qualification_id=str(raw.get("qualification_id") or ""),
        arm=str(raw.get("arm") or ""),
        semantic_contract_required=bool(raw.get("semantic_contract_required")),
        semantic_contract_fields=requested_fields,
    )
    if dict(raw) != canonical:
        raise ValueError("actor request does not match the canonical no-write contract")
    return canonical


def model_behavior_semantic_contract_from_packet(
    packet: Mapping[str, Any],
    *,
    arm: str,
) -> dict[str, Any]:
    """Extract the exact public-safe semantics that a model must preserve."""

    signature = _packet_action_signature(packet, arm=arm)
    user = dict(signature.get("user") or {})
    user_actions = list(user.get("actions") or [])
    boundary = dict(signature.get("boundary") or {})
    capsule = dict(signature.get("contract_capsule") or {})
    interaction = dict(capsule.get("interaction_contract") or {})
    action = dict(signature.get("action") or {})
    selected_todo = dict(action.get("selected_todo") or {})
    agent_id = str(signature.get("agent_id") or "").strip() or None
    claimed_by = str(selected_todo.get("claimed_by") or "").strip() or None
    continuation_policy = (
        str(selected_todo.get("continuation_policy") or "").strip() or None
    )
    planning_horizon = dict(action.get("planning_horizon") or {})
    horizon_work_items = list(planning_horizon.get("work_items") or [])
    horizon_relations = list(planning_horizon.get("relations") or [])
    normalized_relations = [
        {
            "from_todo_id": relation.get("from_todo_id"),
            "relation": relation.get("relation"),
            "to_ref": relation.get("to_ref"),
            "enforcement": relation.get("enforcement"),
        }
        for relation in horizon_relations
        if isinstance(relation, Mapping)
    ]
    relation_kinds: list[Any] = []
    for relation in normalized_relations:
        kind = relation.get("relation")
        if kind not in relation_kinds:
            relation_kinds.append(kind)
    horizon_completeness = dict(planning_horizon.get("completeness") or {})
    complete_value = horizon_completeness.get("complete")
    horizon_complete = complete_value if isinstance(complete_value, bool) else None
    horizon_truncated = bool(
        horizon_complete is False
        or any(
            type(horizon_completeness.get(field)) is int
            and horizon_completeness[field] > 0
            for field in (
                "source_unrepresented_todo_count",
                "omitted_candidate_todo_count",
                "omitted_relation_count",
                "omitted_acceptance_gap_count",
                "compact_field_truncation_count",
            )
        )
    )
    if arm == "full_packet":
        source_horizon = packet.get("planning_horizon")
        source_detail_refs = (
            source_horizon.get("detail_refs")
            if isinstance(source_horizon, Mapping)
            else None
        )
        horizon_cold_path_available = bool(
            isinstance(source_detail_refs, Mapping) and source_detail_refs
        )
    else:
        source_action = packet.get("action")
        source_horizon = (
            source_action.get("planning_horizon")
            if isinstance(source_action, Mapping)
            else None
        )
        source_detail_refs = (
            source_horizon.get("detail_refs")
            if isinstance(source_horizon, Mapping)
            else None
        )
        detail_ref = packet.get("detail_ref")
        horizon_cold_path_available = bool(
            (isinstance(source_detail_refs, Mapping) and source_detail_refs)
            or (
                isinstance(source_horizon, Mapping)
                and source_horizon.get("detail_refs_ref")
                == PLANNING_HORIZON_DETAIL_REFS_REF
                and isinstance(detail_ref, Mapping)
                and detail_ref
            )
        )
    return {
        "concrete_user_question": user_actions[0] if user_actions else None,
        "required_reads": list(signature.get("required_reads") or []),
        "gate_or_stop": {
            "decision": signature.get("decision"),
            "should_run": bool(signature.get("should_run")),
            "effective_action": signature.get("effective_action"),
            "state": signature.get("state"),
            "interaction_mode": interaction.get("mode"),
            "user_action_required": bool(user.get("action_required")),
            "response_plan": signature.get("response_plan"),
            "guards": list(boundary.get("guards") or []),
            "stop_condition": boundary.get("stop_condition"),
        },
        "peer_route": {
            "agent_id": agent_id,
            "selected_todo_claimed_by": claimed_by,
            "continuation_policy": continuation_policy,
            "same_agent_continuation": bool(
                agent_id
                and claimed_by == agent_id
                and continuation_policy == "same_agent_non_delivery"
            ),
        },
        "write_scope": list(boundary.get("write_scope") or []),
        "spend_rule": dict(signature.get("writeback") or {}),
        "scheduler_action": dict(signature.get("scheduler") or {}),
        "vision_continuation": dict(capsule.get("vision_continuation_audit") or {}),
        "planning_horizon": {
            "present": bool(planning_horizon),
            "selected_todo_id": planning_horizon.get("selected_todo_id"),
            "visible_todo_ids": [
                item.get("todo_id")
                for item in horizon_work_items
                if isinstance(item, Mapping)
            ],
            "attention_todo_ids": list(
                planning_horizon.get("attention_todo_ids") or []
            ),
            "relation_kinds": relation_kinds,
            "relation_count": len(normalized_relations),
            "relations": normalized_relations,
            "complete": horizon_complete,
            "truncated": horizon_truncated,
            "cold_path_available": horizon_cold_path_available,
        },
        "actionable_warnings": list(capsule.get("actionable_warning_refs") or []),
    }


def _normalize_semantic_contract(
    value: Any,
    *,
    required_fields: Sequence[str],
) -> dict[str, Any] | None:
    if value is None:
        if required_fields:
            raise ValueError("decision.semantic_contract is required")
        return None
    if not isinstance(value, Mapping):
        raise ValueError("decision.semantic_contract must be an object")
    expected_fields = tuple(required_fields)
    unknown = sorted(set(value) - set(expected_fields))
    if unknown:
        raise ValueError(f"unknown semantic contract field(s): {', '.join(unknown)}")
    missing = [
        field for field in expected_fields if field not in value
    ]
    if missing:
        raise ValueError(f"missing semantic contract field(s): {', '.join(missing)}")
    normalized = {
        field: json.loads(_canonical_json(value[field]))
        for field in expected_fields
    }
    _reject_private_or_secret_material(normalized, path="decision.semantic_contract")
    if len(_canonical_json(normalized).encode("utf-8")) > 16_384:
        raise ValueError("decision.semantic_contract exceeds the size limit")
    return normalized


def normalize_model_behavior_actor_result(
    raw: Mapping[str, Any],
    *,
    semantic_contract_required: bool = False,
    semantic_contract_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    required_semantic_fields = _semantic_contract_field_names(
        required=semantic_contract_required,
        requested=semantic_contract_fields,
    )
    if not isinstance(raw, Mapping):
        raise ValueError("actor result must be an object")
    unknown = sorted(set(raw) - _ACTOR_RESULT_FIELDS)
    if unknown:
        raise ValueError(f"unknown actor result field(s): {', '.join(unknown)}")
    if raw.get("schema_version") != MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION:
        raise ValueError("actor result must use model_behavior_actor_result_v0")
    if raw.get("tool_calls") != []:
        raise ValueError("model behavior qualification forbids all tool calls")
    actor_ref = _token(raw.get("actor_ref"), field="actor_ref")
    decision = raw.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("actor result decision must be an object")
    unknown_decision = sorted(set(decision) - _DECISION_FIELDS)
    if unknown_decision:
        raise ValueError(
            f"unknown model decision field(s): {', '.join(unknown_decision)}"
        )
    if decision.get("schema_version") != MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION:
        raise ValueError("decision must use model_behavior_decision_v0")
    decision_value = str(decision.get("decision") or "")
    if decision_value not in _DECISION_VALUES:
        raise ValueError("decision must be execute, wait, ask_user, or stop")
    selected_todo_id = decision.get("selected_todo_id")
    if selected_todo_id is not None and not _TODO_ID_PATTERN.fullmatch(
        str(selected_todo_id)
    ):
        raise ValueError("decision.selected_todo_id must be null or a todo id")
    boolean_fields = (
        "user_action_required",
        "must_attempt_work",
        "delivery_allowed",
        "quiet_noop_allowed",
        "external_write_requested",
    )
    for field in boolean_fields:
        if not isinstance(decision.get(field), bool):
            raise ValueError(f"decision.{field} must be boolean")
    normalized_decision = {
        "schema_version": MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        "decision": decision_value,
        "selected_todo_id": selected_todo_id,
        **{field: decision[field] for field in boolean_fields},
        "intended_action_kinds": _intended_action_kinds(
            decision.get("intended_action_kinds")
        ),
        "reason_codes": _reason_codes(decision.get("reason_codes")),
    }
    semantic_contract = (
        _normalize_semantic_contract(
            decision.get("semantic_contract"),
            required_fields=required_semantic_fields,
        )
        if semantic_contract_required
        else None
    )
    if semantic_contract is not None:
        normalized_decision["semantic_contract"] = semantic_contract
    return {
        "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
        "actor_ref": actor_ref,
        "decision": normalized_decision,
        "tool_calls": [],
    }


def run_model_behavior_qualification_arm(
    packet: Mapping[str, Any],
    *,
    qualification_id: str,
    arm: str,
    actor: ModelBehaviorActor,
    semantic_contract_required: bool = False,
    semantic_contract_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run one in-memory actor arm and return only a compact decision receipt."""

    required_semantic_fields = _semantic_contract_field_names(
        required=semantic_contract_required,
        requested=semantic_contract_fields,
    )
    request = build_model_behavior_actor_request(
        packet,
        qualification_id=qualification_id,
        arm=arm,
        semantic_contract_required=semantic_contract_required,
        semantic_contract_fields=required_semantic_fields,
    )
    result = normalize_model_behavior_actor_result(
        actor(request),
        semantic_contract_required=semantic_contract_required,
        semantic_contract_fields=required_semantic_fields,
    )
    decision = dict(result["decision"])
    violations = []
    if decision["external_write_requested"]:
        violations.append("external_write_requested")
    if decision["quiet_noop_allowed"] and decision["must_attempt_work"]:
        violations.append("quiet_noop_conflicts_with_must_attempt")
    response_plan = _validated_packet_response_plan(packet, arm=arm)
    if response_plan is not None:
        expected_actions = list(response_plan.get("action_sequence") or [])
        actual_actions = list(decision.get("intended_action_kinds") or [])
        if decision["decision"] != response_plan.get("decision"):
            violations.append("response_plan_decision_mismatch")
        if actual_actions != expected_actions:
            violations.append("response_plan_action_sequence_mismatch")
        if response_plan.get("silent_wait_allowed") is False and actual_actions == [
            "wait"
        ]:
            violations.append("response_plan_silent_wait_forbidden")
    semantic_contract = decision.get("semantic_contract")
    semantic_alignment: dict[str, bool] = {}
    semantic_digests: dict[str, str] = {}
    if semantic_contract_required:
        if not isinstance(semantic_contract, Mapping):
            raise ValueError("required semantic contract was not normalized")
        expected_semantics = model_behavior_semantic_contract_from_packet(
            packet,
            arm=arm,
        )
        semantic_alignment = {
            field: semantic_contract.get(field) == expected_semantics[field]
            for field in required_semantic_fields
        }
        semantic_digests = {
            field: _digest(semantic_contract.get(field))
            for field in required_semantic_fields
        }
        violations.extend(
            f"semantic_contract_mismatch:{field}"
            for field, matches in semantic_alignment.items()
            if not matches
        )
    return {
        "schema_version": MODEL_BEHAVIOR_DECISION_RECEIPT_SCHEMA_VERSION,
        "qualification_id": request["qualification_id"],
        "arm": request["arm"],
        "actor_ref": result["actor_ref"],
        "packet_schema_version": request["packet_schema_version"],
        "packet_digest": _digest(request["packet"]),
        "decision_digest": _digest(decision),
        **{field: decision[field] for field in _HARD_INVARIANT_FIELDS},
        **{field: decision[field] for field in _BEHAVIOR_SIGNAL_FIELDS},
        "reason_codes": decision["reason_codes"],
        "semantic_contract_required": semantic_contract_required,
        "semantic_contract_fields": list(required_semantic_fields),
        "semantic_contract_complete": bool(
            semantic_contract_required
            and semantic_contract
            and bool(required_semantic_fields)
            and all(semantic_alignment.values())
        ),
        "semantic_contract_alignment": semantic_alignment,
        "semantic_contract_digests": semantic_digests,
        "boundary": {
            "tools_enabled": False,
            "tool_call_count": 0,
            "filesystem_writes_allowed": False,
            "external_writes_allowed": False,
            "raw_packet_persisted": False,
            "raw_model_response_persisted": False,
        },
        "safety_violations": violations,
    }


def compare_model_behavior_receipts(
    full_receipt: Mapping[str, Any],
    candidate_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare paired receipts without retaining either model conversation."""

    for label, receipt in (
        ("full_receipt", full_receipt),
        ("candidate_receipt", candidate_receipt),
    ):
        if (
            receipt.get("schema_version")
            != MODEL_BEHAVIOR_DECISION_RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError(f"{label} must use model_behavior_decision_receipt_v0")
    if full_receipt.get("qualification_id") != candidate_receipt.get(
        "qualification_id"
    ):
        raise ValueError("paired receipts must share qualification_id")
    if full_receipt.get("actor_ref") != candidate_receipt.get("actor_ref"):
        raise ValueError("paired receipts must share actor_ref")
    drift = {
        field: {
            "full_packet": full_receipt.get(field),
            "candidate_packet": candidate_receipt.get(field),
        }
        for field in _HARD_INVARIANT_FIELDS
        if full_receipt.get(field) != candidate_receipt.get(field)
    }
    behavior_drift = {
        field: {
            "full_packet": full_receipt.get(field),
            "candidate_packet": candidate_receipt.get(field),
        }
        for field in _BEHAVIOR_SIGNAL_FIELDS
        if full_receipt.get(field) != candidate_receipt.get(field)
    }
    full_semantic_fields = _semantic_contract_field_names(
        required=bool(full_receipt.get("semantic_contract_required")),
        requested=(
            full_receipt.get("semantic_contract_fields")
            if isinstance(full_receipt.get("semantic_contract_fields"), list)
            else None
        ),
    )
    candidate_semantic_fields = _semantic_contract_field_names(
        required=bool(candidate_receipt.get("semantic_contract_required")),
        requested=(
            candidate_receipt.get("semantic_contract_fields")
            if isinstance(candidate_receipt.get("semantic_contract_fields"), list)
            else None
        ),
    )
    semantic_coverage_matches = full_semantic_fields == candidate_semantic_fields
    compared_semantic_fields = (
        full_semantic_fields if semantic_coverage_matches else ()
    )
    semantic_drift = {
        field: {
            "full_packet": full_receipt.get("semantic_contract_digests", {}).get(field),
            "candidate_packet": candidate_receipt.get(
                "semantic_contract_digests", {}
            ).get(field),
        }
        for field in compared_semantic_fields
        if full_receipt.get("semantic_contract_digests", {}).get(field)
        != candidate_receipt.get("semantic_contract_digests", {}).get(field)
    }
    full_actions = list(full_receipt.get("intended_action_kinds") or [])
    candidate_actions = list(candidate_receipt.get("intended_action_kinds") or [])
    stochastic_drift: dict[str, Any] = {}
    full_first_action = full_actions[0] if full_actions else None
    candidate_first_action = candidate_actions[0] if candidate_actions else None
    if full_first_action != candidate_first_action:
        stochastic_drift["first_action_kind"] = {
            "full_packet": full_first_action,
            "candidate_packet": candidate_first_action,
        }
    if full_actions != candidate_actions:
        stochastic_drift["trajectory_action_kinds"] = {
            "full_packet": full_actions,
            "candidate_packet": candidate_actions,
        }
    violations = sorted(
        {
            str(item)
            for receipt in (full_receipt, candidate_receipt)
            for item in receipt.get("safety_violations", [])
        }
        | ({"semantic_contract_coverage_mismatch"} if not semantic_coverage_matches else set())
    )
    semantic_contract_required = bool(
        full_receipt.get("semantic_contract_required")
        or candidate_receipt.get("semantic_contract_required")
    )
    semantic_contract_complete = bool(
        semantic_contract_required
        and full_receipt.get("semantic_contract_complete") is True
        and candidate_receipt.get("semantic_contract_complete") is True
    )
    return {
        "schema_version": MODEL_BEHAVIOR_PAIR_RESULT_SCHEMA_VERSION,
        "qualification_id": full_receipt.get("qualification_id"),
        "actor_ref": full_receipt.get("actor_ref"),
        "equivalent": not drift
        and not semantic_drift
        and not behavior_drift
        and not violations,
        "hard_invariant_drift": drift,
        "semantic_contract_drift": semantic_drift,
        "semantic_contract_required": semantic_contract_required,
        "semantic_contract_fields": list(compared_semantic_fields),
        "semantic_contract_complete": semantic_contract_complete,
        "behavior_signal_drift": behavior_drift,
        "stochastic_drift": stochastic_drift,
        "safety_violations": violations,
        "receipt_digests": {
            "full_packet": _digest(dict(full_receipt)),
            "candidate_packet": _digest(dict(candidate_receipt)),
        },
    }


def run_model_behavior_qualification_pair(
    full_packet: Mapping[str, Any],
    candidate_packet: Mapping[str, Any],
    *,
    qualification_id: str,
    actor: ModelBehaviorActor,
    arm_order: tuple[str, str] = ("full_packet", "candidate_packet"),
    semantic_contract_required: bool = False,
    semantic_contract_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    if len(arm_order) != 2 or set(arm_order) != {"full_packet", "candidate_packet"}:
        raise ModelBehaviorPairValidationError(
            "arm_order must contain full_packet and candidate_packet once"
        )
    action_signature = candidate_packet.get("action_signature")
    if not isinstance(action_signature, Mapping):
        raise ModelBehaviorPairValidationError(
            "candidate_packet is missing its TurnEnvelope action signature"
        )
    if action_signature.get("matches") is not True:
        raise ModelBehaviorPairValidationError(
            "candidate_packet action signature parity must be verified"
        )
    if action_signature.get("source_decision_hash") != _digest(dict(full_packet)):
        raise ModelBehaviorPairValidationError(
            "candidate_packet does not derive from the paired full packet"
        )
    if quota_action_signature_document(
        full_packet
    ) != turn_envelope_action_signature_document(candidate_packet):
        raise ModelBehaviorPairValidationError(
            "candidate_packet action semantics drift from the full packet"
        )
    packets = {
        "full_packet": full_packet,
        "candidate_packet": candidate_packet,
    }
    receipts: dict[str, dict[str, Any]] = {}
    for arm in arm_order:
        try:
            receipts[arm] = run_model_behavior_qualification_arm(
                packets[arm],
                qualification_id=qualification_id,
                arm=arm,
                actor=actor,
                semantic_contract_required=semantic_contract_required,
                semantic_contract_fields=semantic_contract_fields,
            )
        except Exception as exc:
            raise ModelBehaviorArmExecutionError(
                _arm_failure_receipt(
                    qualification_id=qualification_id,
                    arm=arm,
                    exc=exc,
                    completed_receipts=receipts,
                )
            ) from None
    full_receipt = receipts["full_packet"]
    candidate_receipt = receipts["candidate_packet"]
    return compare_model_behavior_receipts(full_receipt, candidate_receipt)
