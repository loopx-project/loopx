from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..agent_context import envelope_agent_context
from ..runtime.public_safety import validate_public_safe_value


SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION = "subagent_execution_topology_v0"
SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION = (
    "subagent_host_execution_receipt_v0"
)
SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION = (
    "subagent_control_plane_reconciliation_v0"
)
CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION = "child_execution_task_packet_v0"
CHILD_EXECUTION_GUARD_SCHEMA_VERSION = "child_execution_guard_v0"
CHILD_EXECUTION_REJECTION_SCHEMA_VERSION = "child_execution_rejection_v0"
MAX_CHILD_EXECUTION_RECEIPTS = 32
MAX_EFFECT_CLASSES = 8
MAX_EVIDENCE_REFS = 12
EXECUTION_KINDS = frozenset({"ephemeral_child"})
RECEIPT_STATUSES = frozenset(
    {"running", "completed", "failed", "cancelled", "rejected"}
)
KNOWN_EFFECT_CLASSES = frozenset(
    {
        "local_read",
        "network_read",
        "held_workspace_write",
        "external_write",
        "production_action",
        "credential_use",
        "monitor",
    }
)
CHILD_CONTEXT_MODES = frozenset({"fresh", "forked_snapshot", "resume"})
CHILD_CONTEXT_INHERITANCE = {
    "fresh": "none",
    "forked_snapshot": "parent_conversation_snapshot",
    "resume": "existing_child_session",
}
CHILD_FALLBACK_ACTIONS = (
    "retry_fresh",
    "replace_child",
    "serial_takeover",
    "ignore_optional_result",
)
# Keep this grammar shared by runtime validation and the host-result JSON schema.
# Slash-delimited opaque ids remain valid, but filesystem-shaped values do not.
OPAQUE_REF_PATTERN = (
    r"^(?![A-Za-z][A-Za-z0-9+.-]*:/)"
    r"(?!\.{1,2}(?:/|$))"
    r"(?!.*?/\.{1,2}(?:/|$))"
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$"
)
_OPAQUE_REF = re.compile(OPAQUE_REF_PATTERN)
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "bundle_id",
        "lane_id",
        "goal_id",
        "todo_id",
        "execution_kind",
        "runtime_id",
        "worker_ref",
        "source_state_ref",
        "task_packet_digest",
        "context_mode",
        "workspace_ref",
        "status",
        "effect_classes",
        "evidence_refs",
        "raw_transcript_copied",
    }
)
HOST_RESULT_FIELDS = frozenset({"child_execution_receipts"})


class ChildExecutionTopologyError(ValueError):
    def __init__(self, *, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def subagent_execution_topology(
    plan: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    topology = plan.get("subagent_execution_topology")
    return topology if isinstance(topology, Mapping) else None


def subagent_host_result_fields(plan: Mapping[str, Any]) -> frozenset[str]:
    return (
        HOST_RESULT_FIELDS
        if subagent_execution_topology(plan) is not None
        else frozenset()
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _opaque_ref(
    value: Any,
    *,
    field: str,
    required: bool = True,
) -> str | None:
    if value is None or value == "":
        text = ""
    elif not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    else:
        text = value.strip()
    if not text:
        if required:
            raise ValueError(f"{field} is required")
        return None
    validate_public_safe_value(text, path=field)
    if not _OPAQUE_REF.fullmatch(text):
        raise ValueError(
            f"{field} must be an opaque 1-192 character public-safe reference"
        )
    return text


def _opaque_refs(
    value: Any,
    *,
    field: str,
    limit: int,
    allowed: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if len(value) > limit:
        raise ValueError(f"{field} accepts at most {limit} items")
    result: list[str] = []
    for item in value:
        ref = _opaque_ref(item, field=f"{field}[]")
        assert ref is not None
        if allowed is not None and ref not in allowed:
            raise ValueError(f"{field} contains unsupported value: {ref}")
        if ref not in result:
            result.append(ref)
    return sorted(result)


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]


def _sha256_ref(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def subagent_bundle_id(*, turn_key: str) -> str:
    return "bundle_" + _digest({"turn_key": turn_key})


def subagent_lane_id(*, bundle_id: str, todo_id: str) -> str:
    return "lane_" + _digest({"bundle_id": bundle_id, "todo_id": todo_id})


def _required_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail=f"{field} is required before child launch",
        )
    return text


def _child_guard_policy(brief: Mapping[str, Any]) -> dict[str, Any]:
    if brief.get("child_guard_policy") != "prevention_first_v0":
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="child_guard_policy is missing or not fail-local",
        )
    return {
        "schema_version": CHILD_EXECUTION_GUARD_SCHEMA_VERSION,
        "pre_spawn": "require_complete_task_packet",
        "on_deviation": "project_stop_and_quarantine_child",
        "evidence_disposition": "candidate_until_parent_accepts",
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": list(CHILD_FALLBACK_ACTIONS),
    }


def _child_context_contract(operation: Mapping[str, Any]) -> dict[str, Any]:
    mode = _required_text(
        operation.get("recommended_context"),
        field="recommended_context",
    )
    available_contexts = operation.get("available_contexts")
    if not isinstance(available_contexts, list):
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="available_contexts must be explicit",
        )
    if mode not in CHILD_CONTEXT_MODES:
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="recommended_context is unsupported",
        )
    if mode not in available_contexts:
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="recommended_context is not available on the selected host",
        )
    return {
        "mode": mode,
        "inheritance": CHILD_CONTEXT_INHERITANCE[mode],
    }


def _child_task_packet(
    *,
    operation: Mapping[str, Any],
    brief: Mapping[str, Any],
    todo_id: str,
    source_state_ref: str,
    allowed_effect_classes: Sequence[str],
    workspace_requirement: str,
    workspace_ref: str | None,
) -> dict[str, Any]:
    required_capabilities = brief.get("required_capabilities")
    required_write_scopes = brief.get("required_write_scopes")
    acceptance = brief.get("acceptance")
    if not isinstance(required_capabilities, list):
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="required_capabilities must be explicit",
        )
    if not isinstance(required_write_scopes, list):
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="required_write_scopes must be explicit",
        )
    if (
        not isinstance(acceptance, list)
        or not acceptance
        or not all(isinstance(item, str) and item.strip() for item in acceptance)
    ):
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="acceptance must be a non-empty string list",
        )
    validation_declared = brief.get("validation_declared") is True
    validation = {
        "declared": validation_declared,
        "authority_ref": (
            f"todo:{todo_id}:completion_validation"
            if validation_declared
            else None
        ),
        "execution_owner": "registered_parent",
        "command_disclosed": False,
        "label": str(brief.get("validation_label") or "").strip() or None,
        "policy": (
            "registered parent runs declared todo completion validation; "
            "child reports relevant validation evidence"
            if validation_declared
            else _required_text(
                brief.get("validation_policy"),
                field="validation_policy",
            )
        ),
    }
    execution_policy = brief.get("execution_policy")
    task_packet = {
        "schema_version": CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION,
        "todo_id": todo_id,
        "objective": _required_text(brief.get("objective"), field="objective"),
        "action_kind": _required_text(
            brief.get("action_kind"),
            field="action_kind",
        ),
        "target_key": str(brief.get("target_key") or "").strip() or None,
        "deliverable": (
            "held_workspace_change_with_evidence"
            if required_write_scopes
            else "public_safe_evidence"
        ),
        "acceptance": [item.strip() for item in acceptance],
        "context_refs": [
            _required_text(brief.get("authority_artifact"), field="authority_artifact"),
            _required_text(brief.get("latest_state_ref"), field="latest_state_ref"),
            source_state_ref,
        ],
        "task_domain": _required_text(
            brief.get("task_domain"),
            field="task_domain",
        ),
        "task_repository": str(brief.get("task_repository") or "").strip() or None,
        "allowed_capabilities": list(required_capabilities),
        "allowed_write_scopes": list(required_write_scopes),
        "allowed_effect_classes": list(allowed_effect_classes),
        "forbidden_effect_classes": sorted(
            KNOWN_EFFECT_CLASSES - set(allowed_effect_classes)
        ),
        "workspace_requirement": workspace_requirement,
        "workspace_ref": workspace_ref,
        "context": _child_context_contract(operation),
        "execution_budget": (
            dict(execution_policy) if isinstance(execution_policy, Mapping) else {}
        ),
        "output_contract": _required_text(
            brief.get("expected_output"),
            field="expected_output",
        ),
        "acceptance_mode": (
            "parent_validation_then_review"
            if validation["declared"]
            else "parent_review_only"
        ),
        "validation": validation,
        "guard": _child_guard_policy(brief),
    }
    if not task_packet["execution_budget"]:
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail="execution_policy must define the child budget",
        )
    validate_public_safe_value(task_packet, path="child_task_packet")
    return task_packet


def _child_pre_spawn_rejection(
    operation: Mapping[str, Any],
    *,
    reason_code: str,
) -> dict[str, Any]:
    try:
        todo_id = _opaque_ref(
            operation.get("todo_id"),
            field="pre_spawn_rejection.todo_id",
            required=False,
        )
    except ValueError:
        todo_id = None
    return {
        "schema_version": CHILD_EXECUTION_REJECTION_SCHEMA_VERSION,
        "todo_id": todo_id,
        "stage": "pre_spawn",
        "reason_codes": [reason_code],
        "launch_allowed": False,
        "recommended_child_action": "do_not_launch",
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": list(CHILD_FALLBACK_ACTIONS),
    }


def _child_execution_lane(
    operation: Mapping[str, Any],
    *,
    bundle_id: str,
    source_state_ref: str,
) -> dict[str, Any]:
    todo_id = _opaque_ref(
        operation.get("todo_id"),
        field="topology.lanes[].todo_id",
    )
    assert todo_id is not None
    brief = _mapping(operation.get("brief"))
    write_scopes = brief.get("required_write_scopes")
    has_workspace_write = isinstance(write_scopes, list) and bool(write_scopes)
    required_capabilities = brief.get("required_capabilities")
    uses_network = (
        isinstance(required_capabilities, list)
        and "network" in required_capabilities
    )
    effect_boundary = (
        "held_workspace_write" if has_workspace_write else "held_evidence_only"
    )
    allowed_effect_classes = ["local_read"]
    if uses_network:
        allowed_effect_classes.append("network_read")
    if has_workspace_write:
        allowed_effect_classes.append("held_workspace_write")
    lane_id = subagent_lane_id(
        bundle_id=bundle_id,
        todo_id=todo_id,
    )
    workspace_ref = f"workspace:{lane_id}" if has_workspace_write else None
    workspace_requirement = (
        "independent_git_worktree" if has_workspace_write else "not_required"
    )
    task_packet = _child_task_packet(
        operation=operation,
        brief=brief,
        todo_id=todo_id,
        source_state_ref=source_state_ref,
        allowed_effect_classes=allowed_effect_classes,
        workspace_requirement=workspace_requirement,
        workspace_ref=workspace_ref,
    )
    return {
        "lane_id": lane_id,
        "todo_id": todo_id,
        "execution_kind": "ephemeral_child",
        "admission_ref": f"task_orchestration_contract_v2:{todo_id}",
        "effect_boundary": effect_boundary,
        "allowed_effect_classes": allowed_effect_classes,
        "workspace_requirement": workspace_requirement,
        "workspace_ref": workspace_ref,
        "task_packet": task_packet,
        "task_packet_digest": _sha256_ref(task_packet),
    }


def build_subagent_execution_topology(
    *,
    turn_envelope: Mapping[str, Any],
    child_operations: Sequence[Mapping[str, Any]],
    turn_key: str,
    source_state_ref: str,
) -> dict[str, Any] | None:
    if not child_operations:
        return None
    orchestration = _mapping(turn_envelope.get("task_orchestration_contract"))
    if (
        orchestration.get("schema_version") != "task_orchestration_contract_v2"
        or orchestration.get("mode") != "adaptive"
    ):
        return None
    normalized_source_state_ref = _opaque_ref(
        source_state_ref,
        field="topology.source_state_ref",
    )
    goal_id = _opaque_ref(
        turn_envelope.get("goal_id"),
        field="topology.goal_id",
    )
    coordinator_agent_id = _opaque_ref(
        orchestration.get("coordinator_agent_id") or turn_envelope.get("agent_id"),
        field="topology.coordinator_agent_id",
    )
    assert normalized_source_state_ref is not None
    assert goal_id is not None
    assert coordinator_agent_id is not None
    bundle_id = subagent_bundle_id(turn_key=turn_key)
    configured_max_children = orchestration.get("max_children")
    max_children = (
        configured_max_children
        if isinstance(configured_max_children, int)
        and not isinstance(configured_max_children, bool)
        and configured_max_children > 0
        else len(child_operations)
    )
    lanes: list[dict[str, Any]] = []
    pre_spawn_rejections: list[dict[str, Any]] = []
    allowed_effect_classes: set[str] = set()
    for operation in child_operations:
        if len(lanes) >= max_children:
            pre_spawn_rejections.append(
                _child_pre_spawn_rejection(
                    operation,
                    reason_code="child_capacity_exceeded",
                )
            )
            continue
        try:
            lane = _child_execution_lane(
                operation,
                bundle_id=bundle_id,
                source_state_ref=normalized_source_state_ref,
            )
        except (ChildExecutionTopologyError, ValueError):
            pre_spawn_rejections.append(
                _child_pre_spawn_rejection(
                    operation,
                    reason_code="child_task_packet_incomplete",
                )
            )
            continue
        allowed_effect_classes.update(lane["allowed_effect_classes"])
        lanes.append(lane)
    rationale_codes = (
        ["admitted_ephemeral_child_lanes"] if lanes else ["parent_serial_fallback"]
    )
    if pre_spawn_rejections:
        rationale_codes.append("child_launch_rejected")
    return {
        "schema_version": SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION,
        "goal_id": goal_id,
        "bundle_id": bundle_id,
        "coordinator_agent_id": coordinator_agent_id,
        "source_state_ref": normalized_source_state_ref,
        "topology": "ephemeral_children" if lanes else "serial",
        "execution_envelope": {
            "source": "task_orchestration_contract_v2",
            "expires_with_turn": True,
            "max_children": max_children,
            "allowed_effect_classes": sorted(allowed_effect_classes),
        },
        "rationale_codes": rationale_codes,
        "lanes": lanes,
        **(
            {"pre_spawn_rejections": pre_spawn_rejections}
            if pre_spawn_rejections
            else {}
        ),
    }


def bind_child_operations_to_topology(
    child_operations: Sequence[Mapping[str, Any]],
    topology: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not topology:
        return [dict(operation) for operation in child_operations]
    lanes_by_todo = {
        str(lane.get("todo_id") or ""): lane
        for lane in topology.get("lanes") or []
        if isinstance(lane, Mapping)
    }
    bound_operations: list[dict[str, Any]] = []
    for operation in child_operations:
        lane = _mapping(lanes_by_todo.get(str(operation.get("todo_id") or "")))
        if not lane:
            continue
        bound_operations.append(
            {
                **dict(operation),
                "bundle_id": topology.get("bundle_id"),
                "lane_id": lane.get("lane_id"),
                "execution_kind": lane.get("execution_kind"),
                "source_state_ref": topology.get("source_state_ref"),
                "task_packet_digest": lane.get("task_packet_digest"),
                "task_packet": _mapping(lane.get("task_packet")),
                "effect_boundary": lane.get("effect_boundary"),
                "workspace_ref": lane.get("workspace_ref"),
            }
        )
    return bound_operations


def subagent_host_request_projection(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    context = plan.get("delegation_context")
    if isinstance(context, Mapping):
        projection["delegation_context"] = dict(context)
    child_operations = plan.get("child_operations")
    if isinstance(child_operations, list) and child_operations:
        projection["child_operations"] = child_operations
    topology = plan.get("subagent_execution_topology")
    if isinstance(topology, Mapping):
        projection["subagent_execution_topology"] = dict(topology)
    return projection


def child_execution_receipts_json_schema() -> dict[str, Any]:
    opaque_ref = {
        "type": "string",
        "pattern": OPAQUE_REF_PATTERN,
        "maxLength": 192,
    }
    nullable_ref = {
        "anyOf": [
            dict(opaque_ref),
            {"type": "null"},
        ]
    }
    properties = {
        "schema_version": {
            "type": "string",
            "enum": [SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION],
        },
        "bundle_id": dict(opaque_ref),
        "lane_id": dict(opaque_ref),
        "goal_id": dict(opaque_ref),
        "todo_id": dict(opaque_ref),
        "execution_kind": {
            "type": "string",
            "enum": sorted(EXECUTION_KINDS),
        },
        "runtime_id": dict(opaque_ref),
        "worker_ref": dict(opaque_ref),
        "source_state_ref": dict(opaque_ref),
        "task_packet_digest": dict(opaque_ref),
        "context_mode": {
            "type": "string",
            "enum": sorted(CHILD_CONTEXT_MODES),
        },
        "workspace_ref": nullable_ref,
        "status": {
            "type": "string",
            "enum": sorted(RECEIPT_STATUSES),
        },
        "effect_classes": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(KNOWN_EFFECT_CLASSES)},
            "maxItems": MAX_EFFECT_CLASSES,
        },
        "evidence_refs": {
            "type": "array",
            "items": dict(opaque_ref),
            "minItems": 1,
            "maxItems": MAX_EVIDENCE_REFS,
        },
        "raw_transcript_copied": {"type": "boolean", "enum": [False]},
    }
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "maxItems": MAX_CHILD_EXECUTION_RECEIPTS,
    }


def normalize_subagent_host_execution_receipts(
    value: Any,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("child_execution_receipts must be a list")
    if len(value) > MAX_CHILD_EXECUTION_RECEIPTS:
        raise ValueError(
            "child_execution_receipts accepts at most "
            f"{MAX_CHILD_EXECUTION_RECEIPTS} items"
        )
    receipts: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        field = f"child_execution_receipts[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{field} must be an object")
        unknown = sorted(set(raw) - _RECEIPT_FIELDS)
        if unknown:
            raise ValueError(
                f"{field} contains unsupported fields: {', '.join(unknown)}"
            )
        missing = sorted(_RECEIPT_FIELDS - set(raw))
        if missing:
            raise ValueError(f"{field} is missing fields: {', '.join(missing)}")
        if (
            raw.get("schema_version")
            != SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError(f"{field} has an unsupported schema_version")
        execution_kind = str(raw.get("execution_kind") or "").strip()
        if execution_kind not in EXECUTION_KINDS:
            raise ValueError(f"{field}.execution_kind is unsupported")
        context_mode = str(raw.get("context_mode") or "").strip()
        if context_mode not in CHILD_CONTEXT_MODES:
            raise ValueError(f"{field}.context_mode is unsupported")
        status = str(raw.get("status") or "").strip()
        if status not in RECEIPT_STATUSES:
            raise ValueError(f"{field}.status is unsupported")
        raw_transcript_copied = raw.get("raw_transcript_copied")
        if raw_transcript_copied is not False:
            raise ValueError(f"{field}.raw_transcript_copied must be false")
        receipt = {
            "schema_version": SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
            "bundle_id": _opaque_ref(
                raw.get("bundle_id"),
                field=f"{field}.bundle_id",
            ),
            "lane_id": _opaque_ref(raw.get("lane_id"), field=f"{field}.lane_id"),
            "goal_id": _opaque_ref(raw.get("goal_id"), field=f"{field}.goal_id"),
            "todo_id": _opaque_ref(raw.get("todo_id"), field=f"{field}.todo_id"),
            "execution_kind": execution_kind,
            "runtime_id": _opaque_ref(
                raw.get("runtime_id"),
                field=f"{field}.runtime_id",
            ),
            "worker_ref": _opaque_ref(
                raw.get("worker_ref"),
                field=f"{field}.worker_ref",
            ),
            "source_state_ref": _opaque_ref(
                raw.get("source_state_ref"),
                field=f"{field}.source_state_ref",
            ),
            "task_packet_digest": _opaque_ref(
                raw.get("task_packet_digest"),
                field=f"{field}.task_packet_digest",
            ),
            "context_mode": context_mode,
            "workspace_ref": _opaque_ref(
                raw.get("workspace_ref"),
                field=f"{field}.workspace_ref",
                required=False,
            ),
            "status": status,
            "effect_classes": _opaque_refs(
                raw.get("effect_classes"),
                field=f"{field}.effect_classes",
                limit=MAX_EFFECT_CLASSES,
                allowed=KNOWN_EFFECT_CLASSES,
            ),
            "evidence_refs": _opaque_refs(
                raw.get("evidence_refs"),
                field=f"{field}.evidence_refs",
                limit=MAX_EVIDENCE_REFS,
            ),
            "raw_transcript_copied": False,
        }
        validate_public_safe_value(
            {
                key: item
                for key, item in receipt.items()
                if key != "raw_transcript_copied"
            },
            path=field,
        )
        receipts.append(receipt)
    return receipts


def _lane_reason_codes(
    *,
    topology: Mapping[str, Any],
    lane: Mapping[str, Any],
    receipt: Mapping[str, Any],
    duplicate_count: int,
) -> list[str]:
    reasons: list[str] = []
    if duplicate_count:
        reasons.append("orphaned_worker_result")
    if receipt.get("bundle_id") != topology.get("bundle_id"):
        reasons.append("orphaned_worker_result")
    if receipt.get("goal_id") != topology.get("goal_id"):
        reasons.append("missing_todo_lineage")
    if receipt.get("todo_id") != lane.get("todo_id"):
        reasons.append("missing_todo_lineage")
    if receipt.get("execution_kind") != lane.get("execution_kind"):
        reasons.append("execution_kind_mismatch")
    if receipt.get("source_state_ref") != topology.get("source_state_ref"):
        reasons.append("source_state_stale")
    if receipt.get("task_packet_digest") != lane.get("task_packet_digest"):
        reasons.append("task_packet_mismatch")
    expected_context_mode = _mapping(
        _mapping(lane.get("task_packet")).get("context")
    ).get("mode")
    if receipt.get("context_mode") != expected_context_mode:
        reasons.append("context_mode_mismatch")
    allowed_effects = set(lane.get("allowed_effect_classes") or [])
    if any(
        effect not in allowed_effects for effect in receipt.get("effect_classes") or []
    ):
        reasons.append("side_effect_boundary_exceeded")
    if receipt.get("status") == "completed" and not receipt.get("evidence_refs"):
        reasons.append("aggregate_settlement_without_lane_evidence")
    if (
        lane.get("workspace_requirement") == "independent_git_worktree"
        and receipt.get("workspace_ref") != lane.get("workspace_ref")
    ):
        reasons.append("workspace_mismatch")
    return sorted(set(reasons))


def _lane_status(
    receipt: Mapping[str, Any] | None,
    *,
    reason_codes: Sequence[str],
) -> str:
    if receipt is None or receipt.get("status") == "running":
        return "incomplete"
    if reason_codes:
        return "drifted"
    if receipt.get("status") == "cancelled":
        return "cancelled"
    if receipt.get("status") in {"failed", "rejected"}:
        return "rejected"
    return "aligned"


def _lane_reconciliation(
    *,
    topology: Mapping[str, Any],
    lane: Mapping[str, Any],
    observed: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = observed[0] if observed else None
    duplicate_receipts = [dict(item) for item in observed[1:]]
    reason_codes = (
        ["worker_receipt_missing"]
        if receipt is None
        else _lane_reason_codes(
            topology=topology,
            lane=lane,
            receipt=receipt,
            duplicate_count=len(duplicate_receipts),
        )
    )
    status = _lane_status(receipt, reason_codes=reason_codes)
    result = {
        "lane_id": str(lane.get("lane_id") or ""),
        "todo_id": lane.get("todo_id"),
        "execution_kind": lane.get("execution_kind"),
        "status": status,
        "reason_codes": reason_codes,
        "receipt_present": receipt is not None,
        "evidence_disposition": (
            "quarantined"
            if status in {"drifted", "rejected", "cancelled"}
            else "candidate"
            if status == "incomplete"
            else "candidate_for_parent_acceptance"
        ),
        "recommended_child_action": (
            "stop_child"
            if status in {"drifted", "rejected", "cancelled"}
            else "wait_for_child"
            if status == "incomplete"
            else "return_to_parent"
        ),
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": list(
            _mapping(_mapping(lane.get("task_packet")).get("guard")).get(
                "fallback_actions"
            )
            or []
        ),
    }
    if receipt is not None:
        result.update(
            {
                "worker_ref": receipt.get("worker_ref"),
                "workspace_ref": receipt.get("workspace_ref"),
                "effect_classes": list(receipt.get("effect_classes") or []),
                (
                    "quarantined_evidence_refs"
                    if status in {"drifted", "rejected", "cancelled"}
                    else "candidate_evidence_refs"
                ): list(receipt.get("evidence_refs") or []),
            }
        )
    return result, duplicate_receipts


def _orphaned_receipt_projection(
    receipt: Mapping[str, Any],
    *,
    unadmitted: bool,
) -> dict[str, Any]:
    return {
        "lane_id": str(receipt.get("lane_id") or "") or None,
        "worker_ref": receipt.get("worker_ref"),
        "reason_codes": (
            ["unadmitted_child_spawn", "orphaned_worker_result"]
            if unadmitted
            else ["orphaned_worker_result"]
        ),
        "evidence_disposition": "quarantined",
        "recommended_child_action": "stop_child",
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": list(CHILD_FALLBACK_ACTIONS),
        "quarantined_evidence_refs": list(receipt.get("evidence_refs") or []),
    }


def reconcile_subagent_execution(
    topology: Mapping[str, Any] | None,
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not topology and not receipts:
        return None
    planned_lanes = [
        dict(lane)
        for lane in (topology or {}).get("lanes") or []
        if isinstance(lane, Mapping)
    ]
    receipts_by_lane: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        receipts_by_lane.setdefault(str(receipt.get("lane_id") or ""), []).append(
            dict(receipt)
        )
    lane_results: list[dict[str, Any]] = []
    status_counts = {
        "aligned": 0,
        "incomplete": 0,
        "rejected": 0,
        "cancelled": 0,
        "drifted": 0,
    }
    duplicate_receipts: list[dict[str, Any]] = []
    for lane in planned_lanes:
        lane_id = str(lane.get("lane_id") or "")
        observed = receipts_by_lane.pop(lane_id, [])
        lane_result, duplicates = _lane_reconciliation(
            topology=topology or {},
            lane=lane,
            observed=observed,
        )
        lane_results.append(lane_result)
        duplicate_receipts.extend(duplicates)
        status_counts[str(lane_result["status"])] += 1
    orphaned_receipts = [
        _orphaned_receipt_projection(receipt, unadmitted=True)
        for lane_id, lane_receipts in sorted(receipts_by_lane.items())
        for receipt in lane_receipts
    ]
    orphaned_receipts.extend(
        _orphaned_receipt_projection(receipt, unadmitted=False)
        for receipt in duplicate_receipts
    )
    orphaned_count = len(orphaned_receipts)
    pre_spawn_rejections = [
        dict(item)
        for item in (topology or {}).get("pre_spawn_rejections") or []
        if isinstance(item, Mapping)
    ]
    payload = {
        "schema_version": SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION,
        "bundle_id": (topology or {}).get("bundle_id"),
        "topology_present": bool(topology),
        "observation_only": True,
        "settlement_enforced": False,
        "child_guard": {
            "schema_version": CHILD_EXECUTION_GUARD_SCHEMA_VERSION,
            "enforced_boundaries": ["pre_spawn_task_packet"],
            "validated_observations": [
                "receipt_task_packet_binding",
                "context_mode_binding",
                "workspace_boundary",
                "effect_boundary",
            ],
            "projected_dispositions": [
                "stop_child",
                "quarantine_evidence",
                "continue_parent",
                "fallback_actions",
            ],
            "unsupported_boundaries": [
                "evidence_acceptance_enforcement",
                "live_host_tool_interception",
                "automatic_host_child_termination",
            ],
            "parent_acceptance_required": True,
        },
        "parent_blocked": False,
        "parent_continuation": "continue",
        "status": (
            "drifted"
            if status_counts["drifted"] or orphaned_count
            else "guarded"
            if pre_spawn_rejections
            or status_counts["rejected"]
            or status_counts["cancelled"]
            else "incomplete"
            if status_counts["incomplete"]
            else "reconciled"
        ),
        "counts": {
            "planned": len(planned_lanes),
            "pre_spawn_rejected": len(pre_spawn_rejections),
            "observed": len(receipts),
            **status_counts,
            "orphaned": orphaned_count,
        },
        "lanes": lane_results,
        "orphaned_receipts": orphaned_receipts,
    }
    if pre_spawn_rejections:
        payload["pre_spawn_rejections"] = pre_spawn_rejections
    return payload


def observe_subagent_host_result(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    normalized: dict[str, Any],
    errors: list[str],
) -> None:
    try:
        receipts = normalize_subagent_host_execution_receipts(
            result.get("child_execution_receipts")
        )
    except ValueError as exc:
        errors.append(str(exc))
        receipts = []
    topology = (
        plan.get("subagent_execution_topology")
        if isinstance(plan.get("subagent_execution_topology"), Mapping)
        else None
    )
    if receipts:
        normalized["child_execution_receipts"] = receipts
    reconciliation = reconcile_subagent_execution(topology, receipts)
    if reconciliation is not None:
        normalized["subagent_reconciliation"] = reconciliation
        context = envelope_agent_context(
            _mapping(plan.get("turn_envelope")),
            phase="after_delegate_result",
            observations={"reconciliation_counts": reconciliation["counts"]},
        )
        if context is not None:
            normalized["agent_context"] = context


def subagent_execution_payload_projection(
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    host_result = _mapping(journal.get("host_result"))
    reconciliation = host_result.get("subagent_reconciliation")
    if not isinstance(reconciliation, Mapping):
        return {}
    projection = {"subagent_reconciliation": dict(reconciliation)}
    context = host_result.get("agent_context")
    if isinstance(context, Mapping):
        projection["agent_context"] = dict(context)
    return projection
