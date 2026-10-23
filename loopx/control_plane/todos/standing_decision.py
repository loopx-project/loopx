"""Legacy metadata decoding and one batch call to the typed receipt owner."""

from __future__ import annotations

from typing import Any

from ..effect_runtime import effect_runtime_result
from .contract import (
    normalize_todo_blocks_agent,
    normalize_todo_decision_outcome,
    normalize_todo_decision_scope,
    normalize_todo_global_gate,
    normalize_todo_id,
    normalize_todo_status,
)


def build_standing_decision_authority(
    user_items: list[dict[str, Any]] | None,
    *,
    legacy_source_order: bool = True,
    canonical_records: bool = False,
) -> dict[str, Any] | None:
    """Decode legacy metadata; canonical records are already decoded.

    Canonical IDs obey their provider contract, not the Markdown token codec.
    Both sources use the same typed eligibility and chronology owner.
    """
    items = standing_decision_source_items(user_items, canonical_records=canonical_records)
    if not items:
        return None
    result = effect_runtime_result(
        "todo.standing_decision.project",
        {
            "schema_version": "standing_decision_projection_request_v0",
            "items": items,
            "legacy_source_order": legacy_source_order and not canonical_records,
        },
    )
    if result is not None and (
        not isinstance(result, dict)
        or result.get("schema_version") != "standing_decision_authority_v0"
    ):
        raise ValueError("invalid typed standing decision projection")
    return result


def standing_decision_source_items(user_items, *, canonical_records=False):
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
