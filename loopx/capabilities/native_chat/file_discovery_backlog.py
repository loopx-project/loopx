"""Revision-bound quota backlog observation cache, turn-event summaries, and recovery reads."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol, TypeVar, overload, runtime_checkable


_CacheKeyT = TypeVar("_CacheKeyT")
_CacheValT = TypeVar("_CacheValT")
_RowT = TypeVar("_RowT", bound=Mapping[str, object])

_MetaKey = tuple[object, ...]
_ObservationMetaEntry = tuple[
    _MetaKey | None,
    Path | None,
    Path | None,
    tuple[int, ...] | None,
    tuple[int, ...] | None,
]
_EventSummary = tuple[dict[str, object] | None, bool, bool]


@runtime_checkable
class _TurnPathStoreProtocol(Protocol):
    def _turn_path(self, session_id: str, turn_id: str) -> Path: ...


class _EventStoreProtocol(Protocol):
    def events_after(self, session_id: str, turn_id: str, event_id: str | None) -> Sequence[Mapping[str, object]]: ...


class _FlushLockProtocol(Protocol):
    def acquire(self, blocking: bool = ...) -> bool: ...
    def release(self) -> None: ...


@runtime_checkable
class _BufferedEventStoreProtocol(_EventStoreProtocol, Protocol):
    _event_lock: AbstractContextManager[object]
    _event_pending: Mapping[tuple[str, str], object]

    def _event_path(self, session_id: str, turn_id: str) -> Path: ...
    def _event_flush_lock(self, key: tuple[str, str]) -> _FlushLockProtocol: ...


class _BacklogStoreProtocol(_EventStoreProtocol, Protocol):
    def load_session(self, session_id: str) -> Mapping[str, object] | None: ...


class _RecoveryStoreProtocol(Protocol):
    def queued_turns(self, session_id: str) -> Sequence[object]: ...


class _RecoveryControllerProtocol(Protocol):
    @property
    def store(self) -> _RecoveryStoreProtocol: ...


class _RecoveryCoreProtocol(Protocol):
    @property
    def controller(self) -> _RecoveryControllerProtocol: ...
    def _cached_user_messages(self, session_id: str) -> Sequence[Mapping[str, object]]: ...
    def source_controls(self) -> Sequence[Mapping[str, object]]: ...


class _DiscoveryRowDict(dict[str, object]):
    """Read-only dict view carrying known ISO-timestamp key types."""

    @overload
    def __getitem__(self, key: Literal["created_at"]) -> str: ...

    @overload
    def __getitem__(self, key: str) -> object: ...

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


def _optional_fp(path: Path) -> tuple[int, ...] | None:
    # Look up through the facade module so platform/monkeypatch test overrides apply uniformly.
    from . import file_discovery

    return file_discovery.optional_file_fingerprint(path)


def _require_str(mapping: Mapping[str, object], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a str")
    return value


def _iso_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"fromisoformat: argument must be str, not {type(value).__name__}")
    return datetime.fromisoformat(value)


def _set_bounded_cache(
    cache_map: dict[_CacheKeyT, _CacheValT],
    key: _CacheKeyT,
    value: _CacheValT,
    max_entries: int,
) -> None:
    if key in cache_map:
        del cache_map[key]
    elif len(cache_map) >= max_entries:
        del cache_map[next(iter(cache_map))]
    cache_map[key] = value


class QuotaBacklogObservationCache:
    """Revision-bound cache for read-only quota backlog health classification.

    Stores only pure boolean verdicts and Turn status labels keyed by file
    fingerprints, Session status, and evaluator identity. Never retains Turn
    bodies or request attachments.
    """

    def __init__(self, *, max_entries: int = 8192) -> None:
        self._lock = Lock()
        self._max_entries = max(1, max_entries)
        self._base: dict[str, tuple[_MetaKey, tuple[bool, str | None]]] = {}
        self._stopped: dict[str, tuple[_MetaKey, bool]] = {}
        self._event_summaries: dict[tuple[str, str], tuple[tuple[int, ...], _EventSummary]] = {}

    def get_base(self, request_ref: str, key: _MetaKey) -> tuple[bool, str | None] | None:
        with self._lock:
            entry = self._base.get(request_ref)
            return entry[1] if entry is not None and entry[0] == key else None

    def has_base_prefix(self, request_ref: str, key_prefix: _MetaKey) -> bool:
        with self._lock:
            entry = self._base.get(request_ref)
            return entry is not None and entry[0][:-1] == key_prefix

    def put_base(self, request_ref: str, key: _MetaKey, pending: bool, turn_status: str | None) -> None:
        with self._lock:
            _set_bounded_cache(self._base, request_ref, (key, (pending, turn_status)), self._max_entries)
            stopped = self._stopped.get(request_ref)
            if stopped is not None and stopped[0] != key:
                self._stopped.pop(request_ref, None)

    def get_stopped(self, request_ref: str, key: _MetaKey) -> bool | None:
        with self._lock:
            entry = self._stopped.get(request_ref)
            return entry[1] if entry is not None and entry[0] == key else None

    def put_stopped(self, request_ref: str, key: _MetaKey, pending: bool) -> None:
        with self._lock:
            _set_bounded_cache(self._stopped, request_ref, (key, pending), self._max_entries)

    def drop_request(self, request_ref: str) -> None:
        with self._lock:
            self._base.pop(request_ref, None)
            self._stopped.pop(request_ref, None)

    @staticmethod
    def build_key_prefix(
        root: Path,
        store: _TurnPathStoreProtocol,
        row: Mapping[str, object],
        evaluator_id: int,
        *,
        req_fp_override: tuple[int, ...] | None = None,
    ) -> _ObservationMetaEntry:
        try:
            request_ref = _require_str(row, "request_ref")
            session_id = _require_str(row, "session_id")
            turn_id = _require_str(row, "turn_id")
            req_path = root / f"{request_ref}.json"
            turn_path = store._turn_path(session_id, turn_id)
            req_fp = req_fp_override if isinstance(req_fp_override, tuple) else _optional_fp(req_path)
            turn_fp = _optional_fp(turn_path)
        except (OSError, TypeError, ValueError):
            return None, None, None, None, None
        if req_fp is None or turn_fp is None:
            return None, req_path, turn_path, req_fp, turn_fp
        prefix: _MetaKey = (
            req_fp,
            turn_fp,
            session_id,
            turn_id,
            row.get("status"),
            row.get("command"),
            evaluator_id,
        )
        return prefix, req_path, turn_path, req_fp, turn_fp

    def commit(
        self,
        request_ref: str,
        meta_entry: _ObservationMetaEntry,
        pending: bool,
        *,
        turn_status: str | None = None,
        stopped: bool = False,
    ) -> None:
        key, req_path, turn_path, req_fp, turn_fp = meta_entry
        if key is None or req_path is None or turn_path is None:
            return
        if (_optional_fp(req_path), _optional_fp(turn_path)) != (req_fp, turn_fp):
            self.drop_request(request_ref)
            return
        if stopped:
            self.put_stopped(request_ref, key, pending)
        else:
            self.put_base(request_ref, key, pending, turn_status)

    def get_event_summary(
        self, session_id: str, turn_id: str, events_fp: tuple[int, ...],
    ) -> _EventSummary | None:
        with self._lock:
            entry = self._event_summaries.get((session_id, turn_id))
            if entry is None or entry[0] != events_fp:
                return None
            anchor, steering, interrupted = entry[1]
            return (deepcopy(anchor) if anchor is not None else None), steering, interrupted

    def put_event_summary(
        self, session_id: str, turn_id: str, events_fp: tuple[int, ...], summary: _EventSummary,
    ) -> None:
        anchor, steering, interrupted = summary
        stored: _EventSummary = (deepcopy(anchor) if anchor is not None else None), steering, interrupted
        with self._lock:
            _set_bounded_cache(self._event_summaries, (session_id, turn_id), (events_fp, stored), self._max_entries)

    def drop_event_summary(self, session_id: str, turn_id: str) -> None:
        with self._lock:
            self._event_summaries.pop((session_id, turn_id), None)

    def get_interrupt(self, session_id: str, turn_id: str, events_fp: tuple[int, ...]) -> bool | None:
        summary = self.get_event_summary(session_id, turn_id, events_fp)
        return summary[2] if summary is not None else None

    def put_interrupt(self, session_id: str, turn_id: str, events_fp: tuple[int, ...], observed: bool) -> None:
        self.put_event_summary(session_id, turn_id, events_fp, (None, False, observed))

    def drop_interrupt(self, session_id: str, turn_id: str) -> None:
        self.drop_event_summary(session_id, turn_id)


def _summarize_turn_events(events: Sequence[Mapping[str, object]]) -> _EventSummary:
    anchor: dict[str, object] | None = None
    anchor_count = 0
    steering = False
    interrupted = False
    for item in events:
        kind = item.get("kind")
        if kind == "turn.started":
            payload = item.get("payload")
            if isinstance(payload, Mapping):
                native_input = payload.get("native_input")
                if native_input and isinstance(native_input, dict):
                    anchor_count += 1
                    anchor = native_input if anchor_count == 1 else None
        elif kind == "turn.steered":
            steering = True
        elif kind in {"turn.interrupt_requested", "turn.interrupted"}:
            interrupted = True
    single_anchor = deepcopy(anchor) if anchor_count == 1 and anchor is not None else None
    return single_anchor, steering, interrupted


def observed_turn_event_summary(
    store: _EventStoreProtocol,
    session_id: str,
    turn_id: str,
    cache_store: QuotaBacklogObservationCache | None,
    *,
    real_events: bool,
) -> _EventSummary:
    """Read compact Turn event summary with fingerprint/pending-buffer guard on real stores."""
    if not real_events or cache_store is None or not isinstance(store, _BufferedEventStoreProtocol):
        return _summarize_turn_events(store.events_after(session_id, turn_id, None))
    key = (session_id, turn_id)
    try:
        event_path = store._event_path(session_id, turn_id)
    except ValueError:
        return _summarize_turn_events(store.events_after(session_id, turn_id, None))
    flush_lock = store._event_flush_lock(key)
    fp_before: tuple[int, ...] | None = None
    if flush_lock.acquire(blocking=False):
        try:
            with store._event_lock:
                has_pending = bool(store._event_pending.get(key))
            if not has_pending:
                fp_before = _optional_fp(event_path)
                if fp_before is not None:
                    cached = cache_store.get_event_summary(session_id, turn_id, fp_before)
                    if cached is not None:
                        return cached
        finally:
            flush_lock.release()
    summary = _summarize_turn_events(store.events_after(session_id, turn_id, None))
    if fp_before is not None and flush_lock.acquire(blocking=False):
        try:
            with store._event_lock:
                has_pending_after = bool(store._event_pending.get(key))
            fp_after = None if has_pending_after else _optional_fp(event_path)
            if fp_after is not None and fp_after == fp_before:
                cache_store.put_event_summary(session_id, turn_id, fp_after, summary)
            else:
                cache_store.drop_event_summary(session_id, turn_id)
        finally:
            flush_lock.release()
    return summary


def observed_turn_interrupt(
    store: _EventStoreProtocol,
    session_id: str,
    turn_id: str,
    cache_store: QuotaBacklogObservationCache | None,
    *,
    real_events: bool,
) -> bool:
    """Check Turn interrupt events with fingerprint/pending-buffer guard on real stores."""
    return observed_turn_event_summary(
        store, session_id, turn_id, cache_store, real_events=real_events,
    )[2]


def _same_store_method(store: object, name: str, expected_cls: type[object]) -> bool:
    return (
        isinstance(store, expected_cls)
        and getattr(getattr(store, name, None), "__func__", None) is getattr(expected_cls, name)
    )


def observe_quota_backlog_batch(
    *,
    root: Path,
    store: _BacklogStoreProtocol,
    rows: Sequence[_RowT],
    build_inputs: Callable[[_RowT], dict[str, object]],
    eval_flags: Callable[[Sequence[dict[str, object]]], list[bool]],
    stop_controls: Callable[[], tuple[tuple[object, object, datetime], ...]],
    evaluator_id: int,
    cache_store: QuotaBacklogObservationCache | None,
) -> list[bool]:
    """Evaluate backlog pending flags with optional revision-bound caching."""
    from ...chat_store import ChatSessionStore

    real_load_turn = (
        cache_store is not None
        and _same_store_method(store, "load_turn", ChatSessionStore)
        and isinstance(store, _TurnPathStoreProtocol)
    )
    real_events = cache_store is not None and _same_store_method(store, "events_after", ChatSessionStore)
    flags: list[bool] = [False] * len(rows)
    turn_statuses: list[str | None] = [None] * len(rows)
    params: list[dict[str, object] | None] = [None] * len(rows)
    meta: list[_ObservationMetaEntry] = [(None, None, None, None, None)] * len(rows)
    for index, row in enumerate(rows):
        if real_load_turn and cache_store is not None and isinstance(store, _TurnPathStoreProtocol):
            req_fp_raw = row.req_fp if isinstance(row, _DeferredBacklogRequest) else getattr(row, "req_fp", None)
            req_fp_override = req_fp_raw if isinstance(req_fp_raw, tuple) else None
            entry = cache_store.build_key_prefix(root, store, row, evaluator_id, req_fp_override=req_fp_override)
        else:
            entry = (None, None, None, None, None)
        meta[index] = entry
        if entry[0] is not None and cache_store is not None and cache_store.has_base_prefix(_require_str(row, "request_ref"), entry[0]):
            continue
        params[index] = build_inputs(row)
    load_session = store.load_session
    sessions: dict[str, dict[str, object] | None] = {}
    for session_id in dict.fromkeys(_require_str(row, "session_id") for row in rows):
        # A transient read failure must not mask a closed Session.
        session = load_session(session_id) or load_session(session_id)
        sessions[session_id] = (
            {key: session[key] for key in ("session_id", "status") if key in session}
            if session is not None
            else None
        )
    misses: list[tuple[int, dict[str, object]]] = []
    for index, row in enumerate(rows):
        session_row = sessions[_require_str(row, "session_id")]
        prefix, req_path, turn_path, req_fp, turn_fp = meta[index]
        if prefix is not None and cache_store is not None:
            session_status = session_row.get("status") if session_row is not None else None
            full_key: _MetaKey = (*prefix, session_status)
            meta[index] = (full_key, req_path, turn_path, req_fp, turn_fp)
            cached = cache_store.get_base(_require_str(row, "request_ref"), full_key)
            if cached is not None:
                flags[index], turn_statuses[index] = cached
                continue
        existing_params = params[index]
        miss_values = existing_params if existing_params is not None else build_inputs(row)
        miss_values["current_session"] = session_row
        params[index] = miss_values
        misses.append((index, miss_values))
    if misses:
        for (index, values), pending in zip(misses, eval_flags([v for _, v in misses]), strict=True):
            pending_obs = values["pending_observation"]
            turn_dict = pending_obs.get("turn") if isinstance(pending_obs, Mapping) else None
            turn_status_raw = turn_dict.get("status") if isinstance(turn_dict, Mapping) else None
            turn_status = turn_status_raw if isinstance(turn_status_raw, str) else None
            flags[index], turn_statuses[index] = pending, turn_status
            if cache_store is not None:
                cache_store.commit(_require_str(rows[index], "request_ref"), meta[index], pending, turn_status=turn_status)
    stopped_misses: list[tuple[int, dict[str, object]]] = []
    controls_cache: tuple[tuple[object, object, datetime], ...] | None = None
    for index, (row, pending) in enumerate(zip(rows, flags, strict=True)):
        if not pending:
            continue
        session_id = _require_str(row, "session_id")
        turn_id = _require_str(row, "turn_id")
        if not observed_turn_interrupt(store, session_id, turn_id, cache_store, real_events=real_events):
            if turn_statuses[index] not in {"failed", "completed"}:
                continue
            row_created = _iso_datetime(row["created_at"])
            if controls_cache is None:
                controls_cache = stop_controls()
            if not any(
                b == row["binding_id"] and s == row["source"] and c >= row_created
                for b, s, c in controls_cache
            ):
                continue
        cache_key = meta[index][0]
        if cache_store is not None and cache_key is not None:
            cached_stop = cache_store.get_stopped(_require_str(row, "request_ref"), cache_key)
            if cached_stop is not None:
                flags[index] = cached_stop
                continue
        stop_values = params[index]
        if stop_values is None:
            stop_values = build_inputs(row)
            stop_values["current_session"] = sessions[session_id]
        pending_obs = stop_values["pending_observation"]
        if not isinstance(pending_obs, dict):
            raise TypeError("pending_observation must be a dict")
        pending_obs["stop_observed"] = True
        stopped_misses.append((index, stop_values))
    if stopped_misses:
        for (index, _), pending in zip(stopped_misses, eval_flags([v for _, v in stopped_misses]), strict=True):
            flags[index] = pending
            if cache_store is not None:
                cache_store.commit(_require_str(rows[index], "request_ref"), meta[index], pending, stopped=True)
    return flags


class _DeferredBacklogRequest(dict[str, object]):
    """Bounded backlog request view carrying its observed file fingerprint."""

    __slots__ = ("req_fp", "needs_full_read")

    def __init__(
        self,
        data: Mapping[str, object],
        *,
        req_fp: tuple[int, ...] | None,
        needs_full_read: bool,
    ) -> None:
        super().__init__(data)
        self.req_fp = req_fp
        self.needs_full_read = needs_full_read


class _RecoveryDiscoveryReads:
    """Lazy facts for one queue-discovery pass, never recovery admission.

    Historical requests share Session history. Retain queue/control/history
    hints, never provider identity or a recovery decision. A new
    pass builds new observations; normal admission never receives this object.
    """

    def __init__(self, core: _RecoveryCoreProtocol) -> None:
        self.core = core
        self.queues: dict[str, bool] = {}
        self.histories: dict[str, list[_DiscoveryRowDict]] = {}
        self.message_indexes: dict[str, dict[str, list[_DiscoveryRowDict]]] = {}
        self.controls: list[dict[str, object]] | None = None

    def queued(self, session_id: str) -> bool:
        if session_id not in self.queues:
            self.queues[session_id] = bool(self.core.controller.store.queued_turns(session_id))
        return self.queues[session_id]

    def user_messages(self, session_id: str) -> list[_DiscoveryRowDict]:
        if session_id not in self.histories:
            self.histories[session_id] = [_DiscoveryRowDict(row) for row in self.core._cached_user_messages(session_id)]
        return self.histories[session_id]

    def source_controls(self) -> list[dict[str, object]]:
        if self.controls is None:
            self.controls = [dict(row) for row in self.core.source_controls()]
        return self.controls

    def matching_user_messages(self, session_id: str, text: str) -> list[_DiscoveryRowDict]:
        if session_id not in self.message_indexes:
            index: dict[str, list[_DiscoveryRowDict]] = {}
            for row in self.user_messages(session_id):
                msg_text = row.get("text", "")
                key = msg_text if isinstance(msg_text, str) else ""
                index.setdefault(key, []).append(row)
            self.message_indexes[session_id] = index
        return self.message_indexes[session_id].get(text, [])
