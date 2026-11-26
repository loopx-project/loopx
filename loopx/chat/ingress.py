"""Durable queued inputs and ingress receipts for the existing Chat store."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from functools import cached_property
import json as json
from pathlib import Path
from typing import TYPE_CHECKING

from ..capabilities.native_chat.file_discovery import FileChatDiscovery
from ..file_lock import exclusive_file_lock
from .ingress_admission import (
    chat_runtime_activation_state as _chat_runtime_activation_state,
    ensure_external_admission_scope as _ensure_external_admission_scope,
    resolve_external_admission_source as _resolve_external_admission_source,
)
from .ingress_common import ConversationBindingsProtocol, as_int
from .ingress_queue import (
    complete_recovered_terminalization as _complete_recovered_terminalization,
    create_queued_turn as _create_queued_turn,
    matched_discovered_turn as _matched_discovered_turn,
    mutate_queued_input as _mutate_queued_input,
    queue_steering_lock as _queue_steering_lock,
    queued_turns as _queued_turns,
    recover_queued_turn_input as _recover_queued_turn_input,
    turn_discovery_rows as _turn_discovery_rows,
    turn_for_client as _turn_for_client,
    write_queue_steering as _write_queue_steering,
)
from .ingress_receipts import (
    create_ingress_receipt as _create_ingress_receipt,
    loopx_ingress as _loopx_ingress,
    steering_delivery_lock as _steering_delivery_lock,
    strict_ingress_receipt as _strict_ingress_receipt,
    update_ingress_receipt as _update_ingress_receipt,
)

SESSION_QUEUE_MAX_PENDING = 20
SESSION_QUEUE_TTL_SECONDS: int | None = None
STEWARD_BINDING_ADOPTION_PENDING = "steward_executor_binding_pending"


def resolve_external_admission_source(
    bindings: ConversationBindingsProtocol,
    binding_id: str,
    source: Mapping[str, object],
    source_preflight: Callable[[dict[str, object]], None] | None,
    *,
    session: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Resolve and optionally preflight-verify the binding for an inbound source."""
    return _resolve_external_admission_source(
        bindings,
        binding_id,
        source,
        source_preflight,
        session=session,
    )


def ensure_external_admission_scope(
    bindings: ConversationBindingsProtocol,
    selected: dict[str, object],
    path: Path,
    request: dict[str, object],
    *,
    source_preflight: Callable[[dict[str, object]], None] | None,
) -> dict[str, object]:
    """Revalidate delivery scope and execute atomic steward binding adoption."""
    return _ensure_external_admission_scope(
        bindings,
        selected,
        path,
        request,
        source_preflight=source_preflight,
        adoption_pending_code=STEWARD_BINDING_ADOPTION_PENDING,
        lock_fn=exclusive_file_lock,
    )


def chat_runtime_activation_lock(
    runtime_root: Path,
    *,
    timeout_seconds: float | None = None,
) -> AbstractContextManager[Path]:
    """Fence new durable input against a managed-service runtime switch."""
    return exclusive_file_lock(
        runtime_root.expanduser().resolve() / "chat" / "runtime-activation",
        agent_id="loopx-chat",
        operation="chat_runtime_activation",
        timeout_seconds=timeout_seconds,
    )


def chat_runtime_activation_state(
    runtime_root: Path,
    *,
    active_session_ids: list[str] | None = None,
) -> dict[str, int]:
    """Strict read of all accepted work; never initialize or recover a store."""
    return _chat_runtime_activation_state(
        runtime_root,
        active_session_ids=active_session_ids,
    )


class ChatIngressStore:
    """Mixin owning durable queued inputs and idempotent ingress receipts on ``ChatSessionStore``."""

    if TYPE_CHECKING:
        root: Path

        def _session_dir(self, session_id: str) -> Path: ...

        def _session_path(self, session_id: str) -> Path: ...

        def _session_lock(self, session_id: str) -> AbstractContextManager[object]: ...

        def _turn_path(self, session_id: str, turn_id: str) -> Path: ...

        def _event_path(self, session_id: str, turn_id: str) -> Path: ...

        def _ingress_path(self, session_id: str, client_ingress_id: str) -> Path: ...

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

    @cached_property
    def _terminal_turn_discovery(self) -> FileChatDiscovery:
        # Queue discovery and client lookup share this hint. Concurrent first use
        # may build two empty observers.
        return FileChatDiscovery()

    def _turn_discovery_rows(
        self,
        session_id: str,
        turns_dir: Path,
        read: Callable[[Path], dict[str, object]],
    ) -> Iterator[tuple[Path, dict[str, object], dict[str, object] | None]]:
        return _turn_discovery_rows(self, session_id, turns_dir, read)

    def _matched_discovered_turn(
        self,
        session_id: str,
        client_id: str,
        path: Path,
        fresh: dict[str, object] | None,
    ) -> dict[str, object]:
        return _matched_discovered_turn(session_id, client_id, path, fresh)

    def turn_for_client(self, session_id: str, client_turn_id: str) -> dict[str, object] | None:
        return _turn_for_client(self, session_id, client_turn_id)

    def runtime_activation_lock(self) -> AbstractContextManager[Path]:
        return chat_runtime_activation_lock(self.root.parent)

    def queued_turns(
        self,
        session_id: str,
        *,
        strict: bool = False,
        include_reserved: bool = False,
        capacity_at: str | None = None,
    ) -> list[dict[str, object]]:
        return _queued_turns(
            self,
            session_id,
            strict=strict,
            include_reserved=include_reserved,
            capacity_at=capacity_at,
        )

    def queue_steering_lock(self, session_id: str, turn_id: str) -> AbstractContextManager[object]:
        """Use the same Session->Turn fences as automatic queue consumption."""
        return _queue_steering_lock(self, session_id, turn_id, lock_fn=exclusive_file_lock)

    def mutate_queued_input(
        self,
        session_id: str,
        turn_id: str,
        request: Mapping[str, object],
    ) -> dict[str, object]:
        """Execute the TS owner's recoverable input plan under the consumer fences."""
        return _mutate_queued_input(self, session_id, turn_id, request, lock_fn=exclusive_file_lock)

    def steering_receipt(self, session_id: str, ingress_id: str) -> dict[str, object] | None:
        """A corrupt receipt cannot be treated as absence and resent."""
        return self._strict_ingress_receipt(session_id, ingress_id)

    def _strict_ingress_receipt(self, session_id: str, ingress_id: str) -> dict[str, object] | None:
        """Absent is ``None``; any other unreadable receipt fails closed."""
        return _strict_ingress_receipt(self, session_id, ingress_id)

    def steering_delivery_lock(self, session_id: str, ingress_id: str) -> AbstractContextManager[Path]:
        """Serialize one native send and its later readback across controllers."""
        return _steering_delivery_lock(self, session_id, ingress_id, lock_fn=exclusive_file_lock)

    def write_queue_steering(
        self,
        session_id: str,
        turn: dict[str, object],
        plan: Mapping[str, object],
    ) -> dict[str, object]:
        """Persist the typed plan while the caller holds both queue fences."""
        return _write_queue_steering(self, session_id, turn, plan)

    def complete_recovered_terminalization(
        self,
        session_id: str,
        turn_id: str,
        session: Mapping[str, object] | None,
        turn: Mapping[str, object] | None,
        *,
        recovery_messages: Mapping[str, str],
    ) -> None:
        """Finish the lifecycle owner's already-persisted failure, never dispatch."""
        _complete_recovered_terminalization(
            self,
            session_id,
            turn_id,
            session,
            turn,
            recovery_messages=recovery_messages,
        )

    def create_queued_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        attachments: Sequence[Mapping[str, object]] | None = None,
        goal_instance_id: str | None = None,
        ttl_seconds: int | None = SESSION_QUEUE_TTL_SECONDS,
        origin: str = "external",
        external_agent_target: Mapping[str, object] | None = None,
        collaboration_request: Mapping[str, object] | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Persist one follow-up without replacing the active Turn."""
        return _create_queued_turn(
            self,
            session_id,
            client_turn_id=client_turn_id,
            message=message,
            attachments=attachments,
            goal_instance_id=goal_instance_id,
            ttl_seconds=ttl_seconds,
            origin=origin,
            external_agent_target=external_agent_target,
            collaboration_request=collaboration_request,
            max_pending=as_int(SESSION_QUEUE_MAX_PENDING, 20),
            lock_fn=exclusive_file_lock,
        )

    def _recover_queued_turn_input(self, session_id: str, turn: Mapping[str, object]) -> None:
        """Finish interrupted input IO under the caller's Session fence."""
        _recover_queued_turn_input(self, session_id, turn)

    def create_ingress_receipt(
        self,
        session_id: str,
        *,
        client_ingress_id: str,
        mode: str,
        message: str,
        expected_turn_id: str | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Reserve one idempotent external ingress before provider delivery."""
        return _create_ingress_receipt(
            self,
            session_id,
            client_ingress_id=client_ingress_id,
            mode=mode,
            message=message,
            expected_turn_id=expected_turn_id,
            lock_fn=exclusive_file_lock,
        )

    def update_ingress_receipt(
        self,
        session_id: str,
        client_ingress_id: str,
        **changes: object,
    ) -> dict[str, object]:
        return _update_ingress_receipt(
            self,
            session_id,
            client_ingress_id,
            exclusive_file_lock,
            **changes,
        )

    def loopx_ingress(self, session_id: str) -> list[dict[str, object]]:
        """Read the existing ingress ledger; no second queue or delivery truth."""
        return _loopx_ingress(self, session_id)
