"""Semantic replan guard companion-field merge and repair helpers."""

from __future__ import annotations

import hashlib
import re
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
from ..work_items.autonomous_replan_obligation import (
    AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION,
    AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
    AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
)
from .error_codes import (
    HeartbeatReceiptIdentityConflictError,
    MalformedSettlementStateError,
)

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

_BARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_CAPABILITY_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFAULT_STOP_CONDITION_SHA256 = hashlib.sha256(
    AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION.encode("utf-8")
).hexdigest()


def _non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _is_valid_capability_guard_details(details: Mapping[str, object]) -> bool:
    cap_id = details.get("semantic_replan_capability_id")
    gap_id = details.get("semantic_replan_gap_id")
    frontier = details.get("semantic_replan_frontier_revision")
    return (
        isinstance(cap_id, str)
        and bool(_CAPABILITY_ID_RE.match(cap_id))
        and isinstance(gap_id, str)
        and bool(_CAPABILITY_REF_RE.match(gap_id))
        and isinstance(frontier, str)
        and bool(_CAPABILITY_REF_RE.match(frontier))
    )


def is_valid_semantic_replan_guard_details(
    details: Mapping[str, object],
) -> bool:
    """Mirror TS ``projectSemanticReplanGuard`` validation without changing owner authority."""

    has_cap = any(field in details for field in SEMANTIC_REPLAN_CAPABILITY_FIELDS)
    has_review = any(field in details for field in SEMANTIC_REPLAN_REVIEW_FIELDS)
    if has_cap and has_review:
        return False
    if "semantic_replan_obligation_id" not in details:
        return not has_cap and not has_review
    raw_obligation = details.get("semantic_replan_obligation_id")
    if raw_obligation in ("", None):
        return not has_cap and not has_review
    if normalize_todo_replan_obligation_id(raw_obligation) is None:
        return False
    if has_review:
        if _non_empty_str(details.get("semantic_replan_review_kind")) is None:
            return False
        has_stop = "semantic_replan_review_stop_condition" in details
        has_digest = "semantic_replan_review_stop_condition_sha256" in details
        has_contract = "semantic_replan_review_stop_contract_id" in details
        stop = (
            _non_empty_str(details.get("semantic_replan_review_stop_condition"))
            if has_stop
            else None
        )
        if has_stop and stop is None:
            return False
        stop_digest = (
            _non_empty_str(details.get("semantic_replan_review_stop_condition_sha256"))
            if has_digest
            else None
        )
        if has_digest and stop_digest is None:
            return False
        stop_contract_id = (
            _non_empty_str(details.get("semantic_replan_review_stop_contract_id"))
            if has_contract
            else None
        )
        if has_contract and stop_contract_id is None:
            return False
        if stop is None and stop_digest is None:
            return False
        if stop is not None and len(stop) > 240:
            return False
        if stop_digest is not None and not _BARE_SHA256_RE.match(stop_digest):
            return False
        if stop_contract_id is not None and (
            stop_contract_id != AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID
            or stop_digest != _DEFAULT_STOP_CONDITION_SHA256
        ):
            return False
        if (
            stop is not None
            and stop_digest is not None
            and stop != AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION
            and hashlib.sha256(stop.encode("utf-8")).hexdigest() != stop_digest
        ):
            return False
    if has_cap and not _is_valid_capability_guard_details(details):
        return False
    return True


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
    """Fast check for an identity_upgrade receipt failing semantic replan validation."""

    if not isinstance(event, Mapping):
        return False
    details = event.get("details")
    if not isinstance(details, Mapping):
        return False
    if details.get("settlement_receipt_revision") != "identity_upgrade":
        return False
    if not str(details.get("settlement_effect_id") or "").strip():
        return False
    return not is_valid_semantic_replan_guard_details(details)


def _causal_parent_receipt(
    effective: Mapping[str, object],
    matching: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    causality = effective.get("causality")
    if not isinstance(causality, Mapping):
        return None
    source_event_id = str(causality.get("source_event_id") or "").strip()
    caused_by = str(causality.get("caused_by") or "").strip()
    if source_event_id and caused_by and source_event_id != caused_by:
        return None
    parent_id = source_event_id or caused_by
    if not parent_id:
        return None
    effective_id = str(effective.get("event_id") or "").strip()
    prior_events: Sequence[Mapping[str, object]] = matching
    for index, event in enumerate(matching):
        if event is effective or (
            effective_id and str(event.get("event_id") or "").strip() == effective_id
        ):
            prior_events = matching[:index]
            break
    parent = next(
        (
            event
            for event in reversed(prior_events)
            if str(event.get("event_id") or "").strip() == parent_id
        ),
        None,
    )
    if parent is None:
        return None
    parent_details = parent.get("details")
    if not isinstance(parent_details, Mapping):
        return None
    if str(parent_details.get("settlement_effect_id") or "").strip():
        return None
    if (
        normalize_todo_replan_obligation_id(
            parent_details.get("semantic_replan_obligation_id")
        )
        is None
    ):
        return None
    if not is_valid_semantic_replan_guard_details(parent_details):
        return None
    return parent, parent_details


def _stale_fields_from_parent_overlay(
    details: Mapping[str, object],
    parent_details: Mapping[str, object],
) -> tuple[list[str], bool] | None:
    """Identify parent-attributable stale guard fields left by legacy dict overlay."""

    present_review = [f for f in SEMANTIC_REPLAN_REVIEW_FIELDS if f in details]
    present_cap = [f for f in SEMANTIC_REPLAN_CAPABILITY_FIELDS if f in details]
    parent_has_review = any(f in parent_details for f in SEMANTIC_REPLAN_REVIEW_FIELDS)
    parent_has_cap = any(f in parent_details for f in SEMANTIC_REPLAN_CAPABILITY_FIELDS)
    if parent_has_review == parent_has_cap:
        return None

    raw_obligation = details.get("semantic_replan_obligation_id")
    if "semantic_replan_obligation_id" in details and raw_obligation in (None, ""):
        if present_review and present_cap:
            return None
        present = present_review + present_cap
        if not present or not all(
            f in parent_details and parent_details[f] == details[f] for f in present
        ):
            return None
        return present, True

    if normalize_todo_replan_obligation_id(raw_obligation) is None:
        return None

    # Mode B: review <-> capability kind rotation while parent was identity-less.
    if present_review and present_cap:
        stale = present_review if parent_has_review else present_cap
        if not all(
            f in parent_details and parent_details[f] == details[f] for f in stale
        ):
            return None
        return stale, False

    if not present_review or not parent_has_review:
        return None

    eff_stop = (
        _non_empty_str(details.get("semantic_replan_review_stop_condition"))
        if "semantic_replan_review_stop_condition" in details
        else None
    )
    if "semantic_replan_review_stop_condition" in details and eff_stop is None:
        return None

    # Mode C: digest-bound review rotated/shortened to an inline stop condition.
    if (
        eff_stop is not None
        and eff_stop != AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION
        and parent_details.get("semantic_replan_review_stop_condition")
        in (None, AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION)
        and "semantic_replan_review_stop_condition_sha256" in parent_details
    ):
        stale_review: list[str] = []
        if "semantic_replan_review_stop_condition_sha256" in details:
            if (
                details["semantic_replan_review_stop_condition_sha256"]
                != parent_details["semantic_replan_review_stop_condition_sha256"]
            ):
                return None
            stale_review.append("semantic_replan_review_stop_condition_sha256")
        if "semantic_replan_review_stop_contract_id" in details:
            if (
                "semantic_replan_review_stop_contract_id" not in parent_details
                or details["semantic_replan_review_stop_contract_id"]
                != parent_details["semantic_replan_review_stop_contract_id"]
            ):
                return None
            stale_review.append("semantic_replan_review_stop_contract_id")
        return (stale_review, False) if stale_review else None

    # Mode D: default-contract digest review rotated to a non-default digest review.
    if (
        eff_stop in (None, AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION)
        and "semantic_replan_review_stop_condition_sha256" in details
        and details["semantic_replan_review_stop_condition_sha256"]
        != parent_details.get("semantic_replan_review_stop_condition_sha256")
        and "semantic_replan_review_stop_contract_id" in details
        and "semantic_replan_review_stop_contract_id" in parent_details
        and details["semantic_replan_review_stop_contract_id"]
        == parent_details["semantic_replan_review_stop_contract_id"]
    ):
        return ["semantic_replan_review_stop_contract_id"], False

    return None


def compute_repaired_identity_upgrade_replan_guard_details(
    effective: Mapping[str, object],
    matching: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """Derive cleaned details for a broken identity_upgrade if parent proof holds."""

    if not has_orphaned_identity_upgrade_replan_guard(effective):
        return None
    details = effective.get("details")
    if not isinstance(details, Mapping):
        return None
    causal = _causal_parent_receipt(effective, matching)
    if causal is None:
        return None
    _, parent_details = causal
    resolution = _stale_fields_from_parent_overlay(details, parent_details)
    if resolution is None:
        return None
    stale_fields, clear_obligation = resolution
    repaired_details = dict(details)
    for field in stale_fields:
        repaired_details.pop(field, None)
    if clear_obligation:
        repaired_details["semantic_replan_obligation_id"] = ""
    repaired_details["settlement_receipt_revision"] = "identity_upgrade"
    if not is_valid_semantic_replan_guard_details(repaired_details):
        return None
    return repaired_details


def is_repairable_orphaned_identity_upgrade_receipt(
    effective: Mapping[str, object],
    matching: Sequence[Mapping[str, object]],
) -> bool:
    """Prove via causal parent lineage that identity_upgrade left stale guard fields."""

    return (
        compute_repaired_identity_upgrade_replan_guard_details(effective, matching)
        is not None
    )


def is_malformed_settlement_state_error(error: BaseException) -> bool:
    """Return True when TS settlement readback emitted ``malformed_settlement_state``."""

    return (
        isinstance(error, MalformedSettlementStateError)
        or getattr(error, "diagnostic_code", None) == "malformed_settlement_state"
    )


def append_repaired_identity_upgrade_receipt(
    log_path: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    effective: Mapping[str, object],
    repaired_details: Mapping[str, object],
    goal_ref: Mapping[str, object] | None,
) -> dict[str, object]:
    """Append a corrective quota_should_run receipt preserving settlement binding."""

    effective_details_value = effective.get("details")
    effective_details: Mapping[str, object] = (
        effective_details_value if isinstance(effective_details_value, Mapping) else {}
    )
    source_event_id = str(effective.get("event_id") or "").strip() or None
    repaired = build_rollout_event(
        goal_id=goal_id,
        event_kind="quota_should_run",
        goal_ref=goal_ref,
        agent_id=agent_id,
        todo_id=normalize_todo_id(
            repaired_details.get("todo_id") or effective_details.get("todo_id")
        ),
        run_id=turn_instance_id,
        status=str(effective.get("status") or "normal_run"),
        summary=(
            "heartbeat quota receipt repaired stale semantic replan guard "
            f"for turn={turn_instance_id}"
        ),
        source_event_id=source_event_id,
        caused_by=source_event_id,
        details=dict(repaired_details),
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
    """Append a same-binding cleanup event when identity_upgrade leaked parent guard."""

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
        repaired_details = compute_repaired_identity_upgrade_replan_guard_details(
            effective, matching
        )
        if repaired_details is None:
            return effective, False
        repaired = append_repaired_identity_upgrade_receipt(
            log_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_instance_id=turn_instance_id,
            effective=effective,
            repaired_details=repaired_details,
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
    """Repair all historical Turn receipts for one agent that leaked parent guards."""

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
            if effective is None or existing_identity is None:
                continue
            repaired_details = compute_repaired_identity_upgrade_replan_guard_details(
                effective, turn_events
            )
            if repaired_details is None:
                continue
            append_repaired_identity_upgrade_receipt(
                log_path,
                goal_id=goal_id,
                agent_id=agent_id,
                turn_instance_id=turn_id,
                effective=effective,
                repaired_details=repaired_details,
                goal_ref=goal_ref,
            )
            repaired_count += 1
        return repaired_count
