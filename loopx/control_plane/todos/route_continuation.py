"""Legacy display/fact codec for the existing typed quota planning batch."""

from __future__ import annotations

from typing import Any

from .contract import normalize_todo_claimed_by, normalize_todo_excluded_agents
from .compact_projection import compact_todo_projection_item, projection_task_class
from .handoff_gate import todo_summary_handoff_gates
from .todo_semantics import todo_presentation_sort_key


def build_todo_route_continuation_facts(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Transport all observed rows; TS owns eligibility, deduplication and lanes."""
    groups: list[tuple[dict[str, Any], bool]] = []
    for key in ("route_continuation_replan_candidates", "route_continuation_candidates"):
        source = value.get(key)
        if isinstance(source, list):
            groups.extend((item, False) for item in source if isinstance(item, dict))
    groups.extend((item, True) for item in todo_summary_handoff_gates(value))
    rows: list[dict[str, Any]] = []
    for item, gate in groups:
        text = str(item.get("text") or item.get("title") or item.get("recommended_action")
                   or item.get("route_continuation_reason") or "").strip()
        identity = str(item.get("todo_id") or item.get("route_id") or item.get("route_key")
                       or item.get("index") or text)
        display = compact_todo_projection_item(item, text=text)
        flag = item.get("route_continuation_replan_required")
        rows.append({
            "display": display, "identity": identity, "gate": gate,
            "replan": flag if isinstance(flag, bool) else None,
            "task_class": projection_task_class(item) if item.get("task_class") is not None else None,
            "claim": normalize_todo_claimed_by(item.get("claimed_by")),
            "excluded": normalize_todo_excluded_agents(item.get("excluded_agents")),
            "sort": list(todo_presentation_sort_key(display)),
        })
    return rows
