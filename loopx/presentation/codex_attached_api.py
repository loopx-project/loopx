"""Owner-only projection and explicit input for registered Desktop conversations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import parse_qs, urlparse

from ..agent_registry import registered_agent_ids_for_goal
from ..attached_session import load_attached_session_registry
from ..chat_attachments import CHAT_JSON_MAX_BYTES
from ..capabilities.native_chat.codex_attached import (
    DesktopProtocolError,
    DesktopClient,
    DesktopUnavailable,
    DesktopUncertain,
    activity,
    history_revision,
    input_fingerprint,
    locate_thread,
    open_conversation,
    read_messages,
)
from ..capabilities.native_chat.codex_history import read_content
from ..capabilities.native_chat.codex_stream import observations
from ..file_lock import exclusive_file_lock
from ..registry import atomic_write_json, find_registry_goal
from ..thread_agent_binding import collect_accepted_bindings
from .goal_source_routing import source_registry_for_goal

CODEX_ATTACHED_PATH = "/api/chat/codex-attached"
CODEX_ATTACHED_OPEN_PATH = f"{CODEX_ATTACHED_PATH}/open"
CODEX_ATTACHED_LIVE_PATH = f"{CODEX_ATTACHED_PATH}/live"
_LIVE_READERS = threading.BoundedSemaphore(2)


def receipt_readback(receipt: dict[str, Any]) -> dict[str, Any]:
    # Content hashes are private journal bookkeeping, not presentation data.
    return {
        "ok": True,
        **{
            key: receipt[key]
            for key in ["operation_id", "status", "turn_id"]
            if key in receipt
        },
    }


def bound_conversations(goal: dict[str, Any]) -> list[dict[str, str]]:
    registered = registered_agent_ids_for_goal(goal)
    rows = [
        row
        for row in collect_accepted_bindings([goal])
        if row["host_surface"] == "codex-app" and row["agent_id"] in registered
    ]
    return [
        {
            **row,
            "binding_ref": hashlib.sha256(
                json.dumps([goal.get("goal_instance_id"), row], sort_keys=True).encode()
            ).hexdigest(),
        }
        for row in rows
    ]


def current_binding(goal: dict[str, Any], agent: str, expected: str) -> dict[str, str]:
    rows = [row for row in bound_conversations(goal) if row["agent_id"] == agent]
    if len(rows) != 1 or rows[0]["binding_ref"] != expected:
        raise ValueError(
            "The Agent conversation binding changed or is ambiguous. Choose it again."
        )
    return rows[0]


class _AttachedHTTP(Protocol):
    """HTTP operations supplied by the composed handler, not mixin overrides."""

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None: ...

    def _send_error(self, message: str, *, status: int = 400) -> None: ...

    def _read_json(self, *, max_bytes: int = CHAT_JSON_MAX_BYTES) -> dict[str, Any]: ...

    wfile: Any
    close_connection: bool

    def send_response(self, code: int) -> None: ...

    def send_header(self, keyword: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


if TYPE_CHECKING:
    from ..chat_server import ChatRequestHandler

    # Check the real composition as well as the adapter's local call sites.
    _attached_http_contract: type[_AttachedHTTP] = ChatRequestHandler


class CodexAttachedRequestMixin:
    server: Any
    path: str

    def _codex_attached_live(self) -> None:
        from ..chat_server import chat_cors_response_headers

        http = cast(_AttachedHTTP, self)
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) != {"goal_id", "agent_id", "binding_ref"} or any(
                len(v) != 1 for v in query.values()
            ):
                raise ValueError("Invalid live observation query")
            goal_id, agent_id, expected = (
                query[key][0] for key in ("goal_id", "agent_id", "binding_ref")
            )
            binding = current_binding(self._codex_goal(goal_id), agent_id, expected)
            home, _ = locate_thread(binding["thread_id"])
        except (ValueError, KeyError, OSError, DesktopUnavailable):
            http._send_error(
                "The original conversation is unavailable or changed.", status=409
            )
            return
        http.send_response(200)
        http.send_header("Content-Type", "text/event-stream; charset=utf-8")
        http.send_header("Cache-Control", "no-store")
        http.send_header("Connection", "close")
        http.send_header("X-Accel-Buffering", "no")
        for key, value in chat_cors_response_headers(
            getattr(self, "_cors_origin", None)
        ).items():
            http.send_header(key, value)
        http.end_headers()

        def emit(packet: dict[str, Any]) -> None:
            data = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            http.wfile.write(
                f"retry: 5000\nevent: observation\ndata: {data}\n\n".encode()
            )
            http.wfile.flush()

        acquired = _LIVE_READERS.acquire(blocking=False)
        stream = None
        try:
            if not acquired:
                emit({"kind": "unavailable", "retry": True})
                return
            stream = observations(home, binding["thread_id"])
            pending, previous = None, None
            emitted_at = checked_at = 0.0
            for packet in stream:
                now = time.monotonic()
                if now - checked_at >= 1:
                    current_binding(self._codex_goal(goal_id), agent_id, expected)
                    checked_at = now
                if packet is not None:
                    pending = packet
                if (
                    pending is not None
                    and now - emitted_at >= 0.2
                    and pending["records"] != previous
                ):
                    current_binding(self._codex_goal(goal_id), agent_id, expected)
                    emit(pending)
                    previous, pending, emitted_at = pending["records"], None, now
                elif now - emitted_at >= 1:
                    http.wfile.write(b": observing\n\n")
                    http.wfile.flush()
                    emitted_at = now
        except DesktopProtocolError:
            emit({"kind": "unavailable", "retry": False})
        except DesktopUnavailable:
            emit({"kind": "unavailable", "retry": True})
        except (ValueError, KeyError, TypeError):
            emit({"kind": "unavailable", "retry": False})
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            if stream is not None:
                stream.close()
            if acquired:
                _LIVE_READERS.release()
            http.close_connection = True

    def _codex_goal(self, goal_id: str) -> dict[str, Any]:
        if not goal_id or len(goal_id) > 160:
            raise ValueError("Choose one registered Goal")
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and selected != goal_id:
            raise ValueError("Goal is outside this workspace")
        source = source_registry_for_goal(self.server.registry_path, goal_id)
        goal = find_registry_goal(load_attached_session_registry(source), goal_id)
        if goal is None:
            raise ValueError("Goal is not available")
        return goal

    def _input_receipt_path(
        self, goal_id: str, agent_id: str, operation_id: str
    ) -> Path:
        if not 8 <= len(operation_id) <= 100:
            raise ValueError("Reply operation is invalid")
        key = hashlib.sha256(
            json.dumps([goal_id, agent_id, operation_id]).encode()
        ).hexdigest()
        return (
            Path(self.server.runtime_root)
            / "native_chat"
            / "attached-input-receipts"
            / f"{key}.json"
        )

    def _codex_attached_read(self) -> None:
        http = cast(_AttachedHTTP, self)
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {
                "goal_id",
                "agent_id",
                "binding_ref",
                "before",
                "revision",
                "operation_id",
                "after",
                "source_ref",
                "entry_id",
                "content_offset",
                "content_ref",
            } or any(len(v) != 1 for v in query.values()):
                raise ValueError("Conversation query is invalid")
            goal = self._codex_goal(query.get("goal_id", [""])[0])
            if "agent_id" not in query:
                http._send_json({"ok": True, "agents": bound_conversations(goal)})
                return
            binding = current_binding(
                goal, query["agent_id"][0], query.get("binding_ref", [""])[0]
            )
            if "operation_id" in query:
                receipt_path = self._input_receipt_path(
                    query["goal_id"][0], query["agent_id"][0], query["operation_id"][0]
                )
                if not receipt_path.exists():
                    http._send_error(
                        "No delivery receipt is recorded for this input.", status=404
                    )
                    return
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("binding_ref") != binding["binding_ref"]:
                    raise ValueError("Delivery belongs to an older binding")
                http._send_json(receipt_readback(receipt))
                return
            home, path = locate_thread(binding["thread_id"])
            if "entry_id" in query:
                content = read_content(
                    path,
                    entry_id=int(query["entry_id"][0]),
                    offset=int(query.get("content_offset", ["0"])[0]),
                    expected_source=query.get("source_ref", [""])[0],
                    content_ref=query.get("content_ref", [""])[0],
                )
                current_binding(
                    self._codex_goal(query["goal_id"][0]),
                    query["agent_id"][0],
                    binding["binding_ref"],
                )
                http._send_json({"ok": True, **content})
                return
            before = int(query["before"][0]) if "before" in query else None
            after = int(query["after"][0]) if "after" in query else None
            stamp = path.stat()
            revision = history_revision(stamp)
            state = activity(home, binding["thread_id"])
            connected = False
            try:
                with DesktopClient(home) as client:
                    connected = bool(client.owner(binding["thread_id"]))
            except (OSError, ValueError, DesktopUnavailable):
                pass  # Persisted history remains readable when Desktop closes.
            can_reply = connected and state["state"] == "idle"
            # Do not repeatedly reparse megabytes when no host append changed.
            if (
                before is None
                and query.get("revision", [""])[0] == revision
                and (after is None or after >= stamp.st_size)
            ):
                current_binding(
                    self._codex_goal(query["goal_id"][0]),
                    query["agent_id"][0],
                    binding["binding_ref"],
                )
                http._send_json(
                    {
                        "ok": True,
                        "unchanged": True,
                        "revision": revision,
                        "activity": state,
                        "connected": connected,
                        "can_reply": can_reply,
                    }
                )
                return
            view = read_messages(
                path,
                before=before,
                after=after,
                expected_source=query.get("source_ref", [None])[0],
            )
            current_binding(
                self._codex_goal(query["goal_id"][0]),
                query["agent_id"][0],
                binding["binding_ref"],
            )
            http._send_json(
                {
                    "ok": True,
                    **binding,
                    **view,
                    "activity": state,
                    "connected": connected,
                    "can_reply": can_reply,
                }
            )
        except (ValueError, KeyError):
            http._send_error(
                "Conversation binding is unavailable or changed. Choose the Agent again.",
                status=409,
            )
        except (OSError, DesktopUnavailable):
            http._send_error(
                "Original Codex conversation is unavailable on this host.", status=503
            )

    def _codex_attached_open(self) -> None:
        http = cast(_AttachedHTTP, self)
        try:
            body = http._read_json(max_bytes=4096)
            if set(body) != {"goal_id", "agent_id", "binding_ref"} or any(
                not isinstance(value, str) for value in body.values()
            ):
                raise ValueError("Conversation fields are invalid")
            binding = current_binding(
                self._codex_goal(body["goal_id"]),
                body["agent_id"],
                body["binding_ref"],
            )
            locate_thread(binding["thread_id"])
            current_binding(
                self._codex_goal(body["goal_id"]),
                body["agent_id"],
                body["binding_ref"],
            )
            open_conversation(binding["thread_id"])
            # OS dispatch is not proof of a displayed conversation or a new turn.
            http._send_json({"ok": True, "requested": True})
        except (ValueError, KeyError, TypeError):
            http._send_error(
                "Conversation binding is invalid or changed. Choose the Agent again.",
                status=409,
            )
        except (OSError, DesktopUnavailable):
            http._send_error(
                "Codex App could not be opened on this host. Check it is installed, then try again.",
                status=503,
            )

    def _codex_attached_send(self) -> None:
        http = cast(_AttachedHTTP, self)
        try:
            # 24,000 characters also fit when a caller JSON-escapes Unicode.
            body = http._read_json(max_bytes=160 * 1024)
            if set(body) != {
                "goal_id",
                "agent_id",
                "binding_ref",
                "operation_id",
                "text",
            } or any(not isinstance(value, str) for value in body.values()):
                raise ValueError("Reply fields are invalid")
            if (
                not body["text"].strip()
                or len(body["text"]) > 24000
                or not 8 <= len(body["operation_id"]) <= 100
            ):
                raise ValueError("Reply is empty or exceeds the input limit")
            goal = self._codex_goal(body["goal_id"])
            binding = current_binding(goal, body["agent_id"], body["binding_ref"])
            home, _ = locate_thread(binding["thread_id"])
            path = self._input_receipt_path(
                body["goal_id"], body["agent_id"], body["operation_id"]
            )
            fingerprint = input_fingerprint(body["binding_ref"], body["text"])
            with exclusive_file_lock(path):
                if path.exists():
                    receipt = json.loads(path.read_text(encoding="utf-8"))
                    if receipt["fingerprint"] != fingerprint:
                        raise ValueError("Operation id belongs to a different input")
                    http._send_json(receipt_readback(receipt))
                    return
                if activity(home, binding["thread_id"])["state"] != "idle":
                    # No receipt or native input exists for this operation. Do not
                    # extend this proof to generic conflicts or transport errors.
                    http._send_json(
                        {
                            "ok": False,
                            "error": "The Agent is busy or its state is unknown. Nothing was sent; your draft is retained.",
                            "input_not_submitted": True,
                            "operation_id": body["operation_id"],
                        },
                        status=409,
                    )
                    return
                with DesktopClient(home) as client:
                    owner = client.owner(binding["thread_id"])
                    # Recheck the authoritative binding after discovery, before effect.
                    current_binding(
                        self._codex_goal(body["goal_id"]),
                        body["agent_id"],
                        body["binding_ref"],
                    )
                    receipt = {
                        "operation_id": body["operation_id"],
                        "fingerprint": fingerprint,
                        "binding_ref": binding["binding_ref"],
                        "status": "pending",
                    }
                    atomic_write_json(path, receipt)
                    try:
                        receipt["turn_id"] = client.send(
                            binding["thread_id"], body["text"], owner
                        )
                        receipt["status"] = "delivered"
                        atomic_write_json(path, receipt)
                    except DesktopUncertain:
                        pass  # Pending is durable; retrying this id never resends.
                    http._send_json(receipt_readback(receipt))
        except (ValueError, KeyError, TypeError):
            http._send_error(
                "Reply or conversation binding is invalid. Choose the Agent again.",
                status=409,
            )
        except (OSError, DesktopUnavailable):
            http._send_error(
                "Reply was not confirmed. Check the original App before trying again.",
                status=503,
            )
