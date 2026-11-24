"""Bounded opportunistic retention pruning for ephemeral context-delivery directories."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import threading
import time
from typing import Iterator

from ..control_plane.content_digest import BARE_SHA256_PATTERN

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

_KNOWN_CAPTURE_DATA_FILENAMES: frozenset[str] = frozenset(
    {
        "decision.json",
        "delivery.json",
        "delivery-request.json",
        "failure.json",
        "projection.json",
        "required-context.json",
        "result.json",
        "settlement.json",
    }
)
_CONTINUITY_FILENAMES: tuple[str, ...] = ("session.json", "claim-source.json")
_DECISION_OWNERSHIP_KEYS: tuple[str, ...] = (
    "decision",
    "effective_action",
    "heartbeat_receipt",
    "should_run",
    "goal_id",
)
_MAX_CAPTURE_JSON_BYTES = 64 * 1024 * 1024


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
    prefix: str
    data_payloads: dict[str, dict[str, object]]
    reader_payloads: dict[str, dict[str, object]]


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


def _read_regular_json_object(
    path: Path,
    *,
    uid: int | None,
    max_bytes: int = _MAX_CAPTURE_JSON_BYTES,
) -> tuple[dict[str, object], bytes] | None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            if uid is not None and info.st_uid != uid:
                return None
            if info.st_size <= 0 or info.st_size > max_bytes:
                return None
            raw = stream.read(max_bytes + 1)
    except OSError:
        return None
    if len(raw) > max_bytes:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed, raw


def _is_reader_filename(name: str) -> bool:
    if name.endswith(".reader.json"):
        return len(name) > len(".reader.json")
    stem, sep, suffix = name.partition(".reader.json.")
    return (
        bool(sep)
        and bool(stem)
        and len(suffix) == 16
        and all(ch in "0123456789abcdef" for ch in suffix)
    )


def _reader_reference_is_valid(
    reader_payload: Mapping[str, object],
    *,
    candidate_resolved: Path,
    data_filenames: frozenset[str],
) -> bool:
    if reader_payload.get("schema_version") != "context_capture_read_reference_v1":
        return False
    digest = reader_payload.get("sha256")
    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None:
        return False
    raw_target = reader_payload.get("path")
    if not isinstance(raw_target, str) or not raw_target.strip():
        return False
    try:
        target = Path(raw_target).expanduser().absolute()
        if target.is_symlink():
            return False
        resolved_target = target.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    return (
        resolved_target.parent == candidate_resolved
        and resolved_target.name in data_filenames
    )


def _prefix_ownership_matches(
    prefix: str,
    *,
    data_payloads: Mapping[str, dict[str, object]],
    has_verified_reader: bool,
) -> bool:
    if prefix == "decision-":
        decision = data_payloads.get("decision.json")
        if decision is not None:
            return (
                any(key in decision for key in _DECISION_OWNERSHIP_KEYS)
                or has_verified_reader
            )
        failure = data_payloads.get("failure.json")
        return failure is not None and bool(failure) and has_verified_reader
    if prefix == "recovery-":
        required = data_payloads.get("required-context.json")
        return required is not None and bool(required)
    if prefix == "settlement-":
        for name in ("result.json", "settlement.json"):
            payload = data_payloads.get(name)
            if payload is not None and bool(payload):
                return True
        return False
    if prefix == "context-":
        for name in ("projection.json", "required-context.json"):
            payload = data_payloads.get(name)
            if payload is not None and bool(payload):
                return True
        delivery = data_payloads.get("delivery.json")
        if delivery is not None and bool(delivery):
            return (
                has_verified_reader
                or delivery.get("schema_version") == "context_delivery_v1"
                or any(key in delivery for key in _DECISION_OWNERSHIP_KEYS)
            )
        return False
    return False


def _inspect_owned_capture_dir(
    candidate_path: Path,
    *,
    name: str,
    prefix: str,
    mtime: float,
    uid: int | None,
) -> _CandidateDirectory | None:
    """Verify strict LoopX capture ownership without mutating non-capture dirs."""
    try:
        candidate_resolved = candidate_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    data_payloads: dict[str, dict[str, object]] = {}
    reader_payloads: dict[str, dict[str, object]] = {}
    try:
        with os.scandir(candidate_path) as entries:
            for entry in entries:
                entry_name = entry.name
                is_data = entry_name in _KNOWN_CAPTURE_DATA_FILENAMES
                is_reader = _is_reader_filename(entry_name)
                if not is_data and not is_reader:
                    return None
                try:
                    if entry.is_symlink():
                        return None
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    return None
                if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(
                    entry_stat.st_mode
                ):
                    return None
                if uid is not None and entry_stat.st_uid != uid:
                    return None
                loaded = _read_regular_json_object(Path(entry.path), uid=uid)
                if loaded is None:
                    return None
                parsed, _ = loaded
                if is_reader:
                    reader_payloads[entry_name] = parsed
                else:
                    data_payloads[entry_name] = parsed
    except OSError:
        return None

    if not data_payloads:
        return None
    data_names = frozenset(data_payloads)
    for reader_payload in reader_payloads.values():
        if not _reader_reference_is_valid(
            reader_payload,
            candidate_resolved=candidate_resolved,
            data_filenames=data_names,
        ):
            return None
    if not _prefix_ownership_matches(
        prefix,
        data_payloads=data_payloads,
        has_verified_reader=bool(reader_payloads),
    ):
        return None
    return _CandidateDirectory(
        mtime=mtime,
        name=name,
        path=candidate_path,
        prefix=prefix,
        data_payloads=data_payloads,
        reader_payloads=reader_payloads,
    )


def _resolved_variants(root: Path) -> tuple[Path, Path]:
    absolute = root.expanduser().absolute()
    try:
        resolved = root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        resolved = absolute
    return absolute, resolved


def _extract_dir_name_from_path_token(
    token: str,
    *,
    abs_root: Path,
    resolved_root: Path,
) -> str | None:
    if not token:
        return None
    candidate_bases = (Path(token).expanduser().absolute(),)
    resolved_targets: list[Path] = []
    for base in candidate_bases:
        resolved_targets.append(base)
        try:
            resolved_targets.append(base.resolve(strict=False))
        except (OSError, RuntimeError):
            pass
    for target in resolved_targets:
        for base_root in (resolved_root, abs_root):
            try:
                rel = target.relative_to(base_root)
            except ValueError:
                continue
            if rel.parts and _matching_ephemeral_prefix(rel.parts[0]) is not None:
                return rel.parts[0]
    return None


def _extract_capture_names_and_digests(
    value: object,
    *,
    abs_root: Path,
    resolved_root: Path,
) -> tuple[set[str], set[str]]:
    """Extract capture directory names and SHA-256 digests referenced in JSON values."""
    names: set[str] = set()
    digests: set[str] = set()
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            stack.extend(current.values())
            continue
        if isinstance(current, (list, tuple)):
            stack.extend(current)
            continue
        if not isinstance(current, str):
            continue
        text = current.strip()
        if not text:
            continue
        if BARE_SHA256_PATTERN.fullmatch(text) is not None:
            digests.add(text)
            continue
        direct = _extract_dir_name_from_path_token(
            text, abs_root=abs_root, resolved_root=resolved_root
        )
        if direct is not None:
            names.add(direct)
        if any(sep in text for sep in (" ", "\t", "\n")):
            try:
                tokens = shlex.split(text)
            except ValueError:
                tokens = text.split()
            for token in tokens:
                found = _extract_dir_name_from_path_token(
                    token, abs_root=abs_root, resolved_root=resolved_root
                )
                if found is not None:
                    names.add(found)
    return names, digests


def _collect_root_entries(
    root: Path,
    *,
    uid: int | None,
) -> tuple[dict[str, list[_CandidateDirectory]], dict[str, _CandidateDirectory], list[Path], int] | None:
    by_prefix: dict[str, list[_CandidateDirectory]] = {
        prefix: [] for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES
    }
    owned_by_name: dict[str, _CandidateDirectory] = {}
    non_ephemeral_dirs: list[Path] = []
    scanned = 0
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
                owned = _inspect_owned_capture_dir(
                    Path(entry.path),
                    name=name,
                    prefix=prefix,
                    mtime=entry_stat.st_mtime,
                    uid=uid,
                )
                if owned is None:
                    continue
                scanned += 1
                by_prefix[prefix].append(owned)
                owned_by_name[name] = owned
    except OSError:
        return None
    return by_prefix, owned_by_name, non_ephemeral_dirs, scanned


def _collect_active_continuity_references(
    non_ephemeral_dirs: list[Path],
    *,
    owned_by_name: Mapping[str, _CandidateDirectory],
    uid: int | None,
    abs_root: Path,
    resolved_root: Path,
) -> set[str]:
    protected: set[str] = set()
    active_digests: set[str] = set()
    for subdir in non_ephemeral_dirs:
        for filename in _CONTINUITY_FILENAMES:
            loaded = _read_regular_json_object(subdir / filename, uid=uid)
            if loaded is None:
                continue
            parsed, _ = loaded
            found_names, found_digests = _extract_capture_names_and_digests(
                parsed, abs_root=abs_root, resolved_root=resolved_root
            )
            protected.update(found_names)
            active_digests.update(found_digests)

    if active_digests:
        for candidate in owned_by_name.values():
            if candidate.name in protected:
                continue
            if any(
                isinstance(reader.get("sha256"), str)
                and reader["sha256"] in active_digests
                for reader in candidate.reader_payloads.values()
            ):
                protected.add(candidate.name)
                continue
            for filename in candidate.data_payloads:
                loaded = _read_regular_json_object(candidate.path / filename, uid=uid)
                if loaded is None:
                    continue
                _, raw = loaded
                if hashlib.sha256(raw).hexdigest() in active_digests:
                    protected.add(candidate.name)
                    break
    return protected


def _expand_protected_closure(
    seed_names: set[str],
    *,
    owned_by_name: Mapping[str, _CandidateDirectory],
    abs_root: Path,
    resolved_root: Path,
) -> set[str]:
    protected = set(seed_names)
    queue: deque[str] = deque(seed_names)
    while queue:
        current = queue.popleft()
        candidate = owned_by_name.get(current)
        if candidate is None:
            continue
        for payload in (*candidate.data_payloads.values(), *candidate.reader_payloads.values()):
            referenced, _ = _extract_capture_names_and_digests(
                payload, abs_root=abs_root, resolved_root=resolved_root
            )
            for ref_name in referenced:
                if ref_name not in protected:
                    protected.add(ref_name)
                    queue.append(ref_name)
    return protected


def _non_empty_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _has_admission_receipt(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    receipt = payload.get("heartbeat_receipt")
    if not isinstance(receipt, Mapping):
        return None
    if (
        _non_empty_str(receipt.get("event_id")) is not None
        or _non_empty_str(receipt.get("turn_instance_id")) is not None
        or receipt.get("settlement_binding_owed") is True
        or receipt.get("closeout_required") is True
    ):
        return receipt
    return None


def _is_turn_settled(
    payload: Mapping[str, object],
    receipt: Mapping[str, object],
    *,
    runtime_root: Path | None,
    settlement_cache: dict[tuple[str, str, str, str], bool],
) -> bool:
    if runtime_root is None:
        return False
    raw_runtime = _non_empty_str(payload.get("runtime_root"))
    if raw_runtime is None:
        return False
    try:
        payload_runtime = Path(raw_runtime).expanduser().resolve()
        target_runtime = runtime_root.expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if payload_runtime != target_runtime:
        return False

    settlement_identity = receipt.get("settlement_identity")
    identity_map = settlement_identity if isinstance(settlement_identity, Mapping) else {}
    goal_id = _non_empty_str(payload.get("goal_id")) or _non_empty_str(
        identity_map.get("goal_id")
    )
    turn_instance_id = (
        _non_empty_str(receipt.get("turn_instance_id"))
        or _non_empty_str(identity_map.get("turn_instance_id"))
        or _non_empty_str(payload.get("turn_instance_id"))
    )
    if goal_id is None or turn_instance_id is None:
        return False

    agent_identity = payload.get("agent_identity")
    agent_map = agent_identity if isinstance(agent_identity, Mapping) else {}
    agent_id = (
        _non_empty_str(agent_map.get("agent_id"))
        or _non_empty_str(identity_map.get("agent_id"))
        or _non_empty_str(payload.get("agent_id"))
    )

    selected_todo = payload.get("selected_todo")
    todo_map = selected_todo if isinstance(selected_todo, Mapping) else {}
    todo_id = (
        _non_empty_str(todo_map.get("todo_id"))
        or _non_empty_str(identity_map.get("todo_id"))
        or _non_empty_str(payload.get("todo_id"))
    )

    replan_obligation = payload.get("autonomous_replan_obligation")
    replan_map = replan_obligation if isinstance(replan_obligation, Mapping) else {}
    replan_obligation_id = _non_empty_str(
        identity_map.get("replan_obligation_id")
    ) or _non_empty_str(replan_map.get("obligation_id"))

    cache_key = (goal_id, turn_instance_id, agent_id or "", todo_id or "")
    cached = settlement_cache.get(cache_key)
    if cached is not None:
        return cached

    from ..control_plane.quota.settlement import read_heartbeat_settlement

    try:
        readback = read_heartbeat_settlement(
            target_runtime,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=replan_obligation_id,
            include_record_bodies=False,
            tolerate_malformed_history=True,
        )
    except Exception:
        settlement_cache[cache_key] = False
        return False
    if readback is None:
        settled = False
    else:
        progress_state = (
            readback.progress.get("state")
            if isinstance(readback.progress, Mapping)
            else None
        )
        settled = progress_state == "settled" or (
            progress_state is None and str(readback.replay_phase) == "settled"
        )
    settlement_cache[cache_key] = settled
    return settled


def _candidate_has_unsettled_admission(
    candidate: _CandidateDirectory,
    *,
    runtime_root: Path | None,
    settlement_cache: dict[tuple[str, str, str, str], bool],
) -> bool:
    for payload in candidate.data_payloads.values():
        receipt = _has_admission_receipt(payload)
        if receipt is None:
            continue
        if not _is_turn_settled(
            payload,
            receipt,
            runtime_root=runtime_root,
            settlement_cache=settlement_cache,
        ):
            return True
    return False


def _select_prune_targets(
    by_prefix: dict[str, list[_CandidateDirectory]],
    *,
    owned_by_name: Mapping[str, _CandidateDirectory],
    protected_names: set[str],
    runtime_root: Path | None,
    abs_root: Path,
    resolved_root: Path,
    current_now: float,
    min_age_seconds: float,
    keep_recent_per_prefix: int,
    max_delete_per_sweep: int,
) -> list[_CandidateDirectory]:
    if max_delete_per_sweep <= 0:
        return []
    cutoff = current_now - min_age_seconds
    aged_excess: list[_CandidateDirectory] = []
    kept_candidates: list[_CandidateDirectory] = []
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        group = [item for item in by_prefix[prefix] if item.name not in protected_names]
        if not group:
            continue
        group.sort(key=lambda item: (item.mtime, item.name))
        excess = len(group) - keep_recent_per_prefix
        if excess <= 0:
            kept_candidates.extend(group)
            continue
        for candidate in group[:excess]:
            if candidate.mtime <= cutoff:
                aged_excess.append(candidate)
            else:
                kept_candidates.append(candidate)
        kept_candidates.extend(group[excess:])

    if not aged_excess:
        return []
    aged_excess.sort(key=lambda item: (item.mtime, item.name))
    excess_names = {item.name for item in aged_excess}
    settlement_cache: dict[tuple[str, str, str, str], bool] = {}

    # Kept captures (within keep_recent or < min_age) referencing any aged candidate
    # also protect that dependency whenever their own Turn remains unsettled.
    unsettled_seeds: set[str] = set()
    for kept in kept_candidates:
        refs: set[str] = set()
        for payload in (*kept.data_payloads.values(), *kept.reader_payloads.values()):
            found_refs, _ = _extract_capture_names_and_digests(
                payload, abs_root=abs_root, resolved_root=resolved_root
            )
            refs.update(found_refs)
        if refs & excess_names and _candidate_has_unsettled_admission(
            kept,
            runtime_root=runtime_root,
            settlement_cache=settlement_cache,
        ):
            unsettled_seeds.add(kept.name)

    reclaimable: list[_CandidateDirectory] = []
    for candidate in aged_excess:
        if _candidate_has_unsettled_admission(
            candidate,
            runtime_root=runtime_root,
            settlement_cache=settlement_cache,
        ):
            unsettled_seeds.add(candidate.name)
            continue
        reclaimable.append(candidate)

    if unsettled_seeds:
        protected_names.update(
            _expand_protected_closure(
                unsettled_seeds,
                owned_by_name=owned_by_name,
                abs_root=abs_root,
                resolved_root=resolved_root,
            )
        )

    final_targets = [
        item for item in reclaimable if item.name not in protected_names
    ]
    return final_targets[:max_delete_per_sweep]


def maybe_prune_context_delivery_root(
    root: Path,
    *,
    runtime_root: Path | None = None,
    min_age_seconds: float | None = None,
    keep_recent_per_prefix: int | None = None,
    max_delete_per_sweep: int | None = None,
    min_sweep_interval_seconds: float | None = None,
    now: float | None = None,
    protected_names: frozenset[str] = frozenset(),
) -> ContextDeliveryPruneResult:
    """Opportunistically prune expired, settled LoopX capture dirs under ``root``.

    Throttled via a lightweight ``.retention-sweep`` timestamp marker so hot
    capture calls skip in sub-millisecond time. Fails closed on symlinks,
    non-LoopX/unexpected directories, active ``session.json``/``claim-source.json``
    references and their dependencies, or turns whose admission receipts are
    missing/unsettled.
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

        collected = _collect_root_entries(root, uid=uid)
        if collected is None:
            return ContextDeliveryPruneResult(
                swept=False,
                skipped_reason="scan_failed",
                scanned=0,
                deleted=0,
                failed=1,
            )
        by_prefix, owned_by_name, non_ephemeral_dirs, scanned = collected
        abs_root, resolved_root = _resolved_variants(root)
        active_refs = _collect_active_continuity_references(
            non_ephemeral_dirs,
            owned_by_name=owned_by_name,
            uid=uid,
            abs_root=abs_root,
            resolved_root=resolved_root,
        )
        protected_set = _expand_protected_closure(
            set(protected_names) | active_refs,
            owned_by_name=owned_by_name,
            abs_root=abs_root,
            resolved_root=resolved_root,
        )
        targets = _select_prune_targets(
            by_prefix,
            owned_by_name=owned_by_name,
            protected_names=protected_set,
            runtime_root=runtime_root,
            abs_root=abs_root,
            resolved_root=resolved_root,
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
