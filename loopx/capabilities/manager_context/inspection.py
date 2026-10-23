"""On-demand manager reads from the existing scoped Core projections."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...chat_manager_details import read_manager_goal_details
from ...chat_manager_history import delivery_history_window, read_manager_delivery_history
from ...control_plane.effect_runtime import effect_runtime_result


TOOL_NAME = "loopx_manager_read"
READ_TOOL = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Read authorized LoopX Core evidence on demand: the global Goal portfolio, "
        "registered Agent responsibilities, one Goal's current Todos, recorded deliveries, or handoff receipt status. Use view=agents to search before reporting a missing worker; delivery targets are not the discovery inventory. Use view=agent_route with exact goal_id and agent_id to observe a binding on the selected source host before asking the user for a task link. A readable route is not execution readiness or delivery authority. "
        "For a current-work overview, start with the index's bounded Agent Todo summaries; "
        "use view=portfolio for source and coverage, and view=todos for needed detail. "
        "Delivery history answers past results, not live activity. "
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
                "enum": ["sources", "portfolio", "todos", "deliveries", "handoffs", "agents", "agent_route"],
            },
            "source_id": {"type": "string", "description": "Default local. For SSH use an exact source_id from view=sources; local Goal IDs do not discover remote Goals."},
            "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "Deliveries lookback; expand for latest known progress older than yesterday."},
            "window_start": {"type": "string", "description": "Local deliveries only: exact inclusive ISO-8601 timestamp with offset. Pair with window_end instead of days, e.g. a frozen report window."},
            "window_end": {"type": "string", "description": "Local deliveries only: exact exclusive ISO-8601 timestamp with offset. Boundaries do not move while paging."},
            "expected_source_revision": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$", "description": "Local deliveries only: copy source.source_revision from the first page onto subsequent pages. A conflicting response requires restarting this read, not merging pages from different indexes."},
            "delivery_ref": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$", "description": "Local deliveries only: use an observed row's source_ref to recover its full permitted recorded_details and evidence references within the same read window/revision. Does not read referenced artifacts or prove the recorded claims."},
            "goal_id": {"type": "string"},
            "agent_id": {"type": "string", "minLength": 1,
                         "description": "Agent_route only, with goal_id: exact registered Agent to locate. Search view=agents first; does not delegate or create an execution driver."},
            "thread_link": {"type": "string", "minLength": 1,
                            "description": "Agent_route only: optional exact host task link already supplied by the user. Do not guess a link to resolve ambiguous bindings."},
            "host_surface": {"type": "string", "minLength": 1,
                             "description": "Agent_route only: optionally narrow to an observed binding's exact host surface. This does not select another machine or grant delivery."},
            "todo_id": {"type": "string", "minLength": 1,
                        "description": "Todos only, with goal_id: read one exact record including its complete permitted text. Recover content_truncated excerpts; a completed record does not authorize new work."},
            "query": {"type": "string", "maxLength": 200, "description": "Agents only: case-insensitive text match on identity and declared responsibility. Omit to browse all permitted registrations."},
            "request_id": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
                "description": "Handoffs only: exact request receipt ID.",
            },
            "include_stopped": {
                "type": "boolean",
                "description": "Portfolio/agents: include stopped Goals for an explicit historical question.",
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
    "Use view=agents to find registered peers, then view=agent_route with exact goal_id and agent_id to observe an existing local host binding. A route is not execution readiness. "
    "Paginate with next_offset. No cross-Goal access, shell, writes or execution authority."}


# The published tool schema is the contract the caller sees, so the reader takes
# its allowlist and ranges from there instead of restating them in prose that can
# drift from what a caller was offered.
_READ_PROPERTIES = READ_TOOL["inputSchema"]["properties"]
READ_ARGUMENT_NAMES = tuple(_READ_PROPERTIES)
READ_VIEWS = tuple(_READ_PROPERTIES["view"]["enum"])
READ_LIMIT_RANGE = (
    _READ_PROPERTIES["limit"]["minimum"],
    _READ_PROPERTIES["limit"]["maximum"],
)
READ_DAYS_RANGE = (
    _READ_PROPERTIES["days"]["minimum"],
    _READ_PROPERTIES["days"]["maximum"],
)


def rejected_read_arguments(arguments: dict[str, Any]) -> list[str]:
    """Name every argument that keeps a manager read from running.

    The caller is a model that can repair its own tool call, but only when the
    refusal says which argument is wrong and what the tool accepts. Each entry
    is ``<argument>:<what it must be>`` so the correction is mechanical instead
    of a guess against a bare ``invalid_arguments``.
    """

    rejected = [
        f"unknown_argument:{name}"
        for name in sorted(set(arguments) - set(READ_ARGUMENT_NAMES))
    ]
    view = arguments.get("view")
    if view not in READ_VIEWS:
        rejected.append("view:must_be_one_of_" + ",".join(READ_VIEWS))
    if "request_id" in arguments and view != "handoffs":
        rejected.append("request_id:only_for_view_handoffs")
    for name in ("agent_id", "thread_link", "host_surface"):
        if name in arguments:
            if view != "agent_route":
                rejected.append(f"{name}:only_for_view_agent_route")
            elif not isinstance(arguments[name], str) or not arguments[name].strip():
                rejected.append(f"{name}:must_be_a_nonempty_string")
    if view == "agent_route":
        for name in ("goal_id", "agent_id"):
            if not arguments.get(name):
                rejected.append(f"agent_route:requires_{name}")
        for name in ("offset", "limit"):
            if name in arguments:
                rejected.append(f"{name}:not_for_view_agent_route")
    if "todo_id" in arguments:
        if view != "todos":
            rejected.append("todo_id:only_for_view_todos")
        elif not isinstance(arguments["todo_id"], str) or not arguments["todo_id"]:
            rejected.append("todo_id:must_be_a_nonempty_string")
        elif not arguments.get("goal_id"):
            rejected.append("todo_id:requires_goal_id")
    if "include_stopped" in arguments:
        if view not in {"portfolio", "agents"}:
            rejected.append("include_stopped:only_for_view_portfolio_or_agents")
        elif type(arguments["include_stopped"]) is not bool:
            rejected.append("include_stopped:must_be_a_boolean")
    if "query" in arguments:
        if view != "agents":
            rejected.append("query:only_for_view_agents")
        elif not isinstance(arguments["query"], str) or len(arguments["query"]) > 200:
            rejected.append("query:must_be_a_string_at_most_200_characters")
    offset = arguments.get("offset", 0)
    if type(offset) is not int or offset < 0:
        rejected.append("offset:must_be_an_integer_at_least_0")
    limit = arguments.get("limit", 8)
    if type(limit) is not int or not READ_LIMIT_RANGE[0] <= limit <= READ_LIMIT_RANGE[1]:
        rejected.append(
            "limit:must_be_an_integer_between_"
            f"{READ_LIMIT_RANGE[0]}_and_{READ_LIMIT_RANGE[1]}"
        )
    goal_id = arguments.get("goal_id")
    if goal_id is not None and not isinstance(goal_id, str):
        rejected.append("goal_id:must_be_a_string")
    if "days" in arguments:
        days = arguments["days"]
        if view != "deliveries":
            rejected.append("days:only_for_view_deliveries")
        elif type(days) is not int or not READ_DAYS_RANGE[0] <= days <= READ_DAYS_RANGE[1]:
            rejected.append(
                "days:must_be_an_integer_between_"
                f"{READ_DAYS_RANGE[0]}_and_{READ_DAYS_RANGE[1]}"
            )
    if not isinstance(arguments.get("source_id", "local"), str):
        rejected.append("source_id:must_be_a_string")
    exact_arguments = {"window_start", "window_end", "expected_source_revision", "delivery_ref"} & arguments.keys()
    for name in sorted(exact_arguments):
        if view != "deliveries":
            rejected.append(f"{name}:only_for_view_deliveries")
        elif arguments.get("source_id", "local") != "local":
            rejected.append(f"{name}:only_for_source_local")
    if {"window_start", "window_end"} & arguments.keys():
        if "days" in arguments:
            rejected.append("days:cannot_combine_with_exact_window")
        try:
            if not all(isinstance(arguments.get(name), str) for name in ("window_start", "window_end")):
                raise ValueError("two timestamps required")
            delivery_history_window(arguments.get("window_start"), arguments.get("window_end"))
        except ValueError:
            rejected.append("window_start,window_end:require_two_offset_timestamps_increasing_at_most_90_days")
        if ((type(offset) is int and offset > 0) or "delivery_ref" in arguments) and "expected_source_revision" not in arguments:
            rejected.append("expected_source_revision:required_for_exact_window_pagination")
    for name in ("expected_source_revision", "delivery_ref"):
        if name in arguments and (not isinstance(arguments[name], str)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", arguments[name])):
            rejected.append(f"{name}:must_be_a_sha256_revision")
    if "delivery_ref" in arguments and offset != 0:
        rejected.append("delivery_ref:requires_offset_0")
    return rejected


def agent_work_summary(agent: dict[str, Any]) -> dict[str, Any]:
    """Keep the existing quota projection small; never infer live presence."""
    verified = agent.get("source_verified") is True
    todos = agent.get("todos", []) if verified else []
    todos = [todo for todo in todos if isinstance(todo, dict)]
    count = agent.get("todo_count_in_projection", len(todos))
    count = max(count, len(todos)) if type(count) is int else len(todos)
    selected: list[dict[str, Any]] = []
    for todo in todos[:3]:
        # The portfolio already normalizes/redacts these fields. Keep that
        # vocabulary and order, without copying raw records or receipt bodies.
        selected.append({
            key: value[:240] if isinstance(value, str) else None
            for key in ("todo_id", "title", "status", "priority", "claimed_by",
                        "readiness", "next_safe_action")
            if (value := todo.get(key)) is not None
        })
        if todo.get("content_truncated") is True or any(
            isinstance(todo.get(key), str) and len(todo[key]) > 240 for key in selected[-1]
        ):
            selected[-1]["content_truncated"] = True
    return {
        "agent_id": agent.get("agent_id"),
        "source_verified": verified,
        "waiting_on": agent.get("waiting_on") if verified else None,
        "owner_gate_ids": agent.get("owner_gate_ids", [])[:8] if verified else [],
        "todo_count_in_projection": count if verified else None,
        "todos_omitted": count - len(selected) if verified else None,
        "todos": selected,
    }


def manager_index(context: dict[str, Any]) -> dict[str, Any]:
    """A small directory, never a second mutable progress store."""
    read_tool = CONTEXT_TOOL_NAME if context.get("scope") == "owner_goal" else TOOL_NAME
    execution = context.get("context_execution")
    # Only the prompt directory uses excerpts. The context and scoped reader
    # retain complete records, source quality and the original authority.
    goals = context.get("goals", [])
    previews = effect_runtime_result(
        "presentation.goal_attention.bound",
        {"goals": [{"goal_id": row.get("goal_id"), "attention": row.get("attention")}
                   for row in goals], "preview": True},
    )["goals"]
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
                **(
                    {"goal_instance_id": row["goal_instance_id"]}
                    if row.get("goal_instance_id")
                    else {}
                ),
                "description": row.get("description"),
                "activation_state": row.get("activation_state", "unknown"),
                "quality": row.get("quality"),
                "source": {key: (row.get("source") or {}).get(key)
                           for key in ("revision", "latest_recorded_at")},
                "agent_coverage": row.get("agent_coverage"),
                "agents": [agent_work_summary(agent) for agent in row.get("agents", [])],
                "progress": row.get("progress"),
                "lifecycle_phase": _lifecycle_phase(row.get("goal_lifecycle")),
                "attention": preview.get("attention") or {
                    "status": "unavailable", "items": [], "reason": "goal_not_read",
                },
                "details": "use_" + read_tool,
            }
            for row, preview in zip(goals, previews, strict=True)
            if row.get("activation_state") != "stopped"
        ],
        "context_delegation": context.get("context_delegation"),
        **({"context_execution": execution} if isinstance(execution, dict)
           and (execution.get("bindings") or execution.get("available") is False) else {}),
        "evidence_sources": context.get("evidence_sources", [])[:12],
        "evidence_source_count": len(context.get("evidence_sources", [])),
        "agent_discovery": {"tool": read_tool, "view": "agents", "scope": "permitted_registry",
                            "independent_of_delivery_targets": True, "route_view": "agent_route"},
        "read_tool": read_tool,
        "agent_work_note": (
            "Agent Todos are bounded declared work from this portfolio snapshot, not live activity "
            "or completed results. Respect each Goal's quality, recorded timestamp and Agent coverage. "
            "An empty or unverified projection does not prove idle or stopped. For a current-work "
            "question, answer the known overview first; read only missing or requested detail."
        ),
        "attention_note": (
            "Attention is a directory preview, not complete decision terms or an action grant. "
            "Resolve read_reference with the scoped read tool before deciding or acting; "
            "details_omitted and content_truncated never prove complete evidence."
        ),
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
        discovery_scope: Callable[[], list[str] | None] | None = None,
        delegation_authority: Callable[[], dict] | None = None,
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
        self.discovery_scope = discovery_scope
        self.delegation_authority = delegation_authority

    def sources(self):
        if self.context.get("scope") == "owner_goal":
            return [{"source_id": "local", "source_host": "local", "status": "available"}]
        from .ssh_evidence import sources
        return sources(self.runtime_root, self.channel_id, self.owner_scope, self.ssh_config_path)

    def _registered_goals(self) -> list[dict[str, Any]]:
        from ...history import decode_registry_snapshot
        inventory = decode_registry_snapshot(self.registry_path, self.registry_path.read_bytes())["goals"]
        if not isinstance(inventory, list) or any(not isinstance(g, dict) or not isinstance(g.get("id"), str) for g in inventory):
            raise ValueError("Goal inventory is unreadable")
        return inventory

    def _goal_scope(self) -> list[str] | None:
        ids = (self.discovery_scope() if self.discovery_scope else
               None if self.owner_scope and self.context.get("scope") == "owner_global" else
               [r["goal_id"] for r in self.context.get("goals", [])])
        if ids is None:
            if not (self.owner_scope and self.context.get("scope") == "owner_global"):
                raise ValueError("current audience scope unavailable")
        elif not isinstance(ids, list) or any(not isinstance(g, str) for g in ids):
            raise ValueError("invalid audience scope")
        return ids

    def _portfolio(self, ids, goal_id, include_stopped, offset, limit):
        from ...control_plane.goals.activation import goal_activation_state
        from ...chat_manager_context import manager_turn_context
        try:
            inventory = self._registered_goals()
            permitted = sorted({g["id"] for g in inventory if ids is None or g["id"] in ids})
            if goal_id is not None:
                permitted = [goal_id] if goal_id in permitted else []
            elif not include_stopped:
                stopped = set()
                for g in inventory:
                    if g["id"] not in permitted:
                        continue
                    try:
                        if goal_activation_state(g).value == "stopped":
                            stopped.add(g["id"])
                    except ValueError:
                        pass  # Unknown activation stays visible as a source gap.
                permitted = [gid for gid in permitted if gid not in stopped]
            selected = permitted[offset:offset + limit]
            context = manager_turn_context(
                self.registry_path,
                {"channel_id": self.channel_id or ("manager" if self.owner_scope else "manager.external.inspection")},
                self.runtime_root, authorized_goal_ids=ids, requested_goal_ids=selected,
                include_stopped=include_stopped or goal_id is not None, include_details=False,
            ) if selected else {"goals": [], "coverage": {"complete": True}}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            return {"ok": False, "view": "portfolio", "error": "goal_inventory_unavailable"}
        page = context.get("goals", [])
        end = offset + len(selected)
        return {"ok": True, "view": "portfolio", "goal_id": goal_id, "rows": page,
                "offset": offset, "included": len(page), "matched": len(permitted),
                "next_offset": end if end < len(permitted) else None,
                "source": {"source": "goal_portfolio", "snapshot_id": context.get("snapshot_id"),
                           "coverage": context.get("coverage")},
                "unknown": bool(context.get("warnings")) or len(page) != len(selected),
                "initial_snapshot_id": self.context.get("snapshot_id"),
                "source_id": "local", "source_host": "local"}

    def read(self, tool: str, arguments: Any) -> dict[str, Any]:
        if tool not in {TOOL_NAME, CONTEXT_TOOL_NAME} or not isinstance(arguments, dict):
            return {"ok": False, "error": "unsupported_read_tool"}
        rejected = rejected_read_arguments(arguments)
        if rejected:
            return {
                "ok": False,
                "error": "invalid_arguments",
                "rejected_arguments": rejected,
                "allowed_arguments": list(READ_ARGUMENT_NAMES),
                "allowed_views": list(READ_VIEWS),
                "detail": (
                    f"resend {tool} with only the allowed arguments; each rejected "
                    "entry names the argument and what it must be"
                ),
            }
        view, goal_id = arguments.get("view"), arguments.get("goal_id")
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 8)
        include_stopped = arguments.get("include_stopped", False)
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
            if not source_id.startswith("ssh:") or view == "handoffs" or (view not in {"portfolio", "agents"} and not goal_id):
                return {"ok": False, "error": "invalid_remote_read"}
            from .ssh_evidence import read_remote
            result = read_remote(self.runtime_root, self.channel_id, self.owner_scope, arguments,
                                 self.scope_valid, config_path=self.ssh_config_path,
                                 **({"runner": self.remote_runner} if self.remote_runner else {}))
            self.record(result)
            return result
        try:
            ids = self._goal_scope()
        except ValueError:
            return {"ok": False, "error": "authorization_changed"}
        if goal_id is not None and ids is not None and goal_id not in ids:
            return {"ok": False, "error": "goal_outside_available_scope"}
        if view in {"agents", "agent_route"}:
            if goal_id is not None:
                ids = [goal_id]
            if view == "agent_route":
                from ...control_plane.collaboration.peer_host_route import resolve_peer_host_route

                result = resolve_peer_host_route(self.registry_path, goal_id=goal_id,
                                                agent_id=arguments["agent_id"],
                                                thread_link=arguments.get("thread_link"),
                                                host_surface=arguments.get("host_surface"))
                if not self.scope_valid():
                    return {"ok": False, "error": "authorization_changed"}
                self.record(result)
                return result
            from .discovery import agent_page

            grant = self.delegation_authority() if self.delegation_authority else self.context.get("context_delegation")
            result = agent_page(self.registry_path, goal_ids=ids, query=arguments.get("query", ""),
                                include_stopped=include_stopped, offset=offset, limit=limit, delegation=grant)
            if not self.scope_valid():
                return {"ok": False, "error": "authorization_changed"}
            self.record(result)
            return result
        if view not in {"portfolio", "handoffs"} and not goal_id:
            return {"ok": False, "error": "goal_outside_available_scope"}
        if view != "portfolio":
            if ids is None:
                try:
                    ids = [g["id"] for g in self._registered_goals()]
                except (OSError, ValueError, KeyError, TypeError):
                    return {"ok": False, "error": "goal_inventory_unavailable"}
            if goal_id is not None and goal_id not in ids:
                return {"ok": False, "error": "goal_outside_available_scope"}
        portfolio_unknown = False
        if view == "portfolio":
            result = self._portfolio(ids, goal_id, include_stopped, offset, limit)
            if not result.get("ok"):
                return result
            page, source, matched = result["rows"], result["source"], result["matched"]
            portfolio_unknown = result["unknown"]
        elif view == "handoffs":
            from .tracking import query

            try:
                source = query(
                    self.runtime_root,
                    self.registry_path,
                    goal_ids=[goal_id] if goal_id else ids,
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
                todo_id=arguments.get("todo_id"),
            )
            page = source.pop("todos", [])
            # Completed title joins remain available through the delivery view.
            source.pop("completed_todos", None)
            matched = source.get("coverage", {}).get("active")
        else:
            source = read_manager_delivery_history(
                self.runtime_root, goal_id, limit=limit, offset=offset, lookback_days=arguments.get("days", 1),
                **{name: arguments[name] for name in ("window_start", "window_end", "expected_source_revision", "delivery_ref")
                   if name in arguments},
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
            # A delivery's task can be beyond the overview page. Recover only
            # its exact canonical record; never mistake the first page's cap
            # for an absent task, or treat a completed record as new authority.
            for todo_id in {row.get("todo_id") for row in page} - titles.keys():
                if not todo_id:
                    continue
                exact = read_manager_goal_details(self.registry_path, self.runtime_root, goal_id,
                    owner_scope=self.owner_scope, todo_id=todo_id)
                for row in exact.get("todos", []):
                    titles[row["todo_id"]] = row.get("title")
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
            "unknown": matched is None or portfolio_unknown
            or ("window_start" in arguments and (
                source.get("window_complete") is False
                or bool(source.get("coverage", {}).get("invalid_delivery_records"))
                or bool(source.get("coverage", {}).get("invalid_index_records"))
                or bool(oversized)))
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
