"""Loopback-only exact Task request reads over the existing Todo owner."""

from urllib.parse import parse_qs, urlparse

from .todo_source import resolve_chat_todo_source
from ..status_server import is_loopback_host
from ..control_plane.todos.list_readback import list_goal_todos


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
            source = resolve_chat_todo_source(self.server, goal_id)
            payload = list_goal_todos(
                **source.writer_scope(), goal_id=goal_id,
                role="agent", todo_id=todo_id,
            )
            if payload.get("ambiguous"):
                self._send_error("The Task source is ambiguous. Refresh its Goal.", status=409)
                return
            item = payload.get("todo")
            if item is None:
                self._send_error("The Task is no longer available in this Goal.", status=404)
                return
            current = resolve_chat_todo_source(self.server, goal_id)
            if (current.source_identity, current.registry_sha256) != (
                    source.source_identity, source.registry_sha256):
                raise OSError("Task registration changed during the read")
            authority = payload.get("authority_read") or {}
            # Echo the same canonical CAS used by the existing Chat edit owner.
            # Legacy display reads deliberately cannot authorize inline edits.
            basis = ({"schema_version": "loopx_chat_canonical_update_basis_v0",
                "provider_revision": authority["provider_revision"],
                "source_authority": authority["source_authority"],
                "source_identity": source.source_identity,
                "registry_sha256": source.registry_sha256}
                if authority.get("decision_read_from_provider") is True else None)
            self._send_json({
                "ok": True, "goal_id": goal_id, "todo_id": item["todo_id"],
                "text": item["text"], "status": item["status"],
                "archive_state": item.get("archive_state", "active"),
                "updated_at": item.get("updated_at"),
                **({"review_basis": basis} if basis is not None else {}),
            })
        except ValueError:
            self._send_error("The Goal or Task source could not be verified.", status=400)
        except (OSError, RuntimeError):
            self._send_error("The Task request could not be read. Retry when its source is available.", status=503)
