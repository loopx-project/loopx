"""Current Todo details for scoped manager analysis, separate from run freshness."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .presentation.public_safety import redact_public_text, scan_public_boundary_text
from .todos import list_goal_todos


def _text(value: object, limit: int = 420) -> str:
    text = redact_public_text(value, limit=limit)
    return text if scan_public_boundary_text(text)["ok"] else "[sensitive text omitted]"


def read_manager_goal_details(
    registry_path: Path, runtime_root: Path, goal_id: str, *, owner_scope: bool,
    limit: int = 48, completed_todo_ids: set[str] | None = None, offset: int = 0,
) -> dict[str, Any]:
    """Use Core's canonical-first read; never parse a private project document."""
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        result = list_goal_todos(
            registry_path=registry_path, runtime_root_arg=str(runtime_root),
            goal_id=goal_id,
        )
        if result.get("ok") is not True:
            raise ValueError("Todo authority unavailable or conflicting")
        records = result.get("todos", [])
        active = [r for r in records if r.get("status") in {"open", "blocked", "deferred"}]
        # Owner decisions first, then declared priority; do not invent urgency.
        active.sort(key=lambda r: (r.get("role") != "user", str(r.get("priority") or "Z")))
        rows = []
        for record in active[offset:offset + limit]:
            row = {
                k: _text(record[k], 160)
                for k in ("todo_id", "role", "status", "priority", "task_class",
                          "claimed_by", "bound_agent", "blocks_agent", "unblocks_todo_id",
                          "action_kind", "next_due_at", "expires_at")
                if record.get(k) is not None
            }
            row["title"] = _text(record.get("title") or record.get("text"))
            if owner_scope:
                row["continuation"] = _text(record.get("note") or record.get("continuation_hint"), 280)
            rows.append(row)
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
            "coverage": {"active": len(active), "included": len(rows),
                         "omitted": max(0, len(active) - len(rows))},
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
            ],
        }
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {"status": "unavailable", "observed_at": observed_at,
                "todos": [], "coverage": {"active": None, "included": 0, "omitted": None}}
