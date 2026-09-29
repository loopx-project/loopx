from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import time
import importlib
from typing import Any, Iterator, TextIO
from uuid import uuid4

try:  # pragma: no cover - exercised on POSIX hosts in integration smokes.
    fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover
    fcntl = None

try:  # pragma: no cover - imported only on Windows hosts.
    msvcrt: Any = importlib.import_module("msvcrt")
except ImportError:  # pragma: no cover
    msvcrt = None


def _prepare_windows_lock(lock_file: TextIO) -> None:
    lock_file.seek(0, 2)
    if lock_file.tell() == 0:
        lock_file.write("0")
        lock_file.flush()
    lock_file.seek(0)


def _try_acquire_kernel_lock(lock_file: TextIO) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if not _lock_is_busy(exc):
                raise
            return False
        return True
    if msvcrt is None:
        raise RuntimeError("no supported file-lock backend is available")

    _prepare_windows_lock(lock_file)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if not _lock_is_busy(exc):
            raise
        return False
    return True


def _release_kernel_lock(lock_file: TextIO) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


LOCK_ACQUIRE_TIMEOUT_ERROR_CODE = "lock_acquire_timeout"
LOCK_HOLDER_SCHEMA_VERSION = "file_lock_holder_v0"
LOCK_INCIDENT_SCHEMA_VERSION = "file_lock_incident_v0"
_SAFE_LABEL_PATTERN = re.compile(r"[^A-Za-z0-9._:@-]+")


class LockAcquisitionPolicy(str, Enum):
    MUTATION = "mutation"
    MONITOR = "monitor"
    SINGLE_FLIGHT = "single_flight"


@dataclass(frozen=True, slots=True)
class LockPolicy:
    timeout_seconds: float
    poll_interval_seconds: float
    retry_mode: str


LOCK_POLICIES = {
    LockAcquisitionPolicy.MUTATION: LockPolicy(
        timeout_seconds=5.0,
        poll_interval_seconds=0.05,
        retry_mode="manual_after_holder_inspection",
    ),
    LockAcquisitionPolicy.MONITOR: LockPolicy(
        timeout_seconds=1.0,
        poll_interval_seconds=0.10,
        retry_mode="next_scheduled_poll_after_holder_inspection",
    ),
    LockAcquisitionPolicy.SINGLE_FLIGHT: LockPolicy(
        timeout_seconds=0.0,
        poll_interval_seconds=0.0,
        retry_mode="skip_duplicate_attempt",
    ),
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_label(value: object, *, fallback: str) -> str:
    compact = _SAFE_LABEL_PATTERN.sub("_", str(value or "").strip()).strip("._-")
    return compact[:128] or fallback


def _policy(value: LockAcquisitionPolicy | str) -> LockAcquisitionPolicy:
    if isinstance(value, LockAcquisitionPolicy):
        return value
    return LockAcquisitionPolicy(str(value))


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def _open_lock_descriptor(path: Path, *, flags: int) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow and path.is_symlink():
        raise OSError(errno.ELOOP, "lock path must not be a symlink", str(path))
    descriptor = os.open(path, flags | no_follow, 0o600)
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(path)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_ino != path_stat.st_ino
            or getattr(descriptor_stat, "st_nlink", 1) != 1
        ):
            raise OSError(errno.EINVAL, "lock path must be a regular file", str(path))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def lock_holder_path(path: Path) -> Path:
    lock_path = _lock_path(path)
    if os.name == "nt":
        return lock_path.with_name(f"{lock_path.name}.holder.json")
    return lock_path


def lock_incident_path(path: Path) -> Path:
    lock_path = _lock_path(path)
    return lock_path.with_name(f"{lock_path.name}.incidents.jsonl")


def _lock_id(path: Path) -> str:
    resolved = str(path.expanduser().resolve(strict=False)).encode("utf-8")
    return hashlib.sha256(resolved).hexdigest()[:16]


def _identity(
    *,
    agent_id: str | None,
    operation: str | None,
    policy: LockAcquisitionPolicy,
) -> dict[str, object]:
    return {
        "pid": os.getpid(),
        # A pid is only meaningful on the machine that wrote this record. Two
        # hosts sharing one runtime root can both read the holder, so the record
        # names its own machine and a reader never has to guess which host a pid
        # belongs to. The name is a sanitized label, not a path or a secret.
        "host": lock_holder_host_label(),
        "agent_id": _safe_label(
            agent_id or os.environ.get("LOOPX_AGENT_ID"),
            fallback="unknown",
        ),
        "operation": _safe_label(operation or policy.value, fallback=policy.value),
    }


def _holder_record(
    path: Path,
    *,
    agent_id: str | None,
    operation: str | None,
    policy: LockAcquisitionPolicy,
) -> dict[str, object]:
    return {
        "schema_version": LOCK_HOLDER_SCHEMA_VERSION,
        "lock_id": _lock_id(path),
        "policy": policy.value,
        **_identity(agent_id=agent_id, operation=operation, policy=policy),
        "acquired_at": _utc_now_iso(),
    }


def _write_holder_record(lock_file: TextIO, record: dict[str, object]) -> None:
    lock_file.seek(0)
    lock_file.truncate()
    json.dump(record, lock_file, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    lock_file.write("\n")
    lock_file.flush()
    os.fsync(lock_file.fileno())


def _write_holder_sidecar(holder_path: Path, record: dict[str, object]) -> None:
    holder_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{holder_path.name}.",
        suffix=".tmp",
        dir=holder_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as holder_file:
            json.dump(
                record,
                holder_file,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            holder_file.write("\n")
            holder_file.flush()
            os.fsync(holder_file.fileno())
        os.replace(temporary_path, holder_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _persist_holder_record(
    lock_file: TextIO,
    *,
    lock_path: Path,
    holder_path: Path,
    record: dict[str, object],
) -> None:
    if holder_path == lock_path:
        _write_holder_record(lock_file, record)
        return
    _write_holder_sidecar(holder_path, record)


def _mark_released(
    lock_file: TextIO,
    *,
    lock_path: Path,
    holder_path: Path,
    record: dict[str, object],
) -> None:
    released = {**record, "released_at": _utc_now_iso()}
    try:
        _persist_holder_record(
            lock_file,
            lock_path=lock_path,
            holder_path=holder_path,
            record=released,
        )
    except OSError:
        # Releasing the kernel lock is more important than refreshing advisory
        # metadata; a future holder overwrites the complete record.
        pass


_HOLDER_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "lock_id",
        "policy",
        "host",
        "pid",
        "agent_id",
        "operation",
        "acquired_at",
        "released_at",
    }
)


def _filter_holder_record(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in _HOLDER_RECORD_FIELDS if key in payload}


def _read_holder_record(lock_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _filter_holder_record(payload)


def lock_holder_host_label() -> str:
    """The machine label a holder record carries; readers compare against it."""

    return _safe_label(socket.gethostname(), fallback="unknown")


# Liveness of a lock's last holder, read from its record alone. The kernel lock
# is never probed: a probe would hold the lock for an instant, and a real
# single-flight acquisition racing that instant would be refused for nothing.
LOCK_HOLDER_LIVE = "live"
LOCK_HOLDER_RELEASED = "released"
LOCK_HOLDER_DEAD = "dead"
LOCK_HOLDER_FOREIGN_HOST = "foreign_host"
LOCK_HOLDER_UNREADABLE = "unreadable"
LOCK_HOLDER_ABSENT = "absent"
LOCK_HOLDER_LIVENESS_STATES = (
    LOCK_HOLDER_LIVE,
    LOCK_HOLDER_RELEASED,
    LOCK_HOLDER_DEAD,
    LOCK_HOLDER_FOREIGN_HOST,
    LOCK_HOLDER_UNREADABLE,
    LOCK_HOLDER_ABSENT,
)


def lock_holder_liveness(path: Path) -> tuple[str, dict[str, object]]:
    """Classify the last holder of one lock without touching the kernel lock.

    Returns the liveness state and the filtered holder record. ``released``
    means the holder wrote ``released_at`` on a clean exit; ``dead`` means the
    record names this machine and the pid is gone, which is what a crashed or
    killed holder leaves behind; ``foreign_host`` means the pid cannot be
    checked from here; ``unreadable`` means a lock file exists but carries no
    parseable record, for example mid-acquisition. Only ``live`` is evidence of
    a running holder, and even that is pid liveness, not the kernel lock: a
    reused pid can keep a crashed holder looking alive until the next holder
    overwrites the record.
    """

    holder_path = lock_holder_path(path)
    try:
        text = holder_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LOCK_HOLDER_ABSENT, {}
    except OSError:
        return LOCK_HOLDER_UNREADABLE, {}
    try:
        record = _filter_holder_record(json.loads(text))
    except ValueError:
        return LOCK_HOLDER_UNREADABLE, {}
    if not record:
        return LOCK_HOLDER_UNREADABLE, {}
    released_at = record.get("released_at")
    if isinstance(released_at, str) and released_at:
        return LOCK_HOLDER_RELEASED, record
    if record.get("host") != lock_holder_host_label():
        return LOCK_HOLDER_FOREIGN_HOST, record
    pid = record.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return LOCK_HOLDER_UNREADABLE, record
    return (LOCK_HOLDER_LIVE if process_is_alive(pid) else LOCK_HOLDER_DEAD), record


def _operator_action(holder: dict[str, object], *, retry_mode: str) -> dict[str, object]:
    return {
        "required": True,
        "action": "inspect_lock_holder",
        # The pid is only meaningful on this host: naming it stops an operator
        # from hunting for a process id that cannot exist on another machine.
        "holder_host": holder.get("host"),
        "holder_pid": holder.get("pid"),
        "retry_mode": retry_mode,
        "steps": [
            "Inspect the recorded holder host, PID and operation on that host.",
            "Confirm the process is stalled before terminating it.",
            "Retry according to retry_mode after the holder exits.",
            "Do not delete the lock file; the kernel lock is authoritative.",
        ],
    }


def _append_incident(path: Path, record: dict[str, object]) -> bool:
    incident_path = lock_incident_path(path)
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = _open_lock_descriptor(
            incident_path,
            flags=os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        )
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return False
    return True


class LockAcquireTimeoutError(TimeoutError):
    code = LOCK_ACQUIRE_TIMEOUT_ERROR_CODE

    def __init__(
        self,
        *,
        incident: dict[str, object],
        incident_recorded: bool,
        incident_channel: str,
    ) -> None:
        self.incident = incident
        self.incident_recorded = incident_recorded
        self.incident_channel = incident_channel
        raw_holder = incident.get("holder")
        holder: dict[str, object] = raw_holder if isinstance(raw_holder, dict) else {}
        super().__init__(
            "file lock acquisition timed out"
            + (f" while waiting for holder pid {holder.get('pid')}" if holder.get("pid") else "")
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": self.code,
            "lock_timeout": self.incident,
            "incident_recorded": self.incident_recorded,
            "incident_channel": self.incident_channel,
            "operator_action": self.incident["operator_action"],
        }


def lock_timeout_error_fields(error: BaseException) -> dict[str, object]:
    if isinstance(error, LockAcquireTimeoutError):
        return error.to_payload()
    return {}


def _timeout_error(
    path: Path,
    *,
    policy: LockAcquisitionPolicy,
    timeout_seconds: float,
    waited_seconds: float,
    started_at: str,
    agent_id: str | None,
    operation: str | None,
) -> LockAcquireTimeoutError:
    holder = _read_holder_record(lock_holder_path(path))
    waiter = {
        **_identity(agent_id=agent_id, operation=operation, policy=policy),
        "started_at": started_at,
        "timeout_seconds": round(timeout_seconds, 3),
        "waited_seconds": round(waited_seconds, 3),
    }
    action = _operator_action(holder, retry_mode=LOCK_POLICIES[policy].retry_mode)
    incident: dict[str, object] = {
        "schema_version": LOCK_INCIDENT_SCHEMA_VERSION,
        "error_code": LOCK_ACQUIRE_TIMEOUT_ERROR_CODE,
        "recorded_at": _utc_now_iso(),
        "lock_id": _lock_id(path),
        "policy": policy.value,
        "holder": holder,
        "waiter": waiter,
        "operator_action": action,
    }
    recorded = _append_incident(path, incident)
    return LockAcquireTimeoutError(
        incident=incident,
        incident_recorded=recorded,
        incident_channel=lock_incident_path(path).name,
    )


def _lock_is_busy(error: OSError) -> bool:
    return isinstance(error, BlockingIOError) or error.errno in {
        errno.EACCES,
        errno.EAGAIN,
    }


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    policy: LockAcquisitionPolicy | str = LockAcquisitionPolicy.MUTATION,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
    agent_id: str | None = None,
    operation: str | None = None,
) -> Iterator[Path]:
    """Hold a sibling lock file with a finite cross-platform deadline."""

    selected_policy = _policy(policy)
    defaults = LOCK_POLICIES[selected_policy]
    timeout = defaults.timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
    poll_interval = (
        defaults.poll_interval_seconds
        if poll_interval_seconds is None
        else max(0.001, poll_interval_seconds)
    )
    lock_path = _lock_path(path)
    holder_path = lock_holder_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = _open_lock_descriptor(
        lock_path,
        flags=os.O_CREAT | os.O_RDWR,
    )
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock_file:
        started = time.monotonic()
        started_at = _utc_now_iso()
        deadline = started + timeout
        while not _try_acquire_kernel_lock(lock_file):
            now = time.monotonic()
            if now >= deadline:
                raise _timeout_error(
                    path,
                    policy=selected_policy,
                    timeout_seconds=timeout,
                    waited_seconds=now - started,
                    started_at=started_at,
                    agent_id=agent_id,
                    operation=operation,
                ) from None
            time.sleep(min(poll_interval, max(0.0, deadline - now)))
        record = _holder_record(
            path,
            agent_id=agent_id,
            operation=operation,
            policy=selected_policy,
        )
        try:
            _persist_holder_record(
                lock_file,
                lock_path=lock_path,
                holder_path=holder_path,
                record=record,
            )
            yield lock_path
        finally:
            _mark_released(
                lock_file,
                lock_path=lock_path,
                holder_path=holder_path,
                record=record,
            )
            _release_kernel_lock(lock_file)


@contextmanager
def try_exclusive_file_lock(
    path: Path,
    *,
    agent_id: str | None = None,
    operation: str | None = None,
) -> Iterator[Path | None]:
    """Try once to hold a sibling lock file for single-flight work.

    ``None`` means another process already owns the lock. POSIX uses ``flock``;
    Windows uses the standard-library ``msvcrt`` byte-range lock.
    """

    lock_path = _lock_path(path)
    holder_path = lock_holder_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = _open_lock_descriptor(
        lock_path,
        flags=os.O_CREAT | os.O_RDWR,
    )
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock_file:
        if not _try_acquire_kernel_lock(lock_file):
            yield None
            return
        record = _holder_record(
            path,
            agent_id=agent_id,
            operation=operation,
            policy=LockAcquisitionPolicy.SINGLE_FLIGHT,
        )
        try:
            _persist_holder_record(
                lock_file,
                lock_path=lock_path,
                holder_path=holder_path,
                record=record,
            )
            yield lock_path
        finally:
            _mark_released(
                lock_file,
                lock_path=lock_path,
                holder_path=holder_path,
                record=record,
            )
            _release_kernel_lock(lock_file)


EFFECT_MUTATION_LOCK_SUFFIX = ".ts-effect.lock"
EFFECT_MUTATION_INVALID_STALE_SECONDS = 10.0
EFFECT_MUTATION_TOKEN_MAX_LENGTH = 256
_EFFECT_MUTATION_INVALID_CLAIM_TOKEN = "__invalid_lock_reclaim__"


def _effect_mutation_lock_path(path: Path) -> Path:
    return Path(f"{path}{EFFECT_MUTATION_LOCK_SUFFIX}")


def _effect_mutation_claim_path(path: Path, token: str) -> Path:
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return path.with_name(f"{path.name}.claim.{token_digest}")


def process_is_alive(pid: object) -> bool:
    """Probe a process without sending signals or console control events."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # Windows signal 0 is CTRL_C_EVENT, not the side-effect-free POSIX
        # existence probe provided by kill(pid, 0).
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _windows_process_is_alive(pid: int) -> bool:
    """Probe a Windows process without sending a console control event."""

    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    error_access_denied = 5
    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(synchronize, False, pid)
    if not handle:
        return bool(getattr(ctypes, "get_last_error")() == error_access_denied)
    try:
        return bool(wait_for_single_object(handle, 0) == wait_timeout)
    finally:
        close_handle(handle)


def _effect_mutation_process_is_alive(pid: object) -> bool:
    return process_is_alive(pid)


def _read_effect_mutation_owner(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get("pid")
    token = payload.get("token")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or not isinstance(token, str)
        or not token
        or len(token) > EFFECT_MUTATION_TOKEN_MAX_LENGTH
        or not token.strip()
    ):
        return None
    return {"pid": pid, "token": token}


@dataclass(frozen=True)
class _EffectMutationClaim:
    path: Path
    token: str
    identity: tuple[int, int, int, int]


def _effect_file_identity_from_stat(info: os.stat_result) -> tuple[int, int, int, int]:
    """Capture a replacement-resistant identity on POSIX and Windows."""

    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(getattr(info, "st_birthtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _effect_file_identity_from_fd(descriptor: int) -> tuple[int, int, int, int]:
    return _effect_file_identity_from_stat(os.fstat(descriptor))


def _effect_file_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        return _effect_file_identity_from_stat(path.stat())
    except OSError:
        return None


def _same_effect_file_identity(
    left: tuple[int, int, int, int] | None,
    right: tuple[int, int, int, int] | None,
) -> bool:
    if left is None or right is None:
        return False
    left_has_device_identity = left[:2] != (0, 0)
    right_has_device_identity = right[:2] != (0, 0)
    if left_has_device_identity != right_has_device_identity:
        return False
    if left_has_device_identity:
        if left[:2] != right[:2]:
            return False
        # A birth marker protects against rapid inode reuse when both stat
        # calls expose it. On POSIX/macOS it may be absent; in that case the
        # stable device/inode pair remains the best available identity and is
        # not invalidated by ordinary content writes (which change ctime).
        if left[2] or right[2]:
            return left[2] != 0 and right[2] != 0 and left[2] == right[2]
        return True
    # Without device/inode identity, prefer a birth marker, then creation time.
    # Two all-zero identities are ambiguous and must fail closed.
    if left[2] or right[2]:
        return left[2] != 0 and right[2] != 0 and left[2] == right[2]
    # Some Windows filesystems expose no device/inode or birth marker.  ctime
    # is the last available creation-like marker; two zero identities are not
    # safe to compare.
    return left[3] != 0 and right[3] != 0 and left[3] == right[3]


def _remove_created_effect_file(
    path: Path,
    identity: tuple[int, int, int, int] | None,
) -> None:
    if identity is None:
        return
    try:
        if _same_effect_file_identity(identity, _effect_file_identity(path)):
            path.unlink(missing_ok=True)
    except OSError:
        # The path was already retired or replaced; never remove an unknown file.
        pass


def _remove_dead_effect_mutation_claim(path: Path) -> bool:
    identity = _effect_file_identity(path)
    if identity is None:
        return False
    owner = _read_effect_mutation_owner(path)
    if owner is not None and _effect_mutation_process_is_alive(owner.get("pid")):
        return False
    if owner is None:
        try:
            age_seconds = time.time() - path.stat().st_mtime
        except OSError:
            return False
        if age_seconds < EFFECT_MUTATION_INVALID_STALE_SECONDS:
            return False
    if not _same_effect_file_identity(identity, _effect_file_identity(path)):
        return False
    _remove_created_effect_file(path, identity)
    return _effect_file_identity(path) is None


def _claim_effect_mutation_lock(
    path: Path,
    token: str,
) -> _EffectMutationClaim | None:
    if (
        not token
        or len(token) > EFFECT_MUTATION_TOKEN_MAX_LENGTH
        or not token.strip()
    ):
        return None
    claim_path = _effect_mutation_claim_path(path, token)
    for _attempt in range(2):
        try:
            descriptor = os.open(
                claim_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            if not _remove_dead_effect_mutation_claim(claim_path):
                return None
            continue
        # Capture identity before publication so a write/fsync failure can
        # still retire the claim without relying on a later path read.
        identity: tuple[int, int, int, int] | None = _effect_file_identity_from_fd(
            descriptor
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as claim_file:
                json.dump(
                    {"pid": os.getpid(), "token": token},
                    claim_file,
                    separators=(",", ":"),
                )
                claim_file.flush()
                os.fsync(claim_file.fileno())
        except BaseException:
            _remove_created_effect_file(claim_path, identity)
            raise
        if identity is None:
            return None
        return _EffectMutationClaim(claim_path, token, identity)
    return None


def _release_effect_mutation_claim(
    claim: _EffectMutationClaim | Path,
    token: str | None = None,
) -> None:
    expected_token: str | None
    identity: tuple[int, int, int, int] | None
    if isinstance(claim, _EffectMutationClaim):
        path = claim.path
        expected_token = claim.token
        identity = claim.identity
    else:
        path = claim
        expected_token = token
        identity = _effect_file_identity(path)
    if not expected_token:
        return
    owner = _read_effect_mutation_owner(path)
    if isinstance(claim, _EffectMutationClaim):
        if (
            owner is None
            or owner.get("pid") != os.getpid()
            or owner.get("token") != expected_token
        ):
            # The pathname may have been corrupted or replaced after this
            # caller created the claim.  Its captured identity still permits
            # removing only its own inode, preventing claim leaks without
            # touching a later claimant.
            _remove_created_effect_file(path, identity)
            return
    elif (
        owner is None
        or owner.get("pid") != os.getpid()
        or owner.get("token") != expected_token
    ):
        return
    _remove_created_effect_file(path, identity)


def _reclaim_stale_effect_mutation_lock(path: Path) -> bool:
    identity = _effect_file_identity(path)
    if identity is None:
        return False
    owner = _read_effect_mutation_owner(path)
    if owner is not None and _effect_mutation_process_is_alive(owner.get("pid")):
        return False
    if owner is None:
        try:
            age_seconds = time.time() - path.stat().st_mtime
        except OSError:
            return False
        if age_seconds < EFFECT_MUTATION_INVALID_STALE_SECONDS:
            return False
    claim_token = (
        str(owner["token"])
        if owner is not None
        else _EFFECT_MUTATION_INVALID_CLAIM_TOKEN
    )
    claim = _claim_effect_mutation_lock(path, claim_token)
    if claim is None:
        return False
    stale_path = path.with_name(f"{path.name}.stale.{uuid4()}")
    try:
        current = _read_effect_mutation_owner(path)
        if owner is not None and (
            current is None or current.get("token") != owner.get("token")
        ):
            return False
        if current is not None and _effect_mutation_process_is_alive(
            current.get("pid")
        ):
            return False
        if current is None:
            try:
                if (
                    time.time() - path.stat().st_mtime
                    < EFFECT_MUTATION_INVALID_STALE_SECONDS
                ):
                    return False
            except OSError:
                return False
        if not _same_effect_file_identity(identity, _effect_file_identity(path)):
            return False
        path.replace(stale_path)
    except FileNotFoundError:
        return False
    finally:
        if claim is not None:
            _release_effect_mutation_claim(claim)
    stale_path.unlink(missing_ok=True)

    return True

def _release_effect_mutation_lock(
    path: Path,
    token: str,
    *,
    suppress_errors: bool = False,
) -> bool:
    claim: _EffectMutationClaim | None = None
    try:
        lock_identity = _effect_file_identity(path)
        if lock_identity is None:
            return False
        owner = _read_effect_mutation_owner(path)
        if owner is None or owner.get("token") != token:
            _release_effect_mutation_claim(
                _effect_mutation_claim_path(path, token),
                token,
            )
            return False
        claim = _claim_effect_mutation_lock(path, token)
        if claim is None:
            return False
        retired_path = path.with_name(f"{path.name}.released.{uuid4()}")
        try:
            current = _read_effect_mutation_owner(path)
            if current is None or current.get("token") != token:
                return False
            if not _same_effect_file_identity(lock_identity, _effect_file_identity(path)):
                return False
            try:
                path.replace(retired_path)
            except FileNotFoundError:
                return False
            try:
                retired_path.unlink(missing_ok=True)
            except OSError:
                pass
            return True
        finally:
            if claim is not None:
                _release_effect_mutation_claim(claim)
            try:
                retired_path.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        if suppress_errors:
            return False
        raise
    finally:
        if claim is not None:
            _release_effect_mutation_claim(claim)


def release_cross_runtime_mutation_lock(path: Path, *, token: str) -> bool:
    """Safely abandon one token-owned TypeScript mutation lock.

    This recovery path is for a caller that still owns a long-lived lock but
    lost the managed-runtime response needed to close it. The token claim and
    file-identity checks make the operation race safely with an in-flight
    native closer: if that closer already owns the claim, this call returns
    ``False`` and leaves the lock untouched.
    """

    return _release_effect_mutation_lock(
        _effect_mutation_lock_path(path),
        token,
        suppress_errors=True,
    )


def cross_runtime_lock_witness(path: Path) -> dict[str, object]:
    """Internal handoff of a lock held by this process, never an Agent token.

    The native effect must claim and recheck it before using adapted facts.
    Its final save owns release; Python's later release is token-checked.
    """
    owner = _read_effect_mutation_owner(_effect_mutation_lock_path(path))
    if owner is None or owner.get("pid") != os.getpid():
        raise RuntimeError("checkpoint handoff requires the caller's held mutation lock")
    return {"target": str(path.resolve()), **owner}


@contextmanager
def exclusive_mutation_file_lock(
    path: Path,
    *,
    policy: LockAcquisitionPolicy | str = LockAcquisitionPolicy.MUTATION,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
    agent_id: str | None = None,
    operation: str | None = None,
) -> Iterator[Path]:
    """Hold the existing TypeScript mutation marker and its token/claim protocol."""

    selected_policy = _policy(policy)
    defaults = LOCK_POLICIES[selected_policy]
    timeout = (
        defaults.timeout_seconds
        if timeout_seconds is None
        else max(0.0, timeout_seconds)
    )
    poll_interval = (
        defaults.poll_interval_seconds
        if poll_interval_seconds is None
        else max(0.001, poll_interval_seconds)
    )
    effect_lock_path = _effect_mutation_lock_path(path)
    effect_lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = str(uuid4())
    started = time.monotonic()
    started_at = _utc_now_iso()
    deadline = started + timeout
    retried_reclaimed_lock = False
    while True:
        try:
            descriptor = os.open(
                effect_lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            reclaimed = _reclaim_stale_effect_mutation_lock(effect_lock_path)
            # A zero-wait probe gets one immediate acquisition attempt after
            # proving and removing a dead owner. It never waits for a live one.
            if reclaimed and not retried_reclaimed_lock:
                retried_reclaimed_lock = True
                continue
            now = time.monotonic()
            if now >= deadline:
                raise _timeout_error(
                    path,
                    policy=selected_policy,
                    timeout_seconds=timeout,
                    waited_seconds=now - started,
                    started_at=started_at,
                    agent_id=agent_id,
                    operation=operation,
                ) from None
            time.sleep(min(poll_interval, max(0.0, deadline - now)))
            continue
        identity = _effect_file_identity_from_fd(descriptor)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
                json.dump(
                    {"pid": os.getpid(), "token": token},
                    lock_file,
                    separators=(",", ":"),
                )
                lock_file.flush()
                os.fsync(lock_file.fileno())
        except BaseException:
            _remove_created_effect_file(effect_lock_path, identity)
            raise
        break

    try:
        yield effect_lock_path
    finally:
        _release_effect_mutation_lock(
            effect_lock_path,
            token,
            # Lock cleanup is secondary to the durable body result.  A cleanup
            # failure must not replace either a successful result or its
            # original exception; stale-owner recovery handles a later retry.
            suppress_errors=True,
        )


@contextmanager
def exclusive_cross_runtime_file_lock(
    path: Path,
    *,
    policy: LockAcquisitionPolicy | str = LockAcquisitionPolicy.MUTATION,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
    agent_id: str | None = None,
    operation: str | None = None,
) -> Iterator[Path]:
    """Source writers retain their existing order: mutation marker, then kernel."""
    with exclusive_mutation_file_lock(
        path, policy=policy, timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds, agent_id=agent_id, operation=operation,
    ):
        with exclusive_file_lock(
            path, policy=policy, timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds, agent_id=agent_id, operation=operation,
        ) as lock_path:
            yield lock_path


@contextmanager
def exclusive_run_index_lock(path: Path, *, operation: str) -> Iterator[Path]:
    """Goal indexes use kernel then marker, matching existing quota adapters.

    Native writers take only the marker, never the kernel lock. Python callers
    must enter here before any source lock; no index path may use the reverse
    order from exclusive_cross_runtime_file_lock. A native checkpoint effect
    claims the marker until append completes, including after caller exit.
    """
    with exclusive_file_lock(path, operation=operation) as lock_path:
        with exclusive_mutation_file_lock(path, operation=operation):
            yield lock_path
