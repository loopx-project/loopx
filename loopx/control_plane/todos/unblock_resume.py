from __future__ import annotations

from typing import Any, Callable

from .active_state_editing import section_bounds, todo_blocks
from .contract import normalize_todo_id


def require_completion_decision_outcome(
    completion_todo: dict[str, Any] | None,
    decision_outcome: str | None,
    *,
    materialized: bool,
) -> str | None:
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    # The shared plan validates its input even with no dependent rows. Python
    # transports the locked source fact; it does not own a second outcome rule.
    try:
        effect_runtime_result("todo.user_completion.plan", {
            "schema_version": "todo_user_completion_request_v0",
            "source": dict(completion_todo or {}), "todos": [],
            "decision_outcome": decision_outcome, "materialized": materialized,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    return decision_outcome


def _find_todo(
    lines: list[str],
    *,
    role: str,
    todo_id: str,
) -> dict[str, Any] | None:
    bounds = section_bounds(lines, role)
    if not bounds:
        return None
    start, end, section = bounds
    return next(
        (
            todo
            for todo in todo_blocks(
                lines,
                start,
                end,
                role=role,
                source_section=section,
            )
            if normalize_todo_id(todo.get("todo_id")) == todo_id
        ),
        None,
    )


def completion_decision_target(
    lines: list[str],
    completion_todo: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the agent Todo whose decision is resolved by this completion."""

    target_todo_id = normalize_todo_id(completion_todo.get("unblocks_todo_id"))
    if not target_todo_id:
        return None
    target = _find_todo(lines, role="agent", todo_id=target_todo_id)
    return {**target, "role": "agent"} if target else None


def apply_completed_user_todo_lifecycle(
    lines: list[str],
    *,
    completion_todo: dict[str, Any] | None,
    update_result: dict[str, Any],
    fallback_todo_id: str,
    decision_outcome: str | None,
    updated_at: str,
    apply_update: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Adapt the locked Markdown snapshot to the shared typed effect owner."""
    from ..effect_runtime import effect_runtime_result

    source = dict(completion_todo or {})
    source["todo_id"] = update_result.get("todo_id") or fallback_todo_id
    source["unblocks_todo_id"] = update_result.get("unblocks_todo_id")
    if source.get("role") != "user" or not source.get("unblocks_todo_id"):
        return None, None
    rows: list[dict[str, Any]] = []
    for role in ("agent", "user"):
        bounds = section_bounds(lines, role)
        if bounds:
            start, end, section = bounds
            rows.extend({**todo, "role": role} for todo in todo_blocks(
                lines, start, end, role=role, source_section=section,
            ))
    plan = effect_runtime_result("todo.user_completion.plan", {
        "schema_version": "todo_user_completion_request_v0",
        "source": source, "todos": rows, "decision_outcome": decision_outcome,
    })
    if not isinstance(plan, dict) or not isinstance(plan.get("updates"), dict):
        raise TypeError("invalid typed User completion plan")
    if plan["updates"]:
        apply_update(lines, todo_id=source["unblocks_todo_id"], role="agent",
                     updated_at=updated_at, **plan["updates"])
    return plan.get("unblock_resume"), plan.get("decision_scope_resolution")
