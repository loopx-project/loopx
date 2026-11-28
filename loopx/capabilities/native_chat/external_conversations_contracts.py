"""Typed request journal contracts and JSON narrowers for external conversations."""
from __future__ import annotations

from collections.abc import Callable, ItemsView, Iterator, KeysView, Mapping, ValuesView
from typing import TYPE_CHECKING, Literal, Self, TypeVar, cast, overload

Payload = dict[str, object]
PayloadView = Mapping[str, object]
EventSink = Callable[[str, Payload], None]
SourceControlsReader = Callable[[], list[Payload]]
SourceObservation = Callable[[Payload], Payload | None]
SourcePreflight = Callable[[Payload], None]
_T = TypeVar("_T")

_SourceReqKey = Literal["source_ref"]
_SourceOptStrKey = Literal["provider_ref", "topic_ref", "group_ref", "sender_ref", "origin"]
_SourceOptBoolKey = Literal["private_human_message", "group_human_message"]

_NativeRecStrKey = Literal["turn_id", "message_id", "message_text"]
_NativeRecBoolKey = Literal["delivery_verified"]
_NativeRecKey = Literal["turn_id", "message_id", "message_text", "delivery_verified"]
_NativeRecOptStrKey = Literal["session_id", "recovered_at"]

_ReplayStrKey = Literal["target_turn_id"]
_ReplayOptStrKey = Literal["original_response"]
_ReplayKey = Literal["target_turn_id", "original_response"]

_BacklogStrKey = Literal["binding_id", "created_at", "request_ref", "status"]
_ReqNullableStrKey = Literal["command", "session_id", "turn_id"]
_ReqSourceKey = Literal["source"]
_BacklogKey = Literal["binding_id", "created_at", "request_ref", "status", "command", "session_id", "turn_id", "source"]

_ReqStrKey = Literal["binding_id", "created_at", "message", "request_ref", "status"]
_ReqKey = Literal[
    "binding_id",
    "created_at",
    "message",
    "request_ref",
    "status",
    "command",
    "session_id",
    "turn_id",
    "source",
]
_ReqNullableStrGetKey = Literal[
    "origin",
    "command",
    "session_id",
    "turn_id",
    "capture_origin",
    "client_turn_id",
    "history_answer_hold",
    "profile",
    "proposal_id",
    "response",
    "response_code",
    "source_created_at",
]
_ReqOptBoolKey = Literal[
    "commission_adoption_pending",
    "delivery_verified",
    "native_mention",
    "selection_recorded",
    "target_recorded",
]
_ReqOptDictKey = Literal[
    "agent_target",
    "command_argument",
    "commission_resources",
    "context_recovery",
    "mention_capture",
    "mention_delivery",
    "selection_target",
    "status_snapshot",
]


if TYPE_CHECKING:
    class _ExternalDictView:
        """Type-check-only dict view restricting [] while preserving dict container methods."""

        def __init__(self, initial: Mapping[str, object] | None = None, /, **kwargs: object) -> None: ...
        def __contains__(self, key: object, /) -> bool: ...
        def __iter__(self) -> Iterator[str]: ...
        def __len__(self) -> int: ...
        def __setitem__(self, key: str, value: object, /) -> None: ...
        def __delitem__(self, key: str, /) -> None: ...
        def copy(self) -> Self: ...
        def values(self) -> ValuesView[object]: ...
        def items(self) -> ItemsView[str, object]: ...
        def update(self, other: Mapping[str, object] = ..., /, **kwargs: object) -> None: ...
        def pop(self, key: str, default: object = ..., /) -> object: ...
        def setdefault(self, key: str, default: object = ..., /) -> object: ...
        def clear(self) -> None: ...

    class _OptionalRequestGetMixin(_ExternalDictView):
        """Explicitly typed optional .get(...) reads shared by request and backlog records."""

        @overload
        def get(self, key: _ReqStrKey, /) -> str | None: ...
        @overload
        def get(self, key: _ReqStrKey, default: _T, /) -> str | _T: ...
        @overload
        def get(self, key: _ReqNullableStrGetKey, /) -> str | None: ...
        @overload
        def get(self, key: _ReqNullableStrGetKey, default: _T, /) -> str | None | _T: ...
        @overload
        def get(self, key: _ReqSourceKey, /) -> ExternalSourceDict | None: ...
        @overload
        def get(self, key: _ReqSourceKey, default: _T, /) -> ExternalSourceDict | _T: ...
        @overload
        def get(self, key: _ReqOptBoolKey, /) -> bool | None: ...
        @overload
        def get(self, key: _ReqOptBoolKey, default: _T, /) -> bool | None | _T: ...
        @overload
        def get(self, key: Literal["selection_order"], /) -> int | None: ...
        @overload
        def get(self, key: Literal["selection_order"], default: _T, /) -> int | None | _T: ...
        @overload
        def get(self, key: Literal["native_result_recovery"], /) -> NativeResultRecoveryDict | None: ...
        @overload
        def get(self, key: Literal["native_result_recovery"], default: _T, /) -> NativeResultRecoveryDict | None | _T: ...
        @overload
        def get(self, key: Literal["rejected_mention_replay"], /) -> RejectedMentionReplayDict | None: ...
        @overload
        def get(self, key: Literal["rejected_mention_replay"], default: _T, /) -> RejectedMentionReplayDict | None | _T: ...
        @overload
        def get(self, key: _ReqOptDictKey, /) -> Payload | None: ...
        @overload
        def get(self, key: _ReqOptDictKey, default: _T, /) -> Payload | None | _T: ...
        @overload
        def get(self, key: Literal["attachments"], /) -> list[Payload] | None: ...
        @overload
        def get(self, key: Literal["attachments"], default: _T, /) -> list[Payload] | None | _T: ...
        @overload
        def get(self, key: str, /) -> object: ...
        @overload
        def get(self, key: str, default: object, /) -> object: ...
        def get(self, key: str, default: object = None, /) -> object: ...

    class ExternalSourceDict(_ExternalDictView):
        """Typed view over an external request source payload preserving dict subtype."""

        def keys(self) -> KeysView[_SourceReqKey]: ...
        def __getitem__(self, key: _SourceReqKey, /) -> str: ...
        @overload
        def get(self, key: _SourceReqKey, /) -> str | None: ...
        @overload
        def get(self, key: _SourceReqKey, default: _T, /) -> str | _T: ...
        @overload
        def get(self, key: _SourceOptStrKey, /) -> str | None: ...
        @overload
        def get(self, key: _SourceOptStrKey, default: _T, /) -> str | None | _T: ...
        @overload
        def get(self, key: _SourceOptBoolKey, /) -> bool | None: ...
        @overload
        def get(self, key: _SourceOptBoolKey, default: _T, /) -> bool | None | _T: ...
        @overload
        def get(self, key: str, /) -> object: ...
        @overload
        def get(self, key: str, default: object, /) -> object: ...
        def get(self, key: str, default: object = None, /) -> object: ...

    class NativeResultRecoveryDict(_ExternalDictView):
        """Typed view over a recovered native-result carrier preserving dict subtype."""

        def keys(self) -> KeysView[_NativeRecKey]: ...
        @overload
        def __getitem__(self, key: _NativeRecStrKey, /) -> str: ...
        @overload
        def __getitem__(self, key: _NativeRecBoolKey, /) -> bool: ...
        def __getitem__(self, key: _NativeRecKey, /) -> object: ...
        @overload
        def get(self, key: _NativeRecStrKey, /) -> str | None: ...
        @overload
        def get(self, key: _NativeRecStrKey, default: _T, /) -> str | _T: ...
        @overload
        def get(self, key: _NativeRecOptStrKey, /) -> str | None: ...
        @overload
        def get(self, key: _NativeRecOptStrKey, default: _T, /) -> str | None | _T: ...
        @overload
        def get(self, key: _NativeRecBoolKey, /) -> bool | None: ...
        @overload
        def get(self, key: _NativeRecBoolKey, default: _T, /) -> bool | _T: ...
        @overload
        def get(self, key: str, /) -> object: ...
        @overload
        def get(self, key: str, default: object, /) -> object: ...
        def get(self, key: str, default: object = None, /) -> object: ...

    class RejectedMentionReplayDict(_ExternalDictView):
        """Typed view over a rejected-mention replay carrier preserving dict subtype."""

        def keys(self) -> KeysView[_ReplayKey]: ...
        @overload
        def __getitem__(self, key: _ReplayStrKey, /) -> str: ...
        @overload
        def __getitem__(self, key: _ReplayOptStrKey, /) -> str | None: ...
        def __getitem__(self, key: _ReplayKey, /) -> object: ...
        @overload
        def get(self, key: _ReplayStrKey, /) -> str | None: ...
        @overload
        def get(self, key: _ReplayStrKey, default: _T, /) -> str | _T: ...
        @overload
        def get(self, key: _ReplayOptStrKey, /) -> str | None: ...
        @overload
        def get(self, key: _ReplayOptStrKey, default: _T, /) -> str | None | _T: ...
        @overload
        def get(self, key: str, /) -> object: ...
        @overload
        def get(self, key: str, default: object, /) -> object: ...
        def get(self, key: str, default: object = None, /) -> object: ...

    class ExternalBacklogRequestDict(_OptionalRequestGetMixin):
        """Typed view over projected backlog request records omitting unprojected body fields."""

        def keys(self) -> KeysView[_BacklogKey]: ...
        @overload
        def __getitem__(self, key: _BacklogStrKey, /) -> str: ...
        @overload
        def __getitem__(self, key: _ReqNullableStrKey, /) -> str | None: ...
        @overload
        def __getitem__(self, key: _ReqSourceKey, /) -> ExternalSourceDict: ...
        def __getitem__(self, key: _BacklogKey, /) -> object: ...

    class ExternalRequestDict(_OptionalRequestGetMixin):
        """Typed view over external conversation journal records preserving dict subtype."""

        def keys(self) -> KeysView[_ReqKey]: ...
        @overload
        def __getitem__(self, key: _ReqStrKey, /) -> str: ...
        @overload
        def __getitem__(self, key: _ReqNullableStrKey, /) -> str | None: ...
        @overload
        def __getitem__(self, key: _ReqSourceKey, /) -> ExternalSourceDict: ...
        def __getitem__(self, key: _ReqKey, /) -> object: ...
else:
    class ExternalSourceDict(dict[str, object]):
        """Typed view over an external request source payload preserving dict subtype."""

    class NativeResultRecoveryDict(dict[str, object]):
        """Typed view over a recovered native-result carrier preserving dict subtype."""

    class RejectedMentionReplayDict(dict[str, object]):
        """Typed view over a rejected-mention replay carrier preserving dict subtype."""

    class ExternalBacklogRequestDict(dict[str, object]):
        """Typed view over projected backlog request records omitting unprojected body fields."""

    class ExternalRequestDict(dict[str, object]):
        """Typed view over external conversation journal records preserving dict subtype."""


RequestRecord = Payload | ExternalRequestDict
RequestView = PayloadView | ExternalRequestDict
SourceMapping = PayloadView | ExternalSourceDict
RequestMapping = (
    PayloadView
    | ExternalRequestDict
    | ExternalBacklogRequestDict
    | ExternalSourceDict
    | NativeResultRecoveryDict
    | RejectedMentionReplayDict
)
RequestPayload = (
    Payload
    | ExternalRequestDict
    | ExternalBacklogRequestDict
    | ExternalSourceDict
    | NativeResultRecoveryDict
    | RejectedMentionReplayDict
)


def as_payload(value: RequestPayload | RequestView) -> Payload:
    """Narrow a closed journal contract back to a mutable dict payload at internal write boundaries."""
    return cast(Payload, value)


def _as_dict(value: object, *, message: str = "expected object payload") -> Payload:
    if not isinstance(value, dict):
        raise ValueError(message)
    return cast(Payload, value)


def as_external_request(value: object, *, message: str = "expected object payload") -> ExternalRequestDict:
    """Narrow a parsed JSON object as ExternalRequestDict without changing runtime dict identity."""
    return cast(ExternalRequestDict, _as_dict(value, message=message))


def as_external_backlog_request(
    value: object, *, message: str = "expected object payload"
) -> ExternalBacklogRequestDict:
    """Narrow a projected backlog record without promising unprojected full-body fields."""
    return cast(ExternalBacklogRequestDict, _as_dict(value, message=message))


def _opt_dict(value: object) -> Payload | None:
    return cast(Payload, value) if isinstance(value, dict) else None


def _dict_or_empty(value: object) -> Payload:
    return cast(Payload, value) if isinstance(value, dict) else {}


def _opt_dict_list(value: object) -> list[Payload] | None:
    return [cast(Payload, item) for item in value if isinstance(item, dict)] if isinstance(value, list) else None


def _dict_list(value: object) -> list[Payload]:
    return _opt_dict_list(value) or []


def _str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _strip_attachments(mapping: RequestMapping) -> Payload:
    return {key: value for key, value in mapping.items() if key != "attachments"}
