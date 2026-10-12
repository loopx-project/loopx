"""One read-only Todo source for report staging and approval retry.

Only the absence of a promotion fence permits Markdown parsing. A caller may
reuse the returned fields for frontier and fact selection without mixing heads.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.coordination.local_authority import (
    canonical_todo_summary_fields,
    read_canonical_todos_if_promoted,
)
from ...control_plane.todos.active_state_todo_parser import parse_active_state_todos
from ...history import load_index_snapshot, load_registry
from ...paths import resolve_runtime_root
from ...registry import find_registry_goal, resolve_state_file


def read_report_source_history(
    *, runtime_root: Path, goal_id: str, run: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Retain only rows appended through this exact committed refresh."""
    records = load_index_snapshot(
        runtime_root / "goals" / goal_id / "runs" / "index.jsonl",
        include_artifact_status=False,
    ).records
    positions = [
        i for i, row in enumerate(records)
        if row.get("generated_at") == run.get("generated_at")
        and row.get("settlement_identity") == run.get("settlement_identity")
        and row.get("json_path") == run.get("json_path")
    ]
    if len(positions) != 1:
        raise ValueError("original refresh history position is unavailable")
    # Append order also distinguishes later writes with an equal clock.
    return list(reversed(records[:positions[0] + 1]))


def read_report_todo_source(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: Path | None = None,
    state_path: Path | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return full evaluated summaries and retained User decision records.

    Archived decisions can still supersede an approval-pending receipt; they
    must not disappear just because a display stopped showing them.
    """
    registry = load_registry(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, Mapping):
        raise ValueError("periodic-report Goal is not registered")
    runtime_root = runtime_root or resolve_runtime_root(
        registry, None, registry_path=registry_path
    )
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=goal_id
    )
    if canonical is not None:
        fields = canonical_todo_summary_fields(
            canonical["todos"],
            rollout_events=rollout_events,
            available_capabilities=available_capabilities,
            goal_acceptance_contract=canonical.get("goal_acceptance_contract"),
            goal_acceptance_work_guards=canonical.get("goal_acceptance_work_guards"),
        )
        return fields, [row for row in canonical["todos"] if row.get("role") == "user"]
    state_path = state_path or resolve_state_file(
        Path(str(goal.get("repo") or "")).expanduser(),
        str(goal.get("state_file") or ""),
    )
    if state_path is None:
        raise ValueError("periodic-report active state is unavailable")
    fields = parse_active_state_todos(
        state_path.read_text(encoding="utf-8"),
        goal=dict(goal),
        state_path=state_path,
        item_limit=None,
        rollout_events=rollout_events,
        available_capabilities=available_capabilities,
    )
    # Legacy decision selection retains its existing active-section boundary.
    return fields, list((fields.get("user_todos") or {}).get("items") or [])
