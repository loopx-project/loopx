"""Decode source metadata for the composed typed Todo read projection."""

from __future__ import annotations

from typing import Any

from .contract import (
    normalize_todo_blocks_agent,
    normalize_todo_decision_outcome,
    normalize_todo_decision_scope,
    normalize_todo_global_gate,
    normalize_todo_id,
    normalize_todo_status,
)


def standing_decision_source_items(user_items: Any, *, canonical_records: bool = False) -> list[dict[str, Any]]:
    items = []
    for item in user_items or []:
        if not isinstance(item, dict):
            continue
        if canonical_records:
            items.append(
                {
                    key: item.get(key)
                    for key in (
                        "schema_version",
                        "todo_id",
                        "role",
                        "status",
                        "task_class",
                        "index",
                        "source_section",
                        "completed_at",
                        "updated_at",
                        "decision_scope",
                        "decision_outcome",
                        "global_gate",
                        "blocks_agent",
                        "unblocks_todo_id",
                    )
                }
            )
            continue
        items.append(
            {
                **{
                    key: item.get(key)
                    for key in (
                        "schema_version",
                        "index",
                        "source_section",
                        "task_class",
                        "completed_at",
                        "updated_at",
                    )
                },
                "todo_id": normalize_todo_id(item.get("todo_id")),
                "role": item.get("role") or "user",
                "status": normalize_todo_status(
                    item.get("status") or ("done" if item.get("done") else "open")
                ),
                "decision_scope": normalize_todo_decision_scope(
                    item.get("decision_scope")
                ),
                "decision_outcome": normalize_todo_decision_outcome(
                    item.get("decision_outcome")
                ),
                "global_gate": bool(
                    normalize_todo_global_gate(item.get("global_gate"))
                ),
                "blocks_agent": normalize_todo_blocks_agent(item.get("blocks_agent")),
                # Even a malformed exact link is not an unlinked broad decision.
                "unblocks_todo_id": item.get("unblocks_todo_id"),
            }
        )
    return items
