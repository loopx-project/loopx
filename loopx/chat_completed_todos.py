"""Bounded, read-only history pages over the canonical Todo reader.

Snapshots keep concurrent completions from shifting page boundaries. They are
short-lived and server-local, never a second source of Todo authority.
"""
from collections import OrderedDict
import json
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlparse

from .paths import resolve_runtime_root
from .status_server import is_loopback_host


def _goal_result_candidates(*, runtime_root, goal_id):
    """Snapshot bounded metadata; exact acceptance is checked per requested page."""
    from .control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    payload = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
    if payload is None:
        return []  # Only the canonical completion writer can bind result bytes.
    candidates = sorted(
        ((index, item) for index, item in enumerate(payload["todos"])
         if item.get("role") == "agent" and item.get("status") == "done"),
        key=lambda pair: (str(pair[1].get("completed_at") or ""), pair[0]),
        reverse=True,
    )
    return [
        {
            "todo_id": todo["todo_id"],
            "title": str(todo.get("title") or todo.get("text") or todo["todo_id"]),
            "sha256": todo["completion_result"].get("sha256"),
            "producer_agent_id": todo["completion_result"].get("producer_agent_id"),
            "completed_at": todo.get("completed_at"),
        }
        for _, todo in candidates
        if todo.get("todo_id") and isinstance(todo.get("completion_result"), dict)
    ]


def _verify_goal_result_page(*, page, registry_path, runtime_root, goal_id):
    from .control_plane.todos.completion_result import read_completion_result

    rows = []
    unavailable_todo_ids = []
    for todo in page["items"]:
        todo_id = todo.get("todo_id")
        try:
            result = read_completion_result(
                registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, todo_id=todo_id,
            )["result"]
        except (OSError, ValueError):
            # Name the unverified rows instead of only counting them: a reader
            # that only needs its own Todo ids must not be blocked by an
            # unrelated unreadable report.
            unavailable_todo_ids.append(todo_id)
            continue
        if (result["sha256"] != todo["sha256"] or
                result["producer_agent_id"] != todo["producer_agent_id"]):
            unavailable_todo_ids.append(todo_id)
            continue
        rows.append({
            "todo_id": todo_id,
            "title": todo["title"],
            "producer_agent_id": result["producer_agent_id"],
            "sha256": result["sha256"],
            "content_type": result["content_type"],
            "size_bytes": result["size_bytes"],
            "completed_at": todo["completed_at"],
        })
    return {**page, "items": rows, "unavailable_count": len(unavailable_todo_ids),
            "unavailable_todo_ids": unavailable_todo_ids}


class CompletedTodoPages:
    page_size = 40
    max_snapshots = 8
    ttl_seconds = 300
    max_cache_bytes = 16 * 1024 * 1024

    def __init__(self):
        self._snapshots = OrderedDict()
        self._lock = Lock()

    def page(self, *, scope, cursor, load):
        with self._lock:
            now = monotonic()
            for key, (created, _, _, _) in list(self._snapshots.items()):
                if now - created >= self.ttl_seconds:
                    del self._snapshots[key]
            if cursor:
                try:
                    key, raw_offset = cursor.split(":")
                    offset = int(raw_offset)
                    _, saved_scope, rows, _ = self._snapshots[key]
                    if saved_scope != scope or offset < 0 or offset % self.page_size or offset >= len(rows):
                        raise ValueError()
                except (ValueError, KeyError):
                    raise ValueError("history_cursor_expired") from None
            else:
                rows = load()
                size = len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))
                if size > self.max_cache_bytes:
                    raise ValueError("history_snapshot_too_large")
                key, offset = token_urlsafe(18), 0
                while self._snapshots and (len(self._snapshots) >= self.max_snapshots or sum(snapshot[3] for snapshot in self._snapshots.values()) + size > self.max_cache_bytes):
                    self._snapshots.popitem(last=False)
                self._snapshots[key] = (now, scope, rows, size)
            end = offset + self.page_size
            return {
                "ok": True,
                "items": rows[offset:end],
                "total": len(rows),
                "next_cursor": f"{key}:{end}" if end < len(rows) else None,
            }


class CompletedTodoRequestMixin:
    def _goal_result_scope(self, goal_id):
        if not is_loopback_host(str(self.server.server_address[0])):
            self._send_error("Goal results require a loopback LoopX Chat server.", status=403)
            return None
        if not self._require_loopback_origin():
            return None
        registry, _goal = self._registry_and_goal(goal_id)
        return resolve_runtime_root(
            registry, self.server.runtime_root_override,
            registry_path=self.server.registry_path,
        )

    def _goal_results(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        goal_id = query.get("goal_id", [""])[0]
        cursor = query.get("cursor", [""])[0]
        try:
            runtime_root = self._goal_result_scope(goal_id)
            if runtime_root is None:
                return
            page = self.server.completed_todo_pages.page(
                scope=("accepted_goal_results", goal_id), cursor=cursor,
                load=lambda: _goal_result_candidates(runtime_root=runtime_root, goal_id=goal_id),
            )
            self._send_json(_verify_goal_result_page(
                page=page, registry_path=self.server.registry_path,
                runtime_root=runtime_root, goal_id=goal_id,
            ))
        except ValueError as exc:
            expired = str(exc) == "history_cursor_expired"
            self._send_error("history_cursor_expired" if expired else
                             "Goal results are unavailable.", status=409 if expired else 400)
        except (OSError, RuntimeError):
            self._send_error("Goal results could not be loaded.", status=503)

    def _goal_result(self, todo_id: str) -> None:
        from .control_plane.todos.completion_result import read_completion_result

        goal_id = parse_qs(urlparse(self.path).query).get("goal_id", [""])[0]
        try:
            runtime_root = self._goal_result_scope(goal_id)
            if runtime_root is None:
                return
            result = read_completion_result(
                registry_path=self.server.registry_path, runtime_root=runtime_root,
                goal_id=goal_id, todo_id=todo_id,
            )
            self._send_json({
                "ok": True, "goal_id": goal_id, "todo_id": todo_id,
                "result": result["result"], "text": result["text"],
            })
        except ValueError:
            self._send_error("The report or its current acceptance could not be verified.", status=409)
        except (OSError, RuntimeError):
            self._send_error("The report could not be read.", status=503)

    def _completed_todos(self) -> None:
        # This loopback-only workspace read preserves task text and evidence.
        # Select display fields without returning the authority's internal metadata.
        from .control_plane.todos.list_readback import list_goal_todos

        if not self._require_loopback_origin():
            return
        query = parse_qs(urlparse(self.path).query)
        goal_id = query.get("goal_id", [""])[0]
        agent_id = query.get("agent_id", [""])[0]
        cursor = query.get("cursor", [""])[0]
        try:
            self._registry_and_goal(goal_id)

            def load():
                payload = list_goal_todos(
                    registry_path=self.server.registry_path, goal_id=goal_id,
                    role="agent", status="done", read_scope="completed_history",
                    runtime_root_arg=self.server.runtime_root_override,
                )
                ordered = sorted(enumerate(payload["todos"]), key=lambda pair: (str(pair[1].get("completed_at") or ""), pair[0]), reverse=True)
                return [
                    {
                        "todo_id": item["todo_id"],
                        "text": str(item.get("text") or item.get("title") or ""),
                        "claimed_by": item.get("claimed_by"),
                        "evidence": item.get("evidence") or item.get("note") or None,
                        "priority": item.get("priority"),
                        "task_class": item.get("task_class"),
                        "done": item["done"],
                        "status": item["status"],
                        "task_domain": item.get("task_domain"),
                        "completed_at": item.get("completed_at"),
                        "resume_when": item.get("resume_when"),
                        "resume_ready": item.get("resume_ready"),
                        "resume_condition": item.get("resume_condition"),
                        "completion_validation_sha256": item.get("completion_validation_sha256"),
                        "completion_validation_revision": item.get("completion_validation_revision"),
                        "completion_validation_revision_history": item.get("completion_validation_revision_history") or [],
                    }
                    for _, item in ordered
                    if item.get("todo_id") and item.get("task_class") != "continuous_monitor"
                    and (not agent_id or item.get("claimed_by") == agent_id)
                ]

            self._send_json(self.server.completed_todo_pages.page(
                scope=(goal_id, agent_id), cursor=cursor, load=load,
            ))
        except ValueError as exc:
            expired = str(exc) == "history_cursor_expired"
            self._send_error("history_cursor_expired" if expired else "Completed history is unavailable for this Goal.", status=409 if expired else 400)
        except (OSError, RuntimeError):
            self._send_error("Completed history could not be loaded.", status=503)
