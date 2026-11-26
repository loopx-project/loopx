"""Optional display selection over one complete registry inspection."""

from __future__ import annotations

from collections.abc import Mapping
import unicodedata


def _search_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def select_registry_goals(
    payload: dict[str, object],
    *,
    goal_id: str | None,
    query: str | None,
    limit: int | None,
    offset: int | None,
) -> dict[str, object]:
    if goal_id is None and query is None and limit is None and offset is None:
        return payload
    page_size = 20 if limit is None else limit
    start = 0 if offset is None else offset
    if page_size < 1 or start < 0:
        raise ValueError("--limit must be positive and --offset must be non-negative")
    if goal_id is not None and not goal_id.strip():
        raise ValueError("--goal-id must not be empty")
    term = _search_text(query).strip() if query is not None else None
    if term is not None and not term:
        raise ValueError("--query must contain non-whitespace text")
    # An unavailable source must retain its original failure, not become an
    # apparently successful empty search or an invented missing-Goal error.
    if "goals" not in payload:
        return payload
    raw_goals = payload.get("goals")
    goals = (
        [row for row in raw_goals if isinstance(row, Mapping)]
        if isinstance(raw_goals, list)
        else []
    )
    if goal_id is not None and not any(row.get("id") == goal_id for row in goals):
        raise ValueError(f"Goal is not present in this registry: {goal_id}")
    matches = [
        row
        for row in goals
        if (goal_id is None or row.get("id") == goal_id)
        and (
            term is None
            or any(
                term in _search_text(row.get(field))
                for field in ("id", "repo", "domain", "status", "adapter_kind")
            )
        )
    ]
    selected = matches[start : start + page_size]
    end = start + len(selected)
    # Keep global health/provenance and collision diagnostics intact. Selection
    # narrows the displayed Goal rows, never the inspection or its authority.
    return {
        **payload,
        "goals": selected,
        "selection": {
            "goal_id": goal_id,
            "query": query,
            "offset": start,
            "limit": page_size,
            "matched_goal_count": len(matches),
            "returned_goal_count": len(selected),
            "next_offset": end if end < len(matches) else None,
        },
    }
