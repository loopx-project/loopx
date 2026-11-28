"""Owner-local durable state for LoopX Chat sessions and turns."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager, suppress
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, BinaryIO, Iterator, Literal
import uuid
from weakref import WeakValueDictionary

from .chat import (
    require_matching_message_replay,
    resolve_attached_completion_replay,
)
from .capabilities.steward_executor.allocation import normalize_manager_executor_allocation
from .capabilities.native_chat.model_settings import (
    MODEL_SELECTION_UNSET as _MODEL_SELECTION_UNSET, normalize_agent_model_id as normalize_agent_model_id,
    normalize_session_model_settings, session_model_settings_supported, normalize_session_model_changes,
)
from .chat.event_cache import (
    ChatEventCache, ChatEventStreamMixin, EventRevision, _event_revision_from_stat, _read_event_bytes,
    recover_completing_turns,
)
from .chat.journal import CachedJournalRows, decode_chat_journal
from .chat.ingress import (
    ChatIngressStore,
    SESSION_QUEUE_MAX_PENDING as SESSION_QUEUE_MAX_PENDING,
    SESSION_QUEUE_TTL_SECONDS as SESSION_QUEUE_TTL_SECONDS,
)
from .control_plane.chat_turn_acceptance import (
    CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA,
    AcceptedManagedTurn,
    plan_managed_turn_acceptance,
)
from .control_plane.collaboration.source_chat_observation import (
    CHAT_SESSION_SCHEMA_VERSION as CHAT_SESSION_SCHEMA_VERSION,
    CHAT_TURN_SCHEMA_VERSION as CHAT_TURN_SCHEMA_VERSION,
    _opaque_id as _opaque_id,
    _read_json as _read_json,
)
from .file_lock import exclusive_file_lock, try_exclusive_file_lock, write_json_atomic


CHAT_STORE_SCHEMA_VERSION = "loopx_chat_store_v1"
CHAT_EVENT_SCHEMA_VERSION = "loopx_chat_event_v1"
CHAT_MESSAGE_SCHEMA_VERSION = "loopx_chat_message_v1"
CHAT_INGRESS_SCHEMA_VERSION = "loopx_chat_ingress_receipt_v1"
CHAT_SESSION_MODES = ("managed_runtime", "attached_host")
CHAT_SESSION_MODE_MANAGED = CHAT_SESSION_MODES[0]
CHAT_SESSION_MODE_ATTACHED = CHAT_SESSION_MODES[1]
RESUMABLE_SESSION_STATES = {"ready", "busy", "stale", "resuming"}
TERMINAL_TURN_STATES = {"completed", "interrupted", "timed_out", "failed"}
TERMINAL_EVENT_KINDS = {"turn.completed", "turn.failed", "turn.interrupted"}
REPLAY_ONLY_EVENT_KINDS = {"answer.delta", "assistant.delta", "agent.phase", "turn.activity"}

_SESSION_RELEASE_UNSET = object()

# Keep active steward and project conversation journals warm across multi-MB image histories.
_PAGED_HISTORY_MAX_SESSIONS = 4
_PAGED_HISTORY_MAX_BYTES = 256 * 1024 * 1024
_PAGED_HISTORY_MAX_ROWS = 65536


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _instant(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _upstream_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token or len(token) > 1024 or any(ord(character) < 32 for character in token):
        raise ValueError("upstream_thread_id must be a bounded opaque string")
    return token


def _read_jsonl(
    path: Path, *, raise_on_error: bool = False, strict: bool = False,
) -> list[dict[str, Any]]:
    try:
        lines = path.read_bytes().split(b"\n")
    except FileNotFoundError:
        return []
    except OSError:
        if raise_on_error:
            raise
        return []
    return decode_chat_journal(lines, strict=strict)


def _session_channel(payload: dict[str, Any]) -> str:
    channel_id = str(payload.get("channel_id") or "").strip()
    if channel_id:
        return channel_id
    return f"goal.{payload.get('goal_id')}"


def _atomic_write_json(path: Path, payload: dict[str, Any], *, preserve_mode: bool = False) -> None:
    write_json_atomic(
        path,
        payload,
        mode=0o600,
        preserve_mode=preserve_mode,
        directory_mode=0o700,
        separators=(",", ":"),
        indent=None,
    )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    _append_jsonl_rows(path, [payload])


def _repair_incomplete_jsonl_tail(handle: BinaryIO) -> None:
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


class ChatJournalRevisionChanged(OSError):
    """The journal changed before the writer could use its observed contents."""


def _append_jsonl_rows(
    path: Path, rows: list[dict[str, Any]], *,
    expected_revision: EventRevision | Literal["unconditional"] = "unconditional",
) -> tuple[os.stat_result, os.stat_result] | None:
    if not rows:
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if expected_revision == "unconditional":
        mode = "a+b"
    else:
        mode = "x+b" if expected_revision is None else "r+b"
    try:
        handle = path.open(mode)
    except (FileNotFoundError, FileExistsError) as exc:
        if expected_revision != "unconditional":
            raise ChatJournalRevisionChanged("Chat event journal changed before append") from exc
        raise
    with handle:
        before = os.fstat(handle.fileno())
        if expected_revision not in (None, "unconditional") and expected_revision != _event_revision_from_stat(before):
            raise ChatJournalRevisionChanged("Chat event journal changed before append")
        _repair_incomplete_jsonl_tail(handle)
        handle.seek(0, os.SEEK_END)
        for payload in rows:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + os.linesep
            handle.write(line.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o600)
        elif expected_revision == "unconditional":
            os.chmod(path, 0o600)
        return before, os.fstat(handle.fileno())


def _replace_jsonl(
    path: Path, rows: list[dict[str, Any]], *, expected_revision: EventRevision,
) -> EventRevision:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    handle = temporary.open("xb")
    try:
        with handle:
            for row in rows:
                line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + os.linesep).encode("utf-8")
                handle.write(line)
                digest.update(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            current = _event_revision_from_stat(path.stat())
        except FileNotFoundError:
            current = None
        if current != expected_revision:
            raise ChatJournalRevisionChanged("Chat event journal changed before compaction")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

    # A replacement can arrive after publication. Mark only verified output.
    try:
        raw_bytes, revision = _read_event_bytes(path)
    except OSError:
        return None
    return revision if hashlib.sha256(raw_bytes).digest() == digest.digest() else None


class ChatSessionStore(ChatEventStreamMixin, ChatIngressStore):
    """Filesystem store kept outside project and public LoopX run history."""

    def __init__(self, runtime_root: Path, *, startup_maintenance: bool = True, compact_on_init: bool = True) -> None:
        self.root = runtime_root.expanduser().resolve() / "chat"
        self.sessions_root = self.root / "sessions"
        self._session_lock_guard = threading.Lock()
        self._session_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
        self._event_lock = threading.RLock()
        self._event_cache = ChatEventCache(self._event_lock)
        self._event_pending: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
        self._event_flush_locks: WeakValueDictionary[tuple[str, str], threading.Lock] = WeakValueDictionary()
        self._paged_history_lock = threading.Lock()
        self._paged_history: OrderedDict[str, tuple[bytes, int, list[dict[str, Any]]]] = OrderedDict()
        self.sessions_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.sessions_root, 0o700)
        # Scoped repairs open the data owner without mutating unrelated history.
        if startup_maintenance:
            if compact_on_init:
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

    @contextmanager
    def _locked_session_path(self, session_id: str, operation: str) -> Iterator[Path]:
        path = self._session_path(session_id)
        with self._session_lock(session_id), exclusive_file_lock(path, agent_id="loopx-chat", operation=operation):
            yield path

    def _turn_path(self, session_id: str, turn_id: str) -> Path:
        return self._session_dir(session_id) / "turns" / f"{_opaque_id(turn_id, field='turn_id')}.json"

    @contextmanager
    def _locked_turn_path(self, session_id: str, turn_id: str, operation: str) -> Iterator[Path]:
        path = self._turn_path(session_id, turn_id)
        with exclusive_file_lock(path, agent_id="loopx-chat", operation=operation):
            yield path

    def turn_execution_lock_path(self, session_id: str, turn_id: str) -> Path:
        return self._turn_path(session_id, turn_id).with_suffix(".execution")

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
        goal_id: str | None,
        goal_instance_id: str | None = None,
        managed_agent_id: str | None = None,
        managed_workspace_path: str | None = None,
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
        project_context: dict[str, Any] | None = None,
        steward_context: dict[str, Any] | None = None,
        agent_model_id: str | None = None,
        requested_agent_model_id: str | None | object = _MODEL_SELECTION_UNSET,
        requested_agent_reasoning_effort: str | None | object = _MODEL_SELECTION_UNSET,
        codex_chat_configuration: dict[str, Any] | None = None,
        agent_reasoning_effort: str | None = None,
        agent_service_tier: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        token = _opaque_id(session_id or uuid.uuid4().hex, field="session_id")
        normalized_mode = _opaque_id(session_mode, field="session_mode")
        if codex_home is not None and (
            not Path(codex_home).is_absolute() or agent_id != "codex"
            or normalized_mode != CHAT_SESSION_MODE_MANAGED
        ):
            raise ValueError("codex_home must be an absolute managed Codex home")
        if normalized_mode not in CHAT_SESSION_MODES:
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
        if project_context is not None:
            from .control_plane.effect_runtime import effect_runtime_result
            selected = effect_runtime_result("collaboration.project.session_identity", {"context": project_context})
            if goal_id is not None or goal_instance_id is not None or channel_id != selected["channel_id"] or normalized_mode != CHAT_SESSION_MODE_MANAGED:
                raise ValueError("ordinary project Sessions require their exact channel and no Goal")
            project_context = selected["context"]
        if steward_context is not None:
            from .control_plane.effect_runtime import effect_runtime_result
            selected = effect_runtime_result("collaboration.steward.session_identity", {"context": steward_context})
            if (project_context is not None or goal_id != "loopx-manager" or goal_instance_id is not None
                    or channel_id != selected["channel_id"] or normalized_mode != CHAT_SESSION_MODE_MANAGED):
                raise ValueError("bound steward Sessions require their exact role and audience")
            steward_context = selected["context"]
        normalized_goal_id = _opaque_id(goal_id, field="goal_id") if goal_id is not None else None
        if normalized_goal_id is None and project_context is None:
            raise ValueError("goal_id is required outside ordinary project Sessions")
        normalized_agent_id = _opaque_id(agent_id, field="agent_id")
        normalized_executor_endpoint_id = _opaque_id(
            executor_endpoint_id or agent_id,
            field="executor_endpoint_id",
        )
        normalized_adapter_kind = _opaque_id(adapter_kind, field="adapter_kind")
        model_settings = normalize_session_model_settings(
            model=agent_model_id, effort=agent_reasoning_effort, tier=agent_service_tier,
            requested_model=requested_agent_model_id, requested_effort=requested_agent_reasoning_effort,
        )
        if codex_chat_configuration is not None:
            from .capabilities.native_chat.machine_defaults import normalize_codex_chat_defaults
            if agent_id != "codex" or normalized_mode != CHAT_SESSION_MODE_MANAGED:
                raise ValueError("Codex defaults require a managed Codex Session")
            codex_chat_configuration = normalize_codex_chat_defaults(codex_chat_configuration)
        normalized_upstream_thread_id = _upstream_id(upstream_thread_id)
        normalized_upstream_mode = _opaque_id(upstream_mode, field="upstream_mode")
        normalized_channel_id = _opaque_id(
            channel_id or f"goal.{goal_id}",
            field="channel_id",
        )
        if managed_agent_id is not None:
            from .control_plane.collaboration import conversation_scope
            if not managed_workspace_path or not Path(managed_workspace_path).is_absolute() or conversation_scope({"goal_id": normalized_goal_id, "managed_agent_id": managed_agent_id,
                    "channel_id": normalized_channel_id, "session_mode": normalized_mode,
                    "project_context": project_context, "steward_context": steward_context})["kind"] != "owner_goal":
                raise ValueError("managed Agent identity requires its exact local Core channel")
        elif managed_workspace_path is not None:
            raise ValueError("a managed workspace requires its registered Agent identity")
        session_dir = self._session_dir(token)
        session_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        (session_dir / "turns").mkdir(mode=0o700)
        payload = {
            "schema_version": CHAT_SESSION_SCHEMA_VERSION,
            "session_id": token,
            "goal_id": normalized_goal_id,
            **({"managed_agent_id": managed_agent_id, "managed_workspace_path": managed_workspace_path} if managed_agent_id is not None else {}),
            **({"project_context": project_context} if project_context is not None else {}),
            **({"steward_context": steward_context} if steward_context is not None else {}),
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
            "agent_id": normalized_agent_id,
            **model_settings,
            **({"codex_chat_configuration": codex_chat_configuration} if codex_chat_configuration is not None else {}),
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

    @contextmanager
    def read_session_fenced(self, session_id: str) -> Iterator[dict[str, Any] | None]:
        """Keep Session state stable through a caller's local decision.

        Reuse the writer fences without waiting for ongoing work or changing
        its holder record. Callers must keep provider IO outside this scope.
        """
        path = self._session_path(session_id)
        if not path.exists():
            yield None
            return
        lock = self._session_lock(session_id)
        if not lock.acquire(blocking=False):
            raise ValueError("chat session is changing; retry")
        try:
            with try_exclusive_file_lock(path, preserve_holder=True) as held:
                if held is None:
                    raise ValueError("chat session is changing; retry")
                yield self.load_session(session_id)
        finally:
            lock.release()

    def reserve_loopx_outcome_request(
        self, session_id: str, *, turn_id: str, operation_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Pin one host envelope before dispatch, including across later Turns.

        This is private transport provenance, not an acceptance receipt. The
        caller must revalidate current admission and replay at the canonical
        owner; a saved request cannot establish that the write committed.
        """
        if self.load_session(session_id) is None:
            raise KeyError("chat session was not found")
        request_digest = hashlib.sha256(json.dumps(
            request, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode()).hexdigest()
        operation_key = hashlib.sha256(operation_id.encode()).hexdigest()
        path = self._session_dir(session_id) / "outcome-requests" / f"{operation_key}.json"
        with exclusive_file_lock(path, agent_id="loopx-chat", operation="reserve_outcome_request"):
            if path.exists():
                existing = _read_json(path)
                if (existing.get("session_id") != session_id
                        or existing.get("operation_id") != operation_id
                        or existing.get("request_sha256") != request_digest
                        or not isinstance(existing.get("source_ref"), str)
                        or not existing["source_ref"]):
                    raise ValueError("outcome refinement operation identity conflict")
                return existing
            scope = hashlib.sha256(json.dumps([session_id, operation_id]).encode()).hexdigest()[:16]
            payload = {
                "session_id": session_id,
                "operation_id": operation_id,
                "request_sha256": request_digest,
                "request": request,
                "source_ref": f"turn:{_opaque_id(turn_id, field='turn_id')}:{scope}",
            }
            _atomic_write_json(path, payload)
            return payload

    def update_session(self, session_id: str, *, expected_statuses: set[str] | None = None,
                       expected_upstream_thread_id: str | None = None, metadata_review: bool = False,
                       quota_recovery: bool = False,
                       answer_review_admission: bool = False,
                       **changes: Any) -> dict[str, Any]:
        with self._locked_session_path(session_id, "update_chat_session") as path:
            payload = self.load_session(session_id)
            if payload is None:
                raise KeyError("chat session was not found")
            if expected_statuses is not None and payload.get("status") not in expected_statuses:
                raise KeyError("chat session is no longer open")
            if expected_upstream_thread_id is not None and payload.get("upstream_thread_id") != expected_upstream_thread_id:
                raise ValueError("native executor binding changed")
            if payload.get("managed_agent_id") is not None and "goal_id" in changes and changes["goal_id"] != payload["goal_id"]:
                raise ValueError("a registered Agent Session cannot change its original Goal")
            allowed = {
                "status", "active_turn_id", "last_activity_at", "last_error_code",
                "upstream_thread_id", "upstream_mode", "codex_home",
                "manager_context_version", "upstream_context_history", "coordination_context_version",
                "external_conversation_binding_id", "loopx_mode", "loopx_tools", "loopx_executor",
                "loopx_deliveries", "native_goal", "manager_authorization_scope_id",
                "manager_runtime_profile", "manager_runtime_configuration_revision", "manager_runtime_status",
                "manager_runtime_sandbox", "manager_runtime_standing_grant", "manager_runtime_tool_classes",
                "manager_executor_allocation", "agent_model_id", "agent_reasoning_effort", "agent_service_tier",
                "requested_agent_model_id", "requested_agent_reasoning_effort", "goal_id",
                "agent_id", "executor_endpoint_id", "adapter_kind", "manager_quota_block",
                "answer_review", "parked_native_working", "native_working_checkpoint",
            }
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"unsupported chat session fields: {sorted(unknown)}")
            if "answer_review" in changes and (type(changes["answer_review"]) is not bool
                    or changes["answer_review"] and payload.get("steward_context") is None):
                raise ValueError("answer review requires a bound steward and a boolean adapter marker")
            if {"native_working_checkpoint", "parked_native_working"} & changes.keys():
                from .capabilities.native_chat.codex_adapter import validate_native_working_update
                active_id = payload.get("active_turn_id") if changes.get("parked_native_working") is not None else None
                validate_native_working_update(payload, changes, expected_thread=expected_upstream_thread_id,
                                               active=self.load_turn(session_id, active_id) if active_id else None)
            if "manager_quota_block" in changes and "agent_id" not in changes:
                raise ValueError("quota observation belongs to an executor transition")
            if any(key in changes for key in ("agent_id", "executor_endpoint_id", "adapter_kind")):
                allocation = normalize_manager_executor_allocation(changes.get("manager_executor_allocation", {}))
                active = self.load_turn(session_id, payload["active_turn_id"]) if payload.get("active_turn_id") else None
                endpoint = changes.get("agent_id")
                history = changes.get("upstream_context_history") or []
                if (expected_upstream_thread_id is None or not _session_channel(payload).startswith("manager")
                        or payload.get("session_mode") != CHAT_SESSION_MODE_MANAGED or payload.get("managed_agent_id")
                        or payload.get("upstream_mode") not in {"default", "chat"}
                        or not active or active.get("status") != "queued" or active.get("upstream_turn_id")
                        or active.get("origin") == "native_loopx"
                        or answer_review_admission and changes.get("answer_review") is not True
                        or ("-recovery-" in str(active.get("client_turn_id")) and not ((metadata_review or quota_recovery or answer_review_admission)
                            and payload.get("steward_context") is not None and endpoint == "codex"
                            and str(active.get("client_turn_id")).endswith(
                                "-metadata-1" if metadata_review else "-review-1" if answer_review_admission else "-quota-1")))
                        or allocation["selection_policy"] != "flexible"
                        or allocation["configured_endpoint"] not in {"antigravity", "codex"}
                        or set(allocation["eligible_endpoints"]) != {"antigravity", "codex"}
                        or endpoint not in {"antigravity", "codex"} or endpoint == payload.get("agent_id")
                        or changes.get("executor_endpoint_id") != endpoint or allocation["executor_endpoint"] != endpoint
                        or changes.get("adapter_kind") != ("codex_app_server" if endpoint == "codex" else "antigravity_ls")
                        or changes.get("upstream_mode") != ("chat" if endpoint == "codex" else "default")
                        or not history or history[-1] != {"thread_id": payload["upstream_thread_id"],
                            "manager_context_version": payload.get("manager_context_version"),
                            "agent_id": payload["agent_id"], "agent_model_id": payload.get("agent_model_id")}
                        or changes.get("upstream_thread_id") in {None, payload["upstream_thread_id"]}):
                    raise ValueError("executor transition requires one unstarted steward Turn and preserved native provenance")
            if "upstream_context_history" in changes:
                history = changes["upstream_context_history"]
                previous = payload.get("upstream_context_history") or []
                if not isinstance(history, list) or history[:len(previous)] != previous:
                    raise ValueError("native context provenance must preserve its prior prefix")
                for item in history:
                    if (not isinstance(item, dict) or set(item) not in (
                            {"thread_id", "manager_context_version"},
                            {"thread_id", "manager_context_version", "agent_id", "agent_model_id"})
                            or item["manager_context_version"] is not None and (
                                type(item["manager_context_version"]) is not int or item["manager_context_version"] < 0)):
                        raise ValueError("invalid native context provenance")
                    _opaque_id(item["thread_id"], field="upstream_thread_id")
                    if "agent_id" in item and (item["agent_id"] not in {"codex", "antigravity"}
                            or not isinstance(item["agent_model_id"], str)):
                        raise ValueError("invalid historical executor provenance")
            if "external_conversation_binding_id" in changes:
                identity = _opaque_id(changes["external_conversation_binding_id"], field="external_conversation_binding_id")
                if payload.get("session_mode") != CHAT_SESSION_MODE_ATTACHED or payload.get("external_conversation_binding_id") not in {None, identity}:
                    raise ValueError("an attached Session cannot change its external audience")
                changes["external_conversation_binding_id"] = identity
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
            normalize_session_model_changes(changes)
            for field in (
                "upstream_mode", "manager_authorization_scope_id",
                "manager_runtime_profile", "manager_runtime_status",
                "manager_runtime_sandbox", "manager_runtime_standing_grant",
            ):
                if field in changes:
                    changes[field] = _opaque_id(changes[field], field=field)
            if "manager_runtime_configuration_revision" in changes:
                revision = str(changes["manager_runtime_configuration_revision"] or "").strip()
                if not revision or len(revision) > 160 or any(ord(character) < 32 for character in revision):
                    raise ValueError("manager_runtime_configuration_revision is invalid")
                changes["manager_runtime_configuration_revision"] = revision
            if "manager_runtime_tool_classes" in changes:
                tool_classes = changes["manager_runtime_tool_classes"]
                if not isinstance(tool_classes, list):
                    raise TypeError("manager_runtime_tool_classes must be a list")
                changes["manager_runtime_tool_classes"] = [
                    _opaque_id(item, field="manager_runtime_tool_class") for item in tool_classes
                ]
            if "manager_executor_allocation" in changes:
                allocation = changes["manager_executor_allocation"]
                if not isinstance(allocation, dict):
                    raise TypeError("manager_executor_allocation must be an object")
                changes["manager_executor_allocation"] = normalize_manager_executor_allocation(allocation)
            if "codex_home" in changes:
                home = changes["codex_home"]
                if (not isinstance(home, str) or not Path(home).is_absolute()
                        or changes.get("agent_id", payload.get("agent_id")) != "codex"
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
        with self._locked_session_path(session_id, "prepare_managed_chat_resume") as path:
            payload = self.load_session(session_id)
            if payload is None or payload.get("status") == "closed":
                raise KeyError("chat session was not found")
            active_turn_id = str(payload.get("active_turn_id") or "")
            active_turn = self.load_turn(session_id, active_turn_id) if active_turn_id else None
            if (
                preserve_active_turn
                and active_turn is not None
                and active_turn.get("status") in {"queued", "starting", "running", "completing", "interrupting"}
            ):
                return payload, True
            resume_snapshot = dict(payload)
            payload.update({"status": "stale", "active_turn_id": None, "last_error_code": None, "updated_at": utc_now()})
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
        with self._locked_session_path(session_id, "restore_managed_chat_session") as path:
            payload = self.load_session(session_id)
            if payload is None:
                raise KeyError("chat session was not found")
            if payload.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                raise ValueError("the selected Session is an attached host session")
            if payload.get("status") == "closed":
                raise KeyError("chat session was not found")
            identity_changes: dict[str, Any] = {}
            if upstream_thread_id is not None:
                identity_changes["upstream_thread_id"] = _upstream_id(upstream_thread_id)
            if upstream_mode is not None:
                identity_changes["upstream_mode"] = _opaque_id(upstream_mode, field="upstream_mode")
            if payload.get("active_turn_id"):
                if identity_changes:
                    payload.update(identity_changes)
                    payload["updated_at"] = utc_now()
                    _atomic_write_json(path, payload, preserve_mode=True)
                return payload
            payload.update(
                {"status": "ready", "last_error_code": None, "last_activity_at": utc_now(), **identity_changes}
            )
            payload["updated_at"] = utc_now()
            _atomic_write_json(path, payload, preserve_mode=True)
            return payload

    def release_active_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        last_activity_at: str | object = _SESSION_RELEASE_UNSET,
        last_error_code: str | None | object = _SESSION_RELEASE_UNSET,
    ) -> bool:
        """Release this Turn's claim, preserving metadata omitted by the caller."""
        with self._locked_session_path(session_id, "release_active_chat_turn") as path:
            payload = self.load_session(session_id)
            if payload is None:
                raise KeyError("chat session was not found")
            if payload.get("active_turn_id") != turn_id:
                return False
            payload.update({"status": "ready", "active_turn_id": None, "updated_at": utc_now()})
            if last_activity_at is not _SESSION_RELEASE_UNSET:
                payload["last_activity_at"] = last_activity_at
            if last_error_code is not _SESSION_RELEASE_UNSET:
                payload["last_error_code"] = last_error_code
            _atomic_write_json(path, payload, preserve_mode=True)
            return True

    def latest_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the newest resumable session row; see ``session_candidates`` for channel scoping."""
        candidates = self.resumable_session_candidates(
            goal_id=goal_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        return max(candidates, key=lambda item: str(item.get("updated_at") or ""), default=None)

    def resumable_session_candidates(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return resumable session rows; see ``session_candidates`` for channel scoping."""

        return [
            candidate
            for candidate in self.session_candidates(
                goal_id=goal_id,
                agent_id=agent_id,
                channel_id=channel_id,
            )
            if candidate.get("status") in RESUMABLE_SESSION_STATES
        ]

    def session_candidates(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching Session records without lifecycle filtering.

        Omitting ``channel_id`` pins the exact ``goal.<goal_id>`` channel:
        a single namespace key, not a search over that goal's recorded
        conversations. Use ``list_sessions`` for intentional discovery.
        Raises ``ValueError`` when ``channel_id`` and ``goal_id`` are both
        omitted.
        """

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
                and _session_channel(payload) == selected_channel
            ):
                candidates.append(payload)
        return candidates

    def list_sessions(self, *, goal_id: str | None = None, agent_id: str | None = None,
                      channel_id: str | None = None) -> list[dict[str, Any]]:
        metadata = self.iter_session_metadata(goal_id=goal_id, agent_id=agent_id, channel_id=channel_id)
        rows = (self.public_session(payload) for _, payload in metadata)
        return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def iter_session_metadata(self, *, goal_id: str | None = None, agent_id: str | None = None,
                              channel_id: str | None = None) -> Iterator[tuple[Path, dict[str, Any]]]:
        """Read discoverable Session records without reading transcript versions."""
        for path in self.sessions_root.glob("*/session.json"):
            try:
                payload = _read_json(path)
            except UnicodeDecodeError:
                # Direct Session reads remain strict. Discovery skips damaged metadata.
                continue
            if (payload.get("schema_version") != CHAT_SESSION_SCHEMA_VERSION
                    or (goal_id and payload.get("goal_id") != goal_id)
                    or (agent_id and payload.get("agent_id") != agent_id)
                    or (channel_id and _session_channel(payload) != channel_id)):
                continue
            yield path, payload

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
        goal_draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a message, or return an identical explicit-ID retry.

        Reusing an explicit ``message_id`` with a different semantic payload
        raises ``ValueError``. Generated timestamps do not affect identity.
        """
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
            **({"goal_draft": goal_draft} if goal_draft and role == "agent" else {}),
            "created_at": utc_now(),
        }
        with exclusive_file_lock(path, agent_id="loopx-chat", operation="append_chat_message"):
            if message_id:
                for existing in _read_jsonl(path):
                    if existing.get("message_id") == payload["message_id"]:
                        require_matching_message_replay(
                            existing,
                            payload,
                        )
                        return existing
            _append_jsonl(path, payload)
        return payload

    def messages(self, session_id: str, *, strict: bool = False) -> list[dict[str, Any]]:
        return _read_jsonl(self._session_dir(session_id) / "messages.jsonl",
                          raise_on_error=strict, strict=strict)

    def _paged_history_messages(
        self, session_id: str, *, copy_cached: bool = True,
    ) -> list[dict[str, Any]]:
        """Reuse a per-session parse only after a fresh read of identical bytes."""
        try:
            raw = (self._session_dir(session_id) / "messages.jsonl").read_bytes()
        except FileNotFoundError:
            raw = b""
        size = len(raw)
        digest = hashlib.sha256(raw).digest() if size <= _PAGED_HISTORY_MAX_BYTES else None
        with self._paged_history_lock:
            cached = self._paged_history.get(session_id)
            if cached is not None and cached[0] == digest:
                self._paged_history.move_to_end(session_id)
            else:
                self._paged_history.pop(session_id, None)
                cached = None
        if cached is not None:
            if not copy_cached:
                return cached[2]
            with suppress(RecursionError):
                return deepcopy(cached[2])
        lines = raw.split(b"\n")
        del raw
        rows = decode_chat_journal(lines, strict=True)
        if digest is not None and len(rows) <= _PAGED_HISTORY_MAX_ROWS:
            with suppress(RecursionError):
                saved = CachedJournalRows(deepcopy(rows), journal_sha256=digest)
                with self._paged_history_lock:
                    self._paged_history[session_id] = (digest, size, saved)
                    self._paged_history.move_to_end(session_id)
                    while (
                        len(self._paged_history) > _PAGED_HISTORY_MAX_SESSIONS
                        or sum(entry[1] for entry in self._paged_history.values()) > _PAGED_HISTORY_MAX_BYTES
                        or sum(len(entry[2]) for entry in self._paged_history.values()) > _PAGED_HISTORY_MAX_ROWS
                    ):
                        self._paged_history.popitem(last=False)
        return rows

    def read_history_message(self, session_id: str, message_id: str) -> dict[str, Any]:
        """Read one original row, sharing the verified page parse for image reads."""
        matches = [row for row in self._paged_history_messages(session_id, copy_cached=False)
                   if row.get("message_id") == message_id]
        if len(matches) != 1:
            raise KeyError("message unavailable")
        return deepcopy(matches[0])

    def prepared_managed_turn_request(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        include_accepted_queued: bool = False,
    ) -> dict[str, Any] | None:
        """Read the original request; acceptance/dispatch stays with its TS owner.

        Session restoration may also replay a fully accepted active request
        which is still queued. Interrupt recovery retains the prepared-only
        default and cannot turn a terminal request into new work.
        """
        session_token = _opaque_id(session_id, field="session_id")
        turn_token = (
            _opaque_id(turn_id, field="turn_id")
            if turn_id is not None
            else None
        )
        with self._locked_session_path(session_token, "read_prepared_managed_chat_turn"):
            session = self.load_session(session_token)
            active_id = (session or {}).get("active_turn_id")
            prepared = [
                payload
                for path in (self._session_dir(session_token) / "turns").glob("*.json")
                if (payload := _read_json(path)).get("schema_version") == CHAT_TURN_SCHEMA_VERSION
                and ("_acceptance" in payload or (
                    include_accepted_queued and payload.get("turn_id") == active_id and payload.get("status") == "queued"
                ))
                and (
                    (turn_token is not None and payload.get("turn_id") == turn_token)
                    or (turn_token is None and payload.get("status") not in TERMINAL_TURN_STATES)
                )
            ]
            if not prepared:
                return None
            if len(prepared) != 1:
                raise ValueError("chat turn acceptance state is inconsistent")
            turn = prepared[0]
            acceptance = turn.get("_acceptance")
            if acceptance is None:
                # Sealed acceptance keeps attachments/display text in its
                # committed transcript. Submit verifies its digests again.
                messages = [
                    message for message in self.messages(session_token)
                    if message.get("turn_id") == turn.get("turn_id") and message.get("role") == "user"
                ]
                if len(messages) != 1:
                    raise ValueError("chat turn acceptance state is inconsistent")
                attachments = messages[0].get("attachments")
                display_message = messages[0].get("text")
            else:
                if (not isinstance(acceptance, dict)
                    or acceptance.get("schema_version") != CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA
                    or acceptance.get("phase") != "prepared"):
                    raise ValueError("chat turn acceptance state is inconsistent")
                attachments = acceptance.get("attachments")
                display_message = acceptance.get("display_message")
            if not isinstance(turn.get("message"), str) or not isinstance(display_message, str):
                raise ValueError("chat turn acceptance state is inconsistent")
            if attachments is not None and (
                not isinstance(attachments, list)
                or any(not isinstance(attachment, dict) for attachment in attachments)
            ):
                raise ValueError("chat turn acceptance state is inconsistent")
            loopx_execution = turn.get("loopx_execution", False)
            loopx_request = turn.get("loopx_request")
            if not isinstance(loopx_execution, bool) or (loopx_request is not None and not isinstance(loopx_request, dict)):
                raise ValueError("chat turn acceptance state is inconsistent")
            return {
                "client_turn_id": _opaque_id(turn.get("client_turn_id"), field="client_turn_id"),
                "message": turn["message"],
                "attachments": attachments,
                "origin": _opaque_id(turn.get("origin"), field="origin"),
                "display_message": display_message,
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
        with self.runtime_activation_lock(), self._locked_session_path(
            session_token,
            "accept_managed_chat_turn",
        ) as session_path:
            turns_dir = self._session_dir(session_token) / "turns"
            turns = []
            existing = None
            for path, turn, fresh in self._turn_discovery_rows(session_token, turns_dir, _read_json):
                if turn.get("schema_version") != CHAT_TURN_SCHEMA_VERSION:
                    continue
                # The planner needs live prepared Turns and the full
                # current request when its client ID matches.
                if existing is None and turn.get("client_turn_id") == client_id:
                    turn = self._matched_discovered_turn(session_token, client_id, path, fresh)
                    existing = turn
                turns.append(turn)
            session = self.load_session(session_token)
            active_turn = (
                self.load_turn(session_token, str(session["active_turn_id"]))
                if session is not None and session.get("active_turn_id")
                else None
            )
            existing_turn_id = str(existing.get("turn_id")) if existing is not None else None
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
                    _read_jsonl(self._event_path(session_token, existing_turn_id))
                    if existing_turn_id is not None
                    else []
                ),
            )
            if plan.writes.prepare_turn:
                if plan.turn_id != candidate_turn_id:
                    raise ValueError("chat turn acceptance selected an invalid candidate")
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
                    **({"loopx_execution": True, "loopx_request": loopx_request} if loopx_execution else {}),
                    "_acceptance": {
                        "schema_version": CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA,
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
                    raise ValueError("chat turn acceptance selected an invalid replay")
                payload = existing

            if plan.writes.activate_session:
                if session is None:
                    raise KeyError("chat session was not found")
                session.update({
                    "status": "busy",
                    "active_turn_id": plan.turn_id,
                    "last_activity_at": accepted_at,
                    "updated_at": utc_now(),
                })
                _atomic_write_json(session_path, session, preserve_mode=True)

            if plan.writes.append_message:
                self.append_message(
                    session_token, role="user", text=visible_message, turn_id=plan.turn_id,
                    attachments=normalized_attachments, origin=normalized_origin, message_id=plan.message_id,
                )
            if plan.writes.append_queued_event:
                self.append_event(session_token, plan.turn_id, kind="turn.queued", payload={})
            if plan.writes.settle_turn:
                with self._locked_turn_path(
                    session_token,
                    plan.turn_id,
                    "settle_managed_chat_turn_acceptance",
                ) as turn_path:
                    settled = self.load_turn(session_token, plan.turn_id)
                    if settled is None:
                        raise ValueError("chat turn acceptance lost its prepared turn")
                    settled.pop("_acceptance", None)
                    _atomic_write_json(turn_path, settled, preserve_mode=True)
                payload = settled
            else:
                current = self.load_turn(session_token, plan.turn_id)
                if current is None:
                    raise ValueError("chat turn acceptance lost its replayed turn")
                payload = current
            return AcceptedManagedTurn(
                turn=payload,
                created=plan.created,
                dispatch_required=plan.dispatch_required,
                dispatch_reason=plan.dispatch_reason,
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
            turn_id = str(turn["turn_id"])
            with self._locked_turn_path(session_id, turn_id, "expire_queued_chat_turn") as path:
                # Queue enumeration is only a candidate; a stop or claim may
                # already own the Turn by the time its write lock is acquired.
                current = self.load_turn(session_id, turn_id)
                if current is None or current.get("status") != "queued":
                    continue
                expires_at = _instant(current.get("expires_at"))
                if expires_at is None or expires_at > now:
                    live_turns.append(current)
                    continue
                completed = utc_now()
                current.update({
                    "status": "timed_out",
                    "error_code": "session_queue_expired",
                    "error": "Queued Agent ingress expired before dispatch.",
                    "completed_at": completed,
                    "last_activity_at": completed,
                })
                _atomic_write_json(path, current, preserve_mode=True)
                self.append_event(
                    session_id, turn_id, kind="turn.failed",
                    payload={"error_code": "session_queue_expired"},
                )
        return live_turns

    def claim_next_queued_turn(
        self,
        session_id: str,
        *,
        host_claim_id: str | None = None,
        admitted_goal_instance_id: str | None = None,
        admission_validator: Any | None = None,
    ) -> dict[str, Any] | None:
        """Atomically make the oldest live queued Turn active for its Session."""
        with self._locked_session_path(session_id, "claim_session_queue_turn") as session_path:
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
                    if admitted_goal_instance_id is not None and (
                        active.get("goal_instance_id") != admitted_goal_instance_id
                        or active.get("admitted_goal_instance_id") != admitted_goal_instance_id
                    ):
                        raise ValueError("active Turn Goal instance admission is invalid")
                    # A committed claim is replayed to the exact host that
                    # already owns it: the matching host claim id and the
                    # Goal lifetime fence above are the authority. Fresh
                    # audience eligibility is checked only when a queued
                    # Turn is first admitted, so a revoked grant cannot
                    # strand an execution the original host already owns.
                    return active
                return None
            for turn in self._settle_expired_queued_turns(
                session_id,
                now=datetime.now(timezone.utc),
            ):
                turn_id = str(turn["turn_id"])
                if admission_validator is not None:
                    try:
                        admission_validator(turn)
                    except (ValueError, OSError, KeyError):
                        self.update_turn(session_id, turn_id, status="failed", error_code="external_agent_grant_unavailable", completed_at=utc_now())
                        continue
                if admitted_goal_instance_id is not None and (
                    turn.get("goal_instance_id") != admitted_goal_instance_id
                ):
                    raise ValueError("queued Turn Goal instance does not match its Session")
                self._recover_queued_turn_input(session_id, turn)
                if host_claim_id:
                    now_text = utc_now()
                    # Share the Turn write lock that `begin_turn_interrupt`
                    # takes: the claim is the other writer of this Turn's
                    # status and authority, so the queue scan above is only
                    # a candidate. Re-reading under the lock means an
                    # interrupt that already committed wins the transition
                    # instead of being overwritten by this write, and a
                    # claim that commits first is never rewritten from a
                    # snapshot that predates it.
                    with self._locked_turn_path(session_id, turn_id, "claim_queued_chat_turn") as turn_path:
                        claimed = self.load_turn(session_id, turn_id)
                        if claimed is None or claimed.get("status") != "queued":
                            # Another writer owns this Turn now (an
                            # interrupt, or a claim from another surface).
                            # Starting a different queued Turn here would
                            # leave the queue head in an unclaimed state.
                            return None
                        claimed.update({
                            "status": "running",
                            "host_claim_id": _opaque_id(host_claim_id, field="host_claim_id"),
                            **(
                                {
                                    "admitted_goal_instance_id": _opaque_id(
                                        admitted_goal_instance_id,
                                        field="admitted_goal_instance_id",
                                    )
                                }
                                if admitted_goal_instance_id is not None
                                else {}
                            ),
                            "started_at": claimed.get("started_at") or now_text,
                            "last_activity_at": now_text,
                        })
                        _atomic_write_json(turn_path, claimed, preserve_mode=True)
                        self.append_event(
                            session_id, turn_id, kind="turn.claimed_by_attached_host",
                            payload={"host_claim_id": host_claim_id},
                        )
                    turn = claimed
                session.update({
                    "status": "busy",
                    "active_turn_id": turn_id,
                    "last_activity_at": utc_now(),
                    "updated_at": utc_now(),
                })
                _atomic_write_json(session_path, session, preserve_mode=True)
                return turn
            return None

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        payload = _read_json(self._turn_path(session_id, turn_id))
        return payload if payload.get("schema_version") == CHAT_TURN_SCHEMA_VERSION else None

    def native_result(self, session_id: str, turn_id: str) -> dict[str, Any]:
        """Private provider facts, outside public Turn/event projections."""
        token = _opaque_id(turn_id, field="turn_id")
        return _read_json(self._session_dir(session_id) / "native-results" / f"{token}.json")

    def retain_native_result(self, session_id: str, turn_id: str, result: dict[str, Any], *,
                             completed_turn: dict[str, Any] | None = None) -> None:
        """Freeze once; a completed backfill requires the exact unchanged Turn.

        The automatic recovery owner first verifies the original native proof
        and current source.
        """
        with self._locked_turn_path(session_id, turn_id, "retain_native_result"):
            turn = self.load_turn(session_id, turn_id)
            if completed_turn is not None:
                if turn != completed_turn or turn is None or turn.get("status") != "completed":
                    raise ValueError("the original completed Turn changed")
            elif turn is None or turn.get("status") != "running":
                return
            if not result.get("upstream_turn_id") or result["upstream_turn_id"] != turn.get("upstream_turn_id"):
                raise ValueError("native result belongs to another invocation")
            token = _opaque_id(turn_id, field="turn_id")
            path = self._session_dir(session_id) / "native-results" / f"{token}.json"
            if path.exists():
                if _read_json(path) != result:
                    raise ValueError("the original native result is immutable")
                return
            path.parent.mkdir(mode=0o700, exist_ok=True)
            _atomic_write_json(path, result)

    def update_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        expected_statuses: set[str] | None = None,
        **changes: Any,
    ) -> dict[str, Any] | None:
        with self._locked_turn_path(session_id, turn_id, "update_chat_turn") as path:
            payload = self.load_turn(session_id, turn_id)
            if payload is None:
                raise KeyError("chat turn was not found")
            allowed = {
                "status", "upstream_turn_id", "response", "error_code", "error",
                "started_at", "first_event_at", "completed_at", "last_activity_at",
                "delta_count", "sse_reconnect_count", "expires_at", "host_claim_id",
                "completion_id", "loopx_execution", "loopx_request",
                "collaboration_answer",
                "native_context_preparation", "execution_dispatch",
            }
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"unsupported chat turn fields: {sorted(unknown)}")
            if expected_statuses is not None and payload.get("status") not in expected_statuses:
                return None
            if "execution_dispatch" in changes:
                previous = payload.get("execution_dispatch")
                dispatch = changes["execution_dispatch"]
                if (dispatch == "pending" and previous is None and payload.get("status") == "queued"
                        and changes.get("status") == "starting"):
                    # This is established in the same write that starts the worker.
                    # Legacy records never acquire evidence about whether they dispatched.
                    if payload.get("origin") != "lark" or not str(payload.get("client_turn_id") or "").startswith("external-"):
                        changes.pop("execution_dispatch")
                elif (dispatch != "committed" or previous not in {"pending", "committed"}
                      or payload.get("status") not in {"starting", "running"} or "status" in changes):
                    raise ValueError("invalid chat execution dispatch transition")
            if "native_context_preparation" in changes:
                observation = changes["native_context_preparation"]
                flags = {"native_checkpoint_requested", "working_checkpoint_available", "manager_context_migration",
                    "provider_context_migration", "project_context_migration", "manager_profile_changed",
                    "settings_changed", "executor_reallocated", "isolated_answer_review"}
                fields = flags | {"schema_version", "planned_method", "source_thread_id", "history_message_count",
                    "manager_context_version", "required_manager_context_version"}
                if (payload.get("status") != "queued" or payload.get("upstream_turn_id")
                        or not isinstance(observation, dict) or set(observation) != fields
                        or observation.get("schema_version") != "codex_native_context_preparation_v0"
                        or observation.get("planned_method") not in {"thread/start", "thread/resume", "thread/fork"}
                        or not isinstance(observation.get("source_thread_id"), str) or not observation["source_thread_id"]
                        or any(type(observation.get(key)) is not bool for key in flags)
                        or any(type(observation.get(key)) is not int or observation[key] < 0
                            for key in ("history_message_count", "required_manager_context_version"))
                        or observation.get("manager_context_version") is not None
                            and type(observation["manager_context_version"]) is not int
                        or payload.get("native_context_preparation", observation) != observation):
                    raise ValueError("native context preparation is an immutable queued-Turn observation")
            if "collaboration_answer" in changes:
                answer = changes["collaboration_answer"]
                if (payload.get("origin") != "peer" or not isinstance(payload.get("collaboration_request"), dict)
                        or not isinstance(answer, dict) or payload.get("collaboration_answer", answer) != answer):
                    raise ValueError("original peer model output is immutable and belongs only to its admitted Turn")
            payload.update(changes)
            _atomic_write_json(path, payload, preserve_mode=True)
            return payload

    def begin_turn_interrupt(
        self,
        session_id: str,
        turn_id: str,
        *,
        expected_statuses: set[str],
    ) -> dict[str, Any] | None:
        """Record stop intent and enter `interrupting` as one Turn transition.

        Holding the Turn write lock across the status check, the durable intent
        event and the status write closes the completion race: a Turn that
        already reached a terminal state cannot gain a stop-intent event after
        its terminal outcome, and a Turn this call does interrupt cannot lose
        that intent to a later completion. The event still precedes the status
        change, so a crash between them leaves the requested stop recorded
        rather than silently turning it into an automatic review.
        """
        with self._locked_turn_path(session_id, turn_id, "begin_chat_turn_interrupt") as path:
            payload = self.load_turn(session_id, turn_id)
            if payload is None:
                raise KeyError("chat turn was not found")
            if payload.get("status") not in expected_statuses:
                return None
            self.append_event(session_id, turn_id, kind="turn.interrupt_requested", payload={})
            payload["status"] = "interrupting"
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
        events.append(("turn.completed", {**(terminal_metadata or {}), "response": response}))
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
        with self._locked_turn_path(session_id, turn_id, "finalize_managed_chat_turn") as path:
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
            attachments = response.get("attachments")
            if attachments:
                from .chat.attachments import local_goal_reply_route
                from .control_plane.collaboration import conversation_scope
                session = self.load_session(session_id) or {}
                if not local_goal_reply_route(session, turn, scope_kind=conversation_scope(session)["kind"]):
                    attachments = None
            if response.get("message") or attachments:
                self.append_message(
                    session_id, role="agent", text=str(response.get("message") or ""), turn_id=turn_id,
                    attachments=attachments,
                    message_id=f"managed.{turn_id}.completed", goal_draft=response.get("goal_draft"),
                )
            self.append_completed_response_events(session_id, turn_id, response=response, idempotent=True)
            self.release_active_turn(session_id, turn_id, last_activity_at=completed_at, last_error_code=None)
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
        recover_completing_turns(self)

    def finalize_attached_turn_completion(
        self,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        """Publish a reserved attached completion after all visible side effects."""
        with self._locked_turn_path(session_id, turn_id, "finalize_attached_chat_turn") as turn_path:
            turn = self.load_turn(session_id, turn_id)
            if turn is None:
                raise KeyError("attached Agent turn was not found")
            if turn.get("status") not in {"completing", "completed"}:
                return None
            response = turn.get("response")
            if not isinstance(response, dict):
                raise ValueError("completing attached Agent turn requires a response")
            completion_id = _opaque_id(turn.get("completion_id"), field="completion_id")
            completed_at = str(turn.get("completed_at") or utc_now())
            message_id = resolve_attached_completion_replay(
                self.messages(session_id), turn_id=turn_id,
                completion_id=completion_id, response_message=str(response.get("message") or ""),
            )
            if message_id:
                self.append_message(
                    session_id, role="agent", text=str(response.get("message") or ""),
                    turn_id=turn_id, origin="attached_host", message_id=message_id,
                    goal_draft=response.get("goal_draft"),
                )
            self.append_completed_response_events(
                session_id, turn_id, response=response,
                terminal_metadata={"delivery_mode": CHAT_SESSION_MODE_ATTACHED, "completion_id": completion_id},
                idempotent=True,
            )
            self.release_active_turn(session_id, turn_id, last_activity_at=completed_at, last_error_code=None)
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
        normalized_claim_id = _opaque_id(claim_id, field="claim_id")
        normalized_completion_id = _opaque_id(completion_id, field="completion_id")
        with self._locked_turn_path(session_id, turn_id, "complete_attached_chat_turn") as turn_path:
            session = self.load_session(session_id)
            turn = self.load_turn(session_id, turn_id)
            if session is None or turn is None:
                raise KeyError("attached Agent turn was not found")
            created = turn.get("status") == "running"
            if turn.get("status") in {"completing", "completed"}:
                if turn.get("completion_id") != normalized_completion_id:
                    raise ValueError("the Turn was already completed by another receipt")
            elif created:
                if session.get("active_turn_id") != turn_id:
                    raise ValueError("the Turn is not the active claimed attached-host Turn")
                if turn.get("host_claim_id") != normalized_claim_id:
                    raise ValueError("claim_id does not own the active attached-host Turn")
                completed_at = utc_now()
                turn.update({
                    "status": "completing",
                    "response": {**response, "message": str(response.get("message") or agent_message)},
                    "completion_id": normalized_completion_id,
                    "completed_at": completed_at,
                    "last_activity_at": completed_at,
                    "error_code": None,
                    "error": None,
                })
                _atomic_write_json(turn_path, turn, preserve_mode=True)
            else:
                raise ValueError("the Turn is not the active claimed attached-host Turn")
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
        with self._locked_session_path(session_id, operation) as session_path:
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
            if self._settle_expired_queued_turns(session_id, now=datetime.now(timezone.utc)):
                raise RuntimeError(queue_error)
            now = utc_now()
            session.update({
                "status": "closed",
                "active_turn_id": None,
                "last_activity_at": now,
                "updated_at": now,
            })
            _atomic_write_json(session_path, session, preserve_mode=True)
            return True

    def _event_flush_lock(self, key: tuple[str, str]) -> threading.Lock:
        with self._event_lock:
            return self._event_flush_locks.setdefault(key, threading.Lock())

    def public_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_mode = str(payload.get("session_mode") or CHAT_SESSION_MODE_MANAGED)
        # A receiver can append a result without changing the execution state.
        # Observe the transcript independently of updated_at, without reading it
        # or exposing filesystem identity. This is a read hint, never authority.
        revision = self._event_revision(
            self._session_dir(payload["session_id"]) / "messages.jsonl"
        )
        return {
            key: payload.get(key)
            for key in (
                "session_id", "goal_id", "agent_id", "adapter_kind", "status",
                "agent_model_id",
                "agent_reasoning_effort", "agent_service_tier",
                "requested_agent_model_id", "requested_agent_reasoning_effort",
                "active_turn_id", "last_error_code", "created_at", "updated_at", "last_activity_at",
            )
        } | {
            **({"managed_agent_id": payload["managed_agent_id"], "goal_instance_id": payload.get("goal_instance_id")}
               if payload.get("managed_agent_id") is not None else {}),
            "transcript_revision": (
                hashlib.sha256(str(revision).encode("ascii")).hexdigest()
                if revision is not None else None
            ),
            "session_mode": session_mode,
            "model_settings_supported": session_model_settings_supported(payload),
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
            "project_ref": (payload.get("project_context") or {}).get("project_ref"),
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

    def session_snapshot(
        self, session_id: str, *, strict_messages: bool = False, copy_messages: bool = True,
    ) -> dict[str, Any]:
        payload = self.load_session(session_id)
        if payload is None:
            raise KeyError("chat session was not found")
        active_turn = self.load_turn(session_id, str(payload["active_turn_id"])) if payload.get("active_turn_id") else None
        if active_turn is not None:
            active_turn = {key: value for key, value in active_turn.items() if key != "_acceptance"}
        return {
            "ok": True,
            "schema_version": CHAT_STORE_SCHEMA_VERSION,
            "session": self.public_session(payload),
            "messages": (
                self._paged_history_messages(session_id, copy_cached=copy_messages)
                if strict_messages else self.messages(session_id)
            ),
            "active_turn": active_turn,
        }
