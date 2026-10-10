"""Current Todo details for scoped manager analysis, separate from run freshness."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .presentation.public_safety import redact_public_text, scan_public_boundary_text
from .todos import list_goal_todos
from .control_plane.effect_runtime import effect_runtime_result


def _text(value: object, limit: int = 420) -> str:
    text = redact_public_text(value, limit=limit)
    return text if scan_public_boundary_text(text)["ok"] else "[sensitive text omitted]"


def _safe_context(value: Any) -> Any:
    if isinstance(value, str):
        return _text(value, len(value))
    if isinstance(value, list):
        return [_safe_context(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_context(item) for key, item in value.items()}
    return value


def read_manager_goal_details(
    registry_path: Path, runtime_root: Path, goal_id: str, *, owner_scope: bool,
    limit: int = 48, completed_todo_ids: set[str] | None = None, offset: int = 0,
    todo_id: str | None = None,
) -> dict[str, Any]:
    """Use Core's canonical-first read; never parse a private project document."""
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        result = list_goal_todos(
            registry_path=registry_path, runtime_root_arg=str(runtime_root),
            goal_id=goal_id,
            **({"todo_id": todo_id} if todo_id else {}),
        )
        if result.get("ok") is not True:
            raise ValueError("Todo authority unavailable or conflicting")
        records = ([result["todo"]] if result.get("todo") else []) if todo_id else result.get("todos", [])
        page = effect_runtime_result("todo.context.page", {
            "records": records, "owner_scope": owner_scope, "offset": offset,
            "limit": limit, "todo_id": todo_id,
        }, large_local_snapshot=True)
        rows = _safe_context(page["todos"])
        completed = [r for r in records if r.get("status") == "done"
                     and (completed_todo_ids is None or r.get("todo_id") in completed_todo_ids)]
        revision = "sha256:" + hashlib.sha256(
            json.dumps(records, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return {
            "status": "read",
            "source": result.get("source"),
            "source_revision": revision,
            "observed_at": observed_at,
            "authority_revision": (result.get("authority_read") or {}).get("provider_revision"),
            "coverage": page["coverage"],
            "todos": rows,
            "completed_todos": [
                {"todo_id": _text(r.get("todo_id"), 160),
                 "title": _text(r.get("title") or r.get("text")),
                 "status": "done"}
                for r in completed
            ][-48:],
            "completed_coverage": {
                "known": sum(r.get("status") == "done" for r in records),
                "matched": len(completed),
                "included": min(48, len(completed)),
                "omitted": max(0, len(completed) - 48),
                "archive_read": False,
            },
            "limitations": [
                "These are currently declared Todo records, not proof of recent execution or renewed owner intent.",
                "Run-history age does not invalidate this independent Todo read. Do not infer deadlines from priority or list order.",
                "content_truncated marks an overview excerpt; read view=todos with goal_id and todo_id for the exact record. Resume conditions, lineage and decision scopes are recorded facts, not new authority or proof of readiness.",
            ],
        }
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {"status": "unavailable", "observed_at": observed_at,
                "todos": [], "coverage": {"active": None, "included": 0, "omitted": None}}
