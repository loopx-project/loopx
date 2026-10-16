"""Automatic owner notices on an existing Core-authorized private steward.

Native Todo readers and the typed planner own discovery. This provider adapter
stores only settings, frozen intents and independently verified delivery facts.
It starts no listener, model Turn, delegated task or heartbeat.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from ...chat_store import _read_json
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.todos.list_readback import list_goal_todos
from ...file_lock import exclusive_file_lock, try_exclusive_file_lock, write_json_atomic
from .goal_channel_transport import call, json_payload, lark_args
from .inbox_reply import _message
from .outbound import lark_readback_matches_outbound, present_lark_conversation_text


class LarkOwnerAttention:
    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.root = transport.core.controller.store.root / "lark-owner-attention"

    def _path(self, binding_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{24}", binding_id):
            raise ValueError("select an existing private steward")
        return self.root / f"{binding_id}.json"

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": "lark_owner_attention_v0", "revision": 0, "enabled": False,
                "initialized": False, "subjects": [], "outbox": [], "last_checked_at": None,
                "error_code": None}

    def _read(self, path: Path) -> dict[str, Any]:
        state = _read_json(path) if path.exists() else self._empty()
        if state.get("schema_version") != "lark_owner_attention_v0":
            raise ValueError("owner notification state is unavailable")
        return state

    @staticmethod
    def _write(path: Path, state: dict[str, Any]) -> None:
        # Cold receipts retain full intent/proof without feeding a growing
        # history back through every planning RPC. Subject generations remain
        # in the hot journal, so compaction cannot re-notify an unchanged task.
        for row in state["outbox"]:
            if row["status"] not in {"delivered", "superseded"}:
                continue
            receipt = path.parent / "receipts" / path.stem / f"{row['id']}.json"
            if receipt.exists():
                saved = _read_json(receipt)
                if any(saved.get(key) != row.get(key) for key in ("id", "key", "digest", "intent", "message_id", "status")):
                    raise ValueError("notification receipt identity changed")
            else:
                write_json_atomic(receipt, row, mode=0o600, directory_mode=0o700, fsync_directory=True)
        state["outbox"] = [row for row in state["outbox"] if row["status"] not in {"delivered", "superseded"}]
        write_json_atomic(path, state, mode=0o600, directory_mode=0o700, fsync_directory=True)

    def health(self, binding_id: str) -> dict[str, Any]:
        try:
            state = self._read(self._path(binding_id))
            return dict(effect_runtime_result("presentation.owner_attention.health", {"state": state}))
        except (ValueError, OSError, KeyError, TypeError, RuntimeError):
            return {"available": False, "error_code": "notification_state_unavailable"}

    def _binding(self, binding_id: str, expected_revision: int | None = None) -> dict[str, Any]:
        current = self.transport.bindings.read()
        if expected_revision is not None and current["revision"] != expected_revision:
            raise ValueError("the connection changed; refresh before changing notifications")
        row = next((row for row in current["bindings"] if row["binding_id"] == binding_id), None)
        if (not row or row.get("enabled") is not True or row.get("context_kind") != "steward"
                or row.get("audience", "owner") != "owner" or row.get("grant") != "portfolio_read"):
            raise ValueError("notifications require an authorized owner private steward")
        return row

    def _route(self, binding: dict[str, Any], saved: dict[str, Any] | None = None) -> dict[str, Any]:
        candidates = [saved] if saved is not None else [
            _read_json(path) for path in sorted(self.transport.root.glob("*.json"), reverse=True)
            if re.fullmatch(r"[a-f0-9]{24}", path.stem)]
        for record in candidates:
            if not isinstance(record, dict):
                continue
            event = record.get("event") or {}
            if (record.get("binding_id") != binding["binding_id"]
                    or record.get("profile") != binding["transport_ref"]
                    or event.get("chat_type") != "p2p" or event.get("sender_type") != "user"
                    or not re.fullmatch(r"[a-f0-9]{24}", str(record.get("request_ref") or ""))):
                continue
            if not (self.transport.core.root / f"{record['request_ref']}.json").exists():
                continue
            native = self.transport.core.read_request(record["request_ref"])
            if (native.get("binding_id") != binding["binding_id"] or native.get("source") != record.get("source")
                    or native.get("origin", "lark") != "lark"
                    or native.get("status") in {"prepared", "rejected"}):
                continue
            selected = self.transport.bindings.resolve(binding_id=binding["binding_id"],
                **record["source"], include_observation=True)
            if not self.transport._source_message(record, selected=selected, render_content=False):
                continue
            # Retain the exact canonical original source; never accept a chat
            # ID or App supplied by the settings request or a Todo body.
            return {**{key: record[key] for key in ("request_ref", "binding_id", "profile", "source")},
                    "bot_app_id": selected["observation"]["bot_app_id"],
                    "event": {key: event[key] for key in ("message_id", "chat_id", "chat_type", "sender_type", "sender_id")}}
        raise ValueError("send a message to this private steward before enabling notifications")

    def _snapshot(self, binding: dict[str, Any]) -> list[dict[str, Any]]:
        controller = self.transport.core.controller
        if controller.registry_path is None:
            raise ValueError("the steward registry is unavailable")
        goals = []
        for goal_id in self.transport.bindings.goal_scope_ids(binding):
            args = dict(registry_path=controller.registry_path, goal_id=goal_id,
                        runtime_root_arg=str(controller.coordination_runtime_root), role="user")
            listing = list_goal_todos(**args)
            if listing.get("ok") is not True or listing.get("todo_count") != len(listing.get("todos", [])):
                raise ValueError("the native Todo source is incomplete")
            todos = []
            # Exact reads restore source text; a bounded summary never supplies
            # notification terms. Read every listed user row without a prefix.
            for row in listing["todos"]:
                detail = list_goal_todos(**args, todo_id=row["todo_id"])
                if detail.get("ok") is not True or not detail.get("matched") or not isinstance(detail.get("todo"), dict):
                    raise ValueError("a native Todo changed while reading")
                todos.append(detail["todo"])
            goals.append({"goal_id": goal_id, "todos": todos})
        return goals

    @staticmethod
    def _plan(state: dict[str, Any], goals: list[dict[str, Any]]) -> dict[str, Any]:
        return dict(effect_runtime_result("presentation.owner_attention.plan",
                    {"state": state, "goals": goals, "source_complete": True}))

    @staticmethod
    def _observe(state: dict[str, Any], row: dict[str, Any], event: str, **facts: Any) -> dict[str, Any]:
        return dict(effect_runtime_result("presentation.owner_attention.observe",
                    {"state": state, "id": row["id"], "event": event, "now": time.time(), **facts}))

    def configure(self, *, binding_id: str, binding_revision: int, revision: int, enabled: bool) -> None:
        if (type(enabled) is not bool or type(revision) is not int or type(binding_revision) is not int):
            raise ValueError("select the current notification settings")
        path = self._path(binding_id)
        with exclusive_file_lock(path, operation="configure_owner_notifications"):
            binding = self._binding(binding_id, binding_revision)
            state = self._read(path)
            if state["revision"] != revision:
                raise ValueError("notification settings changed; refresh first")
            if enabled and not state["enabled"]:
                # First enable baselines existing tasks, avoiding an unsolicited
                # historical burst. Re-enable retains pending/unknown receipts.
                state["route"] = self._route(binding, state.get("route"))
                state = self._plan(state, self._snapshot(binding))
                state["error_code"] = None
            state.update(enabled=enabled, revision=revision + 1)
            self._write(path, state)

    def _verify(self, row: dict[str, Any], route: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any] | None:
        observed = self.transport.bindings.observe(route["profile"])
        if observed["provider_ref"] != binding["provider_ref"] or observed["operator_ref"] != binding["operator_ref"]:
            return None
        result = call(self.transport.runner, lark_args(cli_bin=self.transport.cli_bin, profile=route["profile"],
            tail=["im", "+messages-mget", "--message-ids", row["message_id"], "--as", "bot", "--no-reactions", "--format", "json"]))
        message = _message(json_payload(result), row["message_id"])
        sender = message.get("sender", {}) if message else {}
        matches = bool(result.get("returncode") == 0 and message and not message.get("deleted")
                    and message.get("chat_id") == route["event"]["chat_id"] and sender.get("sender_type") == "app"
                    and sender.get("id") == observed["bot_app_id"]
                    and lark_readback_matches_outbound(outbound_text=row["intent"]["text"], message=message))
        return {"message_id": row["message_id"], "chat_id": message["chat_id"], "app_id": sender["id"],
                "message_sha256": hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=False).encode()).hexdigest()} if matches else None

    def reconcile(self) -> int:
        verified = 0
        # Each binding has one fenced outbox across processes. The caller uses
        # the existing bounded outbound pool, never a new message consumer.
        for path in sorted(self.root.glob("*.json")):
            if not re.fullmatch(r"[a-f0-9]{24}", path.stem):
                continue
            with try_exclusive_file_lock(path, operation="deliver_owner_notifications") as acquired:
                if not acquired:
                    continue
                try:
                    state = self._read(path)
                except (ValueError, OSError, KeyError, TypeError):
                    continue  # one unreadable binding cannot starve other outboxes
                if not state["enabled"]:
                    continue
                try:
                    binding = self._binding(path.stem)
                    state = self._plan(state, self._snapshot(binding))
                    state.update(last_checked_at=time.time(), error_code=None)
                    self._write(path, state)
                    ready = [row["id"] for row in state["outbox"] if row["status"] in {"pending", "unconfirmed"}
                             and float(row.get("next_attempt_at", 0)) <= time.time()
                             and (row["status"] == "pending" or row.get("message_id"))][:2]
                    for notice_id in ready:
                        # Withdrawals and scope revocations are checked again
                        # before each write, including after slow provider IO.
                        binding = self._binding(path.stem)
                        state = self._plan(state, self._snapshot(binding))
                        self._write(path, state)
                        row = next((row for row in state["outbox"] if row["id"] == notice_id), None)
                        if row is None or row["status"] not in {"pending", "unconfirmed"}:
                            continue
                        route = self._route(binding, state["route"])
                        if row["status"] == "pending":
                            text = present_lark_conversation_text(row["body"])
                            intent = {"route": route, "text": text, "idempotency_key": "owner-attn-" + row["id"][:32]}
                            state = self._observe(state, row, "write_started", intent=intent)
                            self._write(path, state)  # frozen before provider effect
                            result = call(self.transport.runner, lark_args(cli_bin=self.transport.cli_bin, profile=route["profile"],
                                tail=["im", "+messages-send", "--as", "bot", "--chat-id", route["event"]["chat_id"],
                                      "--text", text, "--idempotency-key", intent["idempotency_key"], "--json"]))
                            payload = json_payload(result)
                            message_id = (payload.get("data") or {}).get("message_id")
                            state = self._observe(state, row, "spawn_failed" if result.get("spawn_failed") else "write_observed",
                                                  message_id=message_id)
                            self._write(path, state)  # locator survives failed readback/restart
                            row = next(row for row in state["outbox"] if row["id"] == notice_id)
                        if row["status"] == "unconfirmed" and row.get("message_id"):
                            if row["intent"]["route"] != route:
                                raise ValueError("the original notification destination changed")
                            readback = self._verify(row, route, binding)
                            state = self._observe(state, row, "readback_verified" if readback else "readback_pending", readback=readback)
                            self._write(path, state)
                            verified += int(readback is not None)
                except (ValueError, OSError, KeyError, TypeError, RuntimeError):
                    # Keep prior journals and unknown outcomes; no retry of an
                    # ambiguous create. Expose a safe actionable health state.
                    state["error_code"] = "source_or_delivery_unavailable"
                    self._write(path, state)
        return verified
