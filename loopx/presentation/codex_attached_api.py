"""Owner-only projection and explicit input for registered Desktop conversations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..agent_registry import registered_agent_ids_for_goal
from ..attached_session import load_attached_session_registry
from ..capabilities.native_chat.codex_attached import (
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
from ..file_lock import exclusive_file_lock
from ..registry import atomic_write_json, find_registry_goal
from ..thread_agent_binding import collect_accepted_bindings
from .goal_source_routing import source_registry_for_goal

CODEX_ATTACHED_PATH = "/api/chat/codex-attached"
CODEX_ATTACHED_OPEN_PATH = f"{CODEX_ATTACHED_PATH}/open"


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


class CodexAttachedRequestMixin:
    server: Any
    path: str

    def _send_json(self, value: dict[str, Any], **kwargs: Any) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _read_json(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

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
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {
                "goal_id",
                "agent_id",
                "binding_ref",
                "before",
                "revision",
                "operation_id",
            } or any(len(v) != 1 for v in query.values()):
                raise ValueError("Conversation query is invalid")
            goal = self._codex_goal(query.get("goal_id", [""])[0])
            if "agent_id" not in query:
                self._send_json({"ok": True, "agents": bound_conversations(goal)})
                return
            binding = current_binding(
                goal, query["agent_id"][0], query.get("binding_ref", [""])[0]
            )
            if "operation_id" in query:
                receipt_path = self._input_receipt_path(
                    query["goal_id"][0], query["agent_id"][0], query["operation_id"][0]
                )
                if not receipt_path.exists():
                    self._send_error(
                        "No delivery receipt is recorded for this input.", status=404
                    )
                    return
                receipt = json.loads(receipt_path.read_text())
                if receipt.get("binding_ref") != binding["binding_ref"]:
                    raise ValueError("Delivery belongs to an older binding")
                self._send_json(receipt_readback(receipt))
                return
            home, path = locate_thread(binding["thread_id"])
            before = int(query["before"][0]) if "before" in query else None
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
            if before is None and query.get("revision", [""])[0] == revision:
                self._send_json(
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
            view = read_messages(path, before=before)
            self._send_json(
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
            self._send_error(
                "Conversation binding is unavailable or changed. Choose the Agent again.",
                status=409,
            )
        except (OSError, DesktopUnavailable):
            self._send_error(
                "Original Codex conversation is unavailable on this host.", status=503
            )

    def _codex_attached_open(self) -> None:
        try:
            body = self._read_json(max_bytes=4096)
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
            self._send_json({"ok": True, "requested": True})
        except (ValueError, KeyError, TypeError):
            self._send_error(
                "Conversation binding is invalid or changed. Choose the Agent again.",
                status=409,
            )
        except (OSError, DesktopUnavailable):
            self._send_error(
                "Codex App could not be opened on this host. Check it is installed, then try again.",
                status=503,
            )

    def _codex_attached_send(self) -> None:
        try:
            # 24,000 characters also fit when a caller JSON-escapes Unicode.
            body = self._read_json(max_bytes=160 * 1024)
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
                    receipt = json.loads(path.read_text())
                    if receipt["fingerprint"] != fingerprint:
                        raise ValueError("Operation id belongs to a different input")
                    self._send_json(receipt_readback(receipt))
                    return
                if activity(home, binding["thread_id"])["state"] != "idle":
                    self._send_error(
                        "The Agent is busy or its state is unknown. Continue in the original App; this surface does not interrupt it.",
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
                    self._send_json(receipt_readback(receipt))
        except (ValueError, KeyError, TypeError):
            self._send_error(
                "Reply or conversation binding is invalid. Choose the Agent again.",
                status=409,
            )
        except (OSError, DesktopUnavailable):
            self._send_error(
                "Reply was not confirmed. Check the original App before trying again.",
                status=503,
            )
