"""Observe bound Codex threads through Codex's local store.

Codex records each thread in ``<CODEX_HOME>/state_<n>.sqlite`` (``threads``
table) and appends every turn event to the thread's rollout JSONL. Neither is a
public Codex contract, so this adapter checks each shape it depends on and
reports ``unknown`` for anything else. It opens the store read-only and uses
only record types and timestamps; message content is never projected.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .control_plane.agents.host_thread_activity import (
    HostDeliveryExpectation,
    HostDeliveryScope,
    HostThreadActivity,
    HostThreadObserver,
    HostThreadState,
    HostThreadUnknownReason,
)

# Remote surfaces such as codex-app-ssh keep their store on another machine.
CODEX_LOCAL_STORE_SURFACES = frozenset({"codex-app", "codex-cli-tui", "codex-ide-plugin"})

# An installed app automation drives the app surface; the home's other local
# surfaces keep their own loop, so the automation cadence is not theirs to carry.
CODEX_APP_BINDING_SURFACE = "codex-app"
CODEX_HOMES_ENV = "LOOPX_CODEX_HOMES"

_STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")
_REQUIRED_THREAD_COLUMNS = frozenset({"id", "rollout_path", "archived"})
_TURN_START = "task_started"
_TURN_ENDS = frozenset({"task_complete", "turn_aborted"})
_EVENT_MSG_RECORD = b'"event_msg"'
_TAIL_CHUNK_BYTES = 256 * 1024
_TAIL_LIMIT_BYTES = 8 * 1024 * 1024
_ROLLOUT_CACHE_LIMIT = 256

# The canonical rrule parser reads INTERVAL with JavaScript's integer
# conversion, which reports no value outside this range.
_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_SAFE_INTEGER_DIGITS = len(str(_MAX_SAFE_INTEGER))

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


def current_codex_execution_identity(
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read metadata for the exact invoking session in its selected home only.

    Store discovery remains provider I/O; the shared TypeScript runtime owns
    Turn matching, public projection and declaration consistency. Never use the
    store's mutable model preferences as execution evidence.
    """
    from .control_plane.effect_runtime import effect_runtime_result

    env = os.environ if env is None else env
    thread = env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID")
    if not thread:
        return {"status": "unavailable", "reason": "session_not_bound"}
    home = Path(env.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser().resolve()
    try:
        db = _state_db(home)
        rows = _thread_rows(db, [thread]) if db else {}
    except (OSError, sqlite3.Error, _UnrecognizedStore):
        return {"status": "unavailable", "reason": "host_store_unavailable"}
    row = rows.get(thread)
    if row is None:
        return {"status": "unavailable", "reason": "session_not_found"}
    path, archived = row
    if archived != 0 or not isinstance(path, str) or not path:
        return {"status": "unavailable", "reason": "session_record_unavailable"}
    try:
        result: dict[str, Any] = effect_runtime_result("runtime.execution_identity.codex", {
            "home": str(home), "path": path, "thread_id": thread,
        })
        return result
    except RuntimeError:
        return {"status": "unavailable", "reason": "host_runtime_unavailable"}


def _record(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("type"), str):
        return None
    return record if isinstance(record.get("timestamp"), str) else None


def _turn_marker(record: dict[str, Any]) -> HostThreadState | None:
    payload = record.get("payload")
    if record["type"] != "event_msg":
        return None
    if not isinstance(payload, dict):
        return HostThreadState.UNKNOWN
    kind = payload.get("type")
    if not isinstance(kind, str):
        return HostThreadState.UNKNOWN
    if kind == _TURN_START:
        return HostThreadState.TURN_OPEN
    return HostThreadState.IDLE if kind in _TURN_ENDS else None


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
        elif _EVENT_MSG_RECORD not in line and b"\\u" not in line:
            continue
        else:
            record = _record(line)
            if record is None:
                return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
        marker = _turn_marker(record)
        if marker is HostThreadState.UNKNOWN:
            return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
        if marker is HostThreadState.TURN_OPEN:
            return HostThreadActivity(
                state=HostThreadState.TURN_OPEN,
                turn_started_at=record["timestamp"],
                last_event_at=last_event_at,
            )
        if marker is HostThreadState.IDLE:
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


def _automation_rrule_interval_minutes(rrule: Any) -> int | None:
    """Read the interval a minutely automation rrule asks for.

    The canonical parse is ``schedulerRruleIntervalMinutes`` in the TypeScript
    scheduler (``scheduler/state_store.ts``). Its Python twin
    (``scheduler_rrule_interval_minutes``) is an effect-runtime call, so reading
    an installed automation through it would give the status route a scheduler
    runtime dependency. This mirrors the canonical contract rather than keeping
    a second, looser dialect: the same normalization, the same first-``=``
    split, an exact ``FREQ=MINUTELY``, and a positive integer ``INTERVAL``. An
    rrule this adapter cannot read reports no interval, never one.
    """

    text = str(rrule or "").strip()
    text = re.sub(r"\s+", " ", text)
    if text.upper().startswith("RRULE:"):
        text = text[6:].strip()
    parts: dict[str, str] = {}
    for part in text.split(";"):
        separator = part.find("=")
        if separator < 0:
            continue
        parts[part[:separator].strip().upper()] = part[separator + 1 :].strip()
    if parts.get("FREQ", "").upper() != "MINUTELY":
        return None
    # ``INTERVAL`` is read the way the canonical parser reads it: absent,
    # non-numeric and out-of-range all report no interval, so an unreadable
    # rrule can never be measured as if it fired every minute.
    raw_interval = parts.get("INTERVAL", "")
    if re.fullmatch(r"[+-]?[0-9]+", raw_interval) is None:
        return None
    # Rule the value out by its significant digits before asking the interpreter
    # to build it. Python refuses an integer of more than
    # ``sys.get_int_max_str_digits()`` digits, and an unreadable manifest must
    # never escape this provider: the status route turns a local projection
    # failure into one 500 for the whole workspace, so a single oversized
    # manifest would hide every healthy lane. The canonical parser reports no
    # value for anything this large either.
    negative = raw_interval.startswith("-")
    digits = raw_interval.lstrip("+-").lstrip("0")
    if len(digits) > _MAX_SAFE_INTEGER_DIGITS:
        return None
    # Convert the significant digits only: the interpreter's limit counts
    # leading zeros too, so the length check above must bound what is converted.
    interval = int(("-" if negative else "") + digits) if digits else 0
    if interval > _MAX_SAFE_INTEGER or interval < -_MAX_SAFE_INTEGER:
        return None
    return interval if interval > 0 else None


def _installed_automation_intervals(
    homes: list[Path] | None = None,
) -> dict[tuple[str, str, str], int]:
    """Map each installed heartbeat automation to the lane it serves.

    A lane is identified the way the canonical resolver identifies it
    (``loopx.upgrade.resolve_codex_app_automation_rrule``): Goal, agent and the
    bound ``target_thread_id``. Only a ``kind = "heartbeat"`` manifest carries a
    heartbeat cadence, and an automation that cannot be placed on one lane -- or
    two that claim the same lane -- is omitted, so the window stays ``unknown``
    instead of being measured against another lane's cadence.
    """

    from .upgrade import infer_agent_id_from_prompt, infer_goal_id_from_prompt

    intervals: dict[tuple[str, str, str], int] = {}
    ambiguous: set[tuple[str, str, str]] = set()
    for home in codex_homes() if homes is None else homes:
        for path in sorted((home / "automations").glob("*/automation.toml")):
            try:
                item = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                continue
            if str(item.get("status") or "").upper() != "ACTIVE":
                continue
            # Another kind installed for the same Goal is a different loop, not
            # this lane's heartbeat.
            if str(item.get("kind") or "").strip().lower() != "heartbeat":
                continue
            prompt = item.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            goal_id = infer_goal_id_from_prompt(prompt)
            agent_id = infer_agent_id_from_prompt(prompt)
            target_thread_id = str(item.get("target_thread_id") or "").strip()
            interval = _automation_rrule_interval_minutes(item.get("rrule"))
            if not goal_id or not agent_id or not target_thread_id or interval is None:
                continue
            key = (goal_id, agent_id, target_thread_id)
            if key in intervals:
                # Two automations claim one lane: neither is that lane's cadence.
                del intervals[key]
                ambiguous.add(key)
                continue
            if key not in ambiguous:
                intervals[key] = interval
    return intervals


def codex_delivery_expectations(
    scopes: Iterable[HostDeliveryScope],
    *,
    homes: list[Path] | None = None,
) -> dict[HostDeliveryScope, HostDeliveryExpectation]:
    """Resolve each bound app lane's cadence from its installed automation.

    A lane is placed by its canonical binding, so an automation installed for
    another thread of the same Goal is not read as this lane's. A lane the
    installed automations cannot place is omitted, so its window is reported as
    ``unknown`` rather than measured against a guessed cadence.
    """

    wanted = list(scopes)
    if not any(scope.host_surface == CODEX_APP_BINDING_SURFACE for scope in wanted):
        return {}
    intervals = _installed_automation_intervals(homes)
    expectations: dict[HostDeliveryScope, HostDeliveryExpectation] = {}
    for scope in wanted:
        if scope.host_surface != CODEX_APP_BINDING_SURFACE:
            continue
        if not scope.thread_id:
            # Without the binding, this lane cannot be told apart from another
            # lane of the same Goal and agent.
            continue
        interval = intervals.get((scope.goal_id, scope.agent_id, scope.thread_id))
        if interval is None:
            continue
        expectations[scope] = HostDeliveryExpectation(
            expected_interval_minutes=interval,
            source="codex_app_automation_rrule",
        )
    return expectations
