"""Bounded authority-revision hot snapshot cache for manager turn context.

Reuses a verified local ``manager_turn_context`` snapshot only while every
underlying authority store, Goal runtime projection, declared state file,
delivery directory, and machine/policy configuration remains unchanged.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, cast

from . import _root
from .ssh_evidence import grants as _raw_ssh_grants, registered_evidence_hosts
from ...goal_portfolio import registry_semantic_revision
from ...history import decode_registry_snapshot
from ...paths import resolve_runtime_root

DEFAULT_MAX_AGE_SECONDS = 30.0
MAX_CACHE_ENTRIES = 16
MANAGER_CONTEXT_CACHE_ENV_VAR = "LOOPX_MANAGER_CONTEXT_CACHE"
_SQLITE_SUFFIXES = (".sqlite", ".db", ".sqlite3")
_ssh_grants = cast(Callable[[Path, str], Mapping[str, Any]], _raw_ssh_grants)


@dataclass(frozen=True)
class _CacheEntry:
    stored_at_monotonic: float
    payload: dict[str, Any]


_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[str, _CacheEntry] = OrderedDict()


def clear_manager_turn_context_cache() -> None:
    """Clear all in-memory cached manager turn context snapshots."""
    with _CACHE_LOCK:
        _CACHE.clear()


def is_manager_turn_context_cache_enabled(
    *,
    registry_path: Path | None,
    session: Mapping[str, Any],
    runtime_root: Path,
    scope_kind: str,
    owner_scope: bool,
    include_details: bool,
    include_stopped: bool,
    read_goal_status: bool,
    remote_evidence: bool,
    remote_runner: Any = None,
) -> bool:
    """Return False whenever the caller requires an uncached fresh read."""
    del include_details  # part of cache key; both True and False may cache
    if os.environ.get(MANAGER_CONTEXT_CACHE_ENV_VAR, "").strip().lower() == "off":
        return False
    if registry_path is None:
        return False
    if include_stopped is True:
        return False
    if read_goal_status is False:
        return False
    if remote_runner is not None:
        return False
    channel_id = str(session.get("channel_id") or "")
    if _ssh_grants(runtime_root, channel_id):
        return False
    if (
        owner_scope
        and scope_kind != "owner_goal"
        and remote_evidence
        and registered_evidence_hosts(runtime_root)
    ):
        return False
    return True


def _stat_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def _head_bytes_digest(path: Path, max_bytes: int) -> str | None:
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY)
        chunk = os.read(fd, max_bytes)
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    return hashlib.sha256(chunk).hexdigest()[:24]


def _sqlite_pragma_data_version(db_path: Path) -> int | None:
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
    except (OSError, ValueError):
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=0.05)
        row = conn.execute("PRAGMA data_version").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _sqlite_bundle_signature(db_path: Path) -> dict[str, Any] | None:
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")
    db_stat = _stat_signature(db_path)
    wal_stat = _stat_signature(wal_path)
    shm_stat = _stat_signature(shm_path)
    if db_stat is None and wal_stat is None and shm_stat is None:
        return None
    return {
        "db": db_stat,
        "wal": wal_stat,
        "shm": shm_stat,
        "data_version": _sqlite_pragma_data_version(db_path) if db_stat is not None else None,
        "db_hdr": _head_bytes_digest(db_path, 100) if db_stat is not None else None,
        "wal_hdr": _head_bytes_digest(wal_path, 64) if wal_stat is not None else None,
        "shm_hdr": _head_bytes_digest(shm_path, 136) if shm_stat is not None else None,
    }


def _deliveries_dir_signature(deliveries_dir: Path) -> dict[str, Any] | None:
    dir_stat = _stat_signature(deliveries_dir)
    if dir_stat is None:
        return None
    count = 0
    latest: tuple[int, str, int] | None = None
    try:
        with os.scandir(deliveries_dir) as it:
            for entry in it:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                count += 1
                candidate = (st.st_mtime_ns, entry.name, st.st_size)
                if latest is None or candidate > latest:
                    latest = candidate
    except OSError:
        return {"dir": dir_stat, "scan_error": True}
    return {"dir": dir_stat, "count": count, "latest": latest}


def _direct_sqlite_files(directory: Path) -> list[Path]:
    found: list[Path] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                name = entry.name
                if name.endswith(_SQLITE_SUFFIXES):
                    found.append(Path(entry.path))
    except OSError:
        return []
    found.sort(key=lambda item: item.name)
    return found


def _declared_goal_storage_paths(goal: Mapping[str, Any], base_dir: Path) -> list[Path]:
    raw_storage = goal.get("storage")
    candidates: list[str] = []
    if isinstance(raw_storage, str) and raw_storage.strip():
        candidates.append(raw_storage.strip())
    elif isinstance(raw_storage, Mapping):
        for key, value in raw_storage.items():
            if isinstance(value, str) and value.strip() and (
                key in {"path", "root", "directory", "runtime_root", "db_path", "sqlite_path", "file"}
                or key.endswith(("_path", "_dir", "_root", "_file"))
            ):
                candidates.append(value.strip())
    resolved: list[Path] = []
    for raw in candidates:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        try:
            norm = path.resolve()
        except OSError:
            norm = path
        if norm not in resolved:
            resolved.append(norm)
    return resolved


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            norm = path.expanduser().resolve()
        except OSError:
            norm = path.expanduser()
        key = str(norm)
        if key not in seen:
            seen.add(key)
            unique.append(norm)
    return unique


def _goal_local_fingerprint(
    goal: Mapping[str, Any],
    *,
    registry_path: Path,
    base_roots: list[Path],
) -> dict[str, Any]:
    goal_id = str(goal.get("id") or "")
    goal_sha = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()
    goal_sha16 = goal_sha[:16]
    candidate_roots = list(base_roots)

    source_reg_sig = None
    raw_source_reg = str(goal.get("source_registry") or "").strip()
    if raw_source_reg:
        source_reg_path = Path(raw_source_reg).expanduser()
        if not source_reg_path.is_absolute():
            source_reg_path = registry_path.parent / source_reg_path
        source_reg_sig = _stat_signature(source_reg_path)
        try:
            source_raw = source_reg_path.read_bytes()
            source_decoded = decode_registry_snapshot(source_reg_path, source_raw)
            source_root = resolve_runtime_root(
                source_decoded, None, registry_path=source_reg_path,
            )
            candidate_roots.append(source_root)
        except (OSError, ValueError, TypeError, KeyError):
            pass

    roots = _dedupe_paths(candidate_roots)
    per_root: list[dict[str, Any]] = []
    for root in roots:
        goal_dir = root / "goals" / goal_id
        goal_dir_stat = _stat_signature(goal_dir)
        extra_dbs = (
            {
                db_file.name: _sqlite_bundle_signature(db_file)
                for db_file in _direct_sqlite_files(goal_dir)
            }
            if goal_dir_stat is not None
            else {}
        )
        per_root.append(
            {
                "root": str(root),
                "provider_marker": _stat_signature(
                    root / "authority" / f"provider-{goal_sha}.json"
                ),
                "file_authority": _stat_signature(
                    root / "authority" / "file-v0" / f"authority-store-{goal_sha16}.json"
                ),
                "file_identity": _stat_signature(
                    root / "authority" / "file-v0" / "store-identity"
                ),
                "sqlite_authority": _sqlite_bundle_signature(
                    root / "authority" / "sqlite-v0" / f"authority-{goal_sha}.sqlite"
                ),
                "goal_dir": goal_dir_stat,
                "goal_sqlite": extra_dbs,
                "todos_json": _stat_signature(goal_dir / "todos.json"),
                "runs_index": _stat_signature(goal_dir / "runs" / "index.jsonl"),
                "rollout_events": _stat_signature(goal_dir / "rollout-event-log.jsonl"),
                "state_json": _stat_signature(goal_dir / "state.json"),
                "acceptance_json": _stat_signature(goal_dir / "acceptance.json"),
                "deliveries": _deliveries_dir_signature(goal_dir / "deliveries"),
            }
        )

    storage_sigs: list[dict[str, Any]] = []
    for storage_path in _declared_goal_storage_paths(goal, registry_path.parent):
        if storage_path.name.endswith(_SQLITE_SUFFIXES):
            storage_sigs.append(
                {"path": str(storage_path), "sqlite": _sqlite_bundle_signature(storage_path)}
            )
        else:
            st = _stat_signature(storage_path)
            sub_dbs = {
                db_file.name: _sqlite_bundle_signature(db_file)
                for db_file in _direct_sqlite_files(storage_path)
            } if st is not None else {}
            storage_sigs.append(
                {
                    "path": str(storage_path),
                    "stat": st,
                    "sqlite_files": sub_dbs,
                    "todos_json": _stat_signature(storage_path / "todos.json"),
                    "deliveries": _deliveries_dir_signature(storage_path / "deliveries"),
                }
            )

    declared_state_sig = None
    repo_registry_sig = None
    raw_repo = str(goal.get("repo") or "").strip()
    if raw_repo:
        repo_path = Path(raw_repo).expanduser()
        raw_state_file = str(goal.get("state_file") or "").strip()
        if raw_state_file:
            declared_state_sig = _stat_signature(repo_path / raw_state_file)
        repo_registry_sig = _stat_signature(repo_path / ".loopx" / "registry.json")

    return {
        "goal_id": goal_id,
        "source_registry": source_reg_sig,
        "declared_state": declared_state_sig,
        "repo_registry": repo_registry_sig,
        "roots": per_root,
        "storage": storage_sigs,
    }


def compute_manager_turn_context_fingerprint(
    *,
    registry_path: Path | None,
    session: Mapping[str, Any],
    runtime_root: Path,
    scope_kind: str,
    authorized_goal_ids: list[str] | None,
    bound_steward: bool,
    include_details: bool,
    include_stopped: bool,
    read_goal_status: bool,
    remote_evidence: bool,
) -> str | None:
    """Compute a fast zero-subprocess digest over all local authority inputs."""
    if registry_path is None:
        return None
    try:
        resolved_registry_path = registry_path.expanduser().resolve()
        raw = resolved_registry_path.read_bytes()
        registry = decode_registry_snapshot(resolved_registry_path, raw)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    raw_goals = registry.get("goals")
    if not isinstance(raw_goals, list):
        return None
    if bound_steward:
        registry_rev = registry_semantic_revision(resolved_registry_path, raw)
        if registry_rev is None:
            return None
    else:
        registry_rev = "sha256:" + hashlib.sha256(raw).hexdigest()

    try:
        resolved_reg_root = resolve_runtime_root(
            registry, str(runtime_root), registry_path=resolved_registry_path,
        )
    except (OSError, ValueError, TypeError):
        resolved_reg_root = runtime_root
    base_roots = _dedupe_paths([runtime_root, resolved_reg_root])

    allowed_ids = set(authorized_goal_ids) if authorized_goal_ids is not None else None
    target_goals = [
        goal
        for goal in raw_goals
        if isinstance(goal, Mapping)
        and isinstance(goal.get("id"), str)
        and goal["id"]
        and (allowed_ids is None or goal["id"] in allowed_ids)
    ]
    target_goals.sort(key=lambda item: str(item.get("id") or ""))

    machine_configs = [
        {
            "root": str(root),
            "machine_configuration": _stat_signature(
                root / "machine" / "configuration.json"
            ),
            "manager_policy": _stat_signature(_root(root) / "policy.json"),
        }
        for root in base_roots
    ]

    basis = {
        "schema_version": "manager_turn_context_cache_key_v1",
        "registry_path": str(resolved_registry_path),
        "registry_revision": registry_rev,
        "params": [
            scope_kind,
            sorted(allowed_ids) if allowed_ids is not None else None,
            str(session.get("channel_id") or ""),
            bool(bound_steward),
            bool(include_details),
            bool(include_stopped),
            bool(read_goal_status),
            bool(remote_evidence),
            str(os.environ.get("LOOPX_MANAGER_EVIDENCE_WINDOW_DAYS", "") or ""),
        ],
        "machine_configs": machine_configs,
        "goals": [
            _goal_local_fingerprint(
                goal,
                registry_path=resolved_registry_path,
                base_roots=base_roots,
            )
            for goal in target_goals
        ],
    }
    encoded = json.dumps(basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _evict_expired_locked(now_mono: float, max_age_seconds: float) -> None:
    expired = [
        key
        for key, entry in _CACHE.items()
        if now_mono - entry.stored_at_monotonic > max_age_seconds
    ]
    for key in expired:
        _CACHE.pop(key, None)


def get_cached_manager_turn_context(
    fingerprint: str,
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now_monotonic: float | None = None,
) -> dict[str, Any] | None:
    """Return a deep-copied cached context snapshot if present and within TTL."""
    now_mono = time.monotonic() if now_monotonic is None else now_monotonic
    with _CACHE_LOCK:
        _evict_expired_locked(now_mono, max_age_seconds)
        entry = _CACHE.get(fingerprint)
        if entry is None:
            return None
        _CACHE.move_to_end(fingerprint)
        return copy.deepcopy(entry.payload)


def put_cached_manager_turn_context(
    fingerprint: str,
    context: Mapping[str, Any],
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now_monotonic: float | None = None,
) -> bool:
    """Cache a complete, warning-free context snapshot with deep-copy isolation."""
    if context.get("warnings"):
        return False
    coverage = context.get("coverage")
    if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
        return False
    snapshot = copy.deepcopy(dict(context))
    now_mono = time.monotonic() if now_monotonic is None else now_monotonic
    with _CACHE_LOCK:
        _evict_expired_locked(now_mono, max_age_seconds)
        _CACHE[fingerprint] = _CacheEntry(
            stored_at_monotonic=now_mono,
            payload=snapshot,
        )
        _CACHE.move_to_end(fingerprint)
        while len(_CACHE) > MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    return True
