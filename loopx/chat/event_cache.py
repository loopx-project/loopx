"""Revision-bound chat replay snapshots and event stream mixin for ``ChatSessionStore``."""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any
import uuid

from ..file_lock import exclusive_file_lock

EventKey = tuple[str, str]
EventRevision = tuple[int, int, int] | None
EventRows = list[dict[str, Any]]

# These bound terminal retention, not active history or peak decoding memory.
TERMINAL_CACHE_MAX_TURNS = 8
TERMINAL_CACHE_MAX_ROWS = 4096
TERMINAL_CACHE_MAX_ENCODED_BYTES = 2 * 1024 * 1024


def _durable_by_append_id(rows: EventRows) -> dict[str, dict[str, Any]]:
    return {
        row["_append_id"]: row
        for row in rows
        if isinstance(row.get("_append_id"), str)
    }


class ChatEventCache:
    def __init__(self, lock: threading.RLock) -> None:
        self._lock = lock
        self._entries: dict[EventKey, tuple[EventRevision, EventRows]] = {}
        self._finished: dict[EventKey, tuple[int, int]] = {}

    def get(self, key: EventKey, revision: EventRevision) -> EventRows | None:
        with self._lock:
            entry = self._entries.get(key)
            return entry[1] if entry is not None and entry[0] == revision else None

    def put(self, key: EventKey, revision: EventRevision, rows: EventRows) -> None:
        with self._lock:
            self._finished.pop(key, None)
            self._entries[key] = (revision, rows)

    def drop(self, key: EventKey) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._finished.pop(key, None)

    def retain_terminal(self, key: EventKey, rows: EventRows) -> None:
        with self._lock:
            entry = self._entries.get(key)
            # An append or compaction can replace a snapshot while it is replayed.
            if entry is None or entry[1] is not rows:
                return
            revision = entry[0]
            self._finished.pop(key, None)
            self._finished[key] = (len(rows), revision[1] if revision else 0)
            while (
                len(self._finished) > TERMINAL_CACHE_MAX_TURNS
                or sum(weight[0] for weight in self._finished.values()) > TERMINAL_CACHE_MAX_ROWS
                or sum(weight[1] for weight in self._finished.values()) > TERMINAL_CACHE_MAX_ENCODED_BYTES
            ):
                self.drop(next(iter(self._finished)))


class ChatEventStreamMixin:
    if TYPE_CHECKING:
        sessions_root: Path
        _event_lock: threading.RLock
        _event_cache: ChatEventCache
        _event_pending: dict[EventKey, list[tuple[dict[str, Any], str]]]

        def _event_path(self, session_id: str, turn_id: str) -> Path: ...
        def _event_flush_lock(self, key: EventKey) -> threading.Lock: ...

    def _event_rows_locked(self, session_id: str, turn_id: str) -> EventRows:
        from .. import chat_store as _store

        key = (session_id, turn_id)
        path = self._event_path(session_id, turn_id)
        revision = self._event_revision(path)
        rows = self._event_cache.get(key, revision)
        if rows is None:
            rows = _store._read_jsonl(path)
            self._event_cache.put(key, revision, rows)
        return rows

    @staticmethod
    def _event_revision(path: Path) -> EventRevision:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def append_event(
        self,
        session_id: str,
        turn_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        buffered: bool = False,
    ) -> dict[str, Any]:
        from .. import chat_store as _store

        key = (session_id, turn_id)
        event = {
            "schema_version": _store.CHAT_EVENT_SCHEMA_VERSION,
            "kind": str(kind),
            "created_at": _store.utc_now(),
            "payload": payload,
        }
        with self._event_lock:
            self._event_pending.setdefault(key, []).append((event, uuid.uuid4().hex))
        if not buffered:
            self.flush_events(session_id, turn_id)
        return event

    def flush_events(self, session_id: str, turn_id: str) -> int:
        """Persist queued events in one ordered append and one data fsync."""
        from .. import chat_store as _store

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
                        append_started = False
                        try:
                            uncertain = any("sequence" in event for event, _ in pending)
                            rows = (
                                _store._read_jsonl(path, raise_on_error=True)
                                if uncertain
                                else self._event_rows_locked(session_id, turn_id)
                            )
                            # A failed append may already have persisted all or part of
                            # its batch. Match only this store's stable append ids; two
                            # otherwise identical events are still separate writes.
                            durable = _durable_by_append_id(rows) if uncertain else {}
                            # Allocate after the last persisted sequence under the same
                            # file lock. Compaction may leave gaps; another writer may
                            # have appended after a partial failed batch.
                            sequence = int(rows[-1].get("sequence") or 0) if rows else 0
                            to_append: EventRows = []
                            for event, append_id in pending:
                                committed = durable.get(append_id)
                                if committed is not None:
                                    event["event_id"] = committed["event_id"]
                                    event["sequence"] = committed["sequence"]
                                    continue
                                sequence += 1
                                event["event_id"] = str(sequence)
                                event["sequence"] = sequence
                                to_append.append({**event, "_append_id": append_id})
                            append_started = True
                            _store._append_jsonl_rows(path, to_append)
                            if any(event["kind"] in _store.TERMINAL_EVENT_KINDS for event, _ in pending):
                                self._event_cache.drop(key)
                            else:
                                self._event_cache.put(key, self._event_revision(path), [*rows, *to_append])
                        except Exception:
                            if not append_started:
                                raise
                            # Confirm visible writes before releasing the file lock:
                            # another store may compact old events before our retry.
                            try:
                                written = _durable_by_append_id(_store._read_jsonl(path, raise_on_error=True))
                            except OSError:
                                pass  # Keep all uncertain events for a later retry.
                            else:
                                remaining = []
                                for event, append_id in pending:
                                    committed = written.get(append_id)
                                    if committed is None:
                                        remaining.append((event, append_id))
                                    else:
                                        event["event_id"] = committed["event_id"]
                                        event["sequence"] = committed["sequence"]
                                pending = remaining
                            raise
                except Exception:
                    self._event_cache.drop(key)
                    with self._event_lock:
                        later = self._event_pending.get(key, [])
                        if pending or later:
                            self._event_pending[key] = [*pending, *later]
                        else:
                            self._event_pending.pop(key, None)
                    raise
                flushed += len(pending)

    def events_after(self, session_id: str, turn_id: str, event_id: str | None) -> EventRows:
        from .. import chat_store as _store

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
        if rows and rows[-1].get("kind") in _store.TERMINAL_EVENT_KINDS:
            self._event_cache.retain_terminal(key, rows)
        return [
            {field: value for field, value in row.items() if field != "_append_id"}
            for row in result
        ]

    def compact_completed_events(self, *, older_than_hours: float = 24.0) -> int:
        """Drop replay-only deltas after the durable final message is old enough."""
        from .. import chat_store as _store

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.0, older_than_hours))
        compacted = 0
        for turn_path in self.sessions_root.glob("*/turns/*.json"):
            turn = _store._read_json(turn_path)
            if turn.get("status") not in _store.TERMINAL_TURN_STATES:
                continue
            completed = _store._instant(turn.get("completed_at"))
            if completed is None or completed > cutoff:
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
                retained = [row for row in rows if row.get("kind") not in _store.REPLAY_ONLY_EVENT_KINDS]
                if len(retained) != len(rows):
                    _store._replace_jsonl(event_path, retained)
                    compacted += 1
                revision = self._event_revision(event_path)
                self._event_cache.drop((session_id, turn_id))
            with exclusive_file_lock(turn_path, agent_id="loopx-chat", operation="mark_chat_events_compacted"):
                current = _store._read_json(turn_path)
                if current.get("status") in _store.TERMINAL_TURN_STATES:
                    current["event_compaction_revision"] = list(revision or ())
                    _store._atomic_write_json(turn_path, current, preserve_mode=True)
        return compacted
