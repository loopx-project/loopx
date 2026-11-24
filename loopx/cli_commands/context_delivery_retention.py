"""Bounded opportunistic retention pruning for ephemeral context-delivery directories."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
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

from .context_delivery_retention_scan import (
    CONTEXT_DELIVERY_EPHEMERAL_PREFIXES as CONTEXT_DELIVERY_EPHEMERAL_PREFIXES,
    RETENTION_REFSCAN_CHECKPOINT_NAME as RETENTION_REFSCAN_CHECKPOINT_NAME,
    _MAX_CAPTURE_JSON_BYTES as _MAX_CAPTURE_JSON_BYTES,
    _CandidateDirectory,
    _JsonObjectReader,
    _RefScanCheckpoint,
    _ShallowDirectory,
    _candidate_has_unsettled_admission,
    _cohort_identities_for_candidates,
    _collect_active_continuity_references,
    _dir_witness,
    _extract_capture_names_and_digests as _extract_capture_names_and_digests,
    _extract_dir_name_from_path_token as _extract_dir_name_from_path_token,
    _inspect_owned_capture_dir,
    _load_refscan_checkpoint,
    _matching_ephemeral_prefix,
    _measure_candidate_dir,
    _read_regular_json_object as _read_regular_json_object,
    _remove_refscan_checkpoint,
    _resolved_variants as _resolved_variants,
    _save_refscan_checkpoint,
    _scan_dir_entries_for_needles,
)

if sys.platform != "win32":
    import fcntl

RETENTION_SWEEP_MARKER_NAME = ".retention-sweep"

DEFAULT_MIN_AGE_SECONDS = 86400.0
DEFAULT_KEEP_RECENT_PER_PREFIX = 64
DEFAULT_MAX_DELETE_PER_SWEEP = 128
DEFAULT_MIN_SWEEP_INTERVAL_SECONDS = 300.0
DEFAULT_MAX_INSPECT_PER_SWEEP = 128
DEFAULT_MAX_INSPECT_BYTES_PER_SWEEP = 8 * 1024 * 1024
DEFAULT_MAX_REF_SCAN_BYTES_PER_SWEEP = 256 * 1024 * 1024

ENV_MIN_AGE_SECONDS = "LOOPX_CONTEXT_DELIVERY_MIN_AGE_SECONDS"
ENV_KEEP_RECENT_PER_PREFIX = "LOOPX_CONTEXT_DELIVERY_KEEP_RECENT"
ENV_MAX_DELETE_PER_SWEEP = "LOOPX_CONTEXT_DELIVERY_MAX_DELETE"
ENV_MIN_SWEEP_INTERVAL_SECONDS = "LOOPX_CONTEXT_DELIVERY_SWEEP_INTERVAL_SECONDS"
ENV_MAX_INSPECT_PER_SWEEP = "LOOPX_CONTEXT_DELIVERY_MAX_INSPECT"
ENV_MAX_INSPECT_BYTES_PER_SWEEP = "LOOPX_CONTEXT_DELIVERY_MAX_INSPECT_BYTES"
ENV_MAX_REF_SCAN_BYTES_PER_SWEEP = "LOOPX_CONTEXT_DELIVERY_MAX_REF_SCAN_BYTES"

_REF_SCAN_OVERLAP_MIN_BYTES = 256


@dataclass(frozen=True, slots=True)
class ContextDeliveryPruneResult:
    """Summary of one bounded opportunistic context-delivery retention sweep."""

    swept: bool
    skipped_reason: str | None
    scanned: int
    deleted: int
    failed: int


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
            ENV_KEEP_RECENT_PER_PREFIX, DEFAULT_KEEP_RECENT_PER_PREFIX
        )
    return _validate_non_negative_int(value, label="keep_recent_per_prefix")


def _resolve_max_delete_per_sweep(value: int | None) -> int:
    if value is None:
        return _read_env_non_negative_int(
            ENV_MAX_DELETE_PER_SWEEP, DEFAULT_MAX_DELETE_PER_SWEEP
        )
    return _validate_non_negative_int(value, label="max_delete_per_sweep")


def _resolve_min_sweep_interval_seconds(value: float | None) -> float:
    if value is None:
        return _read_env_non_negative_float(
            ENV_MIN_SWEEP_INTERVAL_SECONDS, DEFAULT_MIN_SWEEP_INTERVAL_SECONDS
        )
    return _validate_non_negative_float(value, label="min_sweep_interval_seconds")


def _resolve_budget_int(
    value: int | None, *, env_name: str, default: int, label: str
) -> int:
    if value is None:
        return _read_env_non_negative_int(env_name, default)
    return _validate_non_negative_int(value, label=label)


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


def _collect_root_entries(
    root: Path,
    *,
    uid: int | None,
) -> tuple[dict[str, list[_ShallowDirectory]], list[Path], int] | None:
    by_prefix: dict[str, list[_ShallowDirectory]] = {
        prefix: [] for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES
    }
    non_ephemeral_dirs: list[Path] = []
    ephemeral_count = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                name = entry.name
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
                prefix = _matching_ephemeral_prefix(name)
                if prefix is None:
                    non_ephemeral_dirs.append(Path(entry.path))
                    continue
                ephemeral_count += 1
                by_prefix[prefix].append(
                    _ShallowDirectory(
                        mtime=entry_stat.st_mtime,
                        mtime_ns=entry_stat.st_mtime_ns,
                        name=name,
                        path=Path(entry.path),
                        prefix=prefix,
                    )
                )
    except OSError:
        return None
    return by_prefix, non_ephemeral_dirs, ephemeral_count


def _expand_protected_closure(
    seed_names: set[str],
    *,
    owned_by_name: Mapping[str, _CandidateDirectory],
) -> set[str]:
    protected = set(seed_names)
    queue: deque[str] = deque(seed_names)
    while queue:
        current = queue.popleft()
        candidate = owned_by_name.get(current)
        if candidate is None:
            continue
        for ref_name in candidate.referenced_names:
            if ref_name not in protected:
                protected.add(ref_name)
                queue.append(ref_name)
    return protected


def _partition_shallow_candidates(
    shallow_by_prefix: Mapping[str, list[_ShallowDirectory]],
    *,
    protected_names: frozenset[str] | set[str],
    current_now: float,
    min_age_seconds: float,
    keep_recent_per_prefix: int,
) -> tuple[list[_ShallowDirectory], list[_ShallowDirectory]]:
    cutoff = current_now - min_age_seconds
    aged_shallow: list[_ShallowDirectory] = []
    kept_shallow: list[_ShallowDirectory] = []
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        kept_shallow.extend(
            item for item in shallow_by_prefix[prefix] if item.name in protected_names
        )
        group = [
            item for item in shallow_by_prefix[prefix] if item.name not in protected_names
        ]
        if not group:
            continue
        group.sort(key=lambda item: (item.mtime, item.name))
        excess = len(group) - keep_recent_per_prefix
        if excess <= 0:
            kept_shallow.extend(group)
            continue
        for candidate in group[:excess]:
            if candidate.mtime <= cutoff:
                aged_shallow.append(candidate)
            else:
                kept_shallow.append(candidate)
        kept_shallow.extend(group[excess:])
    aged_shallow.sort(key=lambda item: (item.mtime, item.name))
    return aged_shallow, kept_shallow


def _candidate_is_directly_protected(
    owned: _CandidateDirectory,
    *,
    protected: set[str],
    active_digests: set[str],
    runtime_root: Path | None,
    settlement_cache: dict[tuple[str, str, str, str], bool],
) -> bool:
    return (
        owned.name in protected
        or bool(owned.data_digests & active_digests)
        or bool(owned.reader_digests & active_digests)
        or _candidate_has_unsettled_admission(
            owned, runtime_root=runtime_root, settlement_cache=settlement_cache
        )
    )


def _select_fresh_cohort(
    aged_shallow: list[_ShallowDirectory],
    *,
    protected: set[str],
    active_digests: set[str],
    runtime_root: Path | None,
    abs_root: Path,
    resolved_root: Path,
    uid: int | None,
    max_delete_per_sweep: int,
    max_inspect_per_sweep: int,
    max_inspect_bytes_per_sweep: int,
    inspected_by_name: dict[str, _CandidateDirectory],
    settlement_cache: dict[tuple[str, str, str, str], bool],
    read_json: _JsonObjectReader,
) -> tuple[list[_CandidateDirectory], list[_ShallowDirectory], int]:
    preliminary_reclaimable: list[_CandidateDirectory] = []
    uninspected_aged: list[_ShallowDirectory] = []
    invalid_aged = 0
    inspected_aged_dirs = 0
    inspected_bytes = 0

    for index, shallow in enumerate(aged_shallow):
        if (
            len(preliminary_reclaimable) >= max_delete_per_sweep
            or inspected_aged_dirs >= max_inspect_per_sweep
            or inspected_bytes >= max_inspect_bytes_per_sweep
        ):
            uninspected_aged.extend(aged_shallow[index:])
            break
        measured = _measure_candidate_dir(shallow.path, uid=uid)
        if measured is None:
            invalid_aged += 1
            continue
        entries, dir_bytes, _ = measured
        if dir_bytes > max_inspect_bytes_per_sweep:
            uninspected_aged.append(shallow)
            continue
        if inspected_bytes + dir_bytes > max_inspect_bytes_per_sweep:
            uninspected_aged.extend(aged_shallow[index:])
            break
        inspected_aged_dirs += 1
        inspected_bytes += dir_bytes
        owned = _inspect_owned_capture_dir(
            shallow.path, name=shallow.name, prefix=shallow.prefix,
            mtime=shallow.mtime, mtime_ns=shallow.mtime_ns, uid=uid,
            abs_root=abs_root, resolved_root=resolved_root,
            measured=(entries, dir_bytes), read_json=read_json,
        )
        if owned is None:
            invalid_aged += 1
            continue
        inspected_by_name[owned.name] = owned
        if _candidate_is_directly_protected(
            owned, protected=protected, active_digests=active_digests,
            runtime_root=runtime_root, settlement_cache=settlement_cache,
        ):
            protected.add(owned.name)
            continue
        preliminary_reclaimable.append(owned)
    return preliminary_reclaimable, uninspected_aged, invalid_aged


def _resume_checkpoint_cohort(
    checkpoint: _RefScanCheckpoint,
    aged_shallow: list[_ShallowDirectory],
    *,
    protected: set[str],
    active_digests: set[str],
    runtime_root: Path | None,
    abs_root: Path,
    resolved_root: Path,
    uid: int | None,
    max_delete_per_sweep: int,
    max_inspect_per_sweep: int,
    max_inspect_bytes_per_sweep: int,
    inspected_by_name: dict[str, _CandidateDirectory],
    settlement_cache: dict[tuple[str, str, str, str], bool],
    read_json: _JsonObjectReader,
) -> tuple[list[_CandidateDirectory], list[_ShallowDirectory], dict[str, tuple[int, int, int, int]], int]:
    if checkpoint.active_digests != frozenset(active_digests):
        return [], aged_shallow, {}, 0
    aged_by_name = {item.name: item for item in aged_shallow}
    preliminary_reclaimable: list[_CandidateDirectory] = []
    invalid_aged = 0
    inspected_dirs = 0
    inspected_bytes = 0

    for entry in checkpoint.target_cohort:
        if (
            len(preliminary_reclaimable) >= max_delete_per_sweep
            or inspected_dirs >= max_inspect_per_sweep
        ):
            break
        shallow = aged_by_name.get(entry.name)
        if shallow is None or shallow.mtime_ns != entry.mtime_ns:
            continue
        measured = _measure_candidate_dir(shallow.path, uid=uid)
        if measured is None:
            invalid_aged += 1
            continue
        entries, dir_bytes, _ = measured
        if (
            dir_bytes > max_inspect_bytes_per_sweep
            or inspected_bytes + dir_bytes > max_inspect_bytes_per_sweep
        ):
            continue
        inspected_dirs += 1
        inspected_bytes += dir_bytes
        owned = _inspect_owned_capture_dir(
            shallow.path, name=shallow.name, prefix=shallow.prefix,
            mtime=shallow.mtime, mtime_ns=shallow.mtime_ns, uid=uid,
            abs_root=abs_root, resolved_root=resolved_root,
            measured=(entries, dir_bytes), read_json=read_json,
        )
        if owned is None:
            invalid_aged += 1
            continue
        if (
            owned.data_digests != entry.data_digests
            or owned.reader_digests != entry.reader_digests
        ):
            continue
        inspected_by_name[owned.name] = owned
        if _candidate_is_directly_protected(
            owned, protected=protected, active_digests=active_digests,
            runtime_root=runtime_root, settlement_cache=settlement_cache,
        ):
            protected.add(owned.name)
            continue
        preliminary_reclaimable.append(owned)

    if not preliminary_reclaimable:
        return [], aged_shallow, {}, invalid_aged
    uninspected_aged = [
        item for item in aged_shallow if item.name not in inspected_by_name
    ]
    witnesses = {
        key: val
        for key, val in checkpoint.scanned_witnesses.items()
        if key not in inspected_by_name
    }
    return preliminary_reclaimable, uninspected_aged, witnesses, invalid_aged


def _verify_targets_via_ref_scan(
    preliminary_reclaimable: list[_CandidateDirectory],
    *,
    inspected_by_name: dict[str, _CandidateDirectory],
    unscanned_dirs: dict[str, _ShallowDirectory],
    prior_witnesses: Mapping[str, tuple[int, int, int, int]],
    protected: set[str],
    active_digests: set[str],
    runtime_root: Path | None,
    root: Path,
    abs_root: Path,
    resolved_root: Path,
    uid: int | None,
    ref_scan_remaining: int,
    max_ref_scan_bytes_per_sweep: int,
    settlement_cache: dict[tuple[str, str, str, str], bool],
    read_json: _JsonObjectReader,
) -> tuple[list[_CandidateDirectory], int] | None:
    """Confirm no kept or uninspected capture protects any candidate target; checkpoint on budget exhaustion."""
    invalid_found = 0
    protected.update(_expand_protected_closure(protected, owned_by_name=inspected_by_name))
    remaining_targets = [
        item for item in preliminary_reclaimable if item.name not in protected
    ]
    if not remaining_targets:
        _remove_refscan_checkpoint(root, uid=uid)
        return [], invalid_found

    frontier: set[str] = {item.name for item in remaining_targets}
    first_round = True
    next_witnesses: dict[str, tuple[int, int, int, int]] = {}
    cohort_snapshot = _cohort_identities_for_candidates(remaining_targets)

    while frontier and unscanned_dirs:
        needle_bytes = [name.encode("utf-8") for name in frontier]
        if first_round and active_digests:
            needle_bytes.extend(digest.encode("ascii") for digest in active_digests)
        needles = tuple(sorted(set(needle_bytes)))
        if not needles:
            break
        overlap = max(
            _REF_SCAN_OVERLAP_MIN_BYTES,
            max(len(needle) for needle in needles) - 1,
        )
        next_frontier: set[str] = set()
        for dir_name in sorted(unscanned_dirs):
            shallow = unscanned_dirs[dir_name]
            measured = _measure_candidate_dir(shallow.path, uid=uid)
            if measured is None:
                invalid_found += 1
                unscanned_dirs.pop(dir_name, None)
                continue
            entries, total_bytes, max_file_mtime_ns = measured
            if total_bytes > max_ref_scan_bytes_per_sweep:
                _remove_refscan_checkpoint(root, uid=uid)
                return None
            witness = _dir_witness(
                shallow.mtime_ns, entries, total_bytes, max_file_mtime_ns
            )
            if (
                first_round
                and dir_name in prior_witnesses
                and prior_witnesses[dir_name] == witness
            ):
                next_witnesses[dir_name] = witness
                continue
            if total_bytes > ref_scan_remaining:
                _save_refscan_checkpoint(
                    root,
                    uid=uid,
                    checkpoint=_RefScanCheckpoint(
                        target_cohort=cohort_snapshot,
                        protected_names=frozenset(protected),
                        active_digests=frozenset(active_digests),
                        scanned_witnesses=next_witnesses,
                    ),
                )
                return None

            status, used_bytes = _scan_dir_entries_for_needles(
                entries, needles=needles, overlap=overlap, uid=uid,
                budget_remaining=ref_scan_remaining,
            )
            ref_scan_remaining -= used_bytes
            if status == "unreadable" or ref_scan_remaining < 0:
                return None
            if status == "budget_exceeded":
                _save_refscan_checkpoint(
                    root,
                    uid=uid,
                    checkpoint=_RefScanCheckpoint(
                        target_cohort=cohort_snapshot,
                        protected_names=frozenset(protected),
                        active_digests=frozenset(active_digests),
                        scanned_witnesses=next_witnesses,
                    ),
                )
                return None
            if status == "not_capture":
                invalid_found += 1
                unscanned_dirs.pop(dir_name, None)
                continue
            if status == "miss":
                if first_round:
                    next_witnesses[dir_name] = witness
                continue

            owned = _inspect_owned_capture_dir(
                shallow.path, name=shallow.name, prefix=shallow.prefix,
                mtime=shallow.mtime, mtime_ns=shallow.mtime_ns, uid=uid,
                abs_root=abs_root, resolved_root=resolved_root,
                measured=(entries, total_bytes), read_json=read_json,
            )
            unscanned_dirs.pop(dir_name, None)
            if owned is None:
                invalid_found += 1
                continue
            inspected_by_name[owned.name] = owned
            if _candidate_is_directly_protected(
                owned, protected=protected, active_digests=active_digests,
                runtime_root=runtime_root, settlement_cache=settlement_cache,
            ):
                protected.add(owned.name)
            elif owned.referenced_names & frontier:
                next_frontier.add(owned.name)

        first_round = False
        protected.update(
            _expand_protected_closure(protected, owned_by_name=inspected_by_name)
        )
        remaining_targets = [
            item for item in remaining_targets if item.name not in protected
        ]
        if not remaining_targets:
            _remove_refscan_checkpoint(root, uid=uid)
            return [], invalid_found
        frontier = next_frontier - protected

    _remove_refscan_checkpoint(root, uid=uid)
    return remaining_targets, invalid_found


def _select_prune_targets(
    shallow_by_prefix: dict[str, list[_ShallowDirectory]],
    *,
    non_ephemeral_dirs: list[Path],
    caller_protected_names: frozenset[str],
    runtime_root: Path | None,
    root: Path,
    uid: int | None,
    current_now: float,
    min_age_seconds: float,
    keep_recent_per_prefix: int,
    max_delete_per_sweep: int,
    max_inspect_per_sweep: int,
    max_inspect_bytes_per_sweep: int,
    max_ref_scan_bytes_per_sweep: int,
    read_json: _JsonObjectReader | None = None,
) -> tuple[list[_CandidateDirectory], int]:
    active_reader = read_json or _read_regular_json_object
    if (
        max_delete_per_sweep <= 0
        or max_inspect_per_sweep <= 0
        or max_inspect_bytes_per_sweep <= 0
    ):
        return [], 0
    initial_aged, _ = _partition_shallow_candidates(
        shallow_by_prefix, protected_names=caller_protected_names,
        current_now=current_now, min_age_seconds=min_age_seconds,
        keep_recent_per_prefix=keep_recent_per_prefix,
    )
    if not initial_aged:
        return [], 0

    abs_root, resolved_root = _resolved_variants(root)
    continuity = _collect_active_continuity_references(
        non_ephemeral_dirs, uid=uid, abs_root=abs_root, resolved_root=resolved_root,
        max_scan_bytes=max_ref_scan_bytes_per_sweep, read_json=active_reader,
    )
    if continuity is None:
        return [], 0
    active_refs, active_digests, continuity_bytes = continuity
    ref_scan_remaining = max_ref_scan_bytes_per_sweep - continuity_bytes

    protected = set(caller_protected_names) | active_refs
    aged_shallow, kept_shallow = _partition_shallow_candidates(
        shallow_by_prefix, protected_names=protected, current_now=current_now,
        min_age_seconds=min_age_seconds, keep_recent_per_prefix=keep_recent_per_prefix,
    )
    if not aged_shallow:
        return [], 0

    checkpoint, checkpoint_fail_closed = _load_refscan_checkpoint(
        root, uid=uid, read_json=active_reader
    )
    if checkpoint_fail_closed:
        return [], 0

    inspected_by_name: dict[str, _CandidateDirectory] = {}
    settlement_cache: dict[tuple[str, str, str, str], bool] = {}
    prior_witnesses: dict[str, tuple[int, int, int, int]] = {}
    preliminary_reclaimable: list[_CandidateDirectory] = []
    uninspected_aged: list[_ShallowDirectory] = aged_shallow
    invalid_aged = 0

    if checkpoint is not None:
        preliminary_reclaimable, uninspected_aged, prior_witnesses, invalid_aged = (
            _resume_checkpoint_cohort(
                checkpoint, aged_shallow, protected=protected,
                active_digests=active_digests, runtime_root=runtime_root,
                abs_root=abs_root, resolved_root=resolved_root, uid=uid,
                max_delete_per_sweep=max_delete_per_sweep,
                max_inspect_per_sweep=max_inspect_per_sweep,
                max_inspect_bytes_per_sweep=max_inspect_bytes_per_sweep,
                inspected_by_name=inspected_by_name,
                settlement_cache=settlement_cache, read_json=active_reader,
            )
        )
        if not preliminary_reclaimable:
            _remove_refscan_checkpoint(root, uid=uid)

    if not preliminary_reclaimable:
        preliminary_reclaimable, uninspected_aged, fresh_invalid = _select_fresh_cohort(
            aged_shallow, protected=protected, active_digests=active_digests,
            runtime_root=runtime_root, abs_root=abs_root, resolved_root=resolved_root,
            uid=uid, max_delete_per_sweep=max_delete_per_sweep,
            max_inspect_per_sweep=max_inspect_per_sweep,
            max_inspect_bytes_per_sweep=max_inspect_bytes_per_sweep,
            inspected_by_name=inspected_by_name,
            settlement_cache=settlement_cache, read_json=active_reader,
        )
        invalid_aged += fresh_invalid

    if not preliminary_reclaimable:
        return [], invalid_aged

    unscanned_dirs: dict[str, _ShallowDirectory] = {
        item.name: item for item in (*kept_shallow, *uninspected_aged)
    }
    verified = _verify_targets_via_ref_scan(
        preliminary_reclaimable, inspected_by_name=inspected_by_name,
        unscanned_dirs=unscanned_dirs, prior_witnesses=prior_witnesses,
        protected=protected, active_digests=active_digests,
        runtime_root=runtime_root, root=root, abs_root=abs_root,
        resolved_root=resolved_root, uid=uid,
        ref_scan_remaining=ref_scan_remaining,
        max_ref_scan_bytes_per_sweep=max_ref_scan_bytes_per_sweep,
        settlement_cache=settlement_cache, read_json=active_reader,
    )
    if verified is None:
        return [], invalid_aged
    final_targets, invalid_scanned = verified
    return final_targets[:max_delete_per_sweep], invalid_aged + invalid_scanned


def _skipped_result(reason: str, *, failed: int = 0) -> ContextDeliveryPruneResult:
    return ContextDeliveryPruneResult(
        swept=False, skipped_reason=reason, scanned=0, deleted=0, failed=failed
    )


def _run_prune_sweep(
    root: Path,
    *,
    runtime_root: Path | None,
    effective_min_age: float,
    effective_keep_recent: int,
    effective_max_delete: int,
    effective_interval: float,
    effective_max_inspect: int,
    effective_max_inspect_bytes: int,
    effective_max_ref_scan_bytes: int,
    current_now: float,
    protected_names: frozenset[str],
) -> ContextDeliveryPruneResult:
    uid = _current_uid()

    try:
        root_stat = os.lstat(root)
    except OSError:
        return _skipped_result("missing_or_unreadable")
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return _skipped_result("invalid_root")
    if uid is not None and root_stat.st_uid != uid:
        return _skipped_result("unowned_root")

    marker_path = root / RETENTION_SWEEP_MARKER_NAME
    skip_reason = _check_sweep_marker(
        marker_path, current_now=current_now,
        min_sweep_interval_seconds=effective_interval, uid=uid,
    )
    if skip_reason is not None:
        return _skipped_result(skip_reason)

    with _acquire_sweep_lock(
        root, marker_path, current_now=current_now,
        min_sweep_interval_seconds=effective_interval, uid=uid,
    ) as lock_skip_reason:
        if lock_skip_reason is not None:
            return _skipped_result(lock_skip_reason)

        collected = _collect_root_entries(root, uid=uid)
        if collected is None:
            return _skipped_result("scan_failed", failed=1)
        shallow_by_prefix, non_ephemeral_dirs, scanned = collected
        targets, invalid_inspected = _select_prune_targets(
            shallow_by_prefix, non_ephemeral_dirs=non_ephemeral_dirs,
            caller_protected_names=protected_names, runtime_root=runtime_root,
            root=root, uid=uid, current_now=current_now,
            min_age_seconds=effective_min_age,
            keep_recent_per_prefix=effective_keep_recent,
            max_delete_per_sweep=effective_max_delete,
            max_inspect_per_sweep=effective_max_inspect,
            max_inspect_bytes_per_sweep=effective_max_inspect_bytes,
            max_ref_scan_bytes_per_sweep=effective_max_ref_scan_bytes,
            read_json=_read_regular_json_object,
        )
        scanned = max(0, scanned - invalid_inspected)

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
            swept=True, skipped_reason=None, scanned=scanned, deleted=deleted, failed=failed
        )


def maybe_prune_context_delivery_root(
    root: Path,
    *,
    runtime_root: Path | None = None,
    min_age_seconds: float | None = None,
    keep_recent_per_prefix: int | None = None,
    max_delete_per_sweep: int | None = None,
    min_sweep_interval_seconds: float | None = None,
    max_inspect_per_sweep: int | None = None,
    max_inspect_bytes_per_sweep: int | None = None,
    max_ref_scan_bytes_per_sweep: int | None = None,
    now: float | None = None,
    protected_names: frozenset[str] = frozenset(),
) -> ContextDeliveryPruneResult:
    """Opportunistically prune expired, settled LoopX capture dirs under ``root``.

    Throttled via a lightweight ``.retention-sweep`` timestamp marker so hot
    capture calls skip in sub-millisecond time. Enforces strict per-sweep
    inspection and resumable chunked reference-scan budgets via
    ``.retention-refscan.json`` so arbitrarily large backlogs drain across
    sweeps with constant ``O(1 file)`` heap retention.
    """
    effective_min_age = _resolve_min_age_seconds(min_age_seconds)
    effective_keep_recent = _resolve_keep_recent_per_prefix(keep_recent_per_prefix)
    effective_max_delete = _resolve_max_delete_per_sweep(max_delete_per_sweep)
    effective_interval = _resolve_min_sweep_interval_seconds(min_sweep_interval_seconds)
    effective_max_inspect = _resolve_budget_int(
        max_inspect_per_sweep, env_name=ENV_MAX_INSPECT_PER_SWEEP,
        default=DEFAULT_MAX_INSPECT_PER_SWEEP, label="max_inspect_per_sweep",
    )
    effective_max_inspect_bytes = _resolve_budget_int(
        max_inspect_bytes_per_sweep, env_name=ENV_MAX_INSPECT_BYTES_PER_SWEEP,
        default=DEFAULT_MAX_INSPECT_BYTES_PER_SWEEP, label="max_inspect_bytes_per_sweep",
    )
    effective_max_ref_scan_bytes = _resolve_budget_int(
        max_ref_scan_bytes_per_sweep, env_name=ENV_MAX_REF_SCAN_BYTES_PER_SWEEP,
        default=DEFAULT_MAX_REF_SCAN_BYTES_PER_SWEEP, label="max_ref_scan_bytes_per_sweep",
    )
    current_now = _resolve_now(now)

    try:
        return _run_prune_sweep(
            root, runtime_root=runtime_root,
            effective_min_age=effective_min_age,
            effective_keep_recent=effective_keep_recent,
            effective_max_delete=effective_max_delete,
            effective_interval=effective_interval,
            effective_max_inspect=effective_max_inspect,
            effective_max_inspect_bytes=effective_max_inspect_bytes,
            effective_max_ref_scan_bytes=effective_max_ref_scan_bytes,
            current_now=current_now, protected_names=protected_names,
        )
    except Exception:  # noqa: BLE001 - best-effort sweep fails safely after arg validation.
        return _skipped_result("sweep_failed", failed=1)
