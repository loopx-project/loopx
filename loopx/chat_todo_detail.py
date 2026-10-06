"""Loopback-only exact Task request reads over the existing Todo owner."""

from urllib.parse import parse_qs, urlparse

from .status_server import is_loopback_host
from .todos import list_goal_todos


class TodoDetailRequestMixin:
    def _todo_detail(self) -> None:
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_error("Task requests require a loopback LoopX Chat server.", status=403)
            return
        if not self._require_loopback_origin():
            return
        query = parse_qs(urlparse(self.path).query)
        if any(len(query.get(key, [])) != 1 for key in ("goal_id", "todo_id")):
            self._send_error("Choose one Goal and Task to read.", status=400)
            return
        goal_id, todo_id = query["goal_id"][0], query["todo_id"][0]
        try:
            if not self.server.registry_path.is_file():
                raise OSError("Task registry is unavailable")
            self._registry_and_goal(goal_id)
            payload = list_goal_todos(
                registry_path=self.server.registry_path, goal_id=goal_id,
                role="agent", todo_id=todo_id,
                runtime_root_arg=self.server.runtime_root_override,
            )
            if payload.get("ambiguous"):
                self._send_error("The Task source is ambiguous. Refresh its Goal.", status=409)
                return
            item = payload.get("todo")
            if item is None:
                self._send_error("The Task is no longer available in this Goal.", status=404)
                return
            self._send_json({
                "ok": True, "goal_id": goal_id, "todo_id": item["todo_id"],
                "text": item["text"], "status": item["status"],
                "archive_state": item.get("archive_state", "active"),
                "updated_at": item.get("updated_at"),
            })
        except ValueError:
            self._send_error("The Goal or Task source could not be verified.", status=400)
        except (OSError, RuntimeError):
            self._send_error("The Task request could not be read. Retry when its source is available.", status=503)
