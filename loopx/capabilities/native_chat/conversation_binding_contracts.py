"""Typed contracts, protocols, and shared pure helpers for conversation bindings."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, overload

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result

if TYPE_CHECKING:
    from ...chat_store import ChatSessionStore

BindingPayload = dict[str, object]
BindingPayloadView = Mapping[str, object]
ObserveFn = Callable[[str], BindingPayloadView]
EventSinkFn = Callable[[str, BindingPayload], None]
CoreEffectFn = Callable[..., BindingPayload]
ReadJsonFn = Callable[[Path], BindingPayload]
WriteJsonFn = Callable[..., None]
ReplyStatusFn = Callable[[Path, BindingPayload], Sequence[BindingPayload]]
InspectResultFn = Callable[[BindingPayload, BindingPayload, Path], object]
LockFn = Callable[..., AbstractContextManager[object]]


class ObserveAudienceFn(Protocol):
    def __call__(
        self, transport_ref: str, *, audience: str | None = None
    ) -> BindingPayloadView: ...


class BindingRowDict(dict[str, object]):
    """Structured dict representing one persisted conversation binding entry."""

    @overload
    def __getitem__(
        self,
        key: Literal[
            "schema_version",
            "binding_id",
            "transport_ref",
            "provider_ref",
            "operator_ref",
            "context_kind",
            "project_ref",
            "executor_endpoint_id",
            "grant",
            "goal_scope",
            "audience",
        ],
        /,
    ) -> str: ...

    @overload
    def __getitem__(self, key: Literal["enabled"], /) -> bool: ...

    @overload
    def __getitem__(
        self,
        key: Literal["goal_ids", "group_refs"],
        /,
    ) -> list[str]: ...

    @overload
    def __getitem__(
        self,
        key: Literal["agent_targets"],
        /,
    ) -> list[BindingPayload]: ...

    @overload
    def __getitem__(
        self,
        key: Literal["managed_actor"],
        /,
    ) -> BindingPayload | None: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


class BindingStateDict(dict[str, object]):
    """Structured dict representing the persisted conversation-bindings document."""

    @overload
    def __getitem__(self, key: Literal["schema_version"], /) -> str: ...

    @overload
    def __getitem__(self, key: Literal["revision"], /) -> int: ...

    @overload
    def __getitem__(
        self,
        key: Literal["app_steward_binding_id"],
        /,
    ) -> str | None: ...

    @overload
    def __getitem__(
        self,
        key: Literal["bindings"],
        /,
    ) -> list[BindingRowDict]: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


class ResolvedBindingDict(dict[str, object]):
    """Structured dict returned when resolving a verified bound conversation."""

    @overload
    def __getitem__(self, key: Literal["binding"], /) -> BindingRowDict: ...

    @overload
    def __getitem__(
        self,
        key: Literal["context", "observation"],
        /,
    ) -> BindingPayload: ...

    @overload
    def __getitem__(self, key: Literal["channel_id"], /) -> str: ...

    @overload
    def __getitem__(self, key: Literal["owner_manager_audience"], /) -> bool: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


class AppStewardTargetDict(dict[str, object]):
    """Structured dict returned by ``app_steward_target``."""

    @overload
    def __getitem__(
        self,
        key: Literal["binding_id", "session_id"],
        /,
    ) -> str | None: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


class LocalSessionSnapshotDict(dict[str, object]):
    """Structured dict returned by ``local_session_snapshot``."""

    @overload
    def __getitem__(self, key: Literal["sessions"], /) -> list[BindingPayload]: ...

    @overload
    def __getitem__(
        self,
        key: Literal["conversation_source"],
        /,
    ) -> BindingPayload: ...

    @overload
    def __getitem__(self, key: str, /) -> object: ...

    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


class BindingAuthorityProtocol(Protocol):
    """Protocol implemented by ``ChatConversationBindings`` for peer orchestration."""

    path: Path
    projects: ProjectContextsProtocol | None

    def read(self) -> BindingStateDict: ...

    def observe(
        self,
        transport_ref: str,
        *,
        audience: str | None = None,
    ) -> BindingPayload: ...


class ProjectContextsProtocol(Protocol):
    """Protocol matching ``ChatProjectContexts`` consumed by bindings and requests."""

    workspace_grant: str
    conversation_bindings: BindingAuthorityProtocol | None

    def available(self) -> Sequence[BindingPayloadView]: ...

    def session_context(self, session: BindingPayload, /) -> BindingPayload: ...


class BindingSessionStoreProtocol(Protocol):
    """Protocol matching ``ChatSessionStore`` methods used across bindings."""

    root: Path

    def list_sessions(
        self,
        *,
        goal_id: str | None = None,
        agent_id: str | None = None,
        channel_id: str | None = None,
    ) -> Sequence[BindingPayload]: ...

    def load_session(self, session_id: str, /) -> BindingPayload | None: ...

    def load_turn(
        self,
        session_id: str,
        turn_id: str,
        /,
    ) -> BindingPayload | None: ...

    def queued_turns(
        self,
        session_id: str,
        *,
        strict: bool = False,
        include_reserved: bool = False,
        capacity_at: str | None = None,
    ) -> Sequence[BindingPayload]: ...

    def update_session(
        self,
        session_id: str,
        *,
        expected_statuses: set[str] | None = None,
        expected_upstream_thread_id: str | None = None,
        metadata_review: bool = False,
        quota_recovery: bool = False,
        answer_review_admission: bool = False,
        **changes: object,
    ) -> BindingPayload: ...

    def read_session_fenced(
        self,
        session_id: str,
        /,
    ) -> AbstractContextManager[BindingPayload | None]: ...

    def iter_session_metadata(
        self,
        *,
        goal_id: str | None = None,
        agent_id: str | None = None,
        channel_id: str | None = None,
    ) -> Iterator[tuple[Path, BindingPayload]]: ...

    def public_session(self, payload: BindingPayload, /) -> BindingPayload: ...

    def turn_for_client(
        self,
        session_id: str,
        client_turn_id: str,
        /,
    ) -> BindingPayload | None: ...

    def create_queued_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        origin: str = "external",
        collaboration_request: BindingPayload | None = None,
    ) -> tuple[BindingPayload, bool]: ...

    def update_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        expected_statuses: set[str] | None = None,
        **changes: object,
    ) -> BindingPayload | None: ...

    def resumable_session_candidates(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> Sequence[BindingPayload]: ...


class BindingControllerProtocol(Protocol):
    """Protocol matching ``ChatRuntimeController`` dependencies for bindings."""

    registry_path: Path | None
    coordination_runtime_root: Path
    store: ChatSessionStore
    project_contexts: ProjectContextsProtocol

    def interrupt_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> BindingPayload: ...

    def resume_session_queue(
        self,
        *,
        session_id: str,
        work_dir: Path,
        objective: str,
    ) -> None: ...


class NativeSessionProtocol(Protocol):
    """Protocol matching native session dispatch in original conversation execution."""

    def send(
        self,
        user_message: str,
        /,
        *,
        project_collaboration: bool = False,
        turn_start_guard: Callable[[], AbstractContextManager[None]] | None = None,
        on_event: Callable[[str, BindingPayload], None] | None = None,
    ) -> BindingPayload: ...


def as_binding_payload(value: BindingPayloadView) -> BindingPayload:
    """Convert a string-keyed mapping into a plain mutable payload dict."""
    return {str(key): item for key, item in value.items()}


def as_binding_row(value: BindingPayloadView) -> BindingRowDict:
    """Wrap a binding mapping in ``BindingRowDict`` preserving exact keys."""
    return BindingRowDict({str(key): item for key, item in value.items()})


def as_binding_state(value: BindingPayloadView) -> BindingStateDict:
    """Wrap a bindings document in ``BindingStateDict`` preserving exact keys."""
    copied = {str(key): item for key, item in value.items()}
    raw_bindings = copied.get("bindings")
    if isinstance(raw_bindings, list):
        copied["bindings"] = [
            as_binding_row(item) if isinstance(item, Mapping) else item
            for item in raw_bindings
        ]
    return BindingStateDict(copied)


def core_effect(
    operation: str,
    params: BindingPayloadView,
    *,
    large_local_snapshot: bool = False,
) -> BindingPayload:
    """Execute one typed TypeScript Core rule and normalize rejection into ``ValueError``."""
    try:
        result = effect_runtime_result(
            operation,
            dict(params),
            large_local_snapshot=large_local_snapshot,
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict):
        raise ValueError("conversation authority returned an invalid result")
    return as_binding_payload(result)


def session_observation(session: BindingPayloadView) -> BindingPayload:
    """Project the exact Session fields evaluated by conversation Core policies."""
    return {
        key: session.get(key)
        for key in (
            "session_id",
            "goal_id",
            "agent_id",
            "executor_endpoint_id",
            "channel_id",
            "session_mode",
            "status",
            "project_context",
            "steward_context",
            "manager_executor_allocation",
            "upstream_context_history",
        )
    }


def host_ref(session: BindingPayloadView) -> str:
    """Compute the opaque 24-hex host reference for an attached session."""
    return hashlib.sha256(
        f"{session['host_surface']}\0{session['upstream_thread_id']}".encode()
    ).hexdigest()[:24]


def host_audiences(
    store: BindingSessionStoreProtocol,
    session: BindingPayloadView,
) -> list[str]:
    """Collect content-free binding stamps across all store sessions for a host."""
    owners: set[str] = set()
    for row in store.list_sessions():
        session_id = row.get("session_id")
        if not isinstance(session_id, str):
            continue
        candidate = store.load_session(session_id)
        if (
            candidate
            and candidate.get("host_surface") == session["host_surface"]
            and candidate.get("upstream_thread_id") == session["upstream_thread_id"]
            and candidate.get("external_conversation_binding_id")
        ):
            owners.add(str(candidate["external_conversation_binding_id"]))
    return sorted(owners)
