"""Observe bound Codex threads through Codex's local store.

Codex records each thread in ``<CODEX_HOME>/state_<n>.sqlite`` (``threads``
table) and appends every turn event to the thread's rollout JSONL. Neither is a
public Codex contract, so this adapter checks each shape it depends on and
reports ``unknown`` for anything else. It opens the store read-only and reads
only record types and timestamps, never message content.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .control_plane.agents.host_thread_activity import (
    HostThreadActivity,
    HostThreadObserver,
    HostThreadState,
    HostThreadUnknownReason,
)

# Remote surfaces such as codex-app-ssh keep their store on another machine.
CODEX_LOCAL_STORE_SURFACES = frozenset({"codex-app", "codex-cli-tui", "codex-ide-plugin"})
CODEX_HOMES_ENV = "LOOPX_CODEX_HOMES"

_STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")
_REQUIRED_THREAD_COLUMNS = frozenset({"id", "rollout_path", "archived"})
_TURN_START = "task_started"
_TURN_ENDS = frozenset({"task_complete", "turn_aborted"})
_TURN_MARKERS = tuple(f'"{marker}"'.encode() for marker in (_TURN_START, *_TURN_ENDS))
_TAIL_CHUNK_BYTES = 256 * 1024
_TAIL_LIMIT_BYTES = 8 * 1024 * 1024
_ROLLOUT_CACHE_LIMIT = 256

_rollout_cache: dict[tuple[str, int, int], HostThreadActivity] = {}


class _UnrecognizedStore(Exception):
    pass


def codex_homes(env: Mapping[str, str] | None = None, user_home: Path | None = None) -> list[Path]:
    """Return Codex homes to search, in order.

    ``LOOPX_CODEX_HOMES`` (``os.pathsep``-separated) is used verbatim when set.
    Otherwise ``CODEX_HOME``, ``~/.codex`` and sibling ``~/.codex-*`` homes are
    searched, because Codex App installs can each keep their own home.
    """

    env = os.environ if env is None else env
    user_home = Path.home() if user_home is None else user_home
    explicit = env.get(CODEX_HOMES_ENV)
    if explicit is not None:
        candidates = [Path(part).expanduser() for part in explicit.split(os.pathsep) if part.strip()]
    else:
        candidates = [Path(env["CODEX_HOME"]).expanduser()] if env.get("CODEX_HOME") else []
        candidates.append(user_home / ".codex")
        candidates.extend(sorted(user_home.glob(".codex-*")))
    homes: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in homes:
            homes.append(candidate)
    return homes


def _state_db(home: Path) -> Path | None:
    versions = [
        (int(match.group(1)), path)
        for path in home.iterdir()
        if (match := _STATE_DB_RE.match(path.name)) and path.is_file()
    ]
    return max(versions)[1] if versions else None


def _query_threads(uri: str, thread_ids: list[str]) -> dict[str, tuple[Any, Any]]:
    connection = sqlite3.connect(uri, uri=True)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
        if not _REQUIRED_THREAD_COLUMNS <= columns:
            raise _UnrecognizedStore(uri)
        placeholders = ",".join("?" for _ in thread_ids)
        rows = connection.execute(
            f"SELECT id, rollout_path, archived FROM threads WHERE id IN ({placeholders})",
            thread_ids,
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]): (row[1], row[2]) for row in rows}


def _thread_rows(db: Path, thread_ids: list[str]) -> dict[str, tuple[Any, Any]]:
    try:
        return _query_threads(f"{db.as_uri()}?mode=ro", thread_ids)
    except sqlite3.OperationalError:
        # A WAL store whose -shm is absent cannot be read with mode=ro.
        # Without a -wal file the main file is the whole store.
        if Path(f"{db}-wal").exists():
            raise
        return _query_threads(f"{db.as_uri()}?immutable=1", thread_ids)


def _record(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("type"), str):
        return None
    return record if isinstance(record.get("timestamp"), str) else None


def _turn_marker(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    if record["type"] != "event_msg" or not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    return kind if kind == _TURN_START or kind in _TURN_ENDS else None


def _tail_lines(path: Path) -> Iterable[bytes]:
    """Yield complete lines from the end of the file backwards, within the tail limit.

    A final line without a newline is still being appended and is skipped.
    """

    with path.open("rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        floor = max(0, position - _TAIL_LIMIT_BYTES)
        pending = b""
        in_progress = True
        while position > floor:
            size = min(_TAIL_CHUNK_BYTES, position - floor)
            position -= size
            handle.seek(position)
            parts = (handle.read(size) + pending).split(b"\n")
            pending = parts.pop(0)
            if in_progress:
                if not parts:
                    pending = b""
                    continue
                parts.pop()
                in_progress = False
            for line in reversed(parts):
                if line.strip():
                    yield line
        if position == 0 and not in_progress and pending.strip():
            yield pending


def _rollout_activity(rollout_path: Any, home: Path) -> HostThreadActivity:
    if not isinstance(rollout_path, str) or not rollout_path:
        return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
    path = Path(os.path.realpath(rollout_path))
    if not path.is_relative_to(Path(os.path.realpath(home))):
        return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
    try:
        stat = path.stat()
    except OSError:
        return HostThreadActivity.unknown(HostThreadUnknownReason.STORE_UNAVAILABLE)
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _rollout_cache:
        try:
            activity = _scan_rollout(path)
        except OSError:
            return HostThreadActivity.unknown(HostThreadUnknownReason.STORE_UNAVAILABLE)
        if len(_rollout_cache) >= _ROLLOUT_CACHE_LIMIT:
            _rollout_cache.clear()
        _rollout_cache[key] = activity
    return _rollout_cache[key]


def _scan_rollout(path: Path) -> HostThreadActivity:
    last_event_at: str | None = None
    for line in _tail_lines(path):
        if last_event_at is None:
            record = _record(line)
            if record is None:
                return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
            last_event_at = record["timestamp"]
        elif not any(marker in line for marker in _TURN_MARKERS):
            continue
        else:
            record = _record(line)
        marker = _turn_marker(record) if record else None
        if marker == _TURN_START:
            return HostThreadActivity(
                state=HostThreadState.TURN_OPEN,
                turn_started_at=record["timestamp"],
                last_event_at=last_event_at,
            )
        if marker is not None:
            return HostThreadActivity(
                state=HostThreadState.IDLE,
                last_turn_ended_at=record["timestamp"],
                last_event_at=last_event_at,
            )
    reason = HostThreadUnknownReason.NO_TURN_MARKER if last_event_at else HostThreadUnknownReason.RECORD_UNRECOGNIZED
    return HostThreadActivity.unknown(reason)


def observe_codex_threads(
    thread_ids: Iterable[str],
    *,
    homes: list[Path] | None = None,
) -> dict[str, HostThreadActivity]:
    remaining = sorted(set(thread_ids))
    if not remaining:
        return {}
    homes = codex_homes() if homes is None else homes
    result: dict[str, HostThreadActivity] = {}
    fallback = HostThreadUnknownReason.THREAD_NOT_FOUND
    for home in homes:
        if not remaining:
            break
        try:
            db = _state_db(home)
            if db is None:
                continue
            rows = _thread_rows(db, remaining)
        except _UnrecognizedStore:
            fallback = HostThreadUnknownReason.RECORD_UNRECOGNIZED
            continue
        except (OSError, sqlite3.Error):
            if fallback is HostThreadUnknownReason.THREAD_NOT_FOUND:
                fallback = HostThreadUnknownReason.STORE_UNAVAILABLE
            continue
        for thread_id, (rollout_path, archived) in rows.items():
            if archived not in (0, 1, False, True):
                result[thread_id] = HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
            elif archived:
                result[thread_id] = HostThreadActivity(state=HostThreadState.ARCHIVED)
            else:
                result[thread_id] = _rollout_activity(rollout_path, home)
        remaining = [thread_id for thread_id in remaining if thread_id not in rows]
    for thread_id in remaining:
        result[thread_id] = HostThreadActivity.unknown(fallback)
    return result


def codex_thread_observers(homes: list[Path] | None = None) -> dict[str, HostThreadObserver]:
    def observe(thread_ids: Iterable[str]) -> dict[str, HostThreadActivity]:
        return observe_codex_threads(thread_ids, homes=homes)

    return {surface: observe for surface in CODEX_LOCAL_STORE_SURFACES}
