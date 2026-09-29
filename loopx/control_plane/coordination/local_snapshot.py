"""Normalize current local Todo records for the typed authority bridge.

This is the behavior-preserving edge between the existing Markdown/JSON
writers and the provider-neutral domain model.  It performs no I/O and owns no
authority decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..todos.contract import (
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_claimed_by,
    normalize_todo_decision_scope,
    normalize_todo_excluded_agents,
    normalize_todo_id,
    normalize_todo_required_decision_scopes,
)
from .authority_core import TodoSnapshot


def _scope_identity(scope: Any) -> tuple[str, str, str] | None:
    normalized = normalize_todo_decision_scope(scope)
    if not normalized:
        return None
    return (
        normalized["kind"],
        normalized["granularity"],
        normalized["scope_key"],
    )


def todo_snapshot_from_mapping(
    todo: Mapping[str, Any] | None,
    *,
    infer_status_from_done: bool = False,
) -> TodoSnapshot | None:
    if todo is None:
        return None
    status = str(todo.get("status") or "").strip().lower()
    if not status and infer_status_from_done:
        status = "done" if todo.get("done") is True else "open"
    required_scopes = frozenset(
        identity
        for scope in normalize_todo_required_decision_scopes(
            todo.get("required_decision_scopes")
        )
        if (identity := _scope_identity(scope)) is not None
    )
    return TodoSnapshot(
        todo_id=normalize_todo_id(todo.get("todo_id")) or "",
        status=status,
        role=str(todo.get("role") or ""),
        task_class=str(todo.get("task_class") or "") or None,
        claimed_by=normalize_todo_claimed_by(todo.get("claimed_by")),
        excluded_agents=frozenset(
            normalize_todo_excluded_agents(todo.get("excluded_agents"))
        ),
        bound_agent=normalize_todo_bound_agent(todo.get("bound_agent")),
        blocks_agent=normalize_todo_blocks_agent(todo.get("blocks_agent")),
        decision_scope=_scope_identity(todo.get("decision_scope")),
        required_decision_scopes=required_scopes,
        unblocks_todo_id=normalize_todo_id(todo.get("unblocks_todo_id")),
    )
