from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ...control_plane.todos.active_state_todo_parser import parse_active_state_todos
from ...control_plane.todos.todo_semantics import todo_item_is_actionable_open
from ...registry import find_registry_goal, read_json, resolve_state_file
from .incremental import select_incremental_project_progress


def _stage_timestamp(value: str) -> datetime | None:
    """Parse an offset-aware ISO-8601 timestamp or reject the value.

    Sibling validations (``_validated_snapshot_timestamp``,
    ``_actual_work_window``, ``incremental._timestamp``) all reject naive
    timestamps, so stage filtering must not compare offset-naive and
    offset-aware datetimes either.
    """

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _outcome_completed_at(
    item: Mapping[str, Any], *, stage_time: datetime
) -> str | None:
    """Return the durable completion timestamp an outcome fact may carry.

    A done todo without a trustworthy completion timestamp (handwritten
    agent-lane entries may omit them) must not become an outcome fact: the
    frozen fact would fail timestamp validation on every consumption retry.
    """

    raw = str(item.get("completed_at") or item.get("updated_at") or "").strip()
    parsed = _stage_timestamp(raw)
    if parsed is None or parsed > stage_time:
        return None
    return raw


_META_ACTION_KINDS = frozenset(
    {
        "consume_periodic_report_intent",
        "repair_periodic_report_intent_consumption",
        "repair_periodic_report_editorial",
    }
)


def build_project_progress_snapshot(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    completed_at: str,
    publication_cursor: Mapping[str, Any] | None = None,
    goal_cursors: Sequence[Mapping[str, Any]] | None = None,
    available_capabilities: Any = None,
    rollout_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build a bounded public-safe progress snapshot at a stage boundary."""

    registry = read_json(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, Mapping):
        raise ValueError("periodic-report Goal is not registered")
    repo = Path(str(goal.get("repo") or "")).expanduser()
    state_path = resolve_state_file(repo, str(goal.get("state_file") or ""))
    if state_path is None or not state_path.is_file():
        raise ValueError("periodic-report active state is unavailable")
    return build_project_progress_snapshot_from_state(
        state_text=state_path.read_text(encoding="utf-8"),
        goal=dict(goal),
        state_path=state_path,
        goal_id=goal_id,
        agent_id=agent_id,
        completed_at=completed_at,
        publication_cursor=publication_cursor,
        goal_cursors=goal_cursors,
        available_capabilities=available_capabilities,
        rollout_events=rollout_events,
    )


def build_project_progress_snapshot_from_state(
    *,
    state_text: str,
    goal: Mapping[str, Any],
    state_path: Path,
    goal_id: str,
    agent_id: str,
    completed_at: str,
    publication_cursor: Mapping[str, Any] | None = None,
    goal_cursors: Sequence[Mapping[str, Any]] | None = None,
    available_capabilities: Any = None,
    rollout_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build a progress snapshot from one already-read authoritative state.

    Evidence is selected for the Goal, not for the calling lane: every Agent's
    eligible rows are reportable and ``agent_id`` only ranks the reporting
    Agent's own rows first, so a multi-Agent Goal does not lose peer progress
    and the bounded outcome cap never evicts the reporter's own outcomes. A row
    no Agent claimed has no producer and stays out of the report.

    Resume-gated todos are judged with the same typed resume evidence the
    scheduler consumes: ``rollout_events`` feeds ``pr_merged`` gates and
    ``available_capabilities`` feeds ``capacity_available`` gates. Callers
    that cannot supply authoritative evidence omit both, and unsatisfied
    external gates stay excluded (fail-closed) instead of being guessed.
    """

    parsed = parse_active_state_todos(
        state_text,
        goal=dict(goal),
        state_path=state_path,
        item_limit=None,
        rollout_events=rollout_events,
        available_capabilities=available_capabilities,
    )
    agent_summary = parsed.get("agent_todos")
    items = agent_summary.get("items") if isinstance(agent_summary, Mapping) else []
    stage_time = _stage_timestamp(completed_at)
    if stage_time is None:
        raise ValueError("periodic-report stage completion timestamp is invalid")

    def produced_by(item: Mapping[str, Any]) -> str:
        """Return the Agent whose lane produced this row, or empty for none."""

        return str(item.get("claimed_by") or "").strip()

    def not_after_stage(item: Mapping[str, Any]) -> bool:
        raw = str(item.get("updated_at") or item.get("completed_at") or "").strip()
        if not raw:
            return True
        parsed = _stage_timestamp(raw)
        if parsed is None:
            return False
        return parsed <= stage_time

    done = [
        dict(item)
        for item in items or []
        if isinstance(item, Mapping)
        and item.get("status") == "done"
        and produced_by(item)
        and not_after_stage(item)
        and str(item.get("action_kind") or "") not in _META_ACTION_KINDS
    ]
    # Tier order must not disturb recency order inside a tier, so the lane sort
    # runs last over the already-newest-first list.
    done.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    done.sort(key=lambda item: produced_by(item) != agent_id)
    progress_items: list[dict[str, Any]] = []
    for index, item in enumerate(done):
        outcome_completed_at = _outcome_completed_at(item, stage_time=stage_time)
        if outcome_completed_at is None:
            continue
        summary = " ".join(
            str(
                item.get("evidence") or item.get("note") or item.get("text") or ""
            ).split()
        )
        title = " ".join(str(item.get("text") or "Completed project work").split())
        progress_items.append(
            {
                "item_id": f"completed_{index + 1}",
                "title": title[:240],
                "summary": summary[:360] or "Validated completion is durably recorded.",
                "content_kind": "outcome",
                "value_rank": 10 + index,
                "source_ref": f"todo:{item.get('todo_id')}",
                "completed_at": outcome_completed_at,
            }
        )
    open_items = [
        dict(item)
        for item in items or []
        if isinstance(item, Mapping)
        and todo_item_is_actionable_open(dict(item))
        and produced_by(item)
        and not_after_stage(item)
        and item.get("task_class") != "continuous_monitor"
        and item.get("action_kind")
        not in {
            "consume_periodic_report_intent",
            "repair_periodic_report_intent_consumption",
        }
    ]
    open_items.sort(key=lambda item: produced_by(item) != agent_id)
    if open_items:
        next_item = open_items[0]
        progress_items.append(
            {
                "item_id": "next_action",
                "title": "Next action",
                "summary": " ".join(str(next_item.get("text") or "").split())[:360],
                "content_kind": "next_action",
                "value_rank": 90,
                "source_ref": f"todo:{next_item.get('todo_id')}",
            }
        )
    if not progress_items:
        return None
    snapshot: dict[str, Any] = {
        "schema_version": "periodic_report_project_progress_projection_v0",
        "goal_id": goal_id,
        "observed_at": completed_at,
        "language": "zh-CN",
        "items": progress_items,
    }
    if publication_cursor is not None or goal_cursors:
        # A lane that has never published still must not re-announce what the
        # Goal already announced, so the peer baseline applies without one.
        incremental = select_incremental_project_progress(
            snapshot,
            cursor=publication_cursor,
            goal_cursors=goal_cursors,
        )
        if incremental is None:
            return None
        snapshot = incremental
    outcome_items = [
        item for item in snapshot["items"] if item.get("content_kind") == "outcome"
    ][:6]
    next_items = [
        item for item in snapshot["items"] if item.get("content_kind") == "next_action"
    ][:1]
    snapshot["items"] = outcome_items + next_items
    return snapshot


__all__ = [
    "build_project_progress_snapshot",
    "build_project_progress_snapshot_from_state",
]
