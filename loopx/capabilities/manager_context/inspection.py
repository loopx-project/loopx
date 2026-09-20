"""On-demand manager reads from the existing scoped Core projections."""

from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...chat_manager_details import read_manager_goal_details
from ...chat_manager_history import read_manager_delivery_history


TOOL_NAME = "loopx_manager_read"
READ_TOOL = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Read authorized LoopX Core evidence on demand: the global Goal portfolio, "
        "one Goal's current Todos, recorded deliveries, or handoff receipt status. "
        "Every portfolio row carries its Goal lifecycle readback: reached milestones with "
        "their evidence refs and the phase (starting/qualifying/waiting_owner/closing/closed), "
        "or a typed unavailable gap naming why it could not be derived. Use that to state where "
        "the Goal stands before listing detail. Use concrete evidence "
        "to answer progress and priority questions. Paginate with next_offset. "
        "No shell, writes, raw files, or additional Goal authorization."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "view": {
                "type": "string",
                "enum": ["sources", "portfolio", "todos", "deliveries", "handoffs"],
            },
            "source_id": {"type": "string", "description": "Default local. For SSH use an exact source_id from view=sources; local Goal IDs do not discover remote Goals."},
            "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "Deliveries lookback; expand for latest known progress older than yesterday."},
            "goal_id": {"type": "string"},
            "request_id": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
                "description": "Handoffs only: exact request receipt ID.",
            },
            "include_stopped": {
                "type": "boolean",
                "description": "Portfolio only: include stopped Goals for an explicit historical question.",
            },
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
        },
        "required": ["view"],
    },
}

# Existing manager threads retain their registered tool name. New project
# conversations use a neutral name with the same reader, schema and limits.
CONTEXT_TOOL_NAME = "loopx_context_read"
CONTEXT_READ_TOOL = {**deepcopy(READ_TOOL), "name": CONTEXT_TOOL_NAME,
    "description": "Read this conversation's authorized Goal, Todos, deliveries and handoff receipts. "
    "The Goal row carries its lifecycle readback: reached milestones with evidence refs and the phase "
    "(starting/qualifying/waiting_owner/closing/closed), or a typed unavailable gap naming why. "
    "Paginate with next_offset. No cross-Goal access, shell, writes or execution authority."}


def manager_index(context: dict[str, Any]) -> dict[str, Any]:
    """A small directory, never a second mutable progress store."""
    read_tool = CONTEXT_TOOL_NAME if context.get("scope") == "owner_goal" else TOOL_NAME
    return {
        "schema_version": "manager_evidence_index_v1",
        "snapshot_id": context.get("snapshot_id"),
        "collected_at": context.get("collection_completed_at"),
        "coverage": context.get("coverage"),
        "scope": context.get("scope"),
        "warnings": context.get("warnings", []),
        "stopped_goals_excluded": sum(
            r.get("activation_state") == "stopped" for r in context.get("goals", [])
        ),
        "goals": [
            {
                "goal_id": row["goal_id"],
                "description": row.get("description"),
                "activation_state": row.get("activation_state", "unknown"),
                "quality": row.get("quality"),
                "progress": row.get("progress"),
                "lifecycle_phase": _lifecycle_phase(row.get("goal_lifecycle")),
                "details": "use_" + read_tool,
            }
            for row in context.get("goals", [])
            if row.get("activation_state") != "stopped"
        ],
        "context_delegation": context.get("context_delegation"),
        "evidence_sources": context.get("evidence_sources", [])[:12],
        "evidence_source_count": len(context.get("evidence_sources", [])),
        "read_tool": read_tool,
    }


def _lifecycle_phase(readback: Any) -> str | None:
    """The derived phase, or None when the readback names a gap instead.

    A derived projection always carries a phase string, so None means "not
    derived here", never "this Goal has no phase". The portfolio view carries
    the typed reason next to the row's existing `quality`.
    """

    if not isinstance(readback, dict) or readback.get("status") == "unavailable":
        return None
    phase = readback.get("lifecycle_phase")
    return phase if isinstance(phase, str) and phase else None


class ManagerInspection:
    def __init__(
        self,
        *,
        context: dict[str, Any],
        registry_path: Path,
        runtime_root: Path,
        owner_scope: bool,
        scope_valid: Callable[[], bool],
        record: Callable[[dict[str, Any]], None],
        channel_id: str | None = None,
        remote_runner=None,
        ssh_config_path=None,
    ) -> None:
        self.context = context
        self.registry_path = registry_path
        self.runtime_root = runtime_root
        self.owner_scope = owner_scope
        self.scope_valid = scope_valid
        self.record = record
        self.channel_id = channel_id
        self.remote_runner = remote_runner
        self.ssh_config_path = ssh_config_path

    def sources(self):
        if self.context.get("scope") == "owner_goal":
            return [{"source_id": "local", "source_host": "local", "status": "available"}]
        from .ssh_evidence import sources
        return sources(self.runtime_root, self.channel_id, self.owner_scope, self.ssh_config_path)

    def read(self, tool: str, arguments: Any) -> dict[str, Any]:
        if tool not in {TOOL_NAME, CONTEXT_TOOL_NAME} or not isinstance(arguments, dict):
            return {"ok": False, "error": "unsupported_read_tool"}
        if set(arguments) - {
            "view",
            "goal_id",
            "offset",
            "limit",
            "include_stopped",
            "request_id",
            "source_id",
            "days",
        }:
            return {"ok": False, "error": "invalid_arguments"}
        view, goal_id = arguments.get("view"), arguments.get("goal_id")
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 8)
        include_stopped = arguments.get("include_stopped", False)
        if (
            view not in {"sources", "portfolio", "todos", "deliveries", "handoffs"}
            or ("request_id" in arguments and view != "handoffs")
            or type(include_stopped) is not bool
            or ("include_stopped" in arguments and view != "portfolio")
            or type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 12
            or (goal_id is not None and not isinstance(goal_id, str))
            or ("days" in arguments and (view != "deliveries" or type(arguments["days"]) is not int or not 1 <= arguments["days"] <= 90))
            or not isinstance(arguments.get("source_id", "local"), str)
        ):
            return {"ok": False, "error": "invalid_arguments"}
        if not self.scope_valid():
            return {"ok": False, "error": "authorization_changed"}
        source_id = arguments.get("source_id", "local")
        if self.context.get("scope") == "owner_goal" and source_id != "local":
            return {"ok": False, "error": "source_outside_available_scope"}
        if view == "sources":
            rows = self.sources()
            if not self.scope_valid():
                return {"ok": False, "error": "authorization_changed"}
            result = {"ok": True, "view": view, "rows": rows[offset:offset + limit],
                      "matched": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None,
                      "note": "Configured sources are not yet read. Select source_id for remote evidence; an empty local host_id does not imply missing remote Goals."}
            self.record(result)
            return result
        if source_id != "local":
            if not source_id.startswith("ssh:") or view == "handoffs" or (view != "portfolio" and not goal_id):
                return {"ok": False, "error": "invalid_remote_read"}
            from .ssh_evidence import read_remote
            result = read_remote(self.runtime_root, self.channel_id, self.owner_scope, arguments,
                                 self.scope_valid, config_path=self.ssh_config_path,
                                 **({"runner": self.remote_runner} if self.remote_runner else {}))
            self.record(result)
            return result
        goals = {r["goal_id"]: r for r in self.context.get("goals", [])}
        if (goal_id is not None and goal_id not in goals) or (
            view not in {"portfolio", "handoffs"} and not goal_id
        ):
            return {"ok": False, "error": "goal_outside_available_scope"}
        if not self.scope_valid():
            return {"ok": False, "error": "authorization_changed"}
        if view == "portfolio":
            rows = list(goals.values()) if goal_id is None else [goals[goal_id]]
            if goal_id is None and not include_stopped:
                rows = [r for r in rows if r.get("activation_state") != "stopped"]
            source = {
                "source": "goal_portfolio",
                "snapshot_id": self.context.get("snapshot_id"),
                "coverage": self.context.get("coverage"),
            }
            page = rows[offset : offset + limit]
            matched = len(rows)
        elif view == "handoffs":
            from .tracking import query

            try:
                source = query(
                    self.runtime_root,
                    self.registry_path,
                    goal_ids=[goal_id] if goal_id else list(goals),
                    owner_scope=self.owner_scope,
                    channel_id=self.channel_id,
                    request_id=arguments.get("request_id"),
                    offset=offset,
                    limit=limit,
                )
            except (OSError, ValueError, TypeError):
                return {"ok": False, "error": "handoff_query_unavailable_or_invalid"}
            page = source.pop("rows")
            matched = source.pop("matched")
        elif view == "todos":
            source = read_manager_goal_details(
                self.registry_path,
                self.runtime_root,
                goal_id,
                owner_scope=self.owner_scope,
                limit=limit,
                offset=offset,
            )
            page = source.pop("todos", [])
            # Completed title joins remain available through the delivery view.
            source.pop("completed_todos", None)
            matched = source.get("coverage", {}).get("active")
        else:
            source = read_manager_delivery_history(
                self.runtime_root, goal_id, limit=limit, offset=offset, lookback_days=arguments.get("days", 1)
            )
            page = source.pop("deliveries", [])
            matched = source.get("coverage", {}).get("matched")
            details = read_manager_goal_details(
                self.registry_path,
                self.runtime_root,
                goal_id,
                owner_scope=self.owner_scope,
                completed_todo_ids={r.get("todo_id") for r in page},
            )
            titles = {
                r["todo_id"]: r.get("title")
                for r in details.get("todos", []) + details.get("completed_todos", [])
            }
            page = [{**r, "todo_title": titles.get(r.get("todo_id"))} for r in page]
        if not self.scope_valid():
            return {"ok": False, "error": "authorization_changed"}
        # Trim whole rows, never malformed JSON or undisclosed byte truncation.
        while len(page) > 1 and len(json.dumps(page, ensure_ascii=False)) > 24000:
            page.pop()
        oversized = []
        for i, row in enumerate(page):
            if len(json.dumps(row, ensure_ascii=False)) > 24000:
                oversized.append(offset + i)
                page[i] = {
                    "status": "oversized_record",
                    "row_index": offset + i,
                    "goal_id": row.get("goal_id"),
                    "todo_id": row.get("todo_id"),
                    "details": "omitted_due_to_size",
                }
        end = offset + len(page)
        result = {
            "ok": True,
            "view": view,
            "goal_id": goal_id,
            "source": source,
            "rows": page,
            "offset": offset,
            "included": len(page),
            "matched": matched,
            "next_offset": end
            if isinstance(matched, int) and page and end < matched
            else None,
            "unknown": matched is None
            or (
                view == "handoffs"
                and (
                    not source["coverage"]["scan_complete"]
                    or not source["coverage"]["legacy_audience_scan_complete"]
                    or bool(source["coverage"]["unreadable"])
                )
            ),
            "oversized_rows": oversized,
            "initial_snapshot_id": self.context.get("snapshot_id"),
            "source_id": "local",
            "source_host": "local",
        }
        self.record(result)
        return result
