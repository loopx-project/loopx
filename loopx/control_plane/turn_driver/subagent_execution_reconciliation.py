"""Host receipt normalization and parent-child topology reconciliation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .subagent_execution_contracts import (
    CHILD_CONTEXT_MODES,
    CHILD_EXECUTION_GUARD_SCHEMA_VERSION,
    CHILD_FALLBACK_ACTIONS,
    EXECUTION_KINDS,
    KNOWN_EFFECT_CLASSES,
    MAX_CHILD_EXECUTION_RECEIPTS,
    MAX_EFFECT_CLASSES,
    MAX_EVIDENCE_REFS,
    RECEIPT_STATUSES,
    SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION,
    SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
    _RECEIPT_FIELDS,
    _mapping,
    _opaque_ref,
    _opaque_refs,
)


def _normalize_single_receipt(
    raw: Mapping[str, object],
    *,
    index: int,
) -> dict[str, object]:
    field = f"child_execution_receipts[{index}]"
    raw_keys = set(raw)
    unknown = sorted(raw_keys - _RECEIPT_FIELDS)
    if unknown:
        raise ValueError(
            f"{field} contains unsupported fields: {', '.join(unknown)}"
        )
    missing = sorted(_RECEIPT_FIELDS - raw_keys)
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
    if raw.get("raw_transcript_copied") is not False:
        raise ValueError(f"{field}.raw_transcript_copied must be false")
    return {
        "schema_version": SUBAGENT_HOST_EXECUTION_RECEIPT_SCHEMA_VERSION,
        "bundle_id": _opaque_ref(raw.get("bundle_id"), field=f"{field}.bundle_id"),
        "lane_id": _opaque_ref(raw.get("lane_id"), field=f"{field}.lane_id"),
        "goal_id": _opaque_ref(raw.get("goal_id"), field=f"{field}.goal_id"),
        "todo_id": _opaque_ref(raw.get("todo_id"), field=f"{field}.todo_id"),
        "execution_kind": execution_kind,
        "runtime_id": _opaque_ref(
            raw.get("runtime_id"), field=f"{field}.runtime_id"
        ),
        "worker_ref": _opaque_ref(
            raw.get("worker_ref"), field=f"{field}.worker_ref"
        ),
        "source_state_ref": _opaque_ref(
            raw.get("source_state_ref"), field=f"{field}.source_state_ref"
        ),
        "task_packet_digest": _opaque_ref(
            raw.get("task_packet_digest"), field=f"{field}.task_packet_digest"
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


def normalize_subagent_host_execution_receipts(
    value: object,
) -> list[dict[str, object]]:
    """Validate and normalize host-reported child execution receipts."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("child_execution_receipts must be a list")
    if len(value) > MAX_CHILD_EXECUTION_RECEIPTS:
        raise ValueError(
            "child_execution_receipts accepts at most "
            f"{MAX_CHILD_EXECUTION_RECEIPTS} items"
        )
    receipts: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"child_execution_receipts[{index}] must be an object")
        receipts.append(_normalize_single_receipt(raw, index=index))
    return receipts


def _lane_reason_codes(
    *,
    topology: Mapping[str, object],
    lane: Mapping[str, object],
    receipt: Mapping[str, object],
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
    raw_allowed = lane.get("allowed_effect_classes")
    allowed_effects = (
        set(raw_allowed)
        if isinstance(raw_allowed, Iterable) and not isinstance(raw_allowed, (str, bytes))
        else set()
    )
    raw_effects = receipt.get("effect_classes")
    receipt_effects = (
        raw_effects
        if isinstance(raw_effects, Iterable) and not isinstance(raw_effects, (str, bytes))
        else ()
    )
    if any(effect not in allowed_effects for effect in receipt_effects):
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
    receipt: Mapping[str, object] | None,
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
    topology: Mapping[str, object],
    lane: Mapping[str, object],
    observed: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]]]:
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
    raw_fallbacks = _mapping(
        _mapping(lane.get("task_packet")).get("guard")
    ).get("fallback_actions")
    fallbacks = (
        list(raw_fallbacks)
        if isinstance(raw_fallbacks, Sequence)
        and not isinstance(raw_fallbacks, (str, bytes))
        else []
    )
    result: dict[str, object] = {
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
        "fallback_actions": fallbacks,
    }
    if receipt is not None:
        raw_effects = receipt.get("effect_classes")
        raw_evidence = receipt.get("evidence_refs")
        result.update(
            {
                "worker_ref": receipt.get("worker_ref"),
                "workspace_ref": receipt.get("workspace_ref"),
                "effect_classes": (
                    list(raw_effects)
                    if isinstance(raw_effects, Sequence)
                    and not isinstance(raw_effects, (str, bytes))
                    else []
                ),
                (
                    "quarantined_evidence_refs"
                    if status in {"drifted", "rejected", "cancelled"}
                    else "candidate_evidence_refs"
                ): (
                    list(raw_evidence)
                    if isinstance(raw_evidence, Sequence)
                    and not isinstance(raw_evidence, (str, bytes))
                    else []
                ),
            }
        )
    return result, duplicate_receipts


def _orphaned_receipt_projection(
    receipt: Mapping[str, object],
    *,
    unadmitted: bool,
) -> dict[str, object]:
    raw_evidence = receipt.get("evidence_refs")
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
        "quarantined_evidence_refs": (
            list(raw_evidence)
            if isinstance(raw_evidence, Sequence)
            and not isinstance(raw_evidence, (str, bytes))
            else []
        ),
    }


def _mapping_list(raw: object) -> list[Mapping[str, object]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def reconcile_subagent_execution(
    topology: Mapping[str, object] | None,
    receipts: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """Reconcile planned topology lanes against normalized child execution receipts."""
    if not topology and not receipts:
        return None
    topology_map: Mapping[str, object] = topology or {}
    planned_lanes = _mapping_list(topology_map.get("lanes"))
    receipts_by_lane: dict[str, list[Mapping[str, object]]] = {}
    for receipt in receipts:
        receipts_by_lane.setdefault(str(receipt.get("lane_id") or ""), []).append(
            receipt
        )
    lane_results: list[dict[str, object]] = []
    status_counts = {
        "aligned": 0,
        "incomplete": 0,
        "rejected": 0,
        "cancelled": 0,
        "drifted": 0,
    }
    duplicate_receipts: list[dict[str, object]] = []
    for lane in planned_lanes:
        lane_id = str(lane.get("lane_id") or "")
        observed = receipts_by_lane.pop(lane_id, [])
        lane_result, duplicates = _lane_reconciliation(
            topology=topology_map,
            lane=lane,
            observed=observed,
        )
        lane_results.append(lane_result)
        duplicate_receipts.extend(duplicates)
        status_counts[str(lane_result["status"])] += 1
    orphaned_receipts = [
        _orphaned_receipt_projection(receipt, unadmitted=True)
        for _lane_id, lane_receipts in sorted(receipts_by_lane.items())
        for receipt in lane_receipts
    ]
    orphaned_receipts.extend(
        _orphaned_receipt_projection(receipt, unadmitted=False)
        for receipt in duplicate_receipts
    )
    orphaned_count = len(orphaned_receipts)
    pre_spawn_rejections = [
        dict(item) for item in _mapping_list(topology_map.get("pre_spawn_rejections"))
    ]
    payload: dict[str, object] = {
        "schema_version": SUBAGENT_CONTROL_PLANE_RECONCILIATION_SCHEMA_VERSION,
        "bundle_id": topology_map.get("bundle_id"),
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
