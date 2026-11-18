from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
import re

from ..agent_context import envelope_agent_context
from ..runtime.public_safety import validate_public_safe_value

Payload = dict[str, object]
PayloadView = Mapping[str, object]


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


def subagent_execution_topology(plan: PayloadView) -> PayloadView | None:
    topology = plan.get("subagent_execution_topology")
    return topology if isinstance(topology, Mapping) else None


def subagent_host_result_fields(plan: PayloadView) -> frozenset[str]:
    return HOST_RESULT_FIELDS if subagent_execution_topology(plan) is not None else frozenset()


def _mapping(value: object) -> Payload:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value]
    return []


def _require_packet(condition: bool, detail: str) -> None:
    if not condition:
        raise ChildExecutionTopologyError(reason_code="child_task_packet_incomplete", detail=detail)


def _opaque_ref(value: object, *, field: str, required: bool = True) -> str | None:
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
        raise ValueError(f"{field} must be an opaque 1-192 character public-safe reference")
    return text


def _opaque_refs(
    value: object, *, field: str, limit: int, allowed: frozenset[str] | None = None
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


def _canonical_sha256(value: PayloadView) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def subagent_bundle_id(*, turn_key: str) -> str:
    return "bundle_" + _canonical_sha256({"turn_key": turn_key})[:16]


def subagent_lane_id(*, bundle_id: str, todo_id: str) -> str:
    return "lane_" + _canonical_sha256({"bundle_id": bundle_id, "todo_id": todo_id})[:16]


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    _require_packet(bool(text), f"{field} is required before child launch")
    return text


def _child_guard_policy(brief: PayloadView) -> Payload:
    _require_packet(brief.get("child_guard_policy") == "prevention_first_v0", "child_guard_policy is missing or not fail-local")
    return {
        "schema_version": CHILD_EXECUTION_GUARD_SCHEMA_VERSION,
        "pre_spawn": "require_complete_task_packet",
        "on_deviation": "project_stop_and_quarantine_child",
        "evidence_disposition": "candidate_until_parent_accepts",
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": list(CHILD_FALLBACK_ACTIONS),
    }


def _child_context_contract(operation: PayloadView) -> Payload:
    mode = _required_text(operation.get("recommended_context"), field="recommended_context")
    available_contexts = operation.get("available_contexts")
    _require_packet(isinstance(available_contexts, list), "available_contexts must be explicit")
    _require_packet(mode in CHILD_CONTEXT_MODES, "recommended_context is unsupported")
    assert isinstance(available_contexts, list)
    _require_packet(mode in available_contexts, "recommended_context is not available on the selected host")
    return {"mode": mode, "inheritance": CHILD_CONTEXT_INHERITANCE[mode]}


def _child_task_packet(
    *, operation: PayloadView, brief: PayloadView, todo_id: str, source_state_ref: str,
    allowed_effect_classes: Sequence[str], workspace_requirement: str, workspace_ref: str | None,
) -> Payload:
    required_capabilities = brief.get("required_capabilities")
    required_write_scopes = brief.get("required_write_scopes")
    acceptance = brief.get("acceptance")
    _require_packet(isinstance(required_capabilities, list), "required_capabilities must be explicit")
    _require_packet(isinstance(required_write_scopes, list), "required_write_scopes must be explicit")
    _require_packet(
        isinstance(acceptance, list) and bool(acceptance)
        and all(isinstance(item, str) and bool(item.strip()) for item in acceptance),
        "acceptance must be a non-empty string list",
    )
    assert isinstance(required_capabilities, list)
    assert isinstance(required_write_scopes, list)
    assert isinstance(acceptance, list)
    validation_declared = brief.get("validation_declared") is True
    validation: Payload = {
        "declared": validation_declared,
        "authority_ref": f"todo:{todo_id}:completion_validation" if validation_declared else None,
        "execution_owner": "registered_parent",
        "command_disclosed": False,
        "label": str(brief.get("validation_label") or "").strip() or None,
        "policy": (
            "registered parent runs declared todo completion validation; "
            "child reports relevant validation evidence"
            if validation_declared
            else _required_text(brief.get("validation_policy"), field="validation_policy")
        ),
    }
    execution_policy = brief.get("execution_policy")
    task_packet: Payload = {
        "schema_version": CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION,
        "todo_id": todo_id,
        "objective": _required_text(brief.get("objective"), field="objective"),
        "action_kind": _required_text(brief.get("action_kind"), field="action_kind"),
        "target_key": str(brief.get("target_key") or "").strip() or None,
        "deliverable": "held_workspace_change_with_evidence" if required_write_scopes else "public_safe_evidence",
        "acceptance": [str(item).strip() for item in acceptance],
        "context_refs": [
            _required_text(brief.get("authority_artifact"), field="authority_artifact"),
            _required_text(brief.get("latest_state_ref"), field="latest_state_ref"),
            source_state_ref,
        ],
        "task_domain": _required_text(brief.get("task_domain"), field="task_domain"),
        "task_repository": str(brief.get("task_repository") or "").strip() or None,
        "allowed_capabilities": list(required_capabilities),
        "allowed_write_scopes": list(required_write_scopes),
        "allowed_effect_classes": list(allowed_effect_classes),
        "forbidden_effect_classes": sorted(KNOWN_EFFECT_CLASSES - set(allowed_effect_classes)),
        "workspace_requirement": workspace_requirement,
        "workspace_ref": workspace_ref,
        "context": _child_context_contract(operation),
        "execution_budget": dict(execution_policy) if isinstance(execution_policy, Mapping) else {},
        "output_contract": _required_text(brief.get("expected_output"), field="expected_output"),
        "acceptance_mode": "parent_validation_then_review" if validation["declared"] else "parent_review_only",
        "validation": validation,
        "guard": _child_guard_policy(brief),
    }
    _require_packet(bool(task_packet["execution_budget"]), "execution_policy must define the child budget")
    validate_public_safe_value(task_packet, path="child_task_packet")
    return task_packet


def _child_pre_spawn_rejection(operation: PayloadView, *, reason_code: str) -> Payload:
    try:
        todo_id = _opaque_ref(operation.get("todo_id"), field="pre_spawn_rejection.todo_id", required=False)
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
    operation: PayloadView, *, bundle_id: str, source_state_ref: str
) -> Payload:
    todo_id = _opaque_ref(operation.get("todo_id"), field="topology.lanes[].todo_id")
    assert todo_id is not None
    brief = _mapping(operation.get("brief"))
    write_scopes = brief.get("required_write_scopes")
    has_workspace_write = isinstance(write_scopes, list) and bool(write_scopes)
    required_capabilities = brief.get("required_capabilities")
    uses_network = isinstance(required_capabilities, list) and "network" in required_capabilities
    effect_boundary = "held_workspace_write" if has_workspace_write else "held_evidence_only"
    allowed_effect_classes = ["local_read"]
    if uses_network:
        allowed_effect_classes.append("network_read")
    if has_workspace_write:
        allowed_effect_classes.append("held_workspace_write")
    lane_id = subagent_lane_id(bundle_id=bundle_id, todo_id=todo_id)
    workspace_ref = f"workspace:{lane_id}" if has_workspace_write else None
    workspace_requirement = "independent_git_worktree" if has_workspace_write else "not_required"
    task_packet = _child_task_packet(
        operation=operation, brief=brief, todo_id=todo_id, source_state_ref=source_state_ref,
        allowed_effect_classes=allowed_effect_classes,
        workspace_requirement=workspace_requirement, workspace_ref=workspace_ref,
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
        "task_packet_digest": "sha256:" + _canonical_sha256(task_packet),
    }


def build_subagent_execution_topology(
    *, turn_envelope: PayloadView, child_operations: Sequence[PayloadView],
    turn_key: str, source_state_ref: str,
) -> Payload | None:
    if not child_operations:
        return None
    orchestration = _mapping(turn_envelope.get("task_orchestration_contract"))
    if orchestration.get("schema_version") != "task_orchestration_contract_v2" or orchestration.get("mode") != "adaptive":
        return None
    normalized_source_state_ref = _opaque_ref(source_state_ref, field="topology.source_state_ref")
    goal_id = _opaque_ref(turn_envelope.get("goal_id"), field="topology.goal_id")
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
        if isinstance(configured_max_children, int) and not isinstance(configured_max_children, bool)
        and configured_max_children > 0
        else len(child_operations)
    )
    lanes: list[Payload] = []
    pre_spawn_rejections: list[Payload] = []
    allowed_effect_classes: set[str] = set()
    for operation in child_operations:
        if len(lanes) >= max_children:
            pre_spawn_rejections.append(_child_pre_spawn_rejection(operation, reason_code="child_capacity_exceeded"))
            continue
        try:
            lane = _child_execution_lane(operation, bundle_id=bundle_id, source_state_ref=normalized_source_state_ref)
        except (ChildExecutionTopologyError, ValueError):
            pre_spawn_rejections.append(_child_pre_spawn_rejection(operation, reason_code="child_task_packet_incomplete"))
            continue
        allowed_effect_classes.update(_string_list(lane.get("allowed_effect_classes")))
        lanes.append(lane)
    rationale_codes = ["admitted_ephemeral_child_lanes"] if lanes else ["parent_serial_fallback"]
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
        **({"pre_spawn_rejections": pre_spawn_rejections} if pre_spawn_rejections else {}),
    }


def bind_child_operations_to_topology(
    child_operations: Sequence[PayloadView], topology: PayloadView | None,
) -> list[Payload]:
    if not topology:
        return [dict(operation) for operation in child_operations]
    raw_lanes = topology.get("lanes")
    lanes_by_todo = {
        str(lane.get("todo_id") or ""): lane
        for lane in (raw_lanes if isinstance(raw_lanes, Iterable) and not isinstance(raw_lanes, (str, bytes, Mapping)) else ())
        if isinstance(lane, Mapping)
    }
    bound_operations: list[Payload] = []
    for operation in child_operations:
        lane = _mapping(lanes_by_todo.get(str(operation.get("todo_id") or "")))
        if not lane:
            continue
        bound_operations.append({
            **dict(operation),
            "bundle_id": topology.get("bundle_id"),
            "lane_id": lane.get("lane_id"),
            "execution_kind": lane.get("execution_kind"),
            "source_state_ref": topology.get("source_state_ref"),
            "task_packet_digest": lane.get("task_packet_digest"),
            "task_packet": _mapping(lane.get("task_packet")),
            "effect_boundary": lane.get("effect_boundary"),
            "workspace_ref": lane.get("workspace_ref"),
        })
    return bound_operations


def subagent_host_request_projection(plan: PayloadView) -> Payload:
    projection: Payload = {}
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


def child_execution_receipts_json_schema() -> Payload:
    opaque_ref: Payload = {"type": "string", "pattern": OPAQUE_REF_PATTERN, "maxLength": 192}
    def string_enum(values: Iterable[str]) -> Payload:
        return {"type": "string", "enum": sorted(values)}

    properties: Payload = {
        "schema_version": string_enum([SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION]),
        "bundle_id": dict(opaque_ref),
        "lane_id": dict(opaque_ref),
        "goal_id": dict(opaque_ref),
        "todo_id": dict(opaque_ref),
        "execution_kind": string_enum(EXECUTION_KINDS),
        "runtime_id": dict(opaque_ref),
        "worker_ref": dict(opaque_ref),
        "source_state_ref": dict(opaque_ref),
        "task_packet_digest": dict(opaque_ref),
        "context_mode": string_enum(CHILD_CONTEXT_MODES),
        "workspace_ref": {"anyOf": [dict(opaque_ref), {"type": "null"}]},
        "status": string_enum(RECEIPT_STATUSES),
        "effect_classes": {"type": "array", "items": string_enum(KNOWN_EFFECT_CLASSES), "maxItems": MAX_EFFECT_CLASSES},
        "evidence_refs": {"type": "array", "items": dict(opaque_ref), "minItems": 1, "maxItems": MAX_EVIDENCE_REFS},
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


def normalize_subagent_host_execution_receipts(value: object) -> list[Payload]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("child_execution_receipts must be a list")
    if len(value) > MAX_CHILD_EXECUTION_RECEIPTS:
        raise ValueError(f"child_execution_receipts accepts at most {MAX_CHILD_EXECUTION_RECEIPTS} items")
    receipts: list[Payload] = []
    for index, raw in enumerate(value):
        field = f"child_execution_receipts[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{field} must be an object")
        raw_keys = {str(key) for key in raw}
        unknown = sorted(raw_keys - _RECEIPT_FIELDS)
        if unknown:
            raise ValueError(f"{field} contains unsupported fields: {', '.join(unknown)}")
        missing = sorted(_RECEIPT_FIELDS - raw_keys)
        if missing:
            raise ValueError(f"{field} is missing fields: {', '.join(missing)}")
        if raw.get("schema_version") != SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION:
            raise ValueError(f"{field} has an unsupported schema_version")
        enums: dict[str, str] = {}
        for key, allowed in (
            ("execution_kind", EXECUTION_KINDS),
            ("context_mode", CHILD_CONTEXT_MODES),
            ("status", RECEIPT_STATUSES),
        ):
            parsed = str(raw.get(key) or "").strip()
            if parsed not in allowed:
                raise ValueError(f"{field}.{key} is unsupported")
            enums[key] = parsed
        if raw.get("raw_transcript_copied") is not False:
            raise ValueError(f"{field}.raw_transcript_copied must be false")
        receipt: Payload = {
            "schema_version": SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
            "bundle_id": _opaque_ref(raw.get("bundle_id"), field=f"{field}.bundle_id"),
            "lane_id": _opaque_ref(raw.get("lane_id"), field=f"{field}.lane_id"),
            "goal_id": _opaque_ref(raw.get("goal_id"), field=f"{field}.goal_id"),
            "todo_id": _opaque_ref(raw.get("todo_id"), field=f"{field}.todo_id"),
            "execution_kind": enums["execution_kind"],
            "runtime_id": _opaque_ref(raw.get("runtime_id"), field=f"{field}.runtime_id"),
            "worker_ref": _opaque_ref(raw.get("worker_ref"), field=f"{field}.worker_ref"),
            "source_state_ref": _opaque_ref(raw.get("source_state_ref"), field=f"{field}.source_state_ref"),
            "task_packet_digest": _opaque_ref(raw.get("task_packet_digest"), field=f"{field}.task_packet_digest"),
            "context_mode": enums["context_mode"],
            "workspace_ref": _opaque_ref(raw.get("workspace_ref"), field=f"{field}.workspace_ref", required=False),
            "status": enums["status"],
            "effect_classes": _opaque_refs(
                raw.get("effect_classes"), field=f"{field}.effect_classes",
                limit=MAX_EFFECT_CLASSES, allowed=KNOWN_EFFECT_CLASSES,
            ),
            "evidence_refs": _opaque_refs(
                raw.get("evidence_refs"), field=f"{field}.evidence_refs", limit=MAX_EVIDENCE_REFS,
            ),
            "raw_transcript_copied": False,
        }
        validate_public_safe_value(
            {key: item for key, item in receipt.items() if key != "raw_transcript_copied"}, path=field
        )
        receipts.append(receipt)
    return receipts


def _lane_reason_codes(
    *, topology: PayloadView, lane: PayloadView, receipt: PayloadView, duplicate_count: int
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
    expected_context_mode = _mapping(_mapping(lane.get("task_packet")).get("context")).get("mode")
    if receipt.get("context_mode") != expected_context_mode:
        reasons.append("context_mode_mismatch")
    allowed_effects = set(_string_list(lane.get("allowed_effect_classes")))
    if any(effect not in allowed_effects for effect in _string_list(receipt.get("effect_classes"))):
        reasons.append("side_effect_boundary_exceeded")
    if receipt.get("status") == "completed" and not receipt.get("evidence_refs"):
        reasons.append("aggregate_settlement_without_lane_evidence")
    if lane.get("workspace_requirement") == "independent_git_worktree" and receipt.get("workspace_ref") != lane.get("workspace_ref"):
        reasons.append("workspace_mismatch")
    return sorted(set(reasons))


def _lane_status(receipt: PayloadView | None, *, reason_codes: Sequence[str]) -> str:
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
    *, topology: PayloadView, lane: PayloadView, observed: Sequence[PayloadView]
) -> tuple[Payload, list[Payload]]:
    receipt = observed[0] if observed else None
    duplicate_receipts = [dict(item) for item in observed[1:]]
    reason_codes = (
        ["worker_receipt_missing"]
        if receipt is None
        else _lane_reason_codes(topology=topology, lane=lane, receipt=receipt, duplicate_count=len(duplicate_receipts))
    )
    status = _lane_status(receipt, reason_codes=reason_codes)
    quarantined = status in {"drifted", "rejected", "cancelled"}
    result: Payload = {
        "lane_id": str(lane.get("lane_id") or ""),
        "todo_id": lane.get("todo_id"),
        "execution_kind": lane.get("execution_kind"),
        "status": status,
        "reason_codes": reason_codes,
        "receipt_present": receipt is not None,
        "evidence_disposition": (
            "quarantined" if quarantined else "candidate" if status == "incomplete" else "candidate_for_parent_acceptance"
        ),
        "recommended_child_action": (
            "stop_child" if quarantined else "wait_for_child" if status == "incomplete" else "return_to_parent"
        ),
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": _string_list(_mapping(_mapping(lane.get("task_packet")).get("guard")).get("fallback_actions")),
    }
    if receipt is not None:
        result.update({
            "worker_ref": receipt.get("worker_ref"),
            "workspace_ref": receipt.get("workspace_ref"),
            "effect_classes": _string_list(receipt.get("effect_classes")),
            "quarantined_evidence_refs" if quarantined else "candidate_evidence_refs": _string_list(
                receipt.get("evidence_refs")
            ),
        })
    return result, duplicate_receipts


def _orphaned_receipt_projection(receipt: PayloadView, *, unadmitted: bool) -> Payload:
    return {
        "lane_id": str(receipt.get("lane_id") or "") or None,
        "worker_ref": receipt.get("worker_ref"),
        "reason_codes": ["unadmitted_child_spawn", "orphaned_worker_result"] if unadmitted else ["orphaned_worker_result"],
        "evidence_disposition": "quarantined",
        "recommended_child_action": "stop_child",
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": list(CHILD_FALLBACK_ACTIONS),
        "quarantined_evidence_refs": _string_list(receipt.get("evidence_refs")),
    }


def reconcile_subagent_execution(
    topology: PayloadView | None, receipts: Sequence[PayloadView]
) -> Payload | None:
    if not topology and not receipts:
        return None
    raw_lanes = (topology or {}).get("lanes")
    planned_lanes = [
        dict(lane)
        for lane in (raw_lanes if isinstance(raw_lanes, Iterable) and not isinstance(raw_lanes, (str, bytes, Mapping)) else ())
        if isinstance(lane, Mapping)
    ]
    receipts_by_lane: dict[str, list[Payload]] = {}
    for receipt in receipts:
        receipts_by_lane.setdefault(str(receipt.get("lane_id") or ""), []).append(dict(receipt))
    lane_results: list[Payload] = []
    status_counts = {"aligned": 0, "incomplete": 0, "rejected": 0, "cancelled": 0, "drifted": 0}
    duplicate_receipts: list[Payload] = []
    for lane in planned_lanes:
        lane_id = str(lane.get("lane_id") or "")
        observed = receipts_by_lane.pop(lane_id, [])
        lane_result, duplicates = _lane_reconciliation(topology=topology or {}, lane=lane, observed=observed)
        lane_results.append(lane_result)
        duplicate_receipts.extend(duplicates)
        status_counts[str(lane_result["status"])] += 1
    orphaned_receipts = [
        _orphaned_receipt_projection(receipt, unadmitted=True)
        for _lane_id, lane_receipts in sorted(receipts_by_lane.items())
        for receipt in lane_receipts
    ]
    orphaned_receipts.extend(_orphaned_receipt_projection(receipt, unadmitted=False) for receipt in duplicate_receipts)
    orphaned_count = len(orphaned_receipts)
    raw_rejections = (topology or {}).get("pre_spawn_rejections")
    pre_spawn_rejections = [
        dict(item)
        for item in (raw_rejections if isinstance(raw_rejections, Iterable) and not isinstance(raw_rejections, (str, bytes, Mapping)) else ())
        if isinstance(item, Mapping)
    ]
    payload: Payload = {
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
            if pre_spawn_rejections or status_counts["rejected"] or status_counts["cancelled"]
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
    plan: PayloadView, result: PayloadView, normalized: Payload, errors: list[str]
) -> None:
    try:
        receipts = normalize_subagent_host_execution_receipts(result.get("child_execution_receipts"))
    except ValueError as exc:
        errors.append(str(exc))
        receipts = []
    raw_topology = plan.get("subagent_execution_topology")
    topology = raw_topology if isinstance(raw_topology, Mapping) else None
    if receipts:
        normalized["child_execution_receipts"] = receipts
    reconciliation = reconcile_subagent_execution(topology, receipts)
    if reconciliation is not None:
        normalized["subagent_reconciliation"] = reconciliation
        from ..quota.turn_envelope import read_turn_plan_work

        context = envelope_agent_context(
            read_turn_plan_work(plan),
            phase="after_delegate_result",
            observations={"reconciliation_counts": reconciliation["counts"]},
        )
        if context is not None:
            normalized["agent_context"] = context


def subagent_execution_payload_projection(journal: PayloadView) -> Payload:
    host_result = _mapping(journal.get("host_result"))
    reconciliation = host_result.get("subagent_reconciliation")
    if not isinstance(reconciliation, Mapping):
        return {}
    projection: Payload = {"subagent_reconciliation": dict(reconciliation)}
    context = host_result.get("agent_context")
    if isinstance(context, Mapping):
        projection["agent_context"] = dict(context)
    return projection
