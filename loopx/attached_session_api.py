"""Loopback API mixin for binding already-running Agent sessions."""

from __future__ import annotations

from typing import Any

from .attached_session import (
    bind_attached_agent_session,
    load_attached_session_registry,
)


def _compact_id(value: Any, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) > limit or any(ord(character) < 32 for character in text):
        raise ValueError("attached session field must be a bounded opaque string")
    return text


class AttachedSessionRequestMixin:
    """Serve owner-local attached Session binding and lifecycle boundaries."""

    server: Any

    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _registry_and_goal(
        self,
        goal_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _attach_session(self) -> None:
        try:
            body = self._read_json()
            allowed = {
                "goal_id",
                "agent_id",
                "host_surface",
                "host_session_id",
                "executor_endpoint_id",
                "channel_id",
            }
            if set(body) - allowed:
                raise ValueError("unknown attached session field")
            required = {
                key: _compact_id(body.get(key))
                for key in (
                    "goal_id",
                    "agent_id",
                    "host_surface",
                    "host_session_id",
                    "executor_endpoint_id",
                )
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError(f"missing attached session fields: {', '.join(missing)}")
            registry_path = getattr(self.server, "registry_path", None)
            if registry_path is None:
                registry, _goal = self._registry_and_goal(required["goal_id"])
            else:
                registry = load_attached_session_registry(registry_path)
            packet = bind_attached_agent_session(
                store=self.server.chat_store,
                registry=registry,
                registry_path=registry_path,
                goal_id=required["goal_id"],
                agent_id=required["agent_id"],
                host_surface=required["host_surface"],
                host_session_id=required["host_session_id"],
                executor_endpoint_id=required["executor_endpoint_id"],
                channel_id=_compact_id(body.get("channel_id")) or None,
                execute=True,
            )
        except Exception as exc:  # noqa: BLE001 - compact loopback validation error.
            self._send_error(str(exc))
            return
        self._send_json(packet, status=201 if packet.get("created") else 200)

    def _close_session(self, session_id: str) -> None:
        try:
            closed = self.server.runtime_controller.close_session(session_id)
        except RuntimeError as exc:
            error_code = str(exc)
            messages = {
                "attached_session_turn_active": (
                    "complete the active attached Agent turn before closing the session"
                ),
                "attached_session_queue_pending": (
                    "complete queued attached Agent turns before closing the session"
                ),
                "managed_session_turn_active": (
                    "interrupt the active Agent turn before closing the session"
                ),
                "managed_session_queue_pending": (
                    "complete queued Agent turns before closing the session"
                ),
            }
            if error_code not in messages:
                raise
            self._send_error(
                messages[error_code],
                status=409,
                error_code=error_code,
            )
            return
        if not closed:
            self._send_error("chat session was not found", status=404)
            return
        self._send_json({"ok": True, "session_id": session_id, "closed": True})
