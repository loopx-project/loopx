"""Shared Zero-Any schema, validation, and protocol contracts for Chat ingress."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Set as AbstractSet
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol, TypeAlias, TypeGuard, cast

from ..capabilities.native_chat.file_discovery import FileChatDiscovery

LockFileFn: TypeAlias = Callable[..., AbstractContextManager[Path]]

NONTERMINAL_TURN_STATES: frozenset[str] = frozenset(
    {"queued", "starting", "running", "completing", "interrupting"}
)


def as_mapping(value: object) -> Mapping[str, object]:
    """Narrow an arbitrary JSON object to a read-only string-keyed mapping."""
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def as_dict(value: object) -> dict[str, object]:
    """Copy a mapping value into a mutable dict, preserving concrete dicts as shallow copies."""
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def as_str(value: object, default: str = "") -> str:
    """Convert non-None scalars to ``str`` while mapping ``None`` to ``default``."""
    return default if value is None else str(value)


def read_chat_json(path: Path) -> dict[str, object]:
    """Read one persisted Chat JSON object while preserving ``chat_store._read_json`` hooks."""
    from .. import chat_store
    from ..control_plane.collaboration.source_chat_observation import (
        _read_json as default_read_json,
    )

    read_fn = getattr(chat_store, "_read_json", default_read_json)
    raw = read_fn(path) if callable(read_fn) else default_read_json(path)
    return cast(dict[str, object], raw) if isinstance(raw, dict) else {}


def as_int(value: object, default: int = 0) -> int:
    """Return an integer value when numeric (excluding bool), else ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def is_valid_turn_record(
    payload: object,
    *,
    schema_version: str,
    valid_statuses: AbstractSet[str],
    session_id: str | None = None,
    turn_id: str | None = None,
) -> TypeGuard[dict[str, object]]:
    """Check persistent Chat Turn record structure and lifecycle status."""
    if not isinstance(payload, dict) or payload.get("schema_version") != schema_version:
        return False
    if session_id is not None and payload.get("session_id") != session_id:
        return False
    if turn_id is not None and payload.get("turn_id") != turn_id:
        return False
    status = payload.get("status")
    return isinstance(status, str) and status in valid_statuses


class AdmissionStoreProtocol(Protocol):
    """Subset of Chat store operations used during external steward admission."""

    def resumable_session_candidates(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> list[dict[str, object]]: ...


class AdmissionControllerProtocol(Protocol):
    """Runtime controller attribute exposed on ``ChatConversationBindings``."""

    @property
    def store(self) -> AdmissionStoreProtocol: ...


class ConversationBindingsProtocol(Protocol):
    """Conversation-bindings interface consumed by external Chat ingress admission."""

    path: Path

    @property
    def controller(self) -> AdmissionControllerProtocol | None: ...

    def read(self) -> dict[str, object]: ...

    def resolve(
        self,
        *,
        binding_id: str,
        source_ref: str,
        sender_ref: str,
        private_human_message: bool,
        session_context: dict[str, object] | None = None,
        group_ref: str | None = None,
        topic_ref: str | None = None,
        group_human_message: bool = False,
        include_observation: bool = False,
        origin: str | None = None,
        session: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def ensure_delivery_scope(
        self,
        selected: dict[str, object],
        *,
        if_absent: bool = True,
        execute: bool = True,
    ) -> None: ...

    def _ensure_delivery_scope(
        self,
        selected: dict[str, object],
        *,
        if_absent: bool = True,
        execute: bool = True,
    ) -> None: ...


class IngressStoreHostProtocol(Protocol):
    """Store host contract required by ``ChatIngressStore`` queue and receipt helpers."""

    root: Path

    @property
    def _terminal_turn_discovery(self) -> FileChatDiscovery: ...

    def _session_dir(self, session_id: str) -> Path: ...

    def _session_path(self, session_id: str) -> Path: ...

    def _session_lock(self, session_id: str) -> AbstractContextManager[object]: ...

    def _turn_path(self, session_id: str, turn_id: str) -> Path: ...

    def _event_path(self, session_id: str, turn_id: str) -> Path: ...

    def _ingress_path(self, session_id: str, client_ingress_id: str) -> Path: ...

    def _turn_discovery_rows(
        self,
        session_id: str,
        turns_dir: Path,
        read: Callable[[Path], dict[str, object]],
    ) -> Iterator[tuple[Path, dict[str, object], dict[str, object] | None]]: ...

    def _matched_discovered_turn(
        self,
        session_id: str,
        client_id: str,
        path: Path,
        fresh: dict[str, object] | None,
    ) -> dict[str, object]: ...

    def _strict_ingress_receipt(self, session_id: str, ingress_id: str) -> dict[str, object] | None: ...

    def _recover_queued_turn_input(self, session_id: str, turn: Mapping[str, object]) -> None: ...

    def runtime_activation_lock(self) -> AbstractContextManager[Path]: ...

    def turn_for_client(self, session_id: str, client_turn_id: str) -> dict[str, object] | None: ...

    def queued_turns(
        self,
        session_id: str,
        *,
        strict: bool = False,
        include_reserved: bool = False,
        capacity_at: str | None = None,
    ) -> list[dict[str, object]]: ...

    def queue_steering_lock(self, session_id: str, turn_id: str) -> AbstractContextManager[object]: ...

    def load_session(self, session_id: str) -> dict[str, object] | None: ...

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None: ...

    def events_after(self, session_id: str, turn_id: str, event_id: str | None) -> list[dict[str, object]]: ...

    def messages(self, session_id: str, *, strict: bool = False) -> list[dict[str, object]]: ...

    def append_event(
        self,
        session_id: str,
        turn_id: str,
        *,
        kind: str,
        payload: dict[str, object],
        buffered: bool = False,
    ) -> dict[str, object]: ...

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        text: str,
        turn_id: str | None = None,
        attachments: list[dict[str, object]] | None = None,
        origin: str | None = None,
        message_id: str | None = None,
        goal_draft: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def release_active_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        last_activity_at: str | object = ...,
        last_error_code: str | None | object = ...,
    ) -> bool: ...

    def flush_events(self, session_id: str, turn_id: str) -> int: ...
