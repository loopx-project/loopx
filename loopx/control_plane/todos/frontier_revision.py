"""Compact, complete revisions for Todo advancement frontiers."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

from ..effect_runtime import effect_runtime_result
# Refs #4447: the todo contract owns this vocabulary; import it instead of
# restating the literal in every module that classifies a Todo.
from .contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
)
from .todo_semantics import todo_item_is_actionable_open, todo_item_task_class


TODO_FRONTIER_REVISION_SCHEMA_VERSION = "todo_frontier_revision_v0"
TODO_FRONTIER_REVISION_INDEX_SCHEMA_VERSION = "todo_frontier_revision_index_v0"

FRONTIER_REVISION_FIELDS = (
    "todo_id",
    "status",
    "done",
    "title",
    "text",
    "task_class",
    "claimed_by",
    "bound_agent",
    "blocks_agent",
    "excluded_agents",
    "priority",
    "action_kind",
    "task_domain",
    "task_repository",
    "capability_binding_ref",
    "required_capabilities",
    "target_capabilities",
    "target_key",
    "continuation_policy",
    "removed_continuation_policy",
    "decision_scope",
    "required_decision_scopes",
    "decision_outcome",
    "replan_obligation_id",
    "unblocks_todo_id",
    "depends_on_todo_id",
    "depends_on_todo_ids",
    "resume_when",
    "no_followup",
    "successor_todo_ids",
    "completion_continuation",
)


def frontier_source_facts(
    source_items: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | dict[str, str] | None:
    """Legacy codecs only; TS selects lanes and builds complete revision identity."""
    if not isinstance(source_items, list):
        return None
    rows = [
        {
            "id": str(item.get("todo_id") or "").strip(),
            "claim": normalize_todo_claimed_by(item.get("claimed_by")),
            "excluded": normalize_todo_excluded_agents(item.get("excluded_agents")),
            "updated": str(item.get("updated_at") or item.get("completed_at") or "").strip(),
            "advancement": todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT,
            "actionable": todo_item_is_actionable_open(item),
            "serialized": json.dumps(
                {key: item[key] for key in FRONTIER_REVISION_FIELDS if item.get(key) is not None},
                ensure_ascii=True, separators=(",", ":"), sort_keys=True,
            ),
        }
        for item in source_items if isinstance(item, dict)
    ]
    # Lossless transport codec only: never truncate material identity or raise
    # the shared Effect request limit for large history/frontier reads.
    raw = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode()
    if len(raw) < 512 * 1024:
        return rows
    return {"encoding": "deflate-base64-json-v0",
            "data": base64.b64encode(zlib.compress(raw)).decode("ascii")}


def _request(operation: str, **facts: Any) -> dict[str, Any]:
    result = effect_runtime_result("todo.frontier_revision.project", {
        "schema_version": "todo_frontier_revision_request_v0",
        "operation": operation, **facts,
    })
    if not isinstance(result, dict):
        raise TypeError("typed frontier revision response must be an object")
    return result


def _checkpoint_tuple(value: Any) -> tuple[str | None, str | None, bool] | None:
    if value is None:
        return None
    if value["complete"]:
        return value["frontier_revision"], value["frontier_updated_at"], True
    return None, None, False


def selectable_advancement_frontier_revision(
    source_items: list[dict[str, Any]] | None, *, agent_id: str | None,
) -> tuple[str | None, str | None, bool]:
    result = _checkpoint_tuple(_request("select", rows=frontier_source_facts(source_items),
        agent_id=normalize_todo_claimed_by(agent_id))["checkpoint"])
    assert result is not None
    return result


def build_advancement_frontier_revision_index(
    source_items: list[dict[str, Any]],
) -> dict[str, Any]:
    index = _request("index", rows=frontier_source_facts(source_items)).get("index")
    if not isinstance(index, dict):
        raise TypeError("typed frontier revision response index must be an object")
    return index


def attach_advancement_frontier_revision_index(
    summary: dict[str, Any], source_items: list[dict[str, Any]], *, role: str | None,
) -> None:
    if role == "agent":
        summary["advancement_frontier_revision_index"] = build_advancement_frontier_revision_index(source_items)


def advancement_frontier_revision_from_index(
    value: Any, *, agent_id: str | None,
) -> tuple[str | None, str | None, bool] | None:
    return _checkpoint_tuple(_request("read", index=value,
        agent_id=normalize_todo_claimed_by(agent_id))["checkpoint"])
