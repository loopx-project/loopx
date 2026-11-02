"""Reconcile a canonical Explore result source from a public-safe candidate log."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .result_log import (
    EVENT_KIND_EDGE,
    EVENT_KIND_FINDING,
    EVENT_KIND_NODE,
    PUBLIC_BOUNDARY,
    _append_validated_explore_result_events,
    build_explore_result_projection,
    load_explore_result_events_strict,
    validate_explore_result_event_attribution,
)


SCHEMA_VERSION = "loopx_explore_source_history_reconcile_v0"
_TABLE_BY_EVENT_KIND = {
    EVENT_KIND_NODE: "nodes",
    EVENT_KIND_EDGE: "edges",
    EVENT_KIND_FINDING: "findings",
}
_PROJECTION_COLLECTIONS = (
    ("nodes", "node_id"),
    ("edges", "edge_id"),
    ("findings", "finding_id"),
)


def _digest(values: Sequence[str] | set[str]) -> str | None:
    normalized = sorted(set(values))
    if not normalized:
        return None
    encoded = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _source_ref_digest(path: Path) -> str:
    return hashlib.sha256(str(path.expanduser().resolve()).encode("utf-8")).hexdigest()[:16]


def _projection_result_keys(projection: Mapping[str, Any], *, goal_id: str) -> set[str]:
    keys: set[str] = set()
    for table_key, result_id_key in _PROJECTION_COLLECTIONS:
        for item in projection.get(table_key) or []:
            if not isinstance(item, Mapping):
                continue
            result_id = str(item.get(result_id_key) or "").strip()
            if result_id:
                keys.add(f"{goal_id}:{table_key}:{result_id}")
    return keys


def _projection(events: Sequence[Mapping[str, Any]], *, goal_id: str) -> dict[str, Any]:
    return build_explore_result_projection(
        events,
        goal_id=goal_id,
        finding_limit=len(events),
        mermaid_node_limit=max(1, len(events)),
    )


def _by_table(keys: set[str], *, goal_id: str) -> dict[str, int]:
    prefix = f"{goal_id}:"
    counts: Counter[str] = Counter()
    for key in keys:
        suffix = key.removeprefix(prefix)
        counts[suffix.split(":", 1)[0]] += 1
    return dict(sorted(counts.items()))


def _latest_by_result_id(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        result_id = str(event.get("result_id") or "").strip()
        if result_id:
            latest[result_id] = dict(event)
    return latest


def _direct_projection_key(event: Mapping[str, Any], *, goal_id: str) -> str:
    table = _TABLE_BY_EVENT_KIND[str(event["event_kind"])]
    return f"{goal_id}:{table}:{event['result_id']}"


def _materialized_parent_edge_keys(projection: Mapping[str, Any], *, goal_id: str) -> set[str]:
    return {
        f"{goal_id}:edges:{edge['edge_id']}"
        for edge in projection.get("edges") or []
        if isinstance(edge, Mapping) and edge.get("materialized_from") == "node_parent_id" and edge.get("edge_id")
    }


def reconcile_explore_source_history(
    *,
    canonical_log_path: Path,
    candidate_log_path: Path,
    goal_id: str,
    registered_result_keys: Sequence[str] | set[str],
    execute: bool = False,
) -> dict[str, Any]:
    """Recover registered public-safe results without deleting remote rows.

    Only the latest canonical event for a result id absent from the canonical
    source is eligible. Its direct projection key must already exist in the
    registered sink baseline. Derived rows that remain absent are classified,
    never synthesized or deleted by this non-destructive command.
    This selected subset is an ordinary append, not an archival prefix replay:
    contradictory latest findings remain in the candidate source and are
    reported as blocked, while eligible results can still be recovered.
    """

    canonical_events = load_explore_result_events_strict(canonical_log_path, goal_id=goal_id)
    candidate_events = load_explore_result_events_strict(candidate_log_path, goal_id=goal_id)
    registered = {
        str(key)
        for key in registered_result_keys
        if any(str(key).startswith(f"{goal_id}:{table}:") for table in _TABLE_BY_EVENT_KIND.values())
    }
    if not registered:
        raise ValueError("Explore source reconciliation requires a registered sink baseline")

    canonical_projection = _projection(canonical_events, goal_id=goal_id)
    candidate_projection = _projection(candidate_events, goal_id=goal_id)
    canonical_keys = _projection_result_keys(canonical_projection, goal_id=goal_id)
    candidate_keys = _projection_result_keys(candidate_projection, goal_id=goal_id)
    missing_before = registered - canonical_keys

    canonical_result_ids = {str(event.get("result_id") or "") for event in canonical_events}
    candidate_latest = _latest_by_result_id(candidate_events)
    missing_events = [
        event
        for result_id, event in candidate_latest.items()
        if result_id not in canonical_result_ids and _direct_projection_key(event, goal_id=goal_id) in missing_before
    ]
    missing_events.sort(
        key=lambda event: (
            str(event.get("recorded_at") or ""),
            str(event.get("event_id") or ""),
        )
    )

    selected_events = []
    blocked_event_ids: set[str] = set()
    blocked_keys: set[str] = set()
    blocked_reasons: Counter[str] = Counter()
    for event in missing_events:
        try:
            validate_explore_result_event_attribution(event)
        except ValueError as error:
            # Both complete sources already passed structural validation.
            # Reuse the writer's stricter eligibility check for this subset;
            # runtime failures must propagate rather than look like bad data.
            blocked_event_ids.add(str(event["event_id"]))
            blocked_keys.add(_direct_projection_key(event, goal_id=goal_id))
            blocked_reasons[str(error)] += 1
        else:
            selected_events.append(event)

    planned_events = [*canonical_events, *selected_events]
    planned_projection = _projection(planned_events, goal_id=goal_id)
    planned_keys = _projection_result_keys(planned_projection, goal_id=goal_id)
    newly_projected = planned_keys - canonical_keys
    unexpected_new = newly_projected - registered
    if unexpected_new:
        raise ValueError("candidate history would add projection rows outside the registered baseline")

    recovered = missing_before & planned_keys
    remaining = registered - planned_keys
    stale_parent_edges = remaining & _materialized_parent_edge_keys(candidate_projection, goal_id=goal_id)
    unresolved_orphans = remaining - stale_parent_edges - blocked_keys
    selected_event_ids = {str(event.get("event_id") or "") for event in selected_events}

    writeback = {
        "performed": False,
        "requested_event_count": len(selected_events),
        "appended_event_count": 0,
        "reused_event_count": 0,
        "readback_verified": None,
    }
    post_keys = canonical_keys
    if execute and selected_events:
        # The registered missing subset passed structural and attribution
        # preflight; it is not an archival prefix. Bind this plan to the
        # canonical snapshot under lock.
        append_receipt = _append_validated_explore_result_events(
            canonical_log_path,
            selected_events,
            expected_goal_id=goal_id,
            expected_event_ids=[str(event["event_id"]) for event in canonical_events],
        )
        post_events = load_explore_result_events_strict(canonical_log_path, goal_id=goal_id)
        post_event_ids = {str(event.get("event_id") or "") for event in post_events}
        post_projection = _projection(post_events, goal_id=goal_id)
        post_keys = _projection_result_keys(post_projection, goal_id=goal_id)
        readback_verified = bool(
            selected_event_ids <= post_event_ids
            and planned_keys <= post_keys
            and not (registered & planned_keys) - post_keys
        )
        if not readback_verified:
            raise RuntimeError("Explore source reconciliation readback failed")
        writeback.update(
            {
                "performed": True,
                "appended_event_count": int(append_receipt["appended_event_count"]),
                "reused_event_count": int(append_receipt["reused_event_count"]),
                "readback_verified": True,
            }
        )

    effective_keys = post_keys if execute else planned_keys
    effective_remaining = registered - effective_keys
    if selected_events:
        status = "reconciled" if execute else "would_reconcile"
        if blocked_event_ids:
            status += "_with_blocked_history"
    elif blocked_event_ids:
        status = "blocked"
    else:
        status = "already_reconciled"
    if effective_remaining - blocked_keys:
        status += "_with_remote_orphans"

    warnings = [
        f"Recovery excludes {count} historical result(s): {reason}"
        for reason, count in sorted(blocked_reasons.items())
    ]
    if warnings:
        warnings.append("Append a corrected latest record to the candidate history, then preview recovery again.")

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "goal_id": goal_id,
        "execute": bool(execute),
        "status": status,
        "source_refs": {
            "canonical_sha256_16": _source_ref_digest(canonical_log_path),
            "candidate_sha256_16": _source_ref_digest(candidate_log_path),
            "raw_paths_returned": False,
        },
        "source_counts": {
            "canonical_event_count": len(canonical_events),
            "candidate_event_count": len(candidate_events),
            "candidate_result_count": len(candidate_latest),
        },
        "plan": {
            "selected_event_count": len(selected_events),
            "selected_event_ids_sha256_16": _digest(selected_event_ids),
            "blocked_event_count": len(blocked_event_ids),
            "blocked_event_ids_sha256_16": _digest(blocked_event_ids),
            "blocked_reasons": dict(sorted(blocked_reasons.items())),
            "latest_event_per_missing_result": True,
            "raw_history_copied": False,
        },
        "projection_reconciliation": {
            "canonical_result_count": len(canonical_keys),
            "candidate_result_count": len(candidate_keys),
            "registered_result_count": len(registered),
            "missing_before_count": len(missing_before),
            "missing_before_by_table": _by_table(missing_before, goal_id=goal_id),
            "recovered_lost_history_count": len(recovered),
            "recovered_lost_history_by_table": _by_table(recovered, goal_id=goal_id),
            "recovered_lost_history_sha256_16": _digest(recovered),
            "remaining_registered_result_count": len(effective_remaining),
            "remaining_registered_by_table": _by_table(effective_remaining, goal_id=goal_id),
            "remaining_registered_sha256_16": _digest(effective_remaining),
            "unexpected_new_result_count": len(unexpected_new),
        },
        "classification": {
            "lost_history_recovered_count": len(recovered),
            "blocked_history_count": len(blocked_keys),
            "stale_materialized_parent_edge_count": len(stale_parent_edges),
            "unresolved_orphan_count": len(unresolved_orphans),
            "remote_deletion_required": bool(effective_remaining - blocked_keys),
            "remote_deletion_performed": False,
        },
        "writeback": writeback,
        **({"warnings": warnings} if warnings else {}),
        "boundary": {
            **PUBLIC_BOUNDARY,
            "candidate_events_strictly_validated": True,
            "registered_baseline_only": True,
            "destructive_remote_action_performed": False,
        },
    }
