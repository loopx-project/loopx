"""Bounded opportunistic retention pruning for ephemeral context-delivery directories."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import threading
import time
from typing import Iterator

if sys.platform != "win32":
    import fcntl

CONTEXT_DELIVERY_EPHEMERAL_PREFIXES: tuple[str, ...] = (
    "decision-",
    "recovery-",
    "settlement-",
    "context-",
)
RETENTION_SWEEP_MARKER_NAME = ".retention-sweep"

DEFAULT_MIN_AGE_SECONDS = 86400.0
DEFAULT_KEEP_RECENT_PER_PREFIX = 64
DEFAULT_MAX_DELETE_PER_SWEEP = 128
DEFAULT_MIN_SWEEP_INTERVAL_SECONDS = 300.0

ENV_MIN_AGE_SECONDS = "LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS"
ENV_KEEP_RECENT_PER_PREFIX = "LOOPX_CONTEXT_DELIVERY_KEEP_RECENT"
ENV_MAX_DELETE_PER_SWEEP = "LOOPX_CONTEXT_DELIVERY_MAX_DELETE"
ENV_MIN_SWEEP_INTERVAL_SECONDS = "LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS"


@dataclass(frozen=True, slots=True)
class ContextDeliveryPruneResult:
    """Summary of one bounded opportunistic context-delivery retention sweep."""

    swept: bool
    skipped_reason: str | None
    scanned: int
    deleted: int
    failed: int


@dataclass(frozen=True, slots=True)
class _CandidateDirectory:
    mtime: float
    name: str
    path: Path


def _read_env_non_negative_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    if not math.isfinite(parsed) or parsed < 0.0:
        return default
    return parsed


def _read_env_non_negative_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _validate_non_negative_float(value: float, *, label: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return float(value)


def _validate_non_negative_int(value: int, *, label: str) -> int:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _resolve_min_age_seconds(value: float | None) -> float:
    if value is None:
        return _read_env_non_negative_float(ENV_MIN_AGE_SECONDS, DEFAULT_MIN_AGE_SECONDS)
    return _validate_non_negative_float(value, label="min_age_seconds")


def _resolve_keep_recent_per_prefix(value: int | None) -> int:
    if value is None:
        return _read_env_non_negative_int(
            ENV_KEEP_RECENT_PER_PREFIX,
            DEFAULT_KEEP_RECENT_PER_PREFIX,
        )
    return _validate_non_negative_int(value, label="keep_recent_per_prefix")


def _resolve_max_delete_per_sweep(value: int | None) -> int:
    if value is None:
        return _read_env_non_negative_int(
            ENV_MAX_DELETE_PER_SWEEP,
            DEFAULT_MAX_DELETE_PER_SWEEP,
        )
    return _validate_non_negative_int(value, label="max_delete_per_sweep")


def _resolve_min_sweep_interval_seconds(value: float | None) -> float:
    if value is None:
        return _read_env_non_negative_float(
            ENV_MIN_SWEEP_INTERVAL_SECONDS,
            DEFAULT_MIN_SWEEP_INTERVAL_SECONDS,
        )
    return _validate_non_negative_float(value, label="min_sweep_interval_seconds")


def _resolve_now(value: float | None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("now must be a finite timestamp")
    return float(value)


def _current_uid() -> int | None:
    if os.name == "posix" and hasattr(os, "getuid"):
        return os.getuid()
    return None


def _matching_ephemeral_prefix(name: str) -> str | None:
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return None


def _check_sweep_marker(
    marker_path: Path,
    *,
    current_now: float,
    min_sweep_interval_seconds: float,
    uid: int | None,
) -> str | None:
    try:
        marker_stat = os.lstat(marker_path)
    except FileNotFoundError:
        return None
    except OSError:
        return "marker_unreadable"
    if stat.S_ISLNK(marker_stat.st_mode) or not stat.S_ISREG(marker_stat.st_mode):
        return "unsafe_marker"
    if uid is not None and marker_stat.st_uid != uid:
        return "unowned_marker"
    if min_sweep_interval_seconds > 0.0:
        elapsed = current_now - marker_stat.st_mtime
        if 0.0 <= elapsed < min_sweep_interval_seconds:
            return "throttled"
    return None


_ACTIVE_SWEEP_GUARD = threading.Lock()
_ACTIVE_SWEEP_ROOTS: set[str] = set()


def _open_marker_fd(marker_path: Path) -> tuple[int | None, str | None]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker_path, os.O_RDWR | os.O_CREAT | nofollow, 0o600)
        return fd, None
    except PermissionError:
        try:
            fd = os.open(marker_path, os.O_RDONLY | nofollow)
            return fd, None
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                return None, "unsafe_marker"
            return None, "marker_unwritable"
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            return None, "unsafe_marker"
        return None, "marker_unwritable"


def _set_marker_timestamp(fd: int, marker_path: Path, current_now: float) -> None:
    try:
        if os.utime in os.supports_fd:
            os.utime(fd, (current_now, current_now))
        elif os.utime in os.supports_follow_symlinks:
            os.utime(marker_path, (current_now, current_now), follow_symlinks=False)
        else:
            os.utime(marker_path, (current_now, current_now))
    except OSError:
        pass


@contextmanager
def _acquire_sweep_lock(
    root: Path,
    marker_path: Path,
    *,
    current_now: float,
    min_sweep_interval_seconds: float,
    uid: int | None,
) -> Iterator[str | None]:
    """Acquire non-blocking sweep ownership across threads and processes."""
    key = os.path.normpath(str(root))
    with _ACTIVE_SWEEP_GUARD:
        if key in _ACTIVE_SWEEP_ROOTS:
            yield "sweep_in_progress"
            return
        _ACTIVE_SWEEP_ROOTS.add(key)
    fd: int | None = None
    locked_fd = False
    try:
        fd, open_error = _open_marker_fd(marker_path)
        if fd is None:
            yield open_error or "marker_unwritable"
            return
        try:
            fd_stat = os.fstat(fd)
        except OSError:
            yield "marker_unreadable"
            return
        if not stat.S_ISREG(fd_stat.st_mode):
            yield "unsafe_marker"
            return
        if uid is not None and fd_stat.st_uid != uid:
            yield "unowned_marker"
            return
        if sys.platform != "win32":
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_fd = True
            except OSError as exc:
                if getattr(exc, "errno", None) in (
                    errno.EAGAIN,
                    errno.EACCES,
                    errno.EWOULDBLOCK,
                ):
                    yield "sweep_in_progress"
                    return
                yield "marker_unreadable"
                return
            try:
                locked_stat = os.fstat(fd)
            except OSError:
                yield "marker_unreadable"
                return
            if min_sweep_interval_seconds > 0.0:
                elapsed = current_now - locked_stat.st_mtime
                if 0.0 <= elapsed < min_sweep_interval_seconds:
                    yield "throttled"
                    return
        _set_marker_timestamp(fd, marker_path, current_now)
        yield None
    finally:
        if locked_fd and fd is not None and sys.platform != "win32":
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        with _ACTIVE_SWEEP_GUARD:
            _ACTIVE_SWEEP_ROOTS.discard(key)


def _collect_candidates_by_prefix(
    root: Path,
    *,
    uid: int | None,
    protected_names: frozenset[str],
) -> tuple[dict[str, list[_CandidateDirectory]], int] | None:
    by_prefix: dict[str, list[_CandidateDirectory]] = {
        prefix: [] for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES
    }
    scanned = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                name = entry.name
                if name in protected_names:
                    continue
                prefix = _matching_ephemeral_prefix(name)
                if prefix is None:
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(
                    entry_stat.st_mode
                ):
                    continue
                if uid is not None and entry_stat.st_uid != uid:
                    continue
                scanned += 1
                by_prefix[prefix].append(
                    _CandidateDirectory(
                        mtime=entry_stat.st_mtime,
                        name=name,
                        path=Path(entry.path),
                    )
                )
    except OSError:
        return None
    return by_prefix, scanned


def _select_prune_targets(
    by_prefix: dict[str, list[_CandidateDirectory]],
    *,
    current_now: float,
    min_age_seconds: float,
    keep_recent_per_prefix: int,
    max_delete_per_sweep: int,
) -> list[_CandidateDirectory]:
    if max_delete_per_sweep <= 0:
        return []
    cutoff = current_now - min_age_seconds
    eligible: list[_CandidateDirectory] = []
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        group = by_prefix[prefix]
        if not group:
            continue
        group.sort(key=lambda item: (item.mtime, item.name))
        excess = len(group) - keep_recent_per_prefix
        if excess <= 0:
            continue
        for candidate in group[:excess]:
            if candidate.mtime <= cutoff:
                eligible.append(candidate)
    eligible.sort(key=lambda item: (item.mtime, item.name))
    return eligible[:max_delete_per_sweep]


def maybe_prune_context_delivery_root(
    root: Path,
    *,
    min_age_seconds: float | None = None,
    keep_recent_per_prefix: int | None = None,
    max_delete_per_sweep: int | None = None,
    min_sweep_interval_seconds: float | None = None,
    now: float | None = None,
    protected_names: frozenset[str] = frozenset(),
) -> ContextDeliveryPruneResult:
    """Opportunistically prune expired ephemeral capture dirs under ``root``.

    Throttled via a lightweight ``.retention-sweep`` timestamp marker so hot
    capture calls skip in sub-millisecond time. Never touches symlinks,
    hash-named directories, marker/unexpected files, or captures within either
    ``keep_recent_per_prefix`` per prefix or ``min_age_seconds``.
    """
    effective_min_age = _resolve_min_age_seconds(min_age_seconds)
    effective_keep_recent = _resolve_keep_recent_per_prefix(keep_recent_per_prefix)
    effective_max_delete = _resolve_max_delete_per_sweep(max_delete_per_sweep)
    effective_interval = _resolve_min_sweep_interval_seconds(
        min_sweep_interval_seconds
    )
    current_now = _resolve_now(now)
    uid = _current_uid()

    try:
        root_stat = os.lstat(root)
    except OSError:
        return ContextDeliveryPruneResult(
            swept=False,
            skipped_reason="missing_or_unreadable",
            scanned=0,
            deleted=0,
            failed=0,
        )
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return ContextDeliveryPruneResult(
            swept=False,
            skipped_reason="invalid_root",
            scanned=0,
            deleted=0,
            failed=0,
        )
    if uid is not None and root_stat.st_uid != uid:
        return ContextDeliveryPruneResult(
            swept=False,
            skipped_reason="unowned_root",
            scanned=0,
            deleted=0,
            failed=0,
        )

    marker_path = root / RETENTION_SWEEP_MARKER_NAME
    skip_reason = _check_sweep_marker(
        marker_path,
        current_now=current_now,
        min_sweep_interval_seconds=effective_interval,
        uid=uid,
    )
    if skip_reason is not None:
        return ContextDeliveryPruneResult(
            swept=False,
            skipped_reason=skip_reason,
            scanned=0,
            deleted=0,
            failed=0,
        )

    with _acquire_sweep_lock(
        root,
        marker_path,
        current_now=current_now,
        min_sweep_interval_seconds=effective_interval,
        uid=uid,
    ) as lock_skip_reason:
        if lock_skip_reason is not None:
            return ContextDeliveryPruneResult(
                swept=False,
                skipped_reason=lock_skip_reason,
                scanned=0,
                deleted=0,
                failed=0,
            )

        collected = _collect_candidates_by_prefix(
            root,
            uid=uid,
            protected_names=protected_names,
        )
        if collected is None:
            return ContextDeliveryPruneResult(
                swept=False,
                skipped_reason="scan_failed",
                scanned=0,
                deleted=0,
                failed=1,
            )
        by_prefix, scanned = collected
        targets = _select_prune_targets(
            by_prefix,
            current_now=current_now,
            min_age_seconds=effective_min_age,
            keep_recent_per_prefix=effective_keep_recent,
            max_delete_per_sweep=effective_max_delete,
        )

        deleted = 0
        failed = 0
        for target in targets:
            try:
                target_stat = os.lstat(target.path)
                if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISDIR(
                    target_stat.st_mode
                ):
                    continue
                if uid is not None and target_stat.st_uid != uid:
                    continue
                shutil.rmtree(target.path)
                deleted += 1
            except FileNotFoundError:
                continue
            except OSError:
                failed += 1

        return ContextDeliveryPruneResult(
            swept=True,
            skipped_reason=None,
            scanned=scanned,
            deleted=deleted,
            failed=failed,
        )
