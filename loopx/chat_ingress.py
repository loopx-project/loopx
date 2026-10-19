"""Durable queued inputs and ingress receipts for the existing Chat store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid
from typing import Any
from .chat import require_matching_message_replay, require_matching_replay
from .file_lock import exclusive_file_lock


SESSION_QUEUE_MAX_PENDING = 20
SESSION_QUEUE_TTL_SECONDS = 3600


class ChatIngressStore:
    def create_queued_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
        goal_instance_id: str | None = None,
        ttl_seconds: int = SESSION_QUEUE_TTL_SECONDS,
        origin: str = "external",
        external_agent_target: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist one bounded follow-up without replacing the active Turn."""

        from .chat_store import _opaque_id, _instant, _atomic_write_json, utc_now, CHAT_TURN_SCHEMA_VERSION

        client_id = _opaque_id(client_turn_id, field="client_turn_id")
        from .chat_attachments import normalize_chat_image_attachments, validate_chat_turn_envelope
        normalized_attachments = normalize_chat_image_attachments(attachments) or None
        if normalized_attachments:
            validate_chat_turn_envelope({"message": message, "attachments": normalized_attachments})
        session_path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                session_path,
                agent_id="loopx-chat",
                operation="create_chat_queued_turn",
            ):
                existing = self.turn_for_client(session_id, client_id)
                if existing is not None:
                    require_matching_replay(
                        existing,
                        identity="client_turn_id",
                        request={
                            "message": str(message),
                            "attachments": normalized_attachments,
                            "origin": _opaque_id(origin, field="origin"),
                            "external_agent_target": external_agent_target,
                        },
                    )
                    # Managed acceptance retains its own message identity and
                    # visible text. A queue-mode retry only replays that receipt;
                    # the original typed acceptance owner finishes its writes.
                    if "_acceptance" not in existing:
                        self._recover_queued_turn_input(session_id, existing)
                    return existing, False
                session = self.load_session(session_id)
                if session is None or session.get("status") == "closed":
                    raise KeyError("chat session was not found")
                now = datetime.now(timezone.utc)
                queued = [
                    turn
                    for turn in self.queued_turns(session_id)
                    if (_instant(turn.get("expires_at")) or now + timedelta(seconds=1)) > now
                ]
                if len(queued) >= SESSION_QUEUE_MAX_PENDING:
                    raise RuntimeError("session_queue_full")
                turn_id = uuid.uuid4().hex
                now_text = now.isoformat().replace("+00:00", "Z")
                payload = {
                    "schema_version": CHAT_TURN_SCHEMA_VERSION,
                    "turn_id": turn_id,
                    "session_id": session_id,
                    **(
                        {
                            "goal_instance_id": _opaque_id(
                                goal_instance_id,
                                field="goal_instance_id",
                            )
                        }
                        if goal_instance_id is not None
                        else {}
                    ),
                    "client_turn_id": client_id,
                    "status": "queued",
                    **({"external_agent_target": external_agent_target} if external_agent_target is not None else {}),
                    "message": str(message),
                    **({"attachments": normalized_attachments} if normalized_attachments else {}),
                    "origin": _opaque_id(origin, field="origin"),
                    "upstream_turn_id": None,
                    "response": None,
                    "error_code": None,
                    "error": None,
                    "created_at": now_text,
                    "expires_at": (now + timedelta(seconds=max(1, ttl_seconds)))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "started_at": None,
                    "first_event_at": None,
                    "completed_at": None,
                    "last_activity_at": now_text,
                    "delta_count": 0,
                    "sse_reconnect_count": 0,
                }
                path = self._turn_path(session_id, turn_id)
                _atomic_write_json(path, payload)
                os.chmod(path, 0o600)
                session.update(
                    {
                        "last_activity_at": now_text,
                        "updated_at": utc_now(),
                    }
                )
                _atomic_write_json(session_path, session, preserve_mode=True)
                # Keep the cross-process Session fence through publication:
                # neither another retry nor a queue claim may overtake it.
                self.append_message(
                    session_id,
                    role="user",
                    text=message,
                    turn_id=turn_id,
                    origin=origin,
                    attachments=normalized_attachments,
                )
                self.append_event(
                    session_id,
                    turn_id,
                    kind="turn.queued",
                    payload={"delivery_mode": "session_queue"},
                )
                return payload, True

    def _recover_queued_turn_input(self, session_id: str, turn: dict[str, Any]) -> None:
        """Finish interrupted input IO under the caller's Session fence.

        The existing queued event follows the transcript append. It is the
        publication marker, so healthy claims need no full transcript scan.
        Recovery uses the saved request, never a retry's replacement text.
        """
        from .chat_store import _read_jsonl

        if "_acceptance" in turn:
            raise ValueError("managed Turn acceptance must finish before queue dispatch")
        turn_id = str(turn["turn_id"])
        event_path = self._event_path(session_id, turn_id)
        try:
            with event_path.open("rb") as stream:
                first = stream.readline()
        except FileNotFoundError:
            first = b""
        if first.strip():
            import json

            event = json.loads(first)
            if isinstance(event, dict) and event.get("kind") == "turn.queued":
                return
        self.flush_events(session_id, turn_id)
        events = _read_jsonl(event_path, raise_on_error=True, strict=True)
        if any(row["kind"] == "turn.queued" for row in events):
            return
        inputs = [row for row in _read_jsonl(self._session_dir(session_id) / "messages.jsonl", raise_on_error=True, strict=True)
                  if row.get("turn_id") == turn_id and row.get("role") == "user"]
        expected = {"message_id": "queue." + turn_id, "role": "user", "turn_id": turn_id,
                    "text": turn["message"], "origin": turn["origin"],
                    "attachments": turn.get("attachments")}
        if inputs:
            if len(inputs) != 1:
                raise ValueError("queued Turn has ambiguous input messages")
            require_matching_message_replay(inputs[0], expected)
        else:
            self.append_message(session_id, **expected)
        self.append_event(session_id, turn_id, kind="turn.queued", payload={"delivery_mode": "session_queue"})

    def create_ingress_receipt(
        self,
        session_id: str,
        *,
        client_ingress_id: str,
        mode: str,
        message: str,
        expected_turn_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Reserve one idempotent external ingress before provider delivery."""

        from .chat_store import _opaque_id, _read_json, _atomic_write_json, utc_now, CHAT_INGRESS_SCHEMA_VERSION

        if self.load_session(session_id) is None:
            raise KeyError("chat session was not found")
        path = self._ingress_path(session_id, client_ingress_id)
        with exclusive_file_lock(
            path,
            agent_id="loopx-chat",
            operation="create_chat_ingress_receipt",
        ):
            existing = _read_json(path)
            if existing.get("schema_version") == CHAT_INGRESS_SCHEMA_VERSION:
                request = {"mode": _opaque_id(mode, field="mode"), "message": str(message)}
                if expected_turn_id is not None or "expected_turn_id" in existing:
                    request["expected_turn_id"] = expected_turn_id
                require_matching_replay(
                    existing,
                    identity="client_ingress_id",
                    request=request,
                )
                return existing, False
            now = utc_now()
            payload = {
                "schema_version": CHAT_INGRESS_SCHEMA_VERSION,
                "client_ingress_id": _opaque_id(
                    client_ingress_id,
                    field="client_ingress_id",
                ),
                "session_id": session_id,
                "mode": _opaque_id(mode, field="mode"),
                "status": "pending",
                "message": str(message),
                "active_turn_id": None,
                **({"expected_turn_id": _opaque_id(expected_turn_id, field="expected_turn_id")}
                   if expected_turn_id is not None else {}),
                "error_code": None,
                "created_at": now,
                "updated_at": now,
            }
            _atomic_write_json(path, payload)
            os.chmod(path, 0o600)
            return payload, True

    def update_ingress_receipt(
        self,
        session_id: str,
        client_ingress_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        from .chat_store import _read_json, _atomic_write_json, utc_now, CHAT_INGRESS_SCHEMA_VERSION

        path = self._ingress_path(session_id, client_ingress_id)
        with exclusive_file_lock(
            path,
            agent_id="loopx-chat",
            operation="update_chat_ingress_receipt",
        ):
            payload = _read_json(path)
            if payload.get("schema_version") != CHAT_INGRESS_SCHEMA_VERSION:
                raise KeyError("chat ingress receipt was not found")
            allowed = {"status", "active_turn_id", "error_code"}
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"unsupported chat ingress fields: {sorted(unknown)}")
            payload.update(changes)
            payload["updated_at"] = utc_now()
            _atomic_write_json(path, payload, preserve_mode=True)
            return payload

    def loopx_ingress(self, session_id: str) -> list[dict[str, Any]]:
        """Read the existing ingress ledger; no second queue or delivery truth."""
        from .chat_store import _read_json

        rows = [_read_json(path) for path in (self._session_dir(session_id) / "ingress").glob("*.json")]
        return sorted((row for row in rows if row.get("mode") in {"loopx_queue", "loopx_inbox"}),
                      key=lambda row: (row.get("created_at", ""), row.get("client_ingress_id", "")))
