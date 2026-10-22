"""Observe bound Codex threads through Codex's local store.

Codex records each thread in ``<CODEX_HOME>/state_<n>.sqlite`` (``threads``
table) and appends every turn event to the thread's rollout JSONL. Neither is a
public Codex contract, so this adapter checks each shape it depends on and
reports ``unknown`` for anything else. It opens the store read-only and uses
only record types and timestamps; message content is never projected.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tomllib
from threading import Lock
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .hosts.codex_rollout_cursor import Reader, RolloutCursor
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
_OBSERVATION_CHUNK_BYTES = 256 * 1024
_OBSERVATION_READ_BYTES = 8 * 1024 * 1024
_ROLLOUT_CACHE_LIMIT = 256

# The canonical rrule parser reads INTERVAL with JavaScript's integer
# conversion, which reports no value outside this range.
_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_SAFE_INTEGER_DIGITS = len(str(_MAX_SAFE_INTEGER))

_rollout_cache: dict[str, RolloutCursor] = {}
_rollout_cache_lock = Lock()


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


def _rollout_activity(
    rollout_path: Any, home: Path, *, thread_id: str, cache_root: Path | None = None,
) -> HostThreadActivity:
    if not isinstance(rollout_path, str) or not rollout_path:
        return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
    path, root = Path(os.path.realpath(rollout_path)), Path(os.path.realpath(home))
    if not path.is_relative_to(root):
        return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
    # Never write to Codex's store, even if an optional cache is misconfigured.
    if cache_root is not None:
        try:
            if cache_root.resolve().is_relative_to(root):
                cache_root = None
        except (OSError, RuntimeError):
            cache_root = None
    binding = hashlib.sha256(f"{root}\0{path}\0{thread_id}".encode()).hexdigest()
    try:
        if not path.is_file():
            return HostThreadActivity.unknown(HostThreadUnknownReason.STORE_UNAVAILABLE)
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            if Path(os.path.realpath(path)) != path or (stat.st_dev, stat.st_ino) != (path.stat().st_dev, path.stat().st_ino):
                return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
            reader = Reader(handle, _OBSERVATION_READ_BYTES, chunk_bytes=_OBSERVATION_CHUNK_BYTES)
            with _rollout_cache_lock:
                cursor = _rollout_cache.get(binding)
                if cursor is None:
                    cursor = RolloutCursor(binding, thread_id)
                    if len(_rollout_cache) >= _ROLLOUT_CACHE_LIMIT:
                        _rollout_cache.pop(next(iter(_rollout_cache)))
                    _rollout_cache[binding] = cursor
            if not cursor.lock.acquire(blocking=False):
                return HostThreadActivity.unknown(HostThreadUnknownReason.STORE_UNAVAILABLE)
            try:
                result = cursor.observe(reader, stat, cache_root=cache_root)
                end, named = os.fstat(handle.fileno()), path.stat()
                if Path(os.path.realpath(path)) != path or (end.st_dev, end.st_ino) != (named.st_dev, named.st_ino) or end.st_size < stat.st_size:
                    _rollout_cache.pop(binding, None)
                    return HostThreadActivity.unknown(HostThreadUnknownReason.STORE_UNAVAILABLE)
                if end.st_size == stat.st_size and (end.st_mtime_ns != stat.st_mtime_ns or end.st_ctime_ns != stat.st_ctime_ns):
                    _rollout_cache.pop(binding, None)
                    return HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)
                cursor.save(cache_root)
                # An append may contain a newer end/cancel. Do not publish an old
                # open marker across that unread interval; the next read catches up.
                if end.st_size != stat.st_size:
                    return HostThreadActivity.unknown(HostThreadUnknownReason.NO_TURN_MARKER)
                return result
            finally:
                cursor.lock.release()

    except OSError:
        return HostThreadActivity.unknown(HostThreadUnknownReason.STORE_UNAVAILABLE)


def observe_codex_threads(
    thread_ids: Iterable[str],
    *,
    homes: list[Path] | None = None,
    cache_root: Path | None = None,
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
                result[thread_id] = _rollout_activity(rollout_path, home, thread_id=thread_id, cache_root=cache_root)
        remaining = [thread_id for thread_id in remaining if thread_id not in rows]
    for thread_id in remaining:
        result[thread_id] = HostThreadActivity.unknown(fallback)
    return result


def codex_thread_observers(
    homes: list[Path] | None = None, *, cache_root: Path | None = None,
) -> dict[str, HostThreadObserver]:
    def observe(thread_ids: Iterable[str]) -> dict[str, HostThreadActivity]:
        return observe_codex_threads(thread_ids, homes=homes, cache_root=cache_root)

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
