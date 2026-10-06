"""Fresh Todo readback; transport and presentation, not lifecycle mutation.

The compatibility facade re-exports this exact function. Decision consumers
read the full provider snapshot; filtering and limits remain explicit views.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from ...history import load_registry
from ...paths import resolve_runtime_root
from ...rollout_event_log import load_rollout_events, rollout_event_log_path
from ..goals.state_resolution import resolve_goal_state
from ..coordination.local_authority import (
    canonical_todo_items,
    canonical_todo_summary_fields,
    read_canonical_todos_if_promoted,
)
from .active_state_todo_parser import parse_todo_source
from .contract import normalize_todo_claimed_by, normalize_todo_id, normalize_todo_status
from .goal_todo_projection import (
    exact_archived_todo_summaries,
    retained_todo_summary_fields,
    goal_todo_summaries,
    todo_summaries_from_fields,
)
from .list_projection import (
    compact_agent_lane_todo_summary,
    compact_thin_todo_list_payload,
    todo_item_relations,
    todo_list_projection_contract,
)
from .todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL


def list_goal_todos(
    *,
    registry_path: Path,
    goal_id: str,
    role: str | None = None,
    status: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    runtime_root_arg: str | None = None,
    limit: int | None = None,
    thin: bool = False,
    read_scope: Literal["active", "completed_history"] = "active",
) -> dict[str, Any]:
    if read_scope not in {"active", "completed_history"}:
        raise ValueError("Todo read_scope must be active or completed_history")
    if read_scope == "completed_history" and (role != "agent" or status != "done"):
        raise ValueError("Completed history requires role=agent and status=done")
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    if todo_id and not normalized_todo_id:
        raise ValueError("todo_id must use the public token shape todo_<letters-digits-underscore-hyphen>")
    normalized_agent_id = normalize_todo_claimed_by(agent_id) if agent_id else None
    if agent_id and not normalized_agent_id:
        raise ValueError("agent_id must be a public-safe agent token such as codex-main-control")
    if limit is not None and limit < 1:
        raise ValueError("todo list --limit must be at least 1")
    registry = load_registry(registry_path)
    goal, resolved_project, resolved_state_file = resolve_goal_state(
        registry=registry,
        goal_id=goal_id,
        project_override=project,
        state_file_override=state_file,
    )
    if goal is None:
        raise ValueError(f"goal {goal_id!r} is not present in the registry")

    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_arg,
        registry_path=registry_path,
    )
    rollout_events = load_rollout_events(
        rollout_event_log_path(runtime_root, goal_id),
        limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
    )

    roles = [role] if role else ["user", "agent"]
    canonical_read = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    if canonical_read is not None:
        projected = todo_summaries_from_fields(
            fields=(retained_todo_summary_fields(
                canonical_todo_items(canonical_read["todos"]), rollout_events=rollout_events,
            ) if read_scope == "completed_history" else canonical_todo_summary_fields(
                canonical_read["todos"],
                rollout_events=rollout_events,
                goal_acceptance_contract=canonical_read.get("goal_acceptance_contract"),
                goal_acceptance_work_guards=canonical_read.get("goal_acceptance_work_guards"),
            )),
            source="file_authority",
            rollout_events=rollout_events,
            roles=roles,
            status=status,
            todo_id=normalized_todo_id,
            agent_id=normalized_agent_id,
            limit=limit,
        )
    else:
        if not resolved_state_file.exists():
            raise ValueError(f"active state file does not exist: {resolved_state_file}")
        state_text = resolved_state_file.read_text(encoding="utf-8")
        projected = goal_todo_summaries(
            goal,
            state_text=state_text,
            state_path=resolved_state_file,
            rollout_events=rollout_events,
            roles=roles,
            status=status,
            todo_id=normalized_todo_id,
            agent_id=normalized_agent_id,
            limit=limit,
            include_retained=read_scope == "completed_history",
        )
    if read_scope == "active" and normalized_todo_id and not projected.todos:
        if canonical_read is not None:
            archived_items = [
                item
                for item in canonical_todo_items(canonical_read["todos"])
                if item.get("archive_state") == "archive"
            ]
        else:
            _active_items, archived_items, _source_sections = parse_todo_source(
                state_text,
                goal=goal,
                state_path=resolved_state_file,
            )
        archived_projection = exact_archived_todo_summaries(
            archived_items=archived_items,
            source=projected.source,
            rollout_events=rollout_events,
            roles=roles,
            status=status,
            todo_id=normalized_todo_id,
            agent_id=normalized_agent_id,
            limit=limit,
        )
        if archived_projection is not None:
            projected = archived_projection
    source = projected.source
    summaries = projected.summaries
    todos = projected.todos
    # Exact cold reads restore source bytes after the shared summary owner has
    # evaluated identity/status/guards. List and thin projections stay bounded;
    # a hot summary is never treated as the original request.
    if normalized_todo_id and not thin and len(todos) == 1:
        if canonical_read is not None:
            source_items = canonical_read["todos"]
        else:
            active, archived, _sections = parse_todo_source(
                state_text, goal=goal, state_path=resolved_state_file,
            )
            source_items = [*active["user"], *active["agent"], *archived]
        detail = todos[0]
        matches = [item for item in source_items
            if item.get("todo_id") == normalized_todo_id
            and item.get("role") == detail.get("role")
            and item.get("archive_state", "active") == detail.get("archive_state", "active")]
        if len(matches) == 1:
            detail["text"] = str(matches[0].get("text") or "")
    unfiltered_count = projected.unfiltered_count
    uncapped_todo_count = projected.uncapped_todo_count

    matched_todo_count = len(todos)
    agent_lane_hot_path = bool(
        read_scope == "active" and normalized_agent_id and limit is None
        and role is None
        and status is None
        and normalized_todo_id is None
    )
    if agent_lane_hot_path:
        summaries = {
            key: compact_agent_lane_todo_summary(
                summary,
                role=key.removesuffix("_todos"),
            )
            for key, summary in summaries.items()
        }
        todos = [
            item
            for key in ("user_todos", "agent_todos")
            for item in summaries.get(key, {}).get("items") or []
            if isinstance(item, dict)
        ]

    matched_todo = todos[0] if len(todos) == 1 else None
    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "read_only": True,
        "command": "list",
        "goal_id": goal_id,
        "role": role or "all",
        "status_filter": normalize_todo_status(status) if status else None,
        "source": source,
        "todo_count": matched_todo_count,
        "todos": todos,
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
    }
    if canonical_read is not None:
        payload["authority_read"] = {
            "source_authority": canonical_read["source_authority"],
            "provider_revision": canonical_read.get("provider_revision"),
            "cursor": canonical_read.get("cursor"),
            "todo_read_model": canonical_read.get("todo_read_model"),
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }
    if normalized_agent_id:
        payload["agent_id_filter"] = normalized_agent_id
        payload["unfiltered_todo_count"] = unfiltered_count
        payload["filter_semantics"] = (
            "agent todos include unclaimed items plus claimed_by=<agent>; "
            "User gates use global_gate, then blocks_agent, then legacy claimed_by scope; "
            "User actions use bound_agent, then legacy claimed_by scope; unscoped items remain visible"
        )
    if agent_lane_hot_path:
        payload["returned_todo_count"] = len(todos)
        payload["todo_list_projection"] = todo_list_projection_contract(
            matched_todo_count=matched_todo_count,
            returned_todo_count=len(todos),
        )
    if limit is not None:
        payload["explicit_limit"] = limit
        payload["unfiltered_todo_count"] = unfiltered_count
        payload["returned_todo_count"] = len(todos)
        payload["todo_list_projection"] = todo_list_projection_contract(
            matched_todo_count=uncapped_todo_count,
            returned_todo_count=len(todos),
            view="explicit_limit_cold_path",
            item_limit_per_role=limit,
            full_detail_cold_paths=(
                "todo list without --limit",
                "active state",
            ),
        )
    if normalized_todo_id:
        payload["todo_id_filter"] = normalized_todo_id
        payload["matched"] = bool(todos)
        payload["todo"] = matched_todo
        payload["relations"] = todo_item_relations(matched_todo) if matched_todo else {}
        if len(todos) > 1:
            payload["ambiguous"] = True
        if not todos:
            payload["not_found"] = True
    payload.update(summaries)
    return compact_thin_todo_list_payload(payload) if thin else payload
