"""Loopback model discovery and current-Session configuration routes."""
from __future__ import annotations

from pathlib import Path
from ...chat_agent import CodexChatAgentError
from .model_settings import normalize_agent_model_id


class ChatModelSettingsRequestMixin:
    def _agent_model_options(self) -> None:
        from ...chat_server import _compact_text
        try:
            body = self._read_json()
            if "agent_id" not in body or set(body) - {"agent_id", "goal_id", "agent_model_id"}:
                raise ValueError("agent_id is required")
            agent_id = _compact_text(body.get("agent_id"), limit=80)
            if not agent_id:
                raise ValueError("agent_id is required")
            goal_id = _compact_text(body.get("goal_id"), limit=160) or None
            if goal_id:
                self._registry_and_goal(goal_id)
            requested_model = normalize_agent_model_id(body["agent_model_id"]) if body.get("agent_model_id") is not None else None
            # Discovery is deliberately bounded to a selected, known endpoint.
            # It opens no persisted LoopX Chat session and exposes no command or
            # local workspace data to the browser.
            result = self.server.runtime_controller.inspect_agent_model_options(
                agent_id, work_dir=Path.cwd(), **({"goal_id": goal_id} if goal_id else {}),
                **({"agent_model_id": requested_model} if requested_model else {}),
            )
        except CodexChatAgentError as exc:
            self._send_error(str(exc), status=424, gate=exc.gate, error_code=exc.error_code)
            return
        except Exception as exc:  # noqa: BLE001 - compact local validation error.
            self._send_error(str(exc))
            return
        self._send_json({"ok": True, "schema_version": "loopx_chat_agent_model_options_v1", **result})


    def _configure_session_model(self, session_id: str) -> None:
        try:
            body = self._read_json()
            if "agent_model_id" not in body or set(body) - {"agent_model_id", "agent_reasoning_effort"}:
                raise ValueError("model settings require agent_model_id and optional agent_reasoning_effort")
            session = self.server.chat_store.load_session(session_id)
            if session is None:
                raise KeyError("chat session was not found")
            self._session_context(session)
            updated = self.server.runtime_controller.configure_session_model(session_id,
                model=body["agent_model_id"], effort=body.get("agent_reasoning_effort"))
        except KeyError:
            self._send_error("chat session was not found", status=404)
            return
        except CodexChatAgentError as exc:
            self._send_error(str(exc), status=424, error_code=exc.error_code, gate=exc.gate)
            return
        except (ValueError, TypeError) as exc:
            self._send_error(str(exc))
            return
        except OSError:
            self._send_error("Conversation settings could not be saved; read the current settings and retry.", status=503)
            return
        self._send_json({"ok": True, "session": self.server.chat_store.public_session(updated)})
