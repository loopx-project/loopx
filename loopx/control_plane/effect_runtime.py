from __future__ import annotations

import atexit
import errno
import hashlib
import json
import math
import os
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, closing, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import IO, Any, Literal, NotRequired, TypeAlias, TypedDict, cast

from ..file_lock import (
    _effect_file_identity_from_fd,
    _remove_created_effect_file,
    process_is_alive,
)
from .content_digest import BARE_SHA256_PATTERN
from .runtime.effect_client_errors import (
    EffectRuntimeConflict as EffectRuntimeConflict,
    EffectRuntimeHostPermissionError as EffectRuntimeHostPermissionError,
    EffectRuntimeInternalError as EffectRuntimeInternalError,
    EffectRuntimeIOError as EffectRuntimeIOError,
    EffectRuntimeLockTimeout as EffectRuntimeLockTimeout,
    EffectRuntimeNodeProbeError as EffectRuntimeNodeProbeError,
    EffectRuntimePermanentIOError as EffectRuntimePermanentIOError,
    EffectRuntimeRejected as EffectRuntimeRejected,
    EffectRuntimeRemoteError as EffectRuntimeRemoteError,
    EffectRuntimeResponseAmbiguous as EffectRuntimeResponseAmbiguous,
    EffectRuntimeStartupError as EffectRuntimeStartupError,
    EffectRuntimeTransientIOError as EffectRuntimeTransientIOError,
    _remote_runtime_error,
)
from .runtime.effect_readiness import (
    EFFECT_RUNTIME_READINESS_SCHEMA_VERSION as EFFECT_RUNTIME_READINESS_SCHEMA_VERSION,
    EffectReadinessHooks as _EffectReadinessHooks,
    collect_effect_runtime_readiness as _collect_effect_runtime_readiness,
    runtime_identity_from_info as runtime_identity_from_info,
)
from .runtime.file_reads import iter_binary_file_reads
from .runtime.node_probe import (
    HOST_PERMISSION_RECOMMENDATION as HOST_PERMISSION_RECOMMENDATION,
    MINIMUM_NODE_VERSION as MINIMUM_NODE_VERSION,
    MINIMUM_NODE_VERSION_TEXT as MINIMUM_NODE_VERSION_TEXT,
    STARTUP_READY_TIMEOUT_SECONDS as STARTUP_READY_TIMEOUT_SECONDS,
    node_probe_remediation as node_probe_remediation,
    node_typescript_flags as _node_typescript_flags,
    probe_node as _probe_node,
)
from .runtime.private_permissions import protect_private_path, require_private_path
from .runtime.stdio_transport import EffectRuntimeStdio

EFFECT_RUNTIME_REQUEST_SCHEMA_VERSION = "loopx_effect_runtime_request_v0"
EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION = "loopx_effect_runtime_response_v1"
EFFECT_RUNTIME_INFO_SCHEMA_VERSION = "loopx_effect_runtime_info_v0"
EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION = "loopx_effect_runtime_startup_error_v0"
EFFECT_RUNTIME_RESTART_SCHEMA_VERSION = "loopx_effect_runtime_restart_v0"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_LOCAL_SNAPSHOT_BYTES = 64 * 1024 * 1024
# Shared packaged transport eligibility; business authority stays with each method.
LOCAL_SNAPSHOT_METHODS = frozenset(json.loads(
    Path(__file__).with_name("effect_runtime_snapshot_methods.json").read_text(encoding="utf-8"),
))
MAX_STARTUP_DIAGNOSTIC_BYTES = 8 * 1024
STARTUP_LOCK_TIMEOUT_SECONDS = 15.0
STARTUP_POLL_SECONDS = 0.025
# A managed runtime publishes its typed startup diagnostic when it gives up,
# so the host can only recover the exact code after that publication. When the
# ready budget expires while the runtime is still running, the host gives it
# one more ready budget to publish and exit instead of discarding a diagnostic
# the runtime already computed. The window reuses the ready budget the
# repository already owns rather than introducing an unrelated number.
STARTUP_DIAGNOSTIC_GRACE_SECONDS = STARTUP_READY_TIMEOUT_SECONDS
RUNTIME_LOCATOR_PERMISSION_RETRIES = 3
RUNTIME_RETRY_SETTLE_SECONDS = 0.25
RUNTIME_LOCATOR_RECOVERY_TIMEOUT_SECONDS = 1.0
_RUNTIME_PID_MAX = 0xFFFFFFFF
_STALE_RUNTIME_DESCRIPTOR_GRACE_SECONDS = 60.0
_STALE_RUNTIME_DESCRIPTOR_MAX_INSPECT = 64
_STALE_RUNTIME_DESCRIPTOR_MAX_REMOVALS = 32
_STALE_RUNTIME_DESCRIPTOR_MAX_BYTES = 16 * 1024
_STALE_RUNTIME_DESCRIPTOR_MAX_SCAN_STEPS = 512
_STALE_CLEANUP_SWEEP_LIMIT = 4
_STALE_CLEANUP_SWEEP_IDLE_SECONDS = 300.0
_STALE_CLEANUP_SEEN_LOCK_LIMIT = 65536


@dataclass
class _StaleCleanupSweep:
    scandir_cm: Any
    iterator: Iterator[os.DirEntry[str]] | None
    pending_candidates: deque[os.DirEntry[str]]
    seen_lock_names: set[str]
    dir_identity: tuple[int, int]
    scandir_fn: object
    last_used_monotonic: float


_stale_cleanup_sweeps: dict[str, _StaleCleanupSweep] = {}
_stale_cleanup_sweeps_lock = Lock()
_stale_cleanup_generation = 0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
# Canonical writers may wait 30 seconds for the per-Goal maintenance lock and
# another 5 seconds for the provider lock. Keep the client connected through
# that declared critical section and a bounded readback; a shorter RPC budget
# turns an in-flight write into an avoidable ambiguous response.
CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS = 45.0
CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS = 15.0
_RUNTIME_SOURCE_SUFFIXES = frozenset({".json", ".ts"})
_RUNTIME_SOURCE_PATHS = frozenset({"runtime/private_permissions.py"})
_RuntimeSourceSnapshot: TypeAlias = tuple[tuple[str, int, int, int], ...]
_SOURCE_DIGEST_SCHEMA_VERSION = "loopx_effect_runtime_source_digest_v1"
_SOURCE_DIGEST_MAX_BYTES = 4096
# A coarse ctime clock can give a rewrite the timestamp it already had. Sources
# changed this recently are hashed but not recorded for other processes.
_SOURCE_DIGEST_SETTLE_NS = 2_000_000_000

_RuntimeLocatorState: TypeAlias = Literal["missing", "valid", "invalid"]
_RuntimeLocatorDiagnostic: TypeAlias = Literal[
    "runtime_locator_unreadable",
    "runtime_locator_schema_mismatch",
    "runtime_locator_fingerprint_mismatch",
    "runtime_locator_fields_invalid",
    "runtime_locator_pid_not_alive",
]


class _RuntimeInfo(TypedDict):
    schema_version: str
    fingerprint: str
    pid: int
    host: str
    port: int
    token: str
    runtime_identity: NotRequired[object]


def source_number_tokens(value: object) -> list[list[object]]:
    """Carry numeric IO spellings that a JavaScript number cannot distinguish.

    This is transport metadata.
    Fractional doubles and safe integers keep their existing object transport.
    """
    tokens: list[list[object]] = []

    def visit(item: object, path: list[object]) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Context source numbers must be finite")
        if (isinstance(item, float) and item.is_integer()
                or type(item) is int and abs(item) > 2**53 - 1):
            tokens.append([path, json.dumps(item, allow_nan=False)])
        elif isinstance(item, Mapping):
            for key, child in item.items():
                visit(child, [*path, key])
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, [*path, index])

    visit(value, [])
    return tokens


class _RuntimeSourceChanged(RuntimeError):
    def __init__(self, snapshot: _RuntimeSourceSnapshot) -> None:
        super().__init__("runtime source changed while hashing")
        self.snapshot = snapshot


@dataclass(frozen=True)
class _RuntimeRevision:
    fingerprint: str


class _RequestRuntimeRevision:
    def __init__(self) -> None:
        self._lock = Lock()
        self._revision: _RuntimeRevision | None = None
        self._scope_count = 1
        self._closed = False
        # The Todo context handler is a pure lens over supplied records. Share
        # its exact results only inside this already revision-pinned request;
        # authority reads, clocks, mutation and settlement never enter here.
        self._todo_context: dict[bytes, bytes] = {}
        self._todo_context_bytes = 0

    def try_join(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._scope_count += 1
            return True

    def resolve(self) -> _RuntimeRevision:
        with self._lock:
            if not self._closed:
                if self._revision is None:
                    self._revision = _RuntimeRevision(_runtime_fingerprint())
                return self._revision
        return _RuntimeRevision(_runtime_fingerprint())

    def leave(self) -> None:
        with self._lock:
            self._scope_count -= 1
            if self._scope_count == 0:
                self._closed = True
                self._revision = None
                self._todo_context.clear()
                self._todo_context_bytes = 0

    def todo_context_result(self, key: bytes) -> bytes | None:
        with self._lock:
            return None if self._closed else self._todo_context.get(key)

    def remember_todo_context(self, key: bytes, result: bytes) -> None:
        with self._lock:
            size = len(key) + len(result)
            if (not self._closed and key not in self._todo_context
                    and self._todo_context_bytes + size <= MAX_REQUEST_BYTES):
                self._todo_context[key] = result
                self._todo_context_bytes += size


_REQUEST_RUNTIME_REVISION: ContextVar[_RequestRuntimeRevision | None] = ContextVar(
    "loopx_effect_runtime_request_revision", default=None,
)


def todo_projection_request_identity() -> object | None:
    """Bind a source codec to its current logical request, never to a TTL."""
    state = _REQUEST_RUNTIME_REVISION.get()
    return state if state is not None and not state._closed else None


def _control_plane_root() -> Path:
    return Path(__file__).resolve().parent


def _scan_runtime_source_snapshot(root: Path) -> _RuntimeSourceSnapshot:
    snapshot: list[tuple[str, int, int, int]] = []
    for directory, child_directories, filenames in os.walk(root):
        child_directories.sort()
        relative_directory = os.path.relpath(directory, root)
        relative_prefix = (
            "" if relative_directory == "." else relative_directory.replace(os.sep, "/") + "/"
        )
        for filename in sorted(filenames):
            relative = relative_prefix + filename
            dot = filename.rfind(".")
            if ((dot <= 0 or filename[dot:] not in _RUNTIME_SOURCE_SUFFIXES)
                    and relative not in _RUNTIME_SOURCE_PATHS):
                continue
            source = Path(directory, filename)
            try:
                metadata = source.stat()
            except OSError:
                # Preserve pathlib's missing-file and platform-specific error policy.
                if not source.is_file():
                    continue
                raise
            except ValueError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                snapshot.append((relative, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size))
    return tuple(sorted(snapshot))


def _runtime_source_snapshot(root: Path | None = None) -> _RuntimeSourceSnapshot:
    """Return cheap metadata that invalidates the packaged-source hash."""
    return _scan_runtime_source_snapshot((root or _control_plane_root()).resolve())


def _runtime_source_files(root: Path | None = None) -> tuple[str, ...]:
    """Return the packaged source boundary owned by the managed runtime."""
    return tuple(relative for relative, *_metadata in _runtime_source_snapshot(root))


def _source_digest_location(
    root: str,
    snapshot: _RuntimeSourceSnapshot,
) -> tuple[Path, str] | None:
    """Name one private digest per release and interpreter, keyed by metadata.

    Only POSIX ctime cannot be set by a writer, so other hosts keep full reads.
    """
    if os.name != "posix":
        return None
    try:
        directory = _runtime_dir()
    except OSError:
        return None
    identity = hashlib.sha256()
    for part in (_SOURCE_DIGEST_SCHEMA_VERSION, sys.executable, str(sys.version_info[:3])):
        identity.update(part.encode("utf-8") + b"\0")
    identity.update(os.fsencode(os.path.normcase(root)) + b"\0")
    path = directory / f"source-digest-{identity.hexdigest()[:32]}.json"
    identity.update(json.dumps(snapshot, separators=(",", ":")).encode("ascii"))
    return path, identity.hexdigest()


def _private_directory(path: Path) -> bool:
    return _private_directory_identity(path) is not None


def _read_source_digest(path: Path, key: str) -> str | None:
    if not _private_directory(path.parent):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb") as file:
            metadata = os.fstat(file.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                return None
            payload = json.loads(file.read(_SOURCE_DIGEST_MAX_BYTES + 1))
    except (OSError, ValueError, RecursionError):
        return None
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != _SOURCE_DIGEST_SCHEMA_VERSION
            or payload.get("key") != key):
        return None
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, str) or BARE_SHA256_PATTERN.fullmatch(fingerprint) is None:
        return None
    return fingerprint


def _source_snapshot_is_settled(snapshot: _RuntimeSourceSnapshot) -> bool:
    settled_before = time.time_ns() - _SOURCE_DIGEST_SETTLE_NS
    return all(max(mtime, ctime) <= settled_before for _name, mtime, ctime, _size in snapshot)


def _write_source_digest(
    path: Path,
    key: str,
    fingerprint: str,
    snapshot: _RuntimeSourceSnapshot,
) -> None:
    """Best-effort publication into an existing private runtime namespace."""
    if not _source_snapshot_is_settled(snapshot):
        return
    if not _private_directory(path.parent):
        return
    data = json.dumps({
        "schema_version": _SOURCE_DIGEST_SCHEMA_VERSION,
        "key": key,
        "fingerprint": fingerprint,
    }, separators=(",", ":")).encode("ascii")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except OSError:
        return
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        os.replace(temporary, path)
    except OSError:
        with suppress(OSError):
            temporary.unlink()


def _require_unchanged_snapshot(root: Path, snapshot: _RuntimeSourceSnapshot) -> None:
    current_snapshot = _runtime_source_snapshot(root)
    if current_snapshot != snapshot:
        raise _RuntimeSourceChanged(current_snapshot)


def _hash_runtime_source_snapshot(
    root: str,
    snapshot: _RuntimeSourceSnapshot,
    *,
    use_digest_memo: bool,
) -> str:
    # A fresh CLI process would otherwise open every source; on hosts that scan
    # each open this dominates short commands. The record is only a memo.
    source_root = Path(root)
    location = _source_digest_location(root, snapshot) if use_digest_memo else None
    if location is not None:
        recorded = _read_source_digest(*location)
        if recorded is not None:
            # A hit adopts the record only under the full hash's freshness check.
            _require_unchanged_snapshot(source_root, snapshot)
            return recorded
    digest = hashlib.sha256()
    # The daemon's fixed Python adapter context must follow the interpreter
    # selected by this caller, even when two environments share source files.
    digest.update(sys.executable.encode("utf-8"))
    digest.update(str(sys.version_info[:3]).encode("ascii"))
    # Lazy imports and Python bridges resolve from this physical release. Equal
    # bytes in another checkout must not reuse a loader whose release may retire.
    # Resolved aliases still share one owner; the digest covers every source byte.
    digest.update(b"loopx_effect_runtime_source_instance_v1\0")
    digest.update(os.fsencode(os.path.normcase(root)))
    digest.update(b"\0")
    paths = (source_root / relative for relative, *_metadata in snapshot)
    # A fresh CLI hashes these small package files; starting a pool costs more
    # than sequential local reads. Keep every byte and the freshness recheck.
    with closing(iter_binary_file_reads(paths, max_workers=1)) as reads:
        for (relative, *_metadata), read in zip(snapshot, reads, strict=True):
            if read.error is not None:
                raise read.error
            assert read.data is not None
            digest.update(relative.encode("utf-8"))
            digest.update(read.data)
    _require_unchanged_snapshot(source_root, snapshot)
    fingerprint = digest.hexdigest()
    if location is not None:
        _write_source_digest(*location, fingerprint, snapshot)
    return fingerprint


@lru_cache(maxsize=8)
def _runtime_fingerprint_for_snapshot(root: str, snapshot: _RuntimeSourceSnapshot) -> str:
    return _hash_runtime_source_snapshot(root, snapshot, use_digest_memo=True)


def _runtime_fingerprint() -> str:
    root = _control_plane_root()
    resolved_root = os.fspath(root.resolve())
    snapshot: _RuntimeSourceSnapshot | None = None
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            if snapshot is None:
                snapshot = _runtime_source_snapshot(root)
            if not _source_snapshot_is_settled(snapshot):
                # Multiple equal-size writes can share a filesystem clock tick.
                return _hash_runtime_source_snapshot(resolved_root, snapshot, use_digest_memo=False)
            return _runtime_fingerprint_for_snapshot(
                resolved_root,
                snapshot,
            )
        except _RuntimeSourceChanged as exc:
            last_error = exc
            snapshot = exc.snapshot
        except FileNotFoundError as exc:
            last_error = exc
            snapshot = None
    raise EffectRuntimeStartupError(
        "TypeScript Effect runtime source topology did not stabilize",
        diagnostic_code="packaged_runtime_source_unstable",
    ) from last_error


@contextmanager
def effect_runtime_request_scope() -> Iterator[None]:
    """Pin one managed runtime revision for a logical request."""
    current = _REQUEST_RUNTIME_REVISION.get()
    if current is not None and current.try_join():
        try:
            yield
        finally:
            current.leave()
        return
    state = _RequestRuntimeRevision()
    token = _REQUEST_RUNTIME_REVISION.set(state)
    try:
        yield
    finally:
        state.leave()
        _REQUEST_RUNTIME_REVISION.reset(token)


def _runtime_fingerprint_for_request() -> str:
    state = _REQUEST_RUNTIME_REVISION.get()
    return _runtime_fingerprint() if state is None else state.resolve().fingerprint


def _runtime_dir() -> Path:
    owner = str(getattr(os, "getuid", lambda: Path.home())())
    suffix = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"loopx-effect-runtime-{suffix}"


def _runtime_info_path(fingerprint: str) -> Path:
    return _runtime_dir() / f"runtime-{fingerprint[:16]}.json"


def _runtime_server_path() -> Path:
    return _control_plane_root() / "effect_runtime_server.ts"


def _node_executable() -> str:
    probe = _probe_node(timeout=STARTUP_READY_TIMEOUT_SECONDS)
    if not probe.ready:
        if probe.diagnostic_code == "runtime_host_permission_denied":
            raise EffectRuntimeHostPermissionError()
        raise EffectRuntimeNodeProbeError(
            probe.failure_message,
            diagnostic_code=str(probe.diagnostic_code),
        )
    assert probe.executable is not None
    return probe.executable


def _runtime_environment(token: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["LOOPX_EFFECT_RUNTIME_TOKEN"] = token
    # Fixed launch context. Snapshot ACL checks on Windows also use this interpreter.
    environment["LOOPX_EFFECT_RUNTIME_PYTHON"] = sys.executable
    environment["LOOPX_EFFECT_RUNTIME_PYTHON_BIN"] = sys.executable
    return environment


def _uses_stdio_runtime() -> bool:
    transport = os.environ.get("LOOPX_EFFECT_RUNTIME_TRANSPORT", "tcp")
    if transport == "tcp":
        return False
    if transport != "stdio":
        raise EffectRuntimeStartupError(
            "Set LOOPX_EFFECT_RUNTIME_TRANSPORT to stdio or tcp",
            diagnostic_code="invalid_runtime_transport",
        )
    if os.name != "posix":
        raise EffectRuntimeStartupError(
            "The Effect runtime stdio transport requires POSIX pipes. Set "
            "LOOPX_EFFECT_RUNTIME_TRANSPORT to tcp on this platform",
            diagnostic_code="unsupported_runtime_transport",
        )
    return True


def _pid_is_alive(value: object) -> bool:
    if not process_is_alive(value):
        return False
    # Unreaped Linux zombies still satisfy kill(pid, 0), yet an all-zombie
    # thread group has terminated and must not poison runtime lock/locator recovery.
    if (sys.platform == "linux" and isinstance(value, int) and not isinstance(value, bool)
            and value > 0 and _linux_thread_group_is_zombie(value)):
        return False
    return True


def _recorded_pid_alive(value: object) -> bool | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > _RUNTIME_PID_MAX:
        return None
    try:
        return _pid_is_alive(value)
    except OverflowError:
        return None


def _reap_exited_runtime_child(info: Mapping[str, object] | None) -> bool:
    """Let a dead directly spawned child fail the next non-signaling probe.

    A stopped child can remain a zombie until its Python parent reaps it;
    ``kill(pid, 0)`` still reports that zombie as present. Returns ``True``
    only when POSIX ``waitpid`` confirms ``ECHILD`` (the target is not a child
    of this process), and ``False`` otherwise.
    """
    if os.name == "nt" or not isinstance(info, Mapping):
        return False
    pid = info.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    except OSError as exc:
        return exc.errno == errno.ECHILD
    return False


def _linux_thread_group_is_zombie(pid: int) -> bool:
    """Return True only when every Linux thread in ``pid`` has exited to ``Z``.

    Detached runtimes spawned by short-lived CLI subprocesses reparent to
    container PID 1 or a subreaper that may not reap orphans while running.
    A zombie thread-group leader with any live worker thread returns False.
    """
    if sys.platform != "linux":
        return False
    task_dir = Path("/proc") / str(pid) / "task"
    try:
        tids = os.listdir(task_dir)
        if not tids:
            return False
        for tid in tids:
            stat_text = (task_dir / tid / "stat").read_text(encoding="utf-8")
            if stat_text.rsplit(")", 1)[1].split()[0] != "Z":
                return False
        return set(os.listdir(task_dir)) <= set(tids)
    except (OSError, IndexError, ValueError):
        return False


def _start_lock_holder_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if value > 0 else None


@dataclass(frozen=True)
class _RuntimeLocatorRead:
    info: _RuntimeInfo | None
    present: bool
    diagnostic_code: _RuntimeLocatorDiagnostic | None = None
    recorded_pid_alive: bool | None = None
    raw_payload: Mapping[str, object] | None = None


def _inspect_info(path: Path, *, fingerprint: str | None) -> _RuntimeLocatorRead:
    for attempt in range(RUNTIME_LOCATOR_PERMISSION_RETRIES + 1):
        try:
            raw = path.read_text(encoding="utf-8")
            break
        except PermissionError as exc:
            # On Windows, opening a directory as a file reports access denied.
            # Defer that occupied-locator case to the managed server, which can
            # publish the shared filesystem diagnostic through its startup envelope.
            # A short-lived denial can also overlap locator publication/retirement.
            if path.is_dir() and not path.is_symlink():
                return _RuntimeLocatorRead(info=None, present=True, diagnostic_code="runtime_locator_unreadable")
            if attempt == RUNTIME_LOCATOR_PERMISSION_RETRIES:
                # Keep symlinks and persistently unreadable metadata on the
                # host-permission path after the bounded retry window.
                raise EffectRuntimeHostPermissionError() from exc
            time.sleep(STARTUP_POLL_SECONDS)
        except FileNotFoundError:
            return _RuntimeLocatorRead(info=None, present=False)
        except (UnicodeError, OSError):
            return _RuntimeLocatorRead(info=None, present=True, diagnostic_code="runtime_locator_unreadable")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _RuntimeLocatorRead(info=None, present=True, diagnostic_code="runtime_locator_unreadable")
    if not isinstance(payload, dict):
        return _RuntimeLocatorRead(info=None, present=True, diagnostic_code="runtime_locator_unreadable")
    recorded_pid_alive = _recorded_pid_alive(payload.get("pid"))

    def invalid(code: _RuntimeLocatorDiagnostic) -> _RuntimeLocatorRead:
        return _RuntimeLocatorRead(
            info=None,
            present=True,
            diagnostic_code=code,
            recorded_pid_alive=recorded_pid_alive,
            raw_payload=payload,
        )

    if payload.get("schema_version") != EFFECT_RUNTIME_INFO_SCHEMA_VERSION:
        return invalid("runtime_locator_schema_mismatch")
    raw_fingerprint = payload.get("fingerprint")
    if (
        (fingerprint is not None and raw_fingerprint != fingerprint)
        or (fingerprint is None and (not isinstance(raw_fingerprint, str) or not BARE_SHA256_PATTERN.fullmatch(raw_fingerprint)))
    ):
        return invalid("runtime_locator_fingerprint_mismatch")
    if payload.get("host") != "127.0.0.1" or not isinstance(payload.get("port"), int) or not isinstance(payload.get("token"), str):
        return invalid("runtime_locator_fields_invalid")
    if recorded_pid_alive is not True:
        return invalid("runtime_locator_pid_not_alive")
    return _RuntimeLocatorRead(info=cast(_RuntimeInfo, payload), present=True, recorded_pid_alive=True)


def _read_info(path: Path, *, fingerprint: str | None) -> _RuntimeInfo | None:
    return _inspect_info(path, fingerprint=fingerprint).info


def _wait_for_runtime_locator_turnover(
    path: Path,
    *,
    fingerprint: str,
    observed: Mapping[str, object] | None,
    timeout: float,
) -> None:
    """Give a retiring runtime time to remove or replace its locator."""
    if not isinstance(observed, Mapping):
        return
    token = observed.get("token")
    deadline = time.monotonic() + min(timeout, RUNTIME_RETRY_SETTLE_SECONDS)
    while time.monotonic() < deadline:
        current = _read_info(path, fingerprint=fingerprint)
        if current is None or current.get("token") != token:
            return
        time.sleep(STARTUP_POLL_SECONDS)


def _serving_token(path: Path) -> tuple[bool, str | None]:
    """Report whether a runtime is still publishing itself at ``path``.

    The managed runtime removes its info file as part of its shutdown
    handshake. Its mutation lock and cleanup files must also retire before
    namespace cleanup.
    A readable replacement token proves this runtime no longer serves. The pid is not:
    an exited runtime whose parent has not reaped it still answers a liveness
    probe, which would otherwise report a completed restart as pending.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, None
    except (json.JSONDecodeError, OSError):
        # keep waiting until the deadline instead of claiming a restart that did not happen.
        return True, None
    if not isinstance(payload, dict):
        return True, None
    token = payload.get("token")
    return True, token if isinstance(token, str) else None


def _locator_retirement_pending(info_path: Path) -> bool:
    """Observe the existing TS lock namespace, including its cleanup files."""
    lock_name = info_path.name + ".ts-effect.lock"
    try:
        return any(
            path.name == lock_name
            or path.name.startswith(lock_name + ".claim.")
            or path.name.startswith(lock_name + ".released.")
            for path in info_path.parent.iterdir()
        )
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable directory cannot prove that retirement finished.
        return True


def _restart_result(
    status: str,
    *,
    stopped: bool,
    identity: dict[str, object] | None,
    info_path: Path | None,
    transport: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": EFFECT_RUNTIME_RESTART_SCHEMA_VERSION,
        "status": status,
        "stopped": stopped,
        "previous_runtime_identity": identity,
        "info_path": str(info_path) if info_path is not None else None,
    }
    if transport is not None:
        payload["transport"] = transport
    return payload


def restart_effect_runtime(*, timeout: float = 5.0, wait_for_exit: bool = False) -> dict[str, object]:
    """Stop the managed runtime serving this source revision.

    The replacement runtime resolves Node from the PATH of the next request, so
    this is the operator action after installing a qualified Node: a runtime
    started earlier keeps its own Node until it exits. ``wait_for_exit`` is for
    disposable namespace owners: serving retirement alone permits a delayed
    exit-time cache write, whereas filesystem cleanup requires observed exit.
    """
    if _uses_stdio_runtime():
        close_stale_cleanup_sweeps()
        return _restart_stdio_runtime(timeout)
    fingerprint = _runtime_fingerprint()
    info_path = _runtime_info_path(fingerprint)
    info = _runtime_info_for_request(info_path, fingerprint=fingerprint)
    return _stop_effect_runtime(info_path, info, timeout=timeout, wait_for_process_exit=wait_for_exit)


def _stop_effect_runtime(
    info_path: Path,
    info: Mapping[str, object] | None,
    *,
    timeout: float,
    wait_for_process_exit: bool = False,
) -> dict[str, object]:
    """Transport shutdown for a validated locator, optionally awaiting OS exit.

    Operator restart keeps its existing serving-locator turnover semantics.
    An isolated fixture instead needs the original process to stop writing
    before deleting its namespace. TS still owns admission, drain and retirement.
    """
    identity = runtime_identity_from_info(info)
    if info is None:
        close_stale_cleanup_sweeps(runtime_dir=info_path.parent)
        return _restart_result("not_running", stopped=False, identity=identity, info_path=info_path)
    pid, serving_token = info.get("pid"), info.get("token")
    # A runtime that is already closing must still be reported as pending
    # rather than as a failed restart.
    with suppress(EffectRuntimeRejected, EffectRuntimeRemoteError, EffectRuntimeResponseAmbiguous, OSError):
        _request_with_info(info, request_id=uuid.uuid4().hex, method="runtime.shutdown", params={}, timeout=timeout)
    deadline = time.monotonic() + timeout
    stopped = False
    while time.monotonic() < deadline:
        if wait_for_process_exit:
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                break
            not_caller_child = _reap_exited_runtime_child(info)
            if not _pid_is_alive(pid) or (not_caller_child and _linux_thread_group_is_zombie(pid)):
                _reap_exited_runtime_child(info)
                stopped = True
                break
            # A zombie leader can still have threads writing into the namespace.
            time.sleep(0.05)
            continue
        published, published_token = _serving_token(info_path)
        if ((published and published_token is not None and published_token != serving_token)
                or (not published and not _locator_retirement_pending(info_path))
                or not _pid_is_alive(pid)):
            stopped = True
            break
        time.sleep(0.05)
    close_stale_cleanup_sweeps(runtime_dir=info_path.parent)
    return _restart_result(
        "stopped" if stopped else "shutdown_pending", stopped=stopped, identity=identity, info_path=info_path,
    )


def _encode_rpc_json(value: Mapping[str, object]) -> bytes:
    # Count and transmit UTF-8 bytes; lone surrogates retain valid JSON escapes.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="backslashreplace")


def _tcp_response_frame(
    info: Mapping[str, object],
    encoded: bytes,
    *,
    method: str,
    timeout: float,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    port_value = info["port"]
    if not isinstance(port_value, (int, str, bytes)):
        raise TypeError(f"invalid runtime port: {type(port_value).__name__}")
    try:
        connection = socket.create_connection((str(info["host"]), int(port_value)), timeout=timeout)
    except PermissionError as exc:
        raise EffectRuntimeHostPermissionError() from exc
    with connection:
        try:
            connection.settimeout(timeout)
            # sendall can fail after dispatch. From here a retry needs a receipt.
            connection.sendall(encoded)
            while chunk := connection.recv(64 * 1024):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise RuntimeError("TypeScript Effect runtime response is oversized")
                if b"\n" in chunk:
                    break
        except (OSError, RuntimeError) as exc:
            raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from exc
    return b"".join(chunks)


def _prepare_private_snapshot_file(path: Path, data: bytes | None = None) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    if data is None:
        try:
            protect_private_path(path)
        finally:
            os.close(descriptor)
        return
    with os.fdopen(descriptor, "wb") as file:
        protect_private_path(path)
        file.write(data)


def _request_with_info(
    info: Mapping[str, object],
    *,
    request_id: str,
    method: str,
    params: Mapping[str, object],
    timeout: float,
    large_local_snapshot: bool = False,
    pipe: EffectRuntimeStdio | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "schema_version": EFFECT_RUNTIME_REQUEST_SCHEMA_VERSION,
        "token": info["token"],
        "request_id": request_id,
        "method": method,
        "params": dict(params),
    }
    with ExitStack() as stack:
        response_sink: Path | None = None
        if large_local_snapshot:
            if method not in LOCAL_SNAPSHOT_METHODS:
                raise EffectRuntimeRejected(
                    "local snapshot transport is unavailable for this method",
                    diagnostic_code="invalid_request",
                )
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="loopx-effect-")))
            protect_private_path(directory, directory=True)
            response_sink = directory / "response.json"
            _prepare_private_snapshot_file(response_sink)
            request["response_sink"] = str(response_sink)
            encoded = _encode_rpc_json(request) + b"\n"
            if len(encoded) > MAX_REQUEST_BYTES:
                params_bytes = _encode_rpc_json(dict(params))
                if len(params_bytes) > MAX_LOCAL_SNAPSHOT_BYTES:
                    raise EffectRuntimeRejected(
                        "TypeScript Effect runtime local snapshot is oversized",
                        diagnostic_code="request_too_large",
                    )
                params_path = directory / "params.json"
                _prepare_private_snapshot_file(params_path, params_bytes)
                request.pop("params")
                request["params_ref"] = {
                    "schema_version": "loopx_effect_runtime_snapshot_v0",
                    "path": str(params_path),
                    "byte_count": len(params_bytes),
                    "sha256": hashlib.sha256(params_bytes).hexdigest(),
                }
                encoded = _encode_rpc_json(request) + b"\n"
        else:
            encoded = _encode_rpc_json(request) + b"\n"
        if len(encoded) > MAX_REQUEST_BYTES:
            raise EffectRuntimeRejected(
                "TypeScript Effect runtime request is oversized", diagnostic_code="request_too_large",
            )
        if pipe is None:
            frame = _tcp_response_frame(info, encoded, method=method, timeout=timeout)
        else:
            try:
                frame = pipe.exchange(encoded, timeout, MAX_RESPONSE_BYTES)
            except (OSError, EOFError, ValueError) as exc:
                raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from exc
        try:
            response: object = json.loads(frame.split(b"\n", 1)[0])
        except (ValueError, RecursionError):
            # Decoder limits can fail after commit; they cannot authorize a retry.
            raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from None
        if isinstance(response, dict) and "result_ref" in response:
            response = _read_local_snapshot_response(
                response, response_sink, method=method, request_id=request_id, timeout=timeout,
            )
    if (not isinstance(response, dict)
            or response.get("schema_version") != EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION
            or response.get("request_id") != request_id):
        raise EffectRuntimeResponseAmbiguous(method, timeout=timeout)
    if response.get("ok") is not True:
        raise _remote_runtime_error(response.get("error"))
    return cast(dict[str, object], response)


def _recover_runtime_info(
    path: Path, locator: _RuntimeLocatorRead, *, fingerprint: str,
) -> _RuntimeInfo | None:
    """Reuse a live daemon when its locator bytes are damaged but authenticated."""
    # An invalid locator can still be an identity claim when it retains the
    # loopback endpoint and token. The authenticated ping below is the
    # authority; the damaged bytes are never trusted by themselves.
    candidate: Mapping[str, object] | None = locator.info if locator.info is not None else (
        locator.raw_payload if locator.present else None
    )
    if (not isinstance(candidate, Mapping)
            or candidate.get("host") != "127.0.0.1"
            or not isinstance(candidate.get("port"), int)
            or not isinstance(candidate.get("token"), str)
            or locator.recorded_pid_alive is not True):
        return None
    try:
        response = _request_with_info(
            candidate, request_id=str(uuid.uuid4()), method="runtime.ping",
            params={}, timeout=RUNTIME_LOCATOR_RECOVERY_TIMEOUT_SECONDS,
        )
    except EffectRuntimeHostPermissionError:
        raise
    except (OSError, RuntimeError):
        return None
    result = response.get("result")
    if (not isinstance(result, Mapping) or result.get("ready") is not True
            or result.get("pid") != candidate.get("pid") or result.get("fingerprint") != fingerprint):
        return None
    published = result.get("info_path")
    if isinstance(published, str):
        try:
            if os.path.normcase(str(Path(published).resolve())) != os.path.normcase(str(path.resolve())):
                return None
        except OSError:
            return None
    return cast(_RuntimeInfo, {**candidate, "schema_version": EFFECT_RUNTIME_INFO_SCHEMA_VERSION, "fingerprint": fingerprint})


def _runtime_info_for_request(path: Path, *, fingerprint: str) -> _RuntimeInfo | None:
    info = _read_info(path, fingerprint=fingerprint)
    if info is not None:
        return info
    _cleanup_stale_runtime_descriptors(path.parent, exclude_path=path)
    return _recover_runtime_info(path, _inspect_info(path, fingerprint=fingerprint), fingerprint=fingerprint)


def _runtime_descriptor_lock_file_active(
    lock_path: Path,
    *,
    now: float,
    grace_seconds: float,
    json_owner: bool,
) -> bool:
    try:
        st = lock_path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not stat.S_ISREG(st.st_mode):
        return True
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return True
    if now - st.st_mtime < grace_seconds:
        return True
    if not json_owner:
        holder_pid = _start_lock_holder_pid(lock_path)
        return holder_pid is None or _recorded_pid_alive(holder_pid) is not False
    try:
        raw = lock_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return _recorded_pid_alive(payload.get("pid")) is not False


def _runtime_descriptor_has_active_lock(
    info_path: Path,
    *,
    seen_lock_names: set[str],
    now: float,
    grace_seconds: float,
) -> bool:
    name = info_path.name
    ts_lock_prefix = f"{name}.ts-effect.lock"
    if any(
        entry_name.startswith(ts_lock_prefix + ".claim.")
        or entry_name.startswith(ts_lock_prefix + ".released.")
        or entry_name.startswith(ts_lock_prefix + ".stale.")
        for entry_name in seen_lock_names
    ):
        return True
    for suffix_path in (
        info_path.with_name(f"{ts_lock_prefix}.claim"),
        info_path.with_name(f"{ts_lock_prefix}.released"),
    ):
        try:
            suffix_path.lstat()
            return True
        except FileNotFoundError:
            pass
        except OSError:
            return True
    ts_lock = info_path.with_name(ts_lock_prefix)
    if _runtime_descriptor_lock_file_active(
        ts_lock, now=now, grace_seconds=grace_seconds, json_owner=True,
    ):
        return True
    suffix = name[len("runtime-"):-len(".json")]
    start_lock = info_path.with_name(f"start-{suffix}.lock")
    return _runtime_descriptor_lock_file_active(
        start_lock, now=now, grace_seconds=grace_seconds, json_owner=False,
    )


def _cleanup_cursor_key(runtime_dir: Path) -> str:
    try:
        return os.path.normcase(str(runtime_dir.resolve()))
    except OSError:
        return os.path.normcase(str(runtime_dir))


def _private_directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = os.lstat(path)
    except OSError:
        return None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        or bool(metadata.st_mode & 0o077)
    ):
        return None
    return metadata.st_dev, metadata.st_ino


def _close_sweep_iterator(sweep: _StaleCleanupSweep) -> None:
    sweep.iterator = None
    cm = sweep.scandir_cm
    sweep.scandir_cm = None
    if cm is None:
        return
    exit_fn = getattr(cm, "__exit__", None)
    if callable(exit_fn):
        with suppress(OSError, RuntimeError):
            exit_fn(None, None, None)


def _close_sweeps(sweeps: Iterable[_StaleCleanupSweep]) -> None:
    for sweep in sweeps:
        _close_sweep_iterator(sweep)


def close_stale_cleanup_sweeps(*, runtime_dir: Path | None = None) -> None:
    """Close cached directory sweeps held for bounded stale-descriptor cleanup."""
    global _stale_cleanup_generation
    to_close: list[_StaleCleanupSweep] = []
    with _stale_cleanup_sweeps_lock:
        _stale_cleanup_generation += 1
        if runtime_dir is None:
            to_close.extend(_stale_cleanup_sweeps.values())
            _stale_cleanup_sweeps.clear()
        else:
            sweep = _stale_cleanup_sweeps.pop(_cleanup_cursor_key(runtime_dir), None)
            if sweep is not None:
                to_close.append(sweep)
    _close_sweeps(to_close)


def _reset_stale_cleanup_sweeps_after_fork() -> None:
    global _stale_cleanup_sweeps_lock, _stale_cleanup_generation
    _stale_cleanup_sweeps_lock = Lock()
    _stale_cleanup_generation += 1
    sweeps = list(_stale_cleanup_sweeps.values())
    _stale_cleanup_sweeps.clear()
    _close_sweeps(sweeps)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_stale_cleanup_sweeps_after_fork)
atexit.register(close_stale_cleanup_sweeps)


# Only evict *other* dirs when idle-closing: closing and reopening os.scandir
# for the active key resets the cursor and stalls >=300s low-frequency misses.
def _checkout_stale_cleanup_sweep(
    key: str, *, dir_identity: tuple[int, int], scandir_fn: object,
) -> tuple[_StaleCleanupSweep | None, int]:
    now_mono = time.monotonic()
    to_close: list[_StaleCleanupSweep] = []
    with _stale_cleanup_sweeps_lock:
        expired_keys = [
            k for k, s in _stale_cleanup_sweeps.items()
            if k != key and now_mono - s.last_used_monotonic >= _STALE_CLEANUP_SWEEP_IDLE_SECONDS
        ]
        for expired_key in expired_keys:
            to_close.append(_stale_cleanup_sweeps.pop(expired_key))
        sweep = _stale_cleanup_sweeps.pop(key, None)
        if sweep is not None and (sweep.dir_identity != dir_identity or sweep.scandir_fn is not scandir_fn):
            to_close.append(sweep)
            sweep = None
        generation = _stale_cleanup_generation
    _close_sweeps(to_close)
    return sweep, generation


def _open_stale_cleanup_sweep(
    runtime_dir: Path, *, dir_identity: tuple[int, int], scandir_fn: object,
) -> _StaleCleanupSweep | None:
    try:
        cm = os.scandir(runtime_dir)
    except OSError:
        return None
    try:
        entries = cm.__enter__()
        iterator = iter(entries)
    except OSError:
        with suppress(OSError, RuntimeError):
            cm.__exit__(None, None, None)
        return None
    return _StaleCleanupSweep(
        scandir_cm=cm, iterator=iterator, pending_candidates=deque(), seen_lock_names=set(),
        dir_identity=dir_identity, scandir_fn=scandir_fn, last_used_monotonic=time.monotonic(),
    )


def _advance_stale_cleanup_sweep(
    sweep: _StaleCleanupSweep, *, exclude_name: str | None, max_inspect: int, max_scan_steps: int,
) -> bool:
    window_steps = 0
    while (
        sweep.iterator is not None
        and len(sweep.pending_candidates) < max_inspect
        and window_steps < max_scan_steps
    ):
        try:
            entry = next(sweep.iterator)
        except StopIteration:
            _close_sweep_iterator(sweep)
            break
        except OSError:
            _close_sweep_iterator(sweep)
            return False
        window_steps += 1
        entry_name = entry.name
        if (
            len(sweep.seen_lock_names) < _STALE_CLEANUP_SEEN_LOCK_LIMIT
            and any(
                m in entry_name
                for m in (".ts-effect.lock.claim.", ".ts-effect.lock.released.", ".ts-effect.lock.stale.")
            )
        ):
            sweep.seen_lock_names.add(entry_name)
        if entry_name.startswith("runtime-") and entry_name.endswith(".json") and entry_name != exclude_name:
            sweep.pending_candidates.append(entry)
    return True


def _checkin_stale_cleanup_sweep(key: str, sweep: _StaleCleanupSweep, *, generation: int) -> None:
    if sweep.iterator is None and not sweep.pending_candidates:
        _close_sweep_iterator(sweep)
        return
    to_close: list[_StaleCleanupSweep] = []
    with _stale_cleanup_sweeps_lock:
        if generation != _stale_cleanup_generation or key in _stale_cleanup_sweeps:
            to_close.append(sweep)
        else:
            while len(_stale_cleanup_sweeps) >= _STALE_CLEANUP_SWEEP_LIMIT:
                to_close.append(_stale_cleanup_sweeps.pop(next(iter(_stale_cleanup_sweeps))))
            sweep.last_used_monotonic = time.monotonic()
            _stale_cleanup_sweeps[key] = sweep
    _close_sweeps(to_close)


def _try_unlink_stale_descriptor(
    runtime_dir: Path,
    entry: os.DirEntry[str],
    *,
    seen_lock_names: set[str],
    now: float,
    grace_seconds: float,
    no_follow: int,
) -> bool:
    candidate = runtime_dir / entry.name
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return False
    if now - st.st_mtime < grace_seconds:
        return False
    if _runtime_descriptor_has_active_lock(
        candidate, seen_lock_names=seen_lock_names, now=now, grace_seconds=grace_seconds,
    ):
        return False
    try:
        fd = os.open(candidate, os.O_RDONLY | no_follow)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "rb") as file:
            fst = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(fst.st_mode)
                or fst.st_dev != st.st_dev
                or fst.st_ino != st.st_ino
                or (hasattr(os, "getuid") and fst.st_uid != os.getuid())
            ):
                return False
            raw = file.read(_STALE_RUNTIME_DESCRIPTOR_MAX_BYTES + 1)
    except OSError:
        return False
    if len(raw) > _STALE_RUNTIME_DESCRIPTOR_MAX_BYTES:
        return False
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError):
        return False
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != EFFECT_RUNTIME_INFO_SCHEMA_VERSION
        or not isinstance(payload.get("fingerprint"), str)
    ):
        return False
    if _recorded_pid_alive(payload.get("pid")) is not False:
        return False
    try:
        current_st = candidate.lstat()
    except OSError:
        return False
    if (
        current_st.st_dev != st.st_dev
        or current_st.st_ino != st.st_ino
        or current_st.st_mtime_ns != st.st_mtime_ns
    ):
        return False
    if _runtime_descriptor_has_active_lock(
        candidate, seen_lock_names=seen_lock_names, now=time.time(), grace_seconds=grace_seconds,
    ):
        return False
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _cleanup_stale_runtime_descriptors(
    runtime_dir: Path,
    *,
    exclude_path: Path | None = None,
    grace_seconds: float = _STALE_RUNTIME_DESCRIPTOR_GRACE_SECONDS,
    max_inspect: int = _STALE_RUNTIME_DESCRIPTOR_MAX_INSPECT,
    max_removals: int = _STALE_RUNTIME_DESCRIPTOR_MAX_REMOVALS,
) -> int:
    """Opportunistically unlink bounded dead-PID runtime-*.json descriptors."""
    if max_inspect <= 0 or max_removals <= 0:
        return 0
    dir_identity = _private_directory_identity(runtime_dir)
    if dir_identity is None:
        return 0
    exclude_name: str | None = None
    if exclude_path is not None:
        with suppress(OSError):
            if exclude_path.parent.resolve() == runtime_dir.resolve():
                exclude_name = exclude_path.name
    key = _cleanup_cursor_key(runtime_dir)
    scandir_fn = os.scandir
    sweep, generation = _checkout_stale_cleanup_sweep(
        key, dir_identity=dir_identity, scandir_fn=scandir_fn,
    )
    if sweep is None:
        sweep = _open_stale_cleanup_sweep(
            runtime_dir, dir_identity=dir_identity, scandir_fn=scandir_fn,
        )
        if sweep is None:
            return 0
    healthy = _advance_stale_cleanup_sweep(
        sweep,
        exclude_name=exclude_name,
        max_inspect=max_inspect,
        max_scan_steps=max(max_inspect * 8, _STALE_RUNTIME_DESCRIPTOR_MAX_SCAN_STEPS),
    )
    now = time.time()
    inspected = 0
    removed = 0
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    while sweep.pending_candidates and inspected < max_inspect and removed < max_removals:
        entry = sweep.pending_candidates.popleft()
        if exclude_name is not None and entry.name == exclude_name:
            continue
        inspected += 1
        if _try_unlink_stale_descriptor(
            runtime_dir,
            entry,
            seen_lock_names=sweep.seen_lock_names,
            now=now,
            grace_seconds=grace_seconds,
            no_follow=no_follow,
        ):
            removed += 1
    if healthy:
        _checkin_stale_cleanup_sweep(key, sweep, generation=generation)
    else:
        sweep.pending_candidates.clear()
        _close_sweep_iterator(sweep)
    return removed


def _inspect_runtime_locator(
    fingerprint: str,
    *,
    recover: bool,
) -> tuple[_RuntimeLocatorState, bool | None, _RuntimeLocatorDiagnostic | None, _RuntimeInfo | None]:
    path = _runtime_info_path(fingerprint)
    if recover:
        _cleanup_stale_runtime_descriptors(path.parent, exclude_path=path)
    info = _read_info(path, fingerprint=fingerprint)
    if info is not None:
        return "valid", True, None, info
    locator = _inspect_info(path, fingerprint=fingerprint)
    return (
        "invalid" if locator.present else "missing",
        locator.recorded_pid_alive,
        locator.diagnostic_code,
        _recover_runtime_info(path, locator, fingerprint=fingerprint) if recover else None,
    )


def _locator_projection_after_probe(
    fingerprint: str,
) -> tuple[_RuntimeLocatorState, bool | None, str | None]:
    try:
        state, recorded_pid_alive, diagnostic_code, _ = _inspect_runtime_locator(fingerprint, recover=False)
    except EffectRuntimeHostPermissionError as exc:
        return "invalid", None, exc.diagnostic_code
    return state, recorded_pid_alive, diagnostic_code


def _read_local_snapshot_response(
    envelope: Mapping[str, object],
    sink: Path | None,
    *,
    method: str,
    request_id: str,
    timeout: float,
) -> dict[str, object]:
    """Read an exact private response; unverifiable post-dispatch bytes are ambiguous."""
    try:
        ref = envelope.get("result_ref")
        if (sink is None or envelope.get("schema_version") != EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION
                or envelope.get("request_id") != request_id or envelope.get("ok") is not True
                or not isinstance(ref, dict)):
            raise ValueError("invalid local snapshot envelope")
        size, digest = ref.get("byte_count"), ref.get("sha256")
        if (not isinstance(size, int) or isinstance(size, bool) or size <= 0
                or size > MAX_LOCAL_SNAPSHOT_BYTES or not isinstance(digest, str)
                or BARE_SHA256_PATTERN.fullmatch(digest) is None):
            raise ValueError("invalid local snapshot reference")
        require_private_path(sink.parent, directory=True)
        descriptor = os.open(sink, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as file:
            require_private_path(sink, descriptor=file.fileno())
            metadata = os.fstat(file.fileno())
            if (not os.path.isfile(sink) or metadata.st_size != size
                    or (hasattr(os, "getuid") and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077))):
                raise ValueError("invalid local snapshot file")
            data = file.read(MAX_LOCAL_SNAPSHOT_BYTES + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("local snapshot digest mismatch")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("invalid local snapshot response")
        return cast(dict[str, object], result)
    except (OSError, ValueError, RecursionError) as exc:
        raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from exc


def _read_startup_stderr(capture: IO[bytes]) -> bytes:
    """Return the bounded stderr a managed runtime wrote before it exited."""
    try:
        capture.seek(0)
        return capture.read(MAX_STARTUP_DIAGNOSTIC_BYTES)
    except (OSError, ValueError):
        return b""


def _startup_diagnostic(raw: bytes) -> tuple[str, str] | None:
    """Return the typed diagnostic a managed runtime published before readiness.

    Recognized startup failures publish a JSON envelope to stderr before exit,
    so a matching envelope is the authoritative startup error. Any other stderr
    content, such as a Node.js stack trace, is not a typed diagnostic and must
    not be reported as one.
    """
    if not raw:
        return None
    # Not splitlines(): it also breaks on U+0085/U+2028/U+2029, which a rejected
    # setting can echo back unescaped inside the envelope and tear the record.
    for line in reversed(raw.decode("utf-8", errors="replace").split("\n")):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION:
            continue
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            continue
        rendered = " ".join(str(payload.get("message") or "").split())[:240]
        return code, rendered or "TypeScript Effect runtime rejected its startup configuration"
    return None


def _published_startup_failure(startup_stderr: IO[bytes]) -> EffectRuntimeStartupError | None:
    """Return the typed failure a managed runtime published, if it published one.

    The runtime publishes its startup envelope before it exits, so the
    envelope is authoritative even while the runtime is still running. Other
    stderr content, such as a Node.js stack trace, is not a typed diagnostic
    and must not be reported as one.
    """
    diagnostic = _startup_diagnostic(_read_startup_stderr(startup_stderr))
    if diagnostic is None:
        return None
    code, message = diagnostic
    if code == "runtime_host_permission_denied":
        return EffectRuntimeHostPermissionError()
    return EffectRuntimeStartupError(message, diagnostic_code=code)


def _await_startup_publication(
    process: subprocess.Popen[bytes],
    startup_stderr: IO[bytes],
    *,
    info_path: Path,
    fingerprint: str,
    deadline: float,
    check_running_diagnostic: bool = True,
) -> _RuntimeInfo | None:
    """Wait for a running runtime to publish its locator or its diagnostic.

    Returns the locator the runtime published. Raises the typed failure the
    runtime published, or the stable exit diagnostic when it exits without
    one. Returns None when the deadline passes with neither.
    """
    while time.monotonic() < deadline:
        try:
            info = _read_info(info_path, fingerprint=fingerprint)
        except EffectRuntimeHostPermissionError:
            if process.poll() is None:
                process.terminate()
            raise
        if info is not None:
            return info
        exit_code = process.poll()
        if exit_code is not None or check_running_diagnostic:
            failure = _published_startup_failure(startup_stderr)
            if failure is not None:
                if exit_code is None:
                    process.terminate()
                raise failure
        if exit_code is not None:
            raise EffectRuntimeStartupError(
                f"TypeScript Effect runtime exited before becoming ready (exit_code={exit_code})",
                diagnostic_code="runtime_exited_before_ready",
            )
        time.sleep(STARTUP_POLL_SECONDS)
    return None


def _acquire_start_lock(lock: Path, *, info_path: Path, fingerprint: str) -> _RuntimeInfo | None:
    lock_deadline = time.monotonic() + STARTUP_LOCK_TIMEOUT_SECONDS
    while time.monotonic() < lock_deadline:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = _read_info(info_path, fingerprint=fingerprint)
            if existing is not None:
                return existing
            holder_pid = _start_lock_holder_pid(lock)
            if holder_pid is not None and not _pid_is_alive(holder_pid):
                with suppress(FileNotFoundError):
                    lock.unlink()
                continue
            with suppress(OSError):
                if holder_pid is None and time.time() - lock.stat().st_mtime > 10:
                    lock.unlink(missing_ok=True)
            time.sleep(STARTUP_POLL_SECONDS)
        else:
            identity = None
            try:
                try:
                    identity = _effect_file_identity_from_fd(descriptor)
                    # Keep the raw descriptor owned here across stream construction.
                    with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as lock_file:
                        lock_file.write(f"{os.getpid()}\n")
                        lock_file.flush()
                        os.fsync(lock_file.fileno())
                finally:
                    with suppress(OSError):
                        os.close(descriptor)
            except BaseException:
                _remove_created_effect_file(lock, identity)
                raise
            return None
    existing = _read_info(info_path, fingerprint=fingerprint)
    if existing is not None:
        return existing
    raise EffectRuntimeStartupError(
        "TypeScript Effect runtime startup lock timed out",
        diagnostic_code="startup_lock_timeout",
    )


def _runtime_command(entry: str, *args: str, preload: Path | None = None) -> list[str]:
    command = [_node_executable(), "--no-warnings", "--experimental-strip-types", *_node_typescript_flags()]
    if preload is not None:
        # ESM specifiers require file URLs on Windows; encode reserved path characters as well.
        command.extend(["--import", preload.as_uri()])
    command.extend([entry, *args])
    return command


def _start_runtime(*, fingerprint: str, info_path: Path) -> _RuntimeInfo:
    runtime_dir = info_path.parent
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        runtime_dir.chmod(0o700)
    lock = runtime_dir / f"start-{fingerprint[:16]}.lock"
    existing = _acquire_start_lock(lock, info_path=info_path, fingerprint=fingerprint)
    if existing is not None:
        return existing
    try:
        existing = _read_info(info_path, fingerprint=fingerprint)
        if existing is not None:
            return existing
        environment = _runtime_environment(secrets.token_urlsafe(32))
        # Capture stderr so a rejected startup can publish a typed
        # startup diagnostic instead of a bare exit status. The capture
        # is an unlinked temporary file, so it cannot deadlock the child on a
        # full pipe and it leaves no stale path behind.
        with tempfile.TemporaryFile() as startup_stderr:
            try:
                process = subprocess.Popen(
                    _runtime_command(
                        str(_runtime_server_path()),
                        "--info", str(info_path), "--fingerprint", fingerprint,
                        preload=_control_plane_root() / "effect_runtime_compile_cache.ts",
                    ),
                    env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=startup_stderr, start_new_session=os.name != "nt", close_fds=True,
                    # The host outlives the command that started it. On Windows
                    # a live child whose working directory is the caller's
                    # directory keeps that directory (and its ancestors) from
                    # being renamed or removed, so the host must not inherit it.
                    cwd=tempfile.gettempdir(),
                )
            except PermissionError as exc:
                raise EffectRuntimeHostPermissionError() from exc
            except OSError as exc:
                raise EffectRuntimeStartupError(
                    "TypeScript Effect runtime process could not be launched",
                    diagnostic_code="runtime_launch_failed",
                ) from exc
            # Once the ready budget expires while the runtime still runs, give
            # it one more bounded window to publish a locator or computed diagnostic.
            for budget, check_running in (
                (STARTUP_READY_TIMEOUT_SECONDS, False),
                (STARTUP_DIAGNOSTIC_GRACE_SECONDS, True),
            ):
                info = _await_startup_publication(
                    process, startup_stderr, info_path=info_path, fingerprint=fingerprint,
                    deadline=time.monotonic() + budget, check_running_diagnostic=check_running,
                )
                if info is not None:
                    _cleanup_stale_runtime_descriptors(runtime_dir, exclude_path=info_path)
                    return info
            failure = _published_startup_failure(startup_stderr)
            if failure is not None:
                if process.poll() is None:
                    process.terminate()
                raise failure
            if process.poll() is None:
                process.terminate()
            raise EffectRuntimeStartupError(
                "TypeScript Effect runtime did not become ready before the startup deadline",
                diagnostic_code="runtime_startup_timeout",
            )
    finally:
        lock.unlink(missing_ok=True)


@dataclass(frozen=True)
class _StdioRuntime:
    fingerprint: str
    token: str
    pipe: EffectRuntimeStdio
    identity: dict[str, object] | None


_stdio_runtime: _StdioRuntime | None = None
_stdio_runtime_lock = Lock()


def _reset_stdio_runtime_after_fork() -> None:
    global _stdio_runtime, _stdio_runtime_lock
    if _stdio_runtime is not None:
        _stdio_runtime.pipe.close()
        _stdio_runtime = None
    _stdio_runtime_lock = Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_stdio_runtime_after_fork)


@atexit.register
def _close_stdio_runtime_at_exit() -> None:
    if _stdio_runtime is not None:
        _stdio_runtime.pipe.close(wait_seconds=0.2)


def _start_stdio_runtime(fingerprint: str) -> _StdioRuntime:
    token = secrets.token_urlsafe(32)
    try:
        pipe = EffectRuntimeStdio(
            _runtime_command(str(_control_plane_root() / "effect_runtime_stdio.ts"), fingerprint),
            _runtime_environment(token),
        )
    except PermissionError as exc:
        raise EffectRuntimeHostPermissionError() from exc
    except OSError as exc:
        raise EffectRuntimeStartupError(
            "TypeScript Effect runtime process could not be launched",
            diagnostic_code="runtime_launch_failed",
        ) from exc
    try:
        ping = _request_with_info(
            {"token": token}, request_id=str(uuid.uuid4()), method="runtime.ping",
            params={}, timeout=STARTUP_READY_TIMEOUT_SECONDS, pipe=pipe,
        ).get("result")
        if (not isinstance(ping, Mapping) or ping.get("ready") is not True
                or ping.get("pid") != pipe.pid or ping.get("fingerprint") != fingerprint):
            raise EffectRuntimeStartupError(
                "TypeScript Effect runtime returned an invalid process identity",
                diagnostic_code="semantic_probe_shape_mismatch",
            )
    except (EffectRuntimeRemoteError, EffectRuntimeResponseAmbiguous) as exc:
        pipe.close(reap_in_background=True)
        # The readiness exchange is read-only. No caller effect has been sent.
        raise EffectRuntimeStartupError(
            "TypeScript Effect runtime did not become ready over stdio",
            diagnostic_code="runtime_exited_before_ready",
        ) from exc
    except BaseException:
        pipe.close(reap_in_background=True)
        raise
    return _StdioRuntime(fingerprint, token, pipe, runtime_identity_from_info(ping))


@contextmanager
def _stdio_runtime_access(timeout: float) -> Iterator[None]:
    if not _stdio_runtime_lock.acquire(timeout=timeout):
        raise EffectRuntimeStartupError(
            "TypeScript Effect runtime was busy before request dispatch",
            diagnostic_code="runtime_request_timeout",
        )
    try:
        yield
    finally:
        _stdio_runtime_lock.release()


def _request_stdio_runtime(
    fingerprint: str,
    *,
    request_id: str,
    method: str,
    params: Mapping[str, object],
    timeout: float,
    large_local_snapshot: bool,
) -> dict[str, object]:
    global _stdio_runtime
    with _stdio_runtime_access(timeout):
        runtime = _stdio_runtime
        if runtime is None or runtime.fingerprint != fingerprint or not runtime.pipe.alive:
            if runtime is not None:
                runtime.pipe.close(reap_in_background=True)
                _stdio_runtime = None
            runtime = _start_stdio_runtime(fingerprint)
            _stdio_runtime = runtime
        try:
            response = _request_with_info(
                {"token": runtime.token},
                request_id=request_id,
                method=method,
                params=params,
                timeout=timeout,
                large_local_snapshot=large_local_snapshot,
                pipe=runtime.pipe,
            )
        except (EffectRuntimeResponseAmbiguous, KeyboardInterrupt, SystemExit):
            runtime.pipe.close(reap_in_background=True)
            _stdio_runtime = None
            raise
        if method == "runtime.shutdown":
            runtime.pipe.close(reap_in_background=True)
            _stdio_runtime = None
        return response


def _restart_stdio_runtime(timeout: float) -> dict[str, object]:
    global _stdio_runtime
    with _stdio_runtime_access(timeout):
        runtime = _stdio_runtime
        _stdio_runtime = None
        was_running = runtime is not None and runtime.pipe.alive
        stopped = runtime is not None and runtime.pipe.close(
            wait_seconds=timeout, reap_in_background=True,
        )
    status = ("stopped" if stopped else "shutdown_pending") if was_running else "not_running"
    return _restart_result(
        status,
        stopped=was_running and stopped,
        identity=runtime.identity if runtime is not None else None,
        info_path=None,
        transport="stdio",
    )


def effect_runtime_request(
    method: str,
    params: Mapping[str, object],
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    retry_safe: bool = True,
    large_local_snapshot: bool = False,
) -> dict[str, object]:
    """Call the managed TS runtime, retrying only idempotent typed effects."""
    use_stdio = _uses_stdio_runtime()
    fingerprint = _runtime_fingerprint_for_request()
    request_id = str(uuid.uuid4())
    if use_stdio:
        return _request_stdio_runtime(
            fingerprint, request_id=request_id, method=method, params=params,
            timeout=timeout, large_local_snapshot=large_local_snapshot,
        )
    info_path = _runtime_info_path(fingerprint)
    last_error: OSError | RuntimeError | None = None
    for attempt in range(2 if retry_safe else 1):
        info: _RuntimeInfo | None = None
        try:
            try:
                info = _runtime_info_for_request(info_path, fingerprint=fingerprint)
                if info is None:
                    info = _start_runtime(fingerprint=fingerprint, info_path=info_path)
            except PermissionError as exc:
                raise EffectRuntimeHostPermissionError() from exc
            return _request_with_info(
                info, request_id=request_id, method=method, params=params,
                timeout=timeout, large_local_snapshot=large_local_snapshot,
            )
        except (
            EffectRuntimeRemoteError, EffectRuntimeResponseAmbiguous,
            EffectRuntimeHostPermissionError, EffectRuntimeNodeProbeError,
        ):
            raise
        except EffectRuntimeStartupError as exc:
            last_error = exc
            if attempt == 0 and retry_safe:
                continue
            raise
        except TimeoutError as exc:
            # In particular it must not replace a live server which may still
            # be completing an earlier mutation under the per-Goal lock.
            raise EffectRuntimeStartupError(
                f"TypeScript Effect runtime did not connect for {method} within {timeout:g}s",
                diagnostic_code="runtime_request_timeout",
            ) from exc
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if attempt == 0 and retry_safe:
                # Only pre-send connection failures reach this branch. Re-read
                # the locator on retry: reap our own exited child first so a
                # zombie is rejected by _read_info, while a live server may
                # simply be draining. Even a token check followed by unlink would
                # race with a replacement server publishing its own locator.
                _reap_exited_runtime_child(info)
                _wait_for_runtime_locator_turnover(
                    info_path, fingerprint=fingerprint, observed=info, timeout=timeout,
                )
                continue
            break
    raise EffectRuntimeStartupError(
        f"TypeScript Effect runtime request failed for {method}",
        diagnostic_code="runtime_request_failed",
    ) from last_error


def effect_runtime_result(
    method: str,
    params: Mapping[str, object],
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    retry_safe: bool = True,
    large_local_snapshot: bool = False,
) -> Any:
    state = _REQUEST_RUNTIME_REVISION.get() if method == "todo.context.page" else None

    def validate_result(result: object) -> object:
        # A caller must explicitly request a bundle.
        if set(params) == {"summary_display_views"}:
            views = params["summary_display_views"]
            if not isinstance(views, Mapping):
                raise AttributeError("summary_display_views must be a mapping")
            requested = views.get("view_names", ("summary", "planning", "terminal"))
            if not isinstance(result, dict):
                raise RuntimeError("invalid TypeScript Todo source views")
            if not isinstance(requested, Iterable):
                raise TypeError(f"{type(requested).__name__!r} object is not iterable")
            if any(
                not isinstance(result.get(view), list)
                or any(not isinstance(row, dict) for row in result[view])
                for view in requested
            ):
                raise RuntimeError("invalid TypeScript Todo source views")
        return result

    key = None
    if state is not None:
        # Preserve JSON order and scalar types, including bool/int, signed zero
        # and exact integers. Do not infer equality from a digest or Todo id.
        try:
            key = json.dumps(
                [params, timeout, retry_safe, large_local_snapshot],
                ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
            pass  # Invalid/non-JSON input still reaches the original validator.
        if key is not None:
            prior = state.todo_context_result(key)
            if prior is not None:
                return validate_result(json.loads(prior))  # A caller cannot mutate another view.
    result = effect_runtime_request(
        method, params, timeout=timeout, retry_safe=retry_safe, large_local_snapshot=large_local_snapshot,
    ).get("result")
    validate_result(result)
    if state is not None and key is not None and isinstance(result, dict):
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
            pass
        else:
            state.remember_todo_context(key, encoded)
    return result


def effect_runtime_projection_result(method: str, params: Mapping[str, object]) -> Any:
    """Keep projections inline unless a size refusal requires a local snapshot."""
    try:
        return effect_runtime_result(method, params)
    except EffectRuntimeRejected as error:
        if error.diagnostic_code != "request_too_large":
            raise
        # Reuse the existing bounded transport after refusal, never replay an
        # ambiguous response or truncate the projection's complete source.
        return effect_runtime_result(method, params, large_local_snapshot=True)


def _serving_runtime_identity() -> dict[str, object] | None:
    """Project the identity of the runtime serving this revision, if any."""
    fingerprint = _runtime_fingerprint()
    return runtime_identity_from_info(
        _runtime_info_for_request(_runtime_info_path(fingerprint), fingerprint=fingerprint)
    )


def _stdio_identity_snapshot(fingerprint: str) -> tuple[bool, dict[str, object] | None]:
    runtime = _stdio_runtime
    if runtime is not None and runtime.fingerprint == fingerprint and runtime.pipe.alive:
        return True, runtime.identity
    return False, None


def _stdio_alive_snapshot() -> bool:
    runtime = _stdio_runtime
    return runtime is not None and runtime.pipe.alive


def collect_effect_runtime_readiness(*, deep: bool = False) -> dict[str, object]:
    """Report whether the managed TS Effect runtime can serve control-plane work."""
    return _collect_effect_runtime_readiness(
        _EffectReadinessHooks(
            probe_node=lambda: _probe_node(timeout=STARTUP_READY_TIMEOUT_SECONDS),
            uses_stdio=_uses_stdio_runtime,
            runtime_fingerprint=_runtime_fingerprint,
            stdio_identity=_stdio_identity_snapshot,
            stdio_alive=_stdio_alive_snapshot,
            inspect_locator=lambda fp: _inspect_runtime_locator(fp, recover=True),
            locator_after_probe=_locator_projection_after_probe,
            rpc_result=effect_runtime_result,
            serving_identity=_serving_runtime_identity,
            node_probe_remediation=lambda code: node_probe_remediation(code),
            runtime_identity_from_info=lambda info: runtime_identity_from_info(info),
        ),
        deep=deep,
    )
