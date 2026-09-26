"""Owner-local durable state for LoopX Chat sessions and turns."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Any
import uuid
from weakref import WeakValueDictionary

from .chat import require_matching_replay, resolve_attached_completion_replay
from .capabilities.steward_executor.allocation import (
    normalize_manager_executor_allocation,
)
from .chat_event_cache import ChatEventCache
from .chat_ingress import ChatIngressStore
from .chat_turn_acceptance import (
    CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA,
    AcceptedManagedTurn,
    plan_managed_turn_acceptance,
)
from .file_lock import exclusive_file_lock


CHAT_STORE_SCHEMA_VERSION = "loopx_chat_store_v1"
CHAT_SESSION_SCHEMA_VERSION = "loopx_chat_session_state_v1"
CHAT_TURN_SCHEMA_VERSION = "loopx_chat_turn_state_v1"
CHAT_EVENT_SCHEMA_VERSION = "loopx_chat_event_v1"
CHAT_MESSAGE_SCHEMA_VERSION = "loopx_chat_message_v1"
CHAT_INGRESS_SCHEMA_VERSION = "loopx_chat_ingress_receipt_v1"
CHAT_SESSION_MODE_MANAGED = "managed_runtime"
CHAT_SESSION_MODE_ATTACHED = "attached_host"
RESUMABLE_SESSION_STATES = {"ready", "busy", "stale", "resuming"}
TERMINAL_TURN_STATES = {"completed", "interrupted", "timed_out", "failed"}
TERMINAL_EVENT_KINDS = {"turn.completed", "turn.failed", "turn.interrupted"}
REPLAY_ONLY_EVENT_KINDS = {"answer.delta", "assistant.delta", "agent.phase", "turn.activity"}
SESSION_QUEUE_MAX_PENDING = 20
SESSION_QUEUE_TTL_SECONDS = 3600

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _instant(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _opaque_id(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def _upstream_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token or len(token) > 1024 or any(ord(character) < 32 for character in token):
        raise ValueError("upstream_thread_id must be a bounded opaque string")
    return token


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _session_channel(payload: dict[str, Any]) -> str:
    channel_id = str(payload.get("channel_id") or "").strip()
    if channel_id:
        return channel_id
    return f"goal.{payload.get('goal_id')}"


def _atomic_write_json(path: Path, payload: dict[str, Any], *, preserve_mode: bool = False) -> None:
    previous_mode = path.stat().st_mode & 0o777 if preserve_mode and path.exists() else 0o600
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, previous_mode)
    os.replace(temporary, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    _append_jsonl_rows(path, [payload])


def _repair_incomplete_jsonl_tail(path: Path) -> None:
    try:
        handle = path.open("r+b")
    except FileNotFoundError:
        return
    with handle:
        end = handle.seek(0, os.SEEK_END)
        if end == 0:
            return
        handle.seek(end - 1)
        if handle.read(1) == b"\n":
            return
        cursor = end
        truncate_at = 0
        while cursor > 0:
            start = max(0, cursor - 8192)
            handle.seek(start)
            chunk = handle.read(cursor - start)
            if (newline := chunk.rfind(b"\n")) >= 0:
                truncate_at = start + newline + 1
                break
            cursor = start
        handle.seek(truncate_at)
        tail = handle.read(end - truncate_at)
        try:
            item = json.loads(tail)
        except (UnicodeDecodeError, json.JSONDecodeError):
            handle.truncate(truncate_at)
        else:
            if isinstance(item, dict):
                handle.seek(end)
                handle.write(b"\n")
            else:
                handle.truncate(truncate_at)
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _repair_incomplete_jsonl_tail(path)
    with path.open("a", encoding="utf-8") as handle:
        for payload in rows:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _replace_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class ChatSessionStore(ChatIngressStore):
    """Filesystem store kept outside project and public LoopX run history."""

    def __init__(self, runtime_root: Path) -> None:
        self.root = runtime_root.expanduser().resolve() / "chat"
        self.sessions_root = self.root / "sessions"
        self._session_lock_guard = threading.Lock()
        self._session_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
        self._event_lock = threading.RLock()
        self._event_cache = ChatEventCache(self._event_lock)
        self._event_pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._event_flush_locks: WeakValueDictionary[tuple[str, str], threading.Lock] = WeakValueDictionary()
        self.sessions_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.sessions_root, 0o700)
        self.compact_completed_events()
        self._recover_turn_completions()

    def _session_dir(self, session_id: str) -> Path:
        token = _opaque_id(session_id, field="session_id")
        if token in {".", ".."}:
            raise ValueError("session_id must be a compact opaque id")
        return self.sessions_root / token

    def _session_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _session_lock(self, session_id: str) -> threading.Lock:
        token = _opaque_id(session_id, field="session_id")
        with self._session_lock_guard:
            return self._session_locks.setdefault(token, threading.Lock())

    def _turn_path(self, session_id: str, turn_id: str) -> Path:
        return self._session_dir(session_id) / "turns" / f"{_opaque_id(turn_id, field='turn_id')}.json"

    def _event_path(self, session_id: str, turn_id: str) -> Path:
        return self._session_dir(session_id) / "turns" / f"{_opaque_id(turn_id, field='turn_id')}.events.jsonl"

    def _ingress_path(self, session_id: str, client_ingress_id: str) -> Path:
        return (
            self._session_dir(session_id)
            / "ingress"
            / f"{_opaque_id(client_ingress_id, field='client_ingress_id')}.json"
        )

    def create_session(
        self,
        *,
        goal_id: str,
        agent_id: str,
        adapter_kind: str,
        upstream_thread_id: str,
        upstream_mode: str = "default",
        channel_id: str | None = None,
        session_id: str | None = None,
        session_mode: str = CHAT_SESSION_MODE_MANAGED,
        executor_endpoint_id: str | None = None,
        host_surface: str | None = None,
        attached_capabilities: dict[str, bool] | None = None,
        codex_home: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        token = _opaque_id(session_id or uuid.uuid4().hex, field="session_id")
        normalized_mode = _opaque_id(session_mode, field="session_mode")
        if codex_home is not None and (
            not Path(codex_home).is_absolute() or agent_id != "codex"
            or normalized_mode != CHAT_SESSION_MODE_MANAGED
        ):
            raise ValueError("codex_home must be an absolute managed Codex home")
        if normalized_mode not in {
            CHAT_SESSION_MODE_MANAGED,
            CHAT_SESSION_MODE_ATTACHED,
        }:
            raise ValueError("session_mode must be managed_runtime or attached_host")
        normalized_host_surface = (
            _opaque_id(host_surface, field="host_surface") if host_surface else None
        )
        if normalized_mode == CHAT_SESSION_MODE_ATTACHED and not normalized_host_surface:
            raise ValueError("attached_host sessions require host_surface")
        capabilities = {
            str(key): bool(value)
            for key, value in (attached_capabilities or {}).items()
            if str(key)
            in {"live_steering", "session_queue", "claim_wait", "reply_readback"}
        }
        normalized_goal_id = _opaque_id(goal_id, field="goal_id")
        normalized_agent_id = _opaque_id(agent_id, field="agent_id")
        normalized_executor_endpoint_id = _opaque_id(
            executor_endpoint_id or agent_id,
            field="executor_endpoint_id",
        )
        normalized_adapter_kind = _opaque_id(adapter_kind, field="adapter_kind")
        normalized_upstream_thread_id = _upstream_id(upstream_thread_id)
        normalized_upstream_mode = _opaque_id(upstream_mode, field="upstream_mode")
        normalized_channel_id = _opaque_id(
            channel_id or f"goal.{goal_id}",
            field="channel_id",
        )
        session_dir = self._session_dir(token)
        session_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        (session_dir / "turns").mkdir(mode=0o700)
        payload = {
            "schema_version": CHAT_SESSION_SCHEMA_VERSION,
            "session_id": token,
            "goal_id": normalized_goal_id,
            "agent_id": normalized_agent_id,
            "executor_endpoint_id": normalized_executor_endpoint_id,
            "adapter_kind": normalized_adapter_kind,
            "upstream_thread_id": normalized_upstream_thread_id,
            "upstream_mode": normalized_upstream_mode,
            "codex_home": codex_home,
            "session_mode": normalized_mode,
            "host_surface": normalized_host_surface,
            "attached_capabilities": capabilities,
            "channel_id": normalized_channel_id,
            "status": "ready",
            "active_turn_id": None,
            "last_error_code": None,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        }
        _atomic_write_json(self._session_path(token), payload)
        os.chmod(self._session_path(token), 0o600)
        return payload

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        payload = _read_json(self._session_path(session_id))
        return payload if payload.get("schema_version") == CHAT_SESSION_SCHEMA_VERSION else None

    def update_session(self, session_id: str, **changes: Any) -> dict[str, Any]:
        path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                path,
                agent_id="loopx-chat",
                operation="update_chat_session",
            ):
                payload = self.load_session(session_id)
                if payload is None:
                    raise KeyError("chat session was not found")
                allowed = {
                    "status",
                    "active_turn_id",
                    "last_activity_at",
                    "last_error_code",
                    "upstream_thread_id",
                    "upstream_mode",
                    "codex_home",
                    "manager_context_version",
                    "coordination_context_version",
                    "loopx_mode",
                    "loopx_tools", "loopx_executor",
                    "loopx_deliveries",
                    "native_goal",
                    "manager_authorization_scope_id",
                    "manager_runtime_profile",
                    "manager_runtime_configuration_revision",
                    "manager_runtime_status",
                    "manager_runtime_sandbox",
                    "manager_runtime_standing_grant",
                    "manager_runtime_tool_classes",
                    "manager_executor_allocation",
                    "goal_id",
                }
                unknown = set(changes) - allowed
                if unknown:
                    raise ValueError(f"unsupported chat session fields: {sorted(unknown)}")
                if "coordination_context_version" in changes:
                    version = changes["coordination_context_version"]
                    if type(version) is not int or version < 1:
                        raise ValueError("coordination context version must be a positive integer")
                if "goal_id" in changes:
                    from .chat_manager import MANAGER_AGENT_GOAL_ID
                    if _session_channel(payload) != "manager" or changes["goal_id"] != MANAGER_AGENT_GOAL_ID:
                        raise ValueError("only the owner manager may migrate to global identity")
                if "upstream_thread_id" in changes:
                    changes["upstream_thread_id"] = _upstream_id(changes["upstream_thread_id"])
                if "upstream_mode" in changes:
                    changes["upstream_mode"] = _opaque_id(changes["upstream_mode"], field="upstream_mode")
                if "manager_authorization_scope_id" in changes:
                    changes["manager_authorization_scope_id"] = _opaque_id(
                        changes["manager_authorization_scope_id"],
                        field="manager_authorization_scope_id",
                    )
                for field in (
                    "manager_runtime_profile",
                    "manager_runtime_status",
                    "manager_runtime_sandbox",
                    "manager_runtime_standing_grant",
                ):
                    if field in changes:
                        changes[field] = _opaque_id(changes[field], field=field)
                if "manager_runtime_configuration_revision" in changes:
                    revision = str(
                        changes["manager_runtime_configuration_revision"] or ""
                    ).strip()
                    if not revision or len(revision) > 160 or any(
                        ord(character) < 32 for character in revision
                    ):
                        raise ValueError(
                            "manager_runtime_configuration_revision is invalid"
                        )
                    changes["manager_runtime_configuration_revision"] = revision
                if "manager_runtime_tool_classes" in changes:
                    tool_classes = changes["manager_runtime_tool_classes"]
                    if not isinstance(tool_classes, list):
                        raise TypeError("manager_runtime_tool_classes must be a list")
                    changes["manager_runtime_tool_classes"] = [
                        _opaque_id(item, field="manager_runtime_tool_class")
                        for item in tool_classes
                    ]
                if "manager_executor_allocation" in changes:
                    allocation = changes["manager_executor_allocation"]
                    if not isinstance(allocation, dict):
                        raise TypeError("manager_executor_allocation must be an object")
                    changes["manager_executor_allocation"] = (
                        normalize_manager_executor_allocation(allocation)
                    )
                if "codex_home" in changes:
                    home = changes["codex_home"]
                    if (not isinstance(home, str) or not Path(home).is_absolute()
                            or payload.get("agent_id") != "codex"
                            or payload.get("session_mode") != CHAT_SESSION_MODE_MANAGED):
                        raise ValueError("codex_home must be an absolute managed Codex home")
                    if payload.get("codex_home") not in (None, home):
                        raise ValueError("cannot rebind a managed Codex home")
                payload.update(changes)
                payload["updated_at"] = utc_now()
                _atomic_write_json(path, payload, preserve_mode=True)
                return payload

    def prepare_managed_session_resume(
        self,
        session_id: str,
        *,
        preserve_active_turn: bool,
    ) -> tuple[dict[str, Any], bool]:
        """Begin managed resume without taking ownership from an active Turn."""

        path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                path,
                agent_id="loopx-chat",
                operation="prepare_managed_chat_resume",
            ):
                payload = self.load_session(session_id)
                if payload is None or payload.get("status") == "closed":
                    raise KeyError("chat session was not found")
                active_turn_id = str(payload.get("active_turn_id") or "")
                active_turn = (
                    self.load_turn(session_id, active_turn_id)
                    if active_turn_id
                    else None
                )
                if (
                    preserve_active_turn
                    and active_turn is not None
                    and active_turn.get("status")
                    in {"queued", "starting", "running", "completing", "interrupting"}
                ):
                    return payload, True
                resume_snapshot = dict(payload)
                payload.update(
                    {
                        "status": "stale",
                        "active_turn_id": None,
                        "last_error_code": None,
                        "updated_at": utc_now(),
                    }
                )
                _atomic_write_json(path, payload, preserve_mode=True)
                return resume_snapshot, False

    def restore_managed_session_if_idle(
        self,
        session_id: str,
        *,
        upstream_thread_id: str | None = None,
        upstream_mode: str | None = None,
    ) -> dict[str, Any]:
        """Restore a managed Session only while it has no active Turn."""

        path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                path,
                agent_id="loopx-chat",
                operation="restore_managed_chat_session",
            ):
                payload = self.load_session(session_id)
                if payload is None:
                    raise KeyError("chat session was not found")
                if payload.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                    raise ValueError("the selected Session is an attached host session")
                if payload.get("status") == "closed":
                    raise KeyError("chat session was not found")
                identity_changes: dict[str, Any] = {}
                if upstream_thread_id is not None:
                    identity_changes["upstream_thread_id"] = _upstream_id(
                        upstream_thread_id
                    )
                if upstream_mode is not None:
                    identity_changes["upstream_mode"] = _opaque_id(
                        upstream_mode,
                        field="upstream_mode",
                    )
                if payload.get("active_turn_id"):
                    if identity_changes:
                        payload.update(identity_changes)
                        payload["updated_at"] = utc_now()
                        _atomic_write_json(path, payload, preserve_mode=True)
                    return payload
                changes = {
                    "status": "ready",
                    "last_error_code": None,
                    "last_activity_at": utc_now(),
                    **identity_changes,
                }
                payload.update(changes)
                payload["updated_at"] = utc_now()
                _atomic_write_json(path, payload, preserve_mode=True)
                return payload

    def release_active_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        last_activity_at: str,
        last_error_code: str | None,
    ) -> bool:
        """Make a Session ready only while the completing Turn still owns it."""

        path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                path,
                agent_id="loopx-chat",
                operation="release_active_chat_turn",
            ):
                payload = self.load_session(session_id)
                if payload is None:
                    raise KeyError("chat session was not found")
                if payload.get("active_turn_id") != turn_id:
                    return False
                payload.update(
                    {
                        "status": "ready",
                        "active_turn_id": None,
                        "last_activity_at": last_activity_at,
                        "last_error_code": last_error_code,
                        "updated_at": utc_now(),
                    }
                )
                _atomic_write_json(path, payload, preserve_mode=True)
                return True

    def latest_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> dict[str, Any] | None:
        if goal_id is None and channel_id is None:
            raise ValueError("channel_id is required when goal_id is omitted")
        selected_channel = channel_id or f"goal.{goal_id}"
        candidates: list[dict[str, Any]] = []
        for path in self.sessions_root.glob("*/session.json"):
            payload = _read_json(path)
            if (
                payload.get("schema_version") == CHAT_SESSION_SCHEMA_VERSION
                and (goal_id is None or payload.get("goal_id") == goal_id)
                and payload.get("agent_id") == agent_id
                and payload.get("status") in RESUMABLE_SESSION_STATES
                and _session_channel(payload) == selected_channel
            ):
                candidates.append(payload)
        return max(candidates, key=lambda item: str(item.get("updated_at") or ""), default=None)

    def list_sessions(
        self,
        *,
        goal_id: str | None = None,
        agent_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.sessions_root.glob("*/session.json"):
            payload = _read_json(path)
            if payload.get("schema_version") != CHAT_SESSION_SCHEMA_VERSION:
                continue
            if goal_id and payload.get("goal_id") != goal_id:
                continue
            if agent_id and payload.get("agent_id") != agent_id:
                continue
            if channel_id and _session_channel(payload) != channel_id:
                continue
            rows.append(self.public_session(payload))
        return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        text: str,
        turn_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        origin: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        session_dir = self._session_dir(session_id)
        path = session_dir / "messages.jsonl"
        payload = {
            "schema_version": CHAT_MESSAGE_SCHEMA_VERSION,
            "message_id": _opaque_id(
                message_id or uuid.uuid4().hex,
                field="message_id",
            ),
            "turn_id": _opaque_id(turn_id, field="turn_id") if turn_id else None,
            "role": role,
            "text": str(text),
            **({"origin": _opaque_id(origin, field="origin")} if origin else {}),
            **({"attachments": attachments} if attachments else {}),
            "created_at": utc_now(),
        }
        with exclusive_file_lock(path, agent_id="loopx-chat", operation="append_chat_message"):
            if message_id:
                for existing in _read_jsonl(path):
                    if existing.get("message_id") == payload["message_id"]:
                        return existing
            _append_jsonl(path, payload)
        return payload

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        return _read_jsonl(self._session_dir(session_id) / "messages.jsonl")

    def prepared_managed_turn_request(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        session_token = _opaque_id(session_id, field="session_id")
        session_path = self._session_path(session_token)
        with self._session_lock(session_token):
            with exclusive_file_lock(
                session_path,
                agent_id="loopx-chat",
                operation="read_prepared_managed_chat_turn",
            ):
                prepared = [
                    payload
                    for path in (
                        self._session_dir(session_token) / "turns"
                    ).glob("*.json")
                    if (
                        payload := _read_json(path)
                    ).get("schema_version") == CHAT_TURN_SCHEMA_VERSION
                    and "_acceptance" in payload
                ]
                if not prepared:
                    return None
                if len(prepared) != 1:
                    raise ValueError(
                        "chat turn acceptance state is inconsistent"
                    )
                turn = prepared[0]
                acceptance = turn.get("_acceptance")
                if (
                    not isinstance(acceptance, dict)
                    or acceptance.get("schema_version")
                    != CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA
                    or acceptance.get("phase") != "prepared"
                    or not isinstance(turn.get("message"), str)
                    or not isinstance(
                        acceptance.get("display_message"),
                        str,
                    )
                ):
                    raise ValueError(
                        "chat turn acceptance state is inconsistent"
                    )
                attachments = acceptance.get("attachments")
                if attachments is not None and (
                    not isinstance(attachments, list)
                    or any(
                        not isinstance(attachment, dict)
                        for attachment in attachments
                    )
                ):
                    raise ValueError(
                        "chat turn acceptance state is inconsistent"
                    )
                loopx_execution = turn.get("loopx_execution", False)
                loopx_request = turn.get("loopx_request")
                if (
                    not isinstance(loopx_execution, bool)
                    or (
                        loopx_request is not None
                        and not isinstance(loopx_request, dict)
                    )
                ):
                    raise ValueError(
                        "chat turn acceptance state is inconsistent"
                    )
                return {
                    "client_turn_id": _opaque_id(
                        turn.get("client_turn_id"),
                        field="client_turn_id",
                    ),
                    "message": turn["message"],
                    "attachments": attachments,
                    "origin": _opaque_id(
                        turn.get("origin"),
                        field="origin",
                    ),
                    "display_message": acceptance["display_message"],
                    "loopx_execution": loopx_execution,
                    "loopx_request": loopx_request,
                }

    def create_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
        origin: str = "web",
        display_message: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        accepted = self.accept_managed_turn(
            session_id,
            client_turn_id=client_turn_id,
            message=message,
            attachments=attachments,
            origin=origin,
            display_message=display_message,
        )
        return accepted.turn, accepted.created

    def accept_managed_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
        origin: str = "web",
        display_message: str | None = None,
        loopx_execution: bool = False,
        loopx_request: dict[str, object] | None = None,
    ) -> AcceptedManagedTurn:
        session_token = _opaque_id(session_id, field="session_id")
        client_id = _opaque_id(client_turn_id, field="client_turn_id")
        normalized_origin = _opaque_id(origin, field="origin")
        execution_message = str(message)
        visible_message = (
            str(display_message)
            if display_message is not None
            else execution_message
        )
        normalized_attachments = attachments or None
        candidate_turn_id = uuid.uuid4().hex
        candidate_message_id = uuid.uuid4().hex
        accepted_at = utc_now()
        session_path = self._session_path(session_token)
        with self._session_lock(session_token):
            with exclusive_file_lock(
                session_path,
                agent_id="loopx-chat",
                operation="accept_managed_chat_turn",
            ):
                turns = [
                    payload
                    for path in (
                        self._session_dir(session_token) / "turns"
                    ).glob("*.json")
                    if (
                        payload := _read_json(path)
                    ).get("schema_version") == CHAT_TURN_SCHEMA_VERSION
                ]
                existing = next(
                    (
                        turn
                        for turn in turns
                        if turn.get("client_turn_id") == client_id
                    ),
                    None,
                )
                session = self.load_session(session_token)
                active_turn = None
                if session is not None and session.get("active_turn_id"):
                    active_turn = self.load_turn(
                        session_token,
                        str(session["active_turn_id"]),
                    )
                existing_turn_id = (
                    str(existing.get("turn_id"))
                    if existing is not None
                    else None
                )
                if existing_turn_id is not None:
                    self.flush_events(session_token, existing_turn_id)
                plan = plan_managed_turn_acceptance(
                    session_id=session_token,
                    client_turn_id=client_id,
                    message=execution_message,
                    display_message=visible_message,
                    attachments=normalized_attachments,
                    origin=normalized_origin,
                    loopx_execution=loopx_execution,
                    loopx_request=loopx_request,
                    candidate_turn_id=candidate_turn_id,
                    candidate_message_id=candidate_message_id,
                    accepted_at=accepted_at,
                    session=session,
                    active_turn=active_turn,
                    matching_turn=existing,
                    turns=turns,
                    messages=self.messages(session_token),
                    queued_events=(
                        _read_jsonl(
                            self._event_path(
                                session_token,
                                existing_turn_id,
                            )
                        )
                        if existing_turn_id is not None
                        else []
                    ),
                )
                if plan.writes.prepare_turn:
                    if plan.turn_id != candidate_turn_id:
                        raise ValueError(
                            "chat turn acceptance selected an invalid candidate"
                        )
                    turn_id = candidate_turn_id
                    payload = {
                        "schema_version": CHAT_TURN_SCHEMA_VERSION,
                        "turn_id": turn_id,
                        "session_id": session_token,
                        "client_turn_id": client_id,
                        "status": "queued",
                        "message": execution_message,
                        "origin": normalized_origin,
                        "upstream_turn_id": None,
                        "response": None,
                        "error_code": None,
                        "error": None,
                        "created_at": accepted_at,
                        "started_at": None,
                        "first_event_at": None,
                        "completed_at": None,
                        "last_activity_at": accepted_at,
                        "delta_count": 0,
                        "sse_reconnect_count": 0,
                        **(
                            {
                                "loopx_execution": True,
                                "loopx_request": loopx_request,
                            }
                            if loopx_execution
                            else {}
                        ),
                        "_acceptance": {
                            "schema_version": (
                                CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA
                            ),
                            "phase": "prepared",
                            "request_sha256": plan.request_sha256,
                            "message_id": plan.message_id,
                            "display_message": visible_message,
                            "attachments": normalized_attachments,
                        },
                    }
                    path = self._turn_path(session_token, turn_id)
                    _atomic_write_json(path, payload)
                    os.chmod(path, 0o600)
                else:
                    if existing is None or plan.turn_id != existing_turn_id:
                        raise ValueError(
                            "chat turn acceptance selected an invalid replay"
                        )
                    payload = existing

                if plan.writes.activate_session:
                    if session is None:
                        raise KeyError("chat session was not found")
                    session.update(
                        {
                            "status": "busy",
                            "active_turn_id": plan.turn_id,
                            "last_activity_at": accepted_at,
                            "updated_at": utc_now(),
                        }
                    )
                    _atomic_write_json(
                        session_path,
                        session,
                        preserve_mode=True,
                    )

                if plan.writes.append_message:
                    self.append_message(
                        session_token,
                        role="user",
                        text=visible_message,
                        turn_id=plan.turn_id,
                        attachments=normalized_attachments,
                        origin=normalized_origin,
                        message_id=plan.message_id,
                    )
                if plan.writes.append_queued_event:
                    self.append_event(
                        session_token,
                        plan.turn_id,
                        kind="turn.queued",
                        payload={},
                    )
                if plan.writes.settle_turn:
                    settled = self.load_turn(session_token, plan.turn_id)
                    if settled is None:
                        raise ValueError(
                            "chat turn acceptance lost its prepared turn"
                        )
                    settled.pop("_acceptance", None)
                    _atomic_write_json(
                        self._turn_path(session_token, plan.turn_id),
                        settled,
                        preserve_mode=True,
                    )
                    payload = settled
                else:
                    current = self.load_turn(session_token, plan.turn_id)
                    if current is None:
                        raise ValueError(
                            "chat turn acceptance lost its replayed turn"
                        )
                    payload = current
                return AcceptedManagedTurn(
                    turn=payload,
                    created=plan.created,
                    dispatch_required=plan.dispatch_required,
                    dispatch_reason=plan.dispatch_reason,
                )

    def create_queued_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        ttl_seconds: int = SESSION_QUEUE_TTL_SECONDS,
        origin: str = "external",
    ) -> tuple[dict[str, Any], bool]:
        """Persist one bounded follow-up without replacing the active Turn."""

        client_id = _opaque_id(client_turn_id, field="client_turn_id")
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
                            "origin": _opaque_id(origin, field="origin"),
                        },
                    )
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
                    "client_turn_id": client_id,
                    "status": "queued",
                    "message": str(message),
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
            self.append_message(
                session_id,
                role="user",
                text=message,
                turn_id=turn_id,
                origin=origin,
            )
            self.append_event(
                session_id,
                turn_id,
                kind="turn.queued",
                payload={"delivery_mode": "session_queue"},
            )
            return payload, True

    def queued_turns(self, session_id: str) -> list[dict[str, Any]]:
        session = self.load_session(session_id)
        if session is None:
            raise KeyError("chat session was not found")
        active_turn_id = str(session.get("active_turn_id") or "")
        turns_dir = self._session_dir(session_id) / "turns"
        rows: list[dict[str, Any]] = []
        for path in turns_dir.glob("*.json") if turns_dir.is_dir() else []:
            if path.name.endswith(".events.json"):
                continue
            payload = _read_json(path)
            if (
                payload.get("schema_version") == CHAT_TURN_SCHEMA_VERSION
                and payload.get("status") == "queued"
                and str(payload.get("turn_id") or "") != active_turn_id
            ):
                rows.append(payload)
        return sorted(
            rows,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("turn_id") or ""),
            ),
        )

    def _settle_expired_queued_turns(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Persist queue expiry and return the queued Turns still eligible to run."""

        live_turns: list[dict[str, Any]] = []
        for turn in self.queued_turns(session_id):
            expires_at = _instant(turn.get("expires_at"))
            if expires_at is None or expires_at > now:
                live_turns.append(turn)
                continue
            turn_id = str(turn["turn_id"])
            completed = utc_now()
            turn.update(
                {
                    "status": "timed_out",
                    "error_code": "session_queue_expired",
                    "error": "Queued Agent ingress expired before dispatch.",
                    "completed_at": completed,
                    "last_activity_at": completed,
                }
            )
            _atomic_write_json(
                self._turn_path(session_id, turn_id),
                turn,
                preserve_mode=True,
            )
            self.append_event(
                session_id,
                turn_id,
                kind="turn.failed",
                payload={"error_code": "session_queue_expired"},
            )
        return live_turns

    def claim_next_queued_turn(
        self,
        session_id: str,
        *,
        host_claim_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically make the oldest live queued Turn active for its Session."""

        session_path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                session_path,
                agent_id="loopx-chat",
                operation="claim_session_queue_turn",
            ):
                session = self.load_session(session_id)
                if session is None:
                    raise KeyError("chat session was not found")
                if session.get("status") == "closed":
                    return None
                active_turn_id = str(session.get("active_turn_id") or "")
                if active_turn_id:
                    active = self.load_turn(session_id, active_turn_id)
                    if (
                        active
                        and host_claim_id
                        and active.get("host_claim_id") == host_claim_id
                        and active.get("status") in {"starting", "running"}
                    ):
                        return active
                    return None
                for turn in self._settle_expired_queued_turns(
                    session_id,
                    now=datetime.now(timezone.utc),
                ):
                    turn_id = str(turn["turn_id"])
                    if host_claim_id:
                        now_text = utc_now()
                        turn.update(
                            {
                                "status": "running",
                                "host_claim_id": _opaque_id(
                                    host_claim_id,
                                    field="host_claim_id",
                                ),
                                "started_at": turn.get("started_at") or now_text,
                                "last_activity_at": now_text,
                            }
                        )
                        _atomic_write_json(
                            self._turn_path(session_id, turn_id),
                            turn,
                            preserve_mode=True,
                        )
                        self.append_event(
                            session_id,
                            turn_id,
                            kind="turn.claimed_by_attached_host",
                            payload={"host_claim_id": host_claim_id},
                        )
                    session.update(
                        {
                            "status": "busy",
                            "active_turn_id": turn_id,
                            "last_activity_at": utc_now(),
                            "updated_at": utc_now(),
                        }
                    )
                    _atomic_write_json(session_path, session, preserve_mode=True)
                    return turn
                return None

    def turn_for_client(self, session_id: str, client_turn_id: str) -> dict[str, Any] | None:
        turns_dir = self._session_dir(session_id) / "turns"
        for path in turns_dir.glob("*.json") if turns_dir.is_dir() else []:
            if path.name.endswith(".events.json"):
                continue
            payload = _read_json(path)
            if payload.get("client_turn_id") == client_turn_id:
                return payload
        return None

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        payload = _read_json(self._turn_path(session_id, turn_id))
        return payload if payload.get("schema_version") == CHAT_TURN_SCHEMA_VERSION else None

    def update_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        expected_statuses: set[str] | None = None,
        **changes: Any,
    ) -> dict[str, Any] | None:
        path = self._turn_path(session_id, turn_id)
        with exclusive_file_lock(path, agent_id="loopx-chat", operation="update_chat_turn"):
            payload = self.load_turn(session_id, turn_id)
            if payload is None:
                raise KeyError("chat turn was not found")
            allowed = {
                "status", "upstream_turn_id", "response", "error_code", "error",
                "started_at", "first_event_at", "completed_at", "last_activity_at",
                "delta_count", "sse_reconnect_count", "expires_at", "host_claim_id",
                "completion_id", "loopx_execution", "loopx_request",
            }
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"unsupported chat turn fields: {sorted(unknown)}")
            if expected_statuses is not None and payload.get("status") not in expected_statuses:
                return None
            payload.update(changes)
            _atomic_write_json(path, payload, preserve_mode=True)
            return payload

    def append_completed_response_events(
        self,
        session_id: str,
        turn_id: str,
        *,
        response: dict[str, Any],
        terminal_metadata: dict[str, Any] | None = None,
        idempotent: bool = False,
    ) -> None:
        """Emit the canonical structured-response events for a completed Turn."""

        events: list[tuple[str, dict[str, Any]]] = []
        for proposal in response.get("proposals") or []:
            events.append(("proposal.ready", {"proposal": proposal}))
        if response.get("gate"):
            events.append(("gate.ready", {"gate": response["gate"]}))
        events.append(
            (
                "turn.completed",
                {**(terminal_metadata or {}), "response": response},
            )
        )
        if idempotent:
            kinds = {kind for kind, _payload in events}
            existing = [
                (str(event.get("kind") or ""), event.get("payload"))
                for event in self.events_after(session_id, turn_id, None)
                if event.get("kind") in kinds
            ]
            if existing != events[: len(existing)]:
                raise ValueError("chat completion event history conflicts with response")
            events = events[len(existing) :]
        for kind, payload in events:
            self.append_event(session_id, turn_id, kind=kind, payload=payload)

    def finalize_managed_turn_completion(
        self,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        """Publish a reserved managed completion after all visible side effects."""

        path = self._turn_path(session_id, turn_id)
        with exclusive_file_lock(
            path,
            agent_id="loopx-chat",
            operation="finalize_managed_chat_turn",
        ):
            turn = self.load_turn(session_id, turn_id)
            if turn is None:
                raise KeyError("chat turn was not found")
            if turn.get("status") == "completed":
                return turn
            if turn.get("status") != "completing":
                return None
            response = turn.get("response")
            if not isinstance(response, dict):
                raise ValueError("completing chat turn requires a response")
            completed_at = str(turn.get("completed_at") or utc_now())
            if response.get("message"):
                self.append_message(
                    session_id,
                    role="agent",
                    text=str(response["message"]),
                    turn_id=turn_id,
                    message_id=f"managed.{turn_id}.completed",
                )
            self.append_completed_response_events(
                session_id,
                turn_id,
                response=response,
                idempotent=True,
            )
            self.release_active_turn(
                session_id,
                turn_id,
                last_activity_at=completed_at,
                last_error_code=None,
            )
            turn["status"] = "completed"
            _atomic_write_json(path, turn, preserve_mode=True)
            return turn

    def finalize_turn_completion(
        self,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        turn = self.load_turn(session_id, turn_id)
        if turn is None:
            raise KeyError("chat turn was not found")
        if turn.get("completion_id"):
            return self.finalize_attached_turn_completion(session_id, turn_id)
        return self.finalize_managed_turn_completion(session_id, turn_id)

    def _recover_turn_completions(self) -> None:
        # ponytail: owner-local startup scan; add an index only if history makes it measurable.
        for path in self.sessions_root.glob("*/turns/*.json"):
            turn = _read_json(path)
            if (
                turn.get("schema_version") == CHAT_TURN_SCHEMA_VERSION
                and turn.get("status") == "completing"
            ):
                self.finalize_turn_completion(
                    str(turn["session_id"]),
                    str(turn["turn_id"]),
                )

    def finalize_attached_turn_completion(
        self,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        """Publish a reserved attached completion after all visible side effects."""

        turn_path = self._turn_path(session_id, turn_id)
        with exclusive_file_lock(
            turn_path,
            agent_id="loopx-chat",
            operation="finalize_attached_chat_turn",
        ):
            turn = self.load_turn(session_id, turn_id)
            if turn is None:
                raise KeyError("attached Agent turn was not found")
            if turn.get("status") not in {"completing", "completed"}:
                return None
            response = turn.get("response")
            if not isinstance(response, dict):
                raise ValueError("completing attached Agent turn requires a response")
            completion_id = _opaque_id(
                turn.get("completion_id"),
                field="completion_id",
            )
            completed_at = str(turn.get("completed_at") or utc_now())
            message_id = resolve_attached_completion_replay(
                self.messages(session_id), turn_id=turn_id,
                completion_id=completion_id,
                response_message=str(response.get("message") or ""),
            )
            if message_id:
                self.append_message(
                    session_id, role="agent", text=str(response.get("message") or ""),
                    turn_id=turn_id, origin="attached_host", message_id=message_id,
                )
            self.append_completed_response_events(
                session_id,
                turn_id,
                response=response,
                terminal_metadata={
                    "delivery_mode": CHAT_SESSION_MODE_ATTACHED,
                    "completion_id": completion_id,
                },
                idempotent=True,
            )
            self.release_active_turn(
                session_id,
                turn_id,
                last_activity_at=completed_at,
                last_error_code=None,
            )
            if turn.get("status") == "completing":
                turn["status"] = "completed"
                _atomic_write_json(turn_path, turn, preserve_mode=True)
            return turn

    def complete_attached_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        claim_id: str,
        completion_id: str,
        response: dict[str, Any],
        agent_message: str,
    ) -> tuple[dict[str, Any], bool]:
        """Reserve and finalize one duplicate-safe attached-host completion."""

        turn_path = self._turn_path(session_id, turn_id)
        normalized_claim_id = _opaque_id(claim_id, field="claim_id")
        normalized_completion_id = _opaque_id(completion_id, field="completion_id")
        with exclusive_file_lock(
            turn_path,
            agent_id="loopx-chat",
            operation="complete_attached_chat_turn",
        ):
            session = self.load_session(session_id)
            turn = self.load_turn(session_id, turn_id)
            if session is None or turn is None:
                raise KeyError("attached Agent turn was not found")
            created = turn.get("status") == "running"
            if turn.get("status") in {"completing", "completed"}:
                if turn.get("completion_id") != normalized_completion_id:
                    raise ValueError("the Turn was already completed by another receipt")
            elif created:
                if (
                    session.get("active_turn_id") != turn_id
                ):
                    raise ValueError(
                        "the Turn is not the active claimed attached-host Turn"
                    )
                if turn.get("host_claim_id") != normalized_claim_id:
                    raise ValueError(
                        "claim_id does not own the active attached-host Turn"
                    )
                completed_at = utc_now()
                turn.update(
                    {
                        "status": "completing",
                        "response": {
                            **response,
                            "message": str(response.get("message") or agent_message),
                        },
                        "completion_id": normalized_completion_id,
                        "completed_at": completed_at,
                        "last_activity_at": completed_at,
                        "error_code": None,
                        "error": None,
                    }
                )
                _atomic_write_json(turn_path, turn, preserve_mode=True)
            else:
                raise ValueError(
                    "the Turn is not the active claimed attached-host Turn"
                )
        finalized = self.finalize_attached_turn_completion(session_id, turn_id)
        if finalized is None:
            raise ValueError("the attached Agent turn could not be finalized")
        return finalized, created

    def close_attached_session(self, session_id: str) -> bool:
        """Close an idle attached Session without stranding a claimed Turn."""

        return self._close_session_if_idle(
            session_id,
            expected_mode=CHAT_SESSION_MODE_ATTACHED,
            operation="close_attached_chat_session",
            mode_error="the selected Session is not an attached host session",
            active_error="attached_session_turn_active",
            queue_error="attached_session_queue_pending",
        )

    def close_managed_session(self, session_id: str) -> bool:
        """Close an idle managed Session without stranding background work."""

        return self._close_session_if_idle(
            session_id,
            expected_mode=CHAT_SESSION_MODE_MANAGED,
            operation="close_managed_chat_session",
            mode_error="the selected Session is an attached host session",
            active_error="managed_session_turn_active",
            queue_error="managed_session_queue_pending",
        )

    def _close_session_if_idle(
        self,
        session_id: str,
        *,
        expected_mode: str,
        operation: str,
        mode_error: str,
        active_error: str,
        queue_error: str,
    ) -> bool:
        session_path = self._session_path(session_id)
        with self._session_lock(session_id):
            with exclusive_file_lock(
                session_path,
                agent_id="loopx-chat",
                operation=operation,
            ):
                session = self.load_session(session_id)
                if session is None:
                    return False
                if session.get("session_mode") != expected_mode:
                    raise ValueError(mode_error)
                if session.get("status") == "closed":
                    return True
                active_turn_id = str(session.get("active_turn_id") or "")
                if active_turn_id:
                    active_turn = self.load_turn(session_id, active_turn_id)
                    if active_turn is None or active_turn.get("status") not in TERMINAL_TURN_STATES:
                        raise RuntimeError(active_error)
                live_queued_turns = self._settle_expired_queued_turns(
                    session_id,
                    now=datetime.now(timezone.utc),
                )
                if live_queued_turns:
                    raise RuntimeError(queue_error)
                now = utc_now()
                session.update(
                    {
                        "status": "closed",
                        "active_turn_id": None,
                        "last_activity_at": now,
                        "updated_at": now,
                    }
                )
                _atomic_write_json(session_path, session, preserve_mode=True)
                return True

    def _event_rows_locked(self, session_id: str, turn_id: str) -> list[dict[str, Any]]:
        key = (session_id, turn_id)
        path = self._event_path(session_id, turn_id)
        revision = self._event_revision(path)
        rows = self._event_cache.get(key, revision)
        if rows is None:
            rows = _read_jsonl(path)
            self._event_cache.put(key, revision, rows)
        return rows

    @staticmethod
    def _event_revision(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _event_flush_lock(self, key: tuple[str, str]) -> threading.Lock:
        with self._event_lock:
            return self._event_flush_locks.setdefault(key, threading.Lock())

    def append_event(
        self,
        session_id: str,
        turn_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        buffered: bool = False,
    ) -> dict[str, Any]:
        key = (session_id, turn_id)
        event = {
            "schema_version": CHAT_EVENT_SCHEMA_VERSION,
            "kind": str(kind),
            "created_at": utc_now(),
            "payload": payload,
        }
        with self._event_lock:
            self._event_pending.setdefault(key, []).append(event)
        if not buffered:
            self.flush_events(session_id, turn_id)
        return event

    def flush_events(self, session_id: str, turn_id: str) -> int:
        """Persist queued events in one ordered append and one data fsync."""
        key = (session_id, turn_id)
        path = self._event_path(session_id, turn_id)
        flush_lock = self._event_flush_lock(key)
        flushed = 0
        with flush_lock:
            while True:
                with self._event_lock:
                    pending = self._event_pending.pop(key, [])
                if not pending:
                    return flushed
                try:
                    with exclusive_file_lock(path, agent_id="loopx-chat", operation="append_chat_events"):
                        rows = self._event_rows_locked(session_id, turn_id)
                        # Allocate after the last persisted sequence and append under the
                        # same file lock: row order stays strictly increasing by sequence.
                        # Compaction preserves this order but may leave sequence gaps.
                        sequence = int(rows[-1].get("sequence") or 0) if rows else 0
                        for event in pending:
                            sequence += 1
                            event["event_id"] = str(sequence)
                            event["sequence"] = sequence
                        _append_jsonl_rows(path, pending)
                        if any(row["kind"] in TERMINAL_EVENT_KINDS for row in pending):
                            self._event_cache.drop(key)
                        else:
                            self._event_cache.put(key, self._event_revision(path), [*rows, *pending])
                except Exception:
                    with self._event_lock:
                        later = self._event_pending.get(key, [])
                        self._event_pending[key] = [*pending, *later]
                    raise
                flushed += len(pending)

    def events_after(self, session_id: str, turn_id: str, event_id: str | None) -> list[dict[str, Any]]:
        try:
            after = int(event_id or 0)
        except ValueError:
            after = 0
        self.flush_events(session_id, turn_id)
        key = (session_id, turn_id)
        path = self._event_path(session_id, turn_id)
        revision = self._event_revision(path)
        rows = self._event_cache.get(key, revision)
        if rows is None:
            with exclusive_file_lock(path, agent_id="loopx-chat", operation="read_chat_events"):
                rows = self._event_rows_locked(session_id, turn_id)
        # Relies on the sequence ordering maintained by flush_events and compaction;
        # gaps are valid, but inserting or rewriting rows must preserve that order.
        start = bisect_right(rows, after, key=lambda row: int(row.get("sequence") or 0))
        result = rows[start:]
        if rows and rows[-1].get("kind") in TERMINAL_EVENT_KINDS:
            self._event_cache.retain_terminal(key, rows)
        return result

    def compact_completed_events(self, *, older_than_hours: float = 24.0) -> int:
        """Drop replay-only deltas after the durable final message is old enough."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.0, older_than_hours))
        compacted = 0
        for turn_path in self.sessions_root.glob("*/turns/*.json"):
            turn = _read_json(turn_path)
            if turn.get("status") not in {"completed", "interrupted", "timed_out", "failed"}:
                continue
            completed_at = str(turn.get("completed_at") or "")
            try:
                completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if completed > cutoff:
                continue
            session_id = turn_path.parent.parent.name
            turn_id = turn_path.stem
            self.flush_events(session_id, turn_id)
            event_path = turn_path.with_name(f"{turn_path.stem}.events.jsonl")
            revision = self._event_revision(event_path)
            if turn.get("event_compaction_revision") == list(revision or ()):
                continue
            with exclusive_file_lock(event_path, agent_id="loopx-chat", operation="compact_chat_events"):
                rows = self._event_rows_locked(session_id, turn_id)
                retained = [row for row in rows if row.get("kind") not in REPLAY_ONLY_EVENT_KINDS]
                if len(retained) != len(rows):
                    _replace_jsonl(event_path, retained)
                    compacted += 1
                revision = self._event_revision(event_path)
                self._event_cache.drop((session_id, turn_id))
            with exclusive_file_lock(turn_path, agent_id="loopx-chat", operation="mark_chat_events_compacted"):
                current = _read_json(turn_path)
                if current.get("status") in TERMINAL_TURN_STATES:
                    current["event_compaction_revision"] = list(revision or ())
                    _atomic_write_json(turn_path, current, preserve_mode=True)
        return compacted

    def public_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_mode = str(payload.get("session_mode") or CHAT_SESSION_MODE_MANAGED)
        return {
            key: payload.get(key)
            for key in (
                "session_id", "goal_id", "agent_id", "adapter_kind", "status",
                "active_turn_id", "last_error_code", "created_at", "updated_at", "last_activity_at",
            )
        } | {
            "session_mode": session_mode,
            "executor_endpoint_id": str(
                payload.get("executor_endpoint_id") or payload.get("agent_id") or ""
            ),
            "host_surface": payload.get("host_surface"),
            "attached_capabilities": (
                dict(payload.get("attached_capabilities") or {})
                if session_mode == CHAT_SESSION_MODE_ATTACHED
                else {}
            ),
            "channel_id": _session_channel(payload),
            "manager_runtime": (
                {
                    "schema_version": "manager_runtime_session_readback_v0",
                    "runtime_profile": payload.get("manager_runtime_profile"),
                    "configuration_revision": payload.get(
                        "manager_runtime_configuration_revision"
                    ),
                    "status": payload.get("manager_runtime_status"),
                    "sandbox": payload.get("manager_runtime_sandbox"),
                    "standing_grant": payload.get(
                        "manager_runtime_standing_grant"
                    ),
                    "tool_classes": list(
                        payload.get("manager_runtime_tool_classes") or []
                    ),
                }
                if _session_channel(payload).startswith("manager")
                and payload.get("manager_runtime_profile")
                else None
            ),
            "manager_executor_allocation": (
                dict(payload["manager_executor_allocation"])
                if _session_channel(payload).startswith("manager")
                and isinstance(payload.get("manager_executor_allocation"), dict)
                else None
            ),
            "resumable": bool(payload.get("upstream_thread_id"))
            and payload.get("status") in RESUMABLE_SESSION_STATES,
        }

    def session_snapshot(self, session_id: str) -> dict[str, Any]:
        payload = self.load_session(session_id)
        if payload is None:
            raise KeyError("chat session was not found")
        active_turn = None
        if payload.get("active_turn_id"):
            active_turn = self.load_turn(session_id, str(payload["active_turn_id"]))
            if active_turn is not None:
                active_turn = {
                    key: value
                    for key, value in active_turn.items()
                    if key != "_acceptance"
                }
        return {
            "ok": True,
            "schema_version": CHAT_STORE_SCHEMA_VERSION,
            "session": self.public_session(payload),
            "messages": self.messages(session_id),
            "active_turn": active_turn,
        }
