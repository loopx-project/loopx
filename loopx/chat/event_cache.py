"""Revision-bound chat replay snapshots; file I/O and locking stay in the store."""
from __future__ import annotations

import threading
from typing import Any

EventKey = tuple[str, str]
EventRevision = tuple[int, int, int] | None
EventRows = list[dict[str, Any]]

# These bound terminal retention, not active history or peak decoding memory.
TERMINAL_CACHE_MAX_TURNS = 8
TERMINAL_CACHE_MAX_ROWS = 4096
TERMINAL_CACHE_MAX_ENCODED_BYTES = 2 * 1024 * 1024


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
