"""Contracts, constants, opaque-reference validation, and child task packet builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Literal, TypedDict

from ..runtime.public_safety import validate_public_safe_value as validate_public_safe_value

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


class ChildExecutionLane(TypedDict):
    """Typed record describing an admitted ephemeral child execution lane."""

    lane_id: str
    todo_id: str
    execution_kind: Literal["ephemeral_child"]
    admission_ref: str
    effect_boundary: Literal["held_workspace_write", "held_evidence_only"]
    allowed_effect_classes: list[str]
    workspace_requirement: Literal["independent_git_worktree", "not_required"]
    workspace_ref: str | None
    task_packet: dict[str, object]
    task_packet_digest: str


class ChildReceiptItemSchema(TypedDict):
    """Typed JSON schema shape for one child execution receipt item."""

    type: Literal["object"]
    properties: dict[str, object]
    required: list[str]
    additionalProperties: bool


class ChildExecutionReceiptsSchema(TypedDict):
    """Typed JSON schema shape for the child_execution_receipts array."""

    type: Literal["array"]
    items: ChildReceiptItemSchema
    maxItems: int


class ChildExecutionTopologyError(ValueError):
    """Structured fail-local pre-spawn validation error for child lanes."""

    def __init__(self, *, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def _mapping(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _opaque_ref(
    value: object,
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
    value: object,
    *,
    field: str,
    limit: int,
    allowed: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if len(value) > limit:
        raise ValueError(f"{field} accepts at most {limit} items")
    seen: set[str] = set()
    for item in value:
        ref = _opaque_ref(item, field=f"{field}[]")
        assert ref is not None
        if allowed is not None and ref not in allowed:
            raise ValueError(f"{field} contains unsupported value: {ref}")
        seen.add(ref)
    return sorted(seen)


def _canonical_sha256_hex(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _digest(value: Mapping[str, object]) -> str:
    return _canonical_sha256_hex(value)[:16]


def _sha256_ref(value: Mapping[str, object]) -> str:
    return "sha256:" + _canonical_sha256_hex(value)


def subagent_bundle_id(*, turn_key: str) -> str:
    return "bundle_" + _digest({"turn_key": turn_key})


def subagent_lane_id(*, bundle_id: str, todo_id: str) -> str:
    return "lane_" + _digest({"bundle_id": bundle_id, "todo_id": todo_id})


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ChildExecutionTopologyError(
            reason_code="child_task_packet_incomplete",
            detail=f"{field} is required before child launch",
        )
    return text


def _child_guard_policy(brief: Mapping[str, object]) -> dict[str, object]:
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


def _child_context_contract(operation: Mapping[str, object]) -> dict[str, object]:
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
    operation: Mapping[str, object],
    brief: Mapping[str, object],
    todo_id: str,
    source_state_ref: str,
    allowed_effect_classes: Sequence[str],
    workspace_requirement: str,
    workspace_ref: str | None,
) -> dict[str, object]:
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
    validation: dict[str, object] = {
        "declared": validation_declared,
        "authority_ref": (
            f"todo:{todo_id}:completion_validation" if validation_declared else None
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
    task_packet: dict[str, object] = {
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
    operation: Mapping[str, object],
    *,
    reason_code: str,
) -> dict[str, object]:
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
    operation: Mapping[str, object],
    *,
    bundle_id: str,
    source_state_ref: str,
) -> ChildExecutionLane:
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
    effect_boundary: Literal["held_workspace_write", "held_evidence_only"] = (
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
    workspace_requirement: Literal["independent_git_worktree", "not_required"] = (
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


def child_execution_receipts_json_schema() -> ChildExecutionReceiptsSchema:
    """Return the strict JSON schema for child_execution_receipts host output."""
    opaque_ref: dict[str, object] = {
        "type": "string",
        "pattern": OPAQUE_REF_PATTERN,
        "maxLength": 192,
    }
    nullable_ref: dict[str, object] = {
        "anyOf": [
            dict(opaque_ref),
            {"type": "null"},
        ]
    }
    properties: dict[str, object] = {
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
