"""Metadata-only source route shared by named Goal settings owners."""
from pathlib import Path

from ..control_plane.projects.registry_codec import load_project_registry
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route

GOAL_SOURCE_LINKS_PATH = "/api/chat/goal-sources"
GOAL_SOURCE_STRATEGIES_PATH = "/api/chat/goal-source-strategies"
GOAL_SOURCE_DOCUMENT_PATH = "/api/chat/goal-source-document"


def goal_source_catalog(server):
    if not hasattr(server, "registry_path") or not hasattr(server, "runtime_root"):
        return None
    from ..control_plane.status.goal_source import GoalSourceCatalog

    catalog = getattr(server, "goal_source_catalog", None)
    if catalog is None:
        from ..capabilities.explore.result_log import explore_history_rows
        from ..capabilities.native_chat.project_context import coordination_runtime_root
        catalog = GoalSourceCatalog(server.registry_path,
            coordination_runtime_root(server.registry_path, server.runtime_root),
            history_projector=explore_history_rows)
        server.goal_source_catalog = catalog
    return catalog


def linked_goal_source(server, goal_id):
    catalog = goal_source_catalog(server)
    return catalog.resolve(goal_id) if catalog is not None else None


class GoalSourceRequestMixin:
    def _goal_source_strategies(self):
        return self._goal_source_read("strategies")

    def _goal_source_document(self):
        return self._goal_source_read("document")

    def _goal_source_read(self, operation):
        import subprocess
        from urllib.parse import parse_qs, urlparse

        if not self._require_loopback_origin():
            return
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        if (set(query) != {"goal_id", "source_namespace"}
                or any(len(values) != 1 for values in query.values())):
            self._send_error("Choose one original Goal and source.", status=400)
            return
        goal_id = query["goal_id"][0]
        resolved = linked_goal_source(self.server, goal_id)
        if resolved is None:
            self._send_error("The original Goal source is unavailable.", status=404)
            return
        _, reader = resolved
        if query["source_namespace"] != [reader.source.namespace]:
            self._send_error("Source identity changed; refresh the Goal.", status=409)
            return
        try:
            result = getattr(reader, operation)(reader.source.goal_id)
            latest = linked_goal_source(self.server, goal_id)
            if (latest is None or latest[1].source.namespace != reader.source.namespace
                    or (operation == "document" and latest[1].source != reader.source)):
                self._send_error("Source identity changed; refresh the Goal.", status=409)
                return
            self._send_json(result)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, subprocess.SubprocessError):
            self._send_error("The original strategy history is unavailable; retry after reconnecting."
                if operation == "strategies" else "The original task document is unavailable; check its source binding and retry.", status=503)

    def _completed_todos(self):
        import subprocess
        from urllib.parse import parse_qs, urlparse
        from ..chat.completed_todos import completed_todo_rows

        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        values = query.get("goal_id", [])
        resolved = linked_goal_source(self.server, values[0]) if len(values) == 1 else None
        if resolved is None:
            if "source_namespace" in query:
                self._send_error("Source identity changed; refresh the Goal.", status=409)
                return
            return super()._completed_todos()
        if not self._require_loopback_origin():
            return
        row, reader = resolved
        if (query.keys() - {"goal_id", "source_namespace", "agent_id", "q", "cursor"}
                or any(len(items) != 1 for items in query.values())
                or query.get("source_namespace") != [reader.source.namespace]):
            self._send_error("Source identity changed; refresh the Goal.", status=409)
            return
        goal_id = values[0]
        agent = query.get("agent_id", [""])[0]
        search = query.get("q", [""])[0].strip().casefold()
        try:
            if len(search) > 256:
                raise ValueError("history_search_too_long")
            reader.registered_status(reader.source.goal_id)
            def load():
                payload = reader.source.read_completed_todos(reader.source.goal_id)
                if payload.get("ok") is not True:
                    raise ValueError("Source history unavailable")
                return completed_todo_rows(payload, agent_id=agent, search=search)
            page = self.server.completed_todo_pages.page(
                scope=(reader.source.namespace, goal_id, agent, search),
                cursor=query.get("cursor", [""])[0], load=load)
            latest = linked_goal_source(self.server, goal_id)
            if latest is None or latest[1].source.namespace != reader.source.namespace:
                raise ValueError("Source identity changed")
            self._send_json(page)
        except (ValueError, TypeError, KeyError):
            self._send_error("Source history changed; refresh the Goal.", status=409)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self._send_error("The original history is unavailable; retry after reconnecting.", status=503)

    def _todo_detail(self):
        import subprocess
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        values = query.get("goal_id", [])
        resolved = linked_goal_source(self.server, values[0]) if len(values) == 1 else None
        if resolved is None:
            if "source_namespace" in query:
                self._send_error("Source identity changed; refresh the Goal.", status=409)
                return
            return super()._todo_detail()
        if not self._require_loopback_origin():
            return
        if set(query) != {"goal_id", "todo_id", "source_namespace"} or len(query["todo_id"]) != 1:
            self._send_error("Choose one original Goal and Task.", status=400)
            return
        row, reader = resolved
        if query["source_namespace"] != [reader.source.namespace]:
            self._send_error("Source identity changed; refresh the Goal.", status=409)
            return
        try:
            goal_id, todo_id = values[0], query["todo_id"][0]
            item = reader.source.read_todo(reader.source.goal_id, todo_id).get("todo")
            if item is None or item.get("todo_id") != todo_id:
                self._send_error("The Task is no longer available in this Goal.", status=404)
                return
            latest = linked_goal_source(self.server, goal_id)
            if latest is None or latest[1].source.namespace != reader.source.namespace:
                self._send_error("Source identity changed; refresh the Goal.", status=409)
                return
            self._send_json({"ok": True, "goal_id": goal_id, "todo_id": todo_id,
                "text": item["text"], "status": item["status"],
                "evidence": item.get("evidence"), "note": item.get("note"),
                "archive_state": item.get("archive_state", "active"),
                "updated_at": item.get("updated_at"), "source_access": row["source_access"]})
        except (ValueError, TypeError, KeyError):
            self._send_error("The source Task could not be verified.", status=409)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self._send_error("The original Task is unavailable; retry after reconnecting.", status=503)

    def _goal_source_links(self):
        from ..chat.workspace_directory import workspace_goal_directory
        from ..registry import load_registry
        from urllib.parse import parse_qs, urlparse
        import subprocess

        if not self._require_loopback_origin():
            return
        try:
            if parse_qs(urlparse(self.path).query, keep_blank_values=True):
                raise ValueError("Source links use the current workspace registry")
            if self.command == "POST":
                body = self._read_json(max_bytes=65536)
                catalog = goal_source_catalog(self.server)
                if set(body) == {"parent_goal_id", "goal_id", "source_namespace"} and all(
                    isinstance(value, str) and value for value in body.values()
                ):
                    catalog.remove(parent_goal_id=body["parent_goal_id"], goal_id=body["goal_id"],
                                   namespace=body["source_namespace"])
                elif ({"parent_goal_id", "source"} <= body.keys()
                      and not body.keys() - {"parent_goal_id", "source", "workspace_goal_id"}
                      and isinstance(body["parent_goal_id"], str)):
                    if "workspace_goal_id" in body and not isinstance(body["workspace_goal_id"], str):
                        raise ValueError("Use a workspace source Goal identity")
                    catalog.update(parent_goal_id=body["parent_goal_id"], raw_source=body["source"],
                                   workspace_goal_id=body.get("workspace_goal_id"))
                else:
                    raise ValueError("Choose an existing parent and one source Goal")
            directory = workspace_goal_directory(load_registry(self.server.registry_path))
            self._send_json({"ok": True, "registry_revision": directory["registry_revision"],
                             "goals": [goal for goal in directory["goals"] if goal.get("source_access")]})
        except (ValueError, TypeError, KeyError):
            self._send_error("The original Goal or its parent could not be linked.", status=409)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self._send_error("The original Goal source is unavailable; retry after reconnecting.", status=503)


def source_registry_for_goal(invoked: Path, goal_id: str) -> Path:
    payload = load_project_registry(invoked)
    if payload.get("profile_id") == "source_session_v1":
        return invoked
    route = resolve_goal_source_runtime_route(registry_path=invoked, goal_id=goal_id)
    path = route.get("source_registry")
    if not isinstance(path, str) or not path:
        raise ValueError("Goal source authority could not be resolved")
    return Path(path)
