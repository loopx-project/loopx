"""Loopback read adapter for the canonical Explore result projection."""

from urllib.parse import parse_qs, urlparse

from ..todos import list_goal_todos

from ..capabilities.explore.result_log import (
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
)


def _result_rows(runtime_root, goal_id, registry_path):
    events = load_explore_result_events_strict(
        explore_result_log_path(runtime_root, goal_id), goal_id=goal_id,
    )
    projection = build_explore_result_projection(
        events, goal_id=goal_id, finding_limit=len(events),
    )
    nodes = {node["node_id"]: node for node in projection["nodes"]}
    # Reuse canonical active/history readback; an association is not adoption.
    todos = {}
    for view in ({}, {"status": "done", "read_scope": "completed_history"}):
        for todo in list_goal_todos(
            registry_path=registry_path, runtime_root_arg=str(runtime_root),
            goal_id=goal_id, role="agent", **view,
        )["todos"]:
            todos[todo["todo_id"]] = todo
    links = {}
    for todo in todos.values():
        for node_id in todo.get("explore_result_node_refs") or []:
            links.setdefault(node_id, []).append({
                "todo_id": todo["todo_id"], "text": todo["text"],
                "status": todo["status"], "claimed_by": todo.get("claimed_by") or "",
            })
    return [
        {**finding, "linked_todos": links.get(finding["node_id"], []),
         "question": nodes.get(finding["node_id"], {}).get("title", ""),
         "scope": nodes.get(finding["node_id"], {}).get("summary", "")}
        for finding in projection["findings"]
    ]


class ExploreResultsRequestMixin:
    def _explore_results(self):
        query = parse_qs(urlparse(self.path).query)
        goal_id = query.get("goal_id", [""])[0]
        try:
            runtime_root = self._goal_result_scope(goal_id)
            if runtime_root is None:
                return
            page = self.server.completed_todo_pages.page(
                scope=("explore_results", goal_id),
                cursor=query.get("cursor", [""])[0],
                load=lambda: _result_rows(runtime_root, goal_id, self.server.registry_path),
            )
            self._send_json({**page, "goal_id": goal_id})
        except ValueError as exc:
            expired = str(exc) == "history_cursor_expired"
            self._send_error(
                "Result page expired; refresh to read current evidence." if expired else
                "Explore evidence could not be verified.", status=409,
            )
        except (OSError, RuntimeError):
            self._send_error("Explore evidence could not be read.", status=503)
