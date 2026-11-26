"""Coordinator-child execution topology projection and host result wiring."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Protocol

from ..agent_context import envelope_agent_context
from .subagent_execution_contracts import (
    CHILD_CONTEXT_INHERITANCE as CHILD_CONTEXT_INHERITANCE,
    CHILD_CONTEXT_MODES as CHILD_CONTEXT_MODES,
    CHILD_EXECUTION_GUARD_SCHEMA_VERSION as CHILD_EXECUTION_GUARD_SCHEMA_VERSION,
    CHILD_EXECUTION_REJECTION_SCHEMA_VERSION as CHILD_EXECUTION_REJECTION_SCHEMA_VERSION,
    CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION as CHILD_EXECUTION_TASK_PACKET_SCHEMA_VERSION,
    CHILD_FALLBACK_ACTIONS as CHILD_FALLBACK_ACTIONS,
    EXECUTION_KINDS as EXECUTION_KINDS,
    HOST_RESULT_FIELDS as HOST_RESULT_FIELDS,
    KNOWN_EFFECT_CLASSES as KNOWN_EFFECT_CLASSES,
    MAX_CHILD_EXECUTION_RECEIPTS as MAX_CHILD_EXECUTION_RECEIPTS,
    MAX_EFFECT_CLASSES as MAX_EFFECT_CLASSES,
    MAX_EVIDENCE_REFS as MAX_EVIDENCE_REFS,
    OPAQUE_REF_PATTERN as OPAQUE_REF_PATTERN,
    RECEIPT_STATUSES as RECEIPT_STATUSES,
    SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION as SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION,
    SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION as SUBAGENT_EXECUTION_TOPOLOGY_SCHEMA_VERSION,
    SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION as SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
    ChildExecutionLane as ChildExecutionLane,
    ChildExecutionReceiptsSchema as ChildExecutionReceiptsSchema,
    ChildExecutionTopologyError as ChildExecutionTopologyError,
    ChildReceiptItemSchema as ChildReceiptItemSchema,
    _OPAQUE_REF as _OPAQUE_REF,
    _RECEIPT_FIELDS as _RECEIPT_FIELDS,
    _child_context_contract as _child_context_contract,
    _child_execution_lane as _child_execution_lane,
    _child_guard_policy as _child_guard_policy,
    _child_pre_spawn_rejection,
    _child_task_packet as _child_task_packet,
    _digest as _digest,
    _mapping,
    _opaque_ref as _opaque_ref,
    _opaque_refs as _opaque_refs,
    _required_text as _required_text,
    _sha256_ref as _sha256_ref,
    child_execution_receipts_json_schema as child_execution_receipts_json_schema,
    subagent_bundle_id as subagent_bundle_id,
    subagent_lane_id as subagent_lane_id,
)
from .subagent_execution_reconciliation import (
    _lane_reason_codes as _lane_reason_codes,
    _lane_reconciliation as _lane_reconciliation,
    _lane_status as _lane_status,
    _orphaned_receipt_projection as _orphaned_receipt_projection,
    normalize_subagent_host_execution_receipts as normalize_subagent_host_execution_receipts,
    reconcile_subagent_execution as reconcile_subagent_execution,
)


class _EnvelopeAgentContextFn(Protocol):
    def __call__(
        self,
        envelope: Mapping[str, object],
        *,
        phase: str,
        observations: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None: ...


def subagent_execution_topology(
    plan: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Return the plan's subagent_execution_topology mapping when present."""
    topology = plan.get("subagent_execution_topology")
    return topology if isinstance(topology, Mapping) else None


def subagent_host_result_fields(plan: Mapping[str, object]) -> frozenset[str]:
    """Return additional host-result fields admitted when subagent topology is present."""
    return (
        HOST_RESULT_FIELDS
        if subagent_execution_topology(plan) is not None
        else frozenset()
    )


def build_subagent_execution_topology(
    *,
    turn_envelope: Mapping[str, object],
    child_operations: Sequence[Mapping[str, object]],
    turn_key: str,
    source_state_ref: str,
) -> dict[str, object] | None:
    """Build bounded coordinator-child execution topology for adaptive turns."""
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
    lanes: list[ChildExecutionLane] = []
    pre_spawn_rejections: list[dict[str, object]] = []
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
    child_operations: Sequence[Mapping[str, object]],
    topology: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """Bind child operations to their admitted topology lanes."""
    if not topology:
        return [dict(operation) for operation in child_operations]
    raw_lanes = topology.get("lanes")
    lane_items = (
        raw_lanes
        if isinstance(raw_lanes, Sequence) and not isinstance(raw_lanes, (str, bytes))
        else ()
    )
    lanes_by_todo = {
        str(lane.get("todo_id") or ""): lane
        for lane in lane_items
        if isinstance(lane, Mapping)
    }
    bound_operations: list[dict[str, object]] = []
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
    plan: Mapping[str, object],
) -> dict[str, object]:
    """Project delegation context, child operations, and topology for host request."""
    projection: dict[str, object] = {}
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


def observe_subagent_host_result(
    plan: Mapping[str, object],
    result: Mapping[str, object],
    normalized: MutableMapping[str, object],
    errors: list[str],
) -> None:
    """Normalize child execution receipts and attach reconciliation onto host result."""
    try:
        receipts = normalize_subagent_host_execution_receipts(
            result.get("child_execution_receipts")
        )
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

        raw_counts = reconciliation["counts"]
        observations: dict[str, object] = {"reconciliation_counts": raw_counts}
        envelope_fn: _EnvelopeAgentContextFn = envelope_agent_context
        context = envelope_fn(
            read_turn_plan_work(plan),
            phase="after_delegate_result",
            observations=observations,
        )
        if context is not None:
            normalized["agent_context"] = context


def subagent_execution_payload_projection(
    journal: Mapping[str, object],
) -> dict[str, object]:
    """Extract subagent reconciliation and post-delegation agent context from journal."""
    host_result = _mapping(journal.get("host_result"))
    reconciliation = host_result.get("subagent_reconciliation")
    if not isinstance(reconciliation, Mapping):
        return {}
    projection: dict[str, object] = {
        "subagent_reconciliation": dict(reconciliation)
    }
    context = host_result.get("agent_context")
    if isinstance(context, Mapping):
        projection["agent_context"] = dict(context)
    return projection
