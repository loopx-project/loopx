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
from .contract import normalize_todo_claimed_by, normalize_todo_id, normalize_todo_status
from .todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
from .resume_condition import TodoResumeRolloutEvents




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
    json_references: bool = False,
) -> dict[str, Any]:
    if read_scope not in {"active", "completed_history"}:
        raise ValueError("Todo read_scope must be active or completed_history")
    if read_scope == "completed_history" and (role != "agent" or status != "done"):
        raise ValueError("Completed history requires role=agent and status=done")
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    if todo_id and not normalized_todo_id:
        raise ValueError("todo_id must use the public token shape todo_<letters-digits-underscore-hyphen>")
    if normalized_todo_id and thin:
        raise ValueError("Exact Todo reads return full requirements; remove --thin or omit --todo-id for a bounded list")
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

    rollout_events = TodoResumeRolloutEvents(rollout_events, runtime_root=runtime_root, goal_id=goal_id)

    from ..coordination.local_authority import CanonicalAuthorityRequired, LocalCoordinationAuthorityUnavailable
    from .goal_todo_projection import project_todo_read
    from ..runtime.time import now_utc

    now = now_utc()
    params = {"kind": "query",
        "source": {"runtime_root": str(runtime_root.expanduser().resolve(strict=False)), "goal_id": goal_id},
        "rollout_events": list(rollout_events), "rollout_event_source": rollout_events.source,
        "observed_at": now.timestamp(), "evaluated_at": now.isoformat(),
        "query": {"goal_id": goal_id, "role": role, "status": normalize_todo_status(status),
            "todo_id": normalized_todo_id, "agent_id": normalized_agent_id, "limit": limit,
            "read_scope": read_scope, "json_references": json_references, "thin": thin,
            "state_file": str(resolved_state_file), "project": str(resolved_project) if resolved_project else None}}
    result = project_todo_read(params)
    if "payload" not in result:
        failure = result["canonical"]
        code = str(failure.get("reason_code") or "local_authority_todo_list_unavailable")
        if code == "canonical_authority_required":
            raise CanonicalAuthorityRequired(goal_id=goal_id, operation="todo_read", payload=failure)
        payload = {"source_authority": "unknown_fail_closed"} if code == "local_authority_mode_read_failed" else failure
        raise LocalCoordinationAuthorityUnavailable(str(failure.get("reason") or "canonical Todo authority is unavailable"),
            code=code, payload=payload)
    payload = result["payload"]
    if "canonical_acceptance" in result:
        from .goal_todo_projection import observe_todo_acceptance
        payload = observe_todo_acceptance(payload, result["canonical_acceptance"],
            registry_path=registry_path, goal_id=goal_id)
    return payload
