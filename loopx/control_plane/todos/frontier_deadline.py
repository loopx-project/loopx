from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from ..runtime.time import parse_timestamp

TODO_FRONTIER_DEADLINE_SCHEMA_VERSION = "todo_frontier_deadline_v0"
TODO_FRONTIER_DEADLINE_LANES = (
    "current_agent_claimed_monitor_items",
    "monitor_open_items",
    "gate_open_items",
    "deferred_resume_candidates",
    "current_agent_deferred_resume_candidates",
    "resume_blocked_items",
    "current_agent_monitor_blocked_resume_candidates",
    "current_agent_handoff_gates",
)


def _item_identity(item: dict[str, Any]) -> str:
    for key in ("todo_id", "target_key", "action_kind", "title", "text"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    index = item.get("index")
    return str(index) if index is not None else "frontier_transition"


def _item_source(item: dict[str, Any], *, lane: str) -> str:
    task_class = str(item.get("task_class") or "").strip()
    if task_class:
        return task_class
    action_kind = str(item.get("action_kind") or "").strip().lower()
    if "monitor" in action_kind:
        return "continuous_monitor"
    if "gate" in action_kind or "approval" in action_kind:
        return "user_gate"
    if "monitor" in lane:
        return "continuous_monitor"
    if "gate" in lane:
        return "user_gate"
    return "frontier_todo"


def todo_summary_frontier_deadline(
    summary: Any,
    *,
    current_time: datetime,
) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None

    projected = summary.get("frontier_deadline")
    if isinstance(projected, dict):
        projected_due_at = parse_timestamp(projected.get("next_due_at"))
        if projected_due_at is not None and projected_due_at > current_time:
            return dict(projected)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for lane in TODO_FRONTIER_DEADLINE_LANES:
        items = summary.get(lane)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            expires_at = parse_timestamp(item.get("expires_at"))
            if expires_at is not None and expires_at <= current_time:
                continue
            next_due_at = parse_timestamp(item.get("next_due_at"))
            if next_due_at is None or next_due_at <= current_time:
                continue
            identity = _item_identity(item)
            candidate_key = (identity, next_due_at.isoformat())
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            candidates.append(
                {
                    "identity": identity,
                    "source": _item_source(item, lane=lane),
                    "next_due_at": next_due_at.isoformat(),
                }
            )
    if not candidates:
        return None

    selected = min(candidates, key=lambda item: item["next_due_at"])
    return {
        "schema_version": TODO_FRONTIER_DEADLINE_SCHEMA_VERSION,
        **selected,
        "candidate_count": len(candidates),
    }


def build_frontier_recheck_plan(
    payload: dict[str, Any],
    *,
    current_time: datetime,
) -> dict[str, Any] | None:
    deadlines = [
        deadline
        for summary_key in ("agent_todo_summary", "user_todo_summary")
        if (
            deadline := todo_summary_frontier_deadline(
                payload.get(summary_key),
                current_time=current_time,
            )
        )
        is not None
    ]
    if not deadlines:
        return None

    # Projected deadlines retain their original ISO spelling. Agent/User
    # summaries can use different offsets, so compare actual instants.
    selected = min(deadlines, key=lambda item: parse_timestamp(item["next_due_at"]))
    next_due_at = parse_timestamp(selected["next_due_at"])
    if next_due_at is None:
        return None
    return {
        "frontier_recheck_after_seconds": max(
            1,
            math.ceil((next_due_at - current_time).total_seconds()),
        ),
        "frontier_recheck_source": selected["source"],
        "frontier_recheck_identity": selected["identity"],
        "frontier_recheck_candidate_count": sum(
            int(item.get("candidate_count") or 0) for item in deadlines
        ),
    }
