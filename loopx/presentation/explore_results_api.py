"""Loopback read adapter for the canonical Explore result projection."""

from urllib.parse import parse_qs, urlparse
import subprocess

from ..control_plane.todos.list_readback import list_goal_todos
from ..control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable

from ..capabilities.explore.result_log import (
    build_explore_result_projection,
    explore_history_rows,
    explore_result_log_path,
    load_explore_result_events_strict,
    validate_explore_result_event,
)


def _result_rows(runtime_root, goal_id, registry_path):
    events = load_explore_result_events_strict(
        explore_result_log_path(runtime_root, goal_id), goal_id=goal_id,
    )
    # Reuse canonical active/history readback; an association is not adoption.
    todos = {}
    try:
        for view in ({}, {"status": "done", "read_scope": "completed_history"}):
            for todo in list_goal_todos(
                registry_path=registry_path, runtime_root_arg=str(runtime_root),
                goal_id=goal_id, role="agent", **view,
            )["todos"]:
                todos[todo["todo_id"]] = todo
    except LocalCoordinationAuthorityUnavailable:
        # Task authority is independent of the verified evidence log. Reuse
        # the source reader's unavailable-associations view; never present a
        # partial list as complete or fall back to legacy task state.
        return _finding_rows(events, None, goal_id=goal_id)
    return _finding_rows(events, todos.values(), goal_id=goal_id)


def _finding_rows(events, todos, *, goal_id):
    """Same Explore projection and task associations for every physical source."""
    events = [validate_explore_result_event(event, expected_goal_id=goal_id) for event in events]
    projection = build_explore_result_projection(
        events, goal_id=goal_id, finding_limit=len(events),
    )
    nodes = {node["node_id"]: node for node in projection["nodes"]}
    links = {}
    for todo in todos or []:
        for node_id in todo.get("explore_result_node_refs") or []:
            links.setdefault(node_id, []).append({
                "todo_id": todo["todo_id"], "text": todo["text"],
                "status": todo["status"], "claimed_by": todo.get("claimed_by") or "",
            })
    return [
        {**finding, "linked_todos": links.get(finding["node_id"], []),
         **({"linked_todos_available": False} if todos is None else {}),
         "question": nodes.get(finding["node_id"], {}).get("title", ""),
         "scope": nodes.get(finding["node_id"], {}).get("summary", "")}
        for finding in projection["findings"]
    ]


def _history_rows(runtime_root, goal_id):
    """Adapt scoped canonical records without folding away earlier attempts.

    This is transport adaptation, not a graph, progress or assessment owner.
    In particular, do not expose arbitrary observation payloads or materialize
    parent links as scientific supporting evidence.
    """
    return explore_history_rows(load_explore_result_events_strict(
        explore_result_log_path(runtime_root, goal_id), goal_id=goal_id,
    ))


class ExploreResultsRequestMixin:
    def _explore_results(self):
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        goal_id = query.get("goal_id", [""])[0]
        try:
            if any(len(values) != 1 for values in query.values()):
                self._send_error("Choose one Goal, source and Explore view.", status=400)
                return
            view = query.get("view", ["findings"])[0]
            if view not in {"findings", "history"}:
                self._send_error("Unknown Explore view.", status=400)
                return
            from .goal_source_routing import linked_goal_source
            resolved = linked_goal_source(self.server, goal_id)
            if resolved is not None:
                if not self._require_loopback_origin():
                    return
                row, reader = resolved
                if query.get("source_namespace") != [reader.source.namespace]:
                    self._send_error("Source identity changed; refresh the Goal.", status=409)
                    return
                cursor = query.get("cursor", [""])[0]
                page = (reader.history(goal_id, cursor) if view == "history" else
                        reader.findings(goal_id, cursor, projector=_finding_rows))
                latest = linked_goal_source(self.server, goal_id)
                if latest is None or latest[1].source.namespace != reader.source.namespace:
                    self._send_error("Source identity changed; refresh the Goal.", status=409)
                    return
                self._send_json(page)
                return
            if "source_namespace" in query:
                self._send_error("Source identity changed; refresh the Goal.", status=409)
                return
            runtime_root = self._goal_result_scope(goal_id)
            if runtime_root is None:
                return
            # Local HTTP view selection; canonical event vocabularies are unchanged.
            page = self.server.completed_todo_pages.page(
                scope=("explore_history" if view == "history" else "explore_results", goal_id),
                cursor=query.get("cursor", [""])[0],
                load=lambda: _history_rows(runtime_root, goal_id) if view == "history" else
                    _result_rows(runtime_root, goal_id, self.server.registry_path),
            )
            self._send_json({**page, "goal_id": goal_id})
        except ValueError as exc:
            expired = str(exc) == "history_cursor_expired"
            self._send_error(
                "Result page expired; refresh to read current evidence." if expired else
                "Explore evidence could not be verified.", status=409,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self._send_error("Explore evidence could not be read.", status=503)
