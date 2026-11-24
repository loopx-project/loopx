"""Semantic replan guard companion-field merge and repair helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ...file_lock import exclusive_cross_runtime_file_lock
from ...rollout_event_log import (
    _append_rollout_event_line,
    build_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
)
from ..todos.contract import normalize_todo_id, normalize_todo_replan_obligation_id
from .error_codes import HeartbeatReceiptIdentityConflictError

SEMANTIC_REPLAN_REVIEW_FIELDS = (
    "semantic_replan_review_kind",
    "semantic_replan_review_stop_condition",
    "semantic_replan_review_stop_condition_sha256",
    "semantic_replan_review_stop_contract_id",
)
SEMANTIC_REPLAN_CAPABILITY_FIELDS = (
    "semantic_replan_capability_id",
    "semantic_replan_gap_id",
    "semantic_replan_frontier_revision",
)
SEMANTIC_REPLAN_COMPANION_FIELDS = (
    SEMANTIC_REPLAN_REVIEW_FIELDS + SEMANTIC_REPLAN_CAPABILITY_FIELDS
)


def strip_semantic_replan_companions(
    details: Mapping[str, object],
) -> dict[str, object]:
    """Return a copy of receipt details with all semantic replan companions removed."""

    stripped = dict(details)
    for field in SEMANTIC_REPLAN_COMPANION_FIELDS:
        stripped.pop(field, None)
    return stripped


def merge_semantic_replan_guard_details(
    effective_details: Mapping[str, object],
    incoming_details: Mapping[str, object],
) -> dict[str, object]:
    """Merge receipt details without leaving orphaned replan companion fields."""

    corrected_details = dict(effective_details)
    touches_semantic_guard = "semantic_replan_obligation_id" in incoming_details or any(
        field in incoming_details for field in SEMANTIC_REPLAN_COMPANION_FIELDS
    )
    if not touches_semantic_guard:
        corrected_details.update(incoming_details)
        return corrected_details

    existing_obligation = normalize_todo_replan_obligation_id(
        effective_details.get("semantic_replan_obligation_id")
    )
    if "semantic_replan_obligation_id" in incoming_details:
        raw_incoming = incoming_details.get("semantic_replan_obligation_id")
        incoming_obligation = normalize_todo_replan_obligation_id(raw_incoming)
        if raw_incoming not in (None, "") and incoming_obligation is None:
            raise HeartbeatReceiptIdentityConflictError(
                "heartbeat receipt semantic replan guard is malformed"
            )
    else:
        incoming_obligation = existing_obligation

    incoming_has_review = any(
        field in incoming_details for field in SEMANTIC_REPLAN_REVIEW_FIELDS
    )
    incoming_has_capability = any(
        field in incoming_details for field in SEMANTIC_REPLAN_CAPABILITY_FIELDS
    )
    if incoming_has_review and incoming_has_capability:
        raise HeartbeatReceiptIdentityConflictError(
            "a review source cannot replace a capability guard"
        )
    if (incoming_has_review or incoming_has_capability) and incoming_obligation is None:
        raise HeartbeatReceiptIdentityConflictError(
            "heartbeat receipt semantic replan guard requires a selected obligation"
        )

    if (
        incoming_obligation is None
        or incoming_obligation != existing_obligation
        or incoming_has_review
        or incoming_has_capability
    ):
        corrected_details = strip_semantic_replan_companions(corrected_details)

    corrected_details.update(incoming_details)
    return corrected_details


def has_orphaned_identity_upgrade_replan_guard(
    event: Mapping[str, object] | None,
) -> bool:
    """Fast check for an identity_upgrade receipt carrying orphaned guard companions."""

    if not isinstance(event, Mapping):
        return False
    details = event.get("details")
    if not isinstance(details, Mapping):
        return False
    if details.get("settlement_receipt_revision") != "identity_upgrade":
        return False
    if not str(details.get("settlement_effect_id") or "").strip():
        return False
    if "semantic_replan_obligation_id" not in details:
        return False
    if details.get("semantic_replan_obligation_id") not in (None, ""):
        return False
    return any(field in details for field in SEMANTIC_REPLAN_COMPANION_FIELDS)


def is_repairable_orphaned_identity_upgrade_receipt(
    effective: Mapping[str, object],
    matching: Sequence[Mapping[str, object]],
) -> bool:
    """Prove via causal parent lineage that an identity_upgrade orphaned its companions."""

    if not has_orphaned_identity_upgrade_replan_guard(effective):
        return False
    details = effective.get("details")
    if not isinstance(details, Mapping):
        return False
    has_review = any(field in details for field in SEMANTIC_REPLAN_REVIEW_FIELDS)
    has_capability = any(
        field in details for field in SEMANTIC_REPLAN_CAPABILITY_FIELDS
    )
    if has_review and has_capability:
        return False
    orphaned = [field for field in SEMANTIC_REPLAN_COMPANION_FIELDS if field in details]
    if not orphaned:
        return False
    causality = effective.get("causality")
    if not isinstance(causality, Mapping):
        return False
    parent_id = str(
        causality.get("source_event_id") or causality.get("caused_by") or ""
    ).strip()
    if not parent_id:
        return False
    parent = next(
        (
            event
            for event in matching
            if str(event.get("event_id") or "").strip() == parent_id
        ),
        None,
    )
    if parent is None:
        return False
    parent_details = parent.get("details")
    if not isinstance(parent_details, Mapping):
        return False
    if (
        normalize_todo_replan_obligation_id(
            parent_details.get("semantic_replan_obligation_id")
        )
        is None
    ):
        return False
    return all(
        field in parent_details and parent_details[field] == details[field]
        for field in orphaned
    )


def is_orphaned_semantic_replan_guard_error(error: BaseException) -> bool:
    """Return True when TS settlement readback failed on orphaned guard companions."""

    message = str(error)
    return (
        "a review source requires a selected obligation" in message
        or "a capability guard requires a selected obligation" in message
    )


def append_repaired_identity_upgrade_receipt(
    log_path: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    effective: Mapping[str, object],
    goal_ref: Mapping[str, object] | None,
) -> dict[str, object]:
    """Append a corrective quota_should_run receipt stripping orphaned companions."""

    effective_details_value = effective.get("details")
    effective_details: Mapping[str, object] = (
        effective_details_value if isinstance(effective_details_value, Mapping) else {}
    )
    repaired_details = strip_semantic_replan_companions(effective_details)
    repaired_details["semantic_replan_obligation_id"] = ""
    repaired_details["settlement_receipt_revision"] = "identity_upgrade"
    source_event_id = str(effective.get("event_id") or "").strip() or None
    repaired = build_rollout_event(
        goal_id=goal_id,
        event_kind="quota_should_run",
        goal_ref=goal_ref,
        agent_id=agent_id,
        todo_id=normalize_todo_id(effective_details.get("todo_id")),
        run_id=turn_instance_id,
        status=str(effective.get("status") or "normal_run"),
        summary=(
            "heartbeat quota receipt repaired orphaned semantic replan guard "
            f"for turn={turn_instance_id}"
        ),
        source_event_id=source_event_id,
        caused_by=source_event_id,
        details=repaired_details,
    )
    _append_rollout_event_line(log_path, repaired)
    return repaired


def repair_orphaned_identity_upgrade_heartbeat_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, object] | None = None,
) -> tuple[dict[str, object] | None, bool]:
    """Append a same-binding cleanup event when identity_upgrade leaked companions."""

    from .heartbeat_receipt import _effective_heartbeat_receipt, _heartbeat_receipt_events

    log_path = rollout_event_log_path(runtime_root, goal_id)
    if not log_path.exists():
        return None, False
    with exclusive_cross_runtime_file_lock(
        log_path, operation="repair_heartbeat_receipt_replan_guard"
    ):
        events = load_rollout_events(log_path)
        matching = _heartbeat_receipt_events(
            events,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
        effective, existing_identity = _effective_heartbeat_receipt(matching)
        if effective is None or existing_identity is None:
            return effective, False
        if not is_repairable_orphaned_identity_upgrade_receipt(effective, matching):
            return effective, False
        repaired = append_repaired_identity_upgrade_receipt(
            log_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            effective=effective,
            goal_ref=goal_ref,
        )
        return repaired, True


def repair_orphaned_identity_upgrade_heartbeat_receipts_for_agent(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    goal_ref: Mapping[str, object] | None = None,
) -> int:
    """Repair all historical Turn receipts for one agent that leaked companions."""

    from .heartbeat_receipt import (
        _agent_heartbeat_receipt_events,
        _effective_heartbeat_receipt,
    )

    log_path = rollout_event_log_path(runtime_root, goal_id)
    if not log_path.exists():
        return 0
    with exclusive_cross_runtime_file_lock(
        log_path, operation="repair_heartbeat_receipt_replan_guard"
    ):
        events = load_rollout_events(log_path)
        agent_events = _agent_heartbeat_receipt_events(
            events, goal_id=goal_id, agent_id=agent_id, goal_ref=goal_ref,
        )
        events_by_turn: dict[str, list[dict[str, object]]] = {}
        for event in agent_events:
            turn_id = str(event.get("run_id") or "").strip()
            if turn_id:
                events_by_turn.setdefault(turn_id, []).append(event)
        repaired_count = 0
        for turn_id, turn_events in events_by_turn.items():
            effective, existing_identity = _effective_heartbeat_receipt(turn_events)
            if (
                effective is not None
                and existing_identity is not None
                and is_repairable_orphaned_identity_upgrade_receipt(
                    effective, turn_events
                )
            ):
                append_repaired_identity_upgrade_receipt(
                    log_path,
                    goal_id=goal_id,
                    agent_id=agent_id,
                    turn_instance_id=turn_id,
                    effective=effective,
                    goal_ref=goal_ref,
                )
                repaired_count += 1
        return repaired_count
