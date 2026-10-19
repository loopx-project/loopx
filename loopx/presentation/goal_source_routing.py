"""Metadata-only source route shared by named Goal settings owners."""
from pathlib import Path

from ..control_plane.projects.registry_codec import load_project_registry
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route

GOAL_SOURCE_LINKS_PATH = "/api/chat/goal-sources"


def goal_source_catalog(server):
    if not hasattr(server, "registry_path") or not hasattr(server, "runtime_root"):
        return None
    from ..control_plane.status.goal_source import GoalSourceCatalog

    catalog = getattr(server, "goal_source_catalog", None)
    if catalog is None:
        from ..capabilities.explore.result_log import explore_history_rows
        catalog = GoalSourceCatalog(server.registry_path, server.runtime_root, history_projector=explore_history_rows)
        server.goal_source_catalog = catalog
    return catalog


def linked_goal_source(server, goal_id):
    catalog = goal_source_catalog(server)
    return catalog.resolve(goal_id) if catalog is not None else None


class GoalSourceRequestMixin:
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
            item = reader.source.read_todo(goal_id, todo_id).get("todo")
            if item is None or item.get("todo_id") != todo_id:
                self._send_error("The Task is no longer available in this Goal.", status=404)
                return
            latest = linked_goal_source(self.server, goal_id)
            if latest is None or latest[1].source.namespace != reader.source.namespace:
                self._send_error("Source identity changed; refresh the Goal.", status=409)
                return
            self._send_json({"ok": True, "goal_id": goal_id, "todo_id": todo_id,
                "text": item["text"], "status": item["status"],
                "archive_state": item.get("archive_state", "active"),
                "updated_at": item.get("updated_at"), "source_access": row["source_access"]})
        except (ValueError, TypeError, KeyError):
            self._send_error("The source Task could not be verified.", status=409)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self._send_error("The original Task is unavailable; retry after reconnecting.", status=503)

    def _goal_source_links(self):
        from ..chat_workspace_directory import workspace_goal_directory
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
                elif set(body) == {"parent_goal_id", "source"} and isinstance(body["parent_goal_id"], str):
                    catalog.update(parent_goal_id=body["parent_goal_id"], raw_source=body["source"])
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
