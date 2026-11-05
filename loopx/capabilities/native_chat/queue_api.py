"""HTTP projection and explicit steering of the existing persisted queue."""

from typing import Any

from ...chat_store import CHAT_STORE_SCHEMA_VERSION


class ChatQueueRequestMixin:
    def _dispatch_chat_queue(self, path: str, *, apply: bool) -> bool:
        parts = path.strip("/").split("/")
        if parts[:3] != ["api", "chat", "sessions"] or len(parts) < 5 or parts[4] != "queue":
            return False
        if len(parts) == 5 and not apply:
            self._session_queue(parts[3])
        elif len(parts) == 7 and parts[6] == "input" and apply:
            self._mutate_queued_turn(parts[3], parts[5])
        elif len(parts) == 7 and parts[6] == "steer" and apply:
            self._steer_queued_turn(parts[3], parts[5])
        else:
            return False
        return True

    def _session_queue(self, session_id: str) -> None:
        try:
            store = self.server.chat_store
            session = store.load_session(session_id)
            if session is None:
                raise KeyError("chat session was not found")
            if session.get("steward_context") is not None:
                self.server.runtime_controller.project_contexts.session_context(session)
            else:
                self._session_context(session)
            rows: list[dict[str, Any]] = []
            for turn in store.queued_turns(session_id, strict=True, include_reserved=True):
                text = str(turn.get("message") or "")
                rows.append({
                    "turn_id": turn["turn_id"],
                    "created_at": turn["created_at"],
                    "origin": turn.get("origin"),
                    "preview": text[:240],
                    "character_count": len(text),
                    "attachment_count": len(turn.get("attachments") or []),
                    "steering": turn.get("_queue_steering"),
                    "input_revision": turn.get("input_revision", 0),
                    "mutation": turn.get("_queue_mutation"),
                })
            self._send_json({"ok": True, "schema_version": CHAT_STORE_SCHEMA_VERSION,
                             "session_id": session_id, "active_turn_id": session.get("active_turn_id"), "turns": rows})
        except KeyError:
            self._send_error("chat session was not found", status=404)
        except ValueError as exc:
            self._send_error(str(exc), status=400)
        except (OSError, RuntimeError):
            self._send_error("Queued messages are temporarily unavailable", status=503)

    def _mutate_queued_turn(self, session_id: str, queued_turn_id: str) -> None:
        try:
            controller = self.server.runtime_controller
            # Reuse current Session/source admission without requiring native
            # steering support. Bound sources retain their original input entry.
            controller._require_steering_session(session_id)
            result = controller.store.mutate_queued_input(session_id, queued_turn_id, self._read_json())
            self._send_json({"ok": True, **result})
        except KeyError:
            self._send_error("chat turn was not found", status=404)
        except ValueError as exc:
            self._send_error(str(exc), status=400)
        except RuntimeError as exc:
            self._send_error(str(exc), status=409, error_code=str(exc))
        except OSError:
            self._send_error("Queue change is unconfirmed; retry the original operation", status=503)

    def _steer_queued_turn(self, session_id: str, queued_turn_id: str) -> None:
        try:
            body = self._read_json()
            if set(body) != {"target_turn_id", "client_ingress_id"}:
                raise ValueError("target_turn_id and client_ingress_id are required")
            for key in body:
                if not isinstance(body[key], str) or not body[key] or len(body[key]) > 160:
                    raise ValueError("invalid queue steering identity")
            result = self.server.runtime_controller.steer_queued_turn(session_id=session_id,
                queued_turn_id=queued_turn_id, expected_turn_id=body["target_turn_id"],
                client_ingress_id=body["client_ingress_id"])
            self._send_json({"ok": True, **result})
        except KeyError:
            self._send_error("chat turn was not found", status=404)
        except ValueError as exc:
            self._send_error(str(exc), status=400)
        except RuntimeError as exc:
            self._send_error(str(exc), status=409, error_code=str(exc))
        except OSError:
            self._send_error("Queue steering is temporarily unavailable; retain the original request", status=503)
