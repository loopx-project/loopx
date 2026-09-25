from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import IO, Any

from ..file_lock import process_is_alive

EFFECT_RUNTIME_REQUEST_SCHEMA_VERSION = "loopx_effect_runtime_request_v0"
EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION = "loopx_effect_runtime_response_v1"
EFFECT_RUNTIME_INFO_SCHEMA_VERSION = "loopx_effect_runtime_info_v0"
EFFECT_RUNTIME_READINESS_SCHEMA_VERSION = "loopx_effect_runtime_readiness_v0"
EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION = (
    "loopx_effect_runtime_startup_error_v0"
)
MINIMUM_NODE_VERSION = (22, 22, 3)
MINIMUM_NODE_VERSION_TEXT = ".".join(str(part) for part in MINIMUM_NODE_VERSION)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_LOCAL_SNAPSHOT_BYTES = 64 * 1024 * 1024
LOCAL_SNAPSHOT_METHODS = frozenset({
    "goal.checkpoint_read_context.source",
    "goal.checkpoint_read_context.evaluate",
    "goal.checkpoint_read_context.commit",
    "goal.checkpoint_read_context.inspect_replay",
})
MAX_STARTUP_DIAGNOSTIC_BYTES = 8 * 1024
STARTUP_LOCK_TIMEOUT_SECONDS = 15.0
STARTUP_READY_TIMEOUT_SECONDS = 15.0
STARTUP_POLL_SECONDS = 0.025
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
_NODE_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_RUNTIME_SOURCE_SUFFIXES = frozenset({".json", ".ts"})
_RuntimeSourceSnapshot = tuple[tuple[str, int, int, int], ...]


@dataclass(frozen=True)
class _RuntimeRevision:
    fingerprint: str


class _RequestRuntimeRevision:
    def __init__(self) -> None:
        self._lock = Lock()
        self._revision: _RuntimeRevision | None = None
        self._scope_count = 1
        self._closed = False

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


_REQUEST_RUNTIME_REVISION: ContextVar[_RequestRuntimeRevision | None] = (
    ContextVar("loopx_effect_runtime_request_revision", default=None)
)


class EffectRuntimeRemoteError(RuntimeError):
    """A typed exception returned by the managed TypeScript runtime."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        diagnostic_code: str,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.diagnostic_code = diagnostic_code


class EffectRuntimeRejected(EffectRuntimeRemoteError):
    """A typed request reached the runtime but failed semantic validation."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: str = "invalid_request",
    ) -> None:
        super().__init__(
            message,
            error_kind="request_rejected",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeConflict(EffectRuntimeRemoteError):
    """The request conflicted with newer persisted state."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="conflict",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeIOError(EffectRuntimeRemoteError):
    """A managed runtime filesystem operation failed."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        diagnostic_code: str,
        transient: bool,
    ) -> None:
        super().__init__(
            message,
            error_kind=error_kind,
            diagnostic_code=diagnostic_code,
        )
        self.transient = transient


class EffectRuntimeTransientIOError(EffectRuntimeIOError):
    """A retryable managed runtime filesystem operation failed."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="io_transient",
            diagnostic_code=diagnostic_code,
            transient=True,
        )


class EffectRuntimePermanentIOError(EffectRuntimeIOError):
    """A non-retryable managed runtime filesystem operation failed."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="io_permanent",
            diagnostic_code=diagnostic_code,
            transient=False,
        )


class EffectRuntimeLockTimeout(EffectRuntimeRemoteError):
    """A managed mutation lock could not be acquired before its deadline."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="lock_timeout",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeInternalError(EffectRuntimeRemoteError):
    """The managed handler failed outside a declared request or I/O error."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="internal_failure",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeStartupError(RuntimeError):
    """The managed runtime could not reach a request-serving state."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


class EffectRuntimeResponseAmbiguous(EffectRuntimeStartupError):
    """The request may have executed even though its response was lost."""

    def __init__(self, method: str, *, timeout: float) -> None:
        super().__init__(
            f"TypeScript Effect runtime returned no verifiable response for {method} "
            f"(request budget {timeout:g}s); the operation may have committed. Read its exact "
            "durable receipt before any retry",
            diagnostic_code="runtime_response_ambiguous",
        )


def _control_plane_root() -> Path:
    return Path(__file__).resolve().parent


def _scan_runtime_source_files(root: Path) -> tuple[str, ...]:
    files: list[str] = []
    for directory, child_directories, filenames in os.walk(root):
        child_directories.sort()
        relative_directory = os.path.relpath(directory, root)
        if relative_directory == ".":
            relative_directory = ""
        files.extend(
            Path(relative_directory, filename).as_posix()
            for filename in sorted(filenames)
            if Path(filename).suffix in _RUNTIME_SOURCE_SUFFIXES
            and Path(directory, filename).is_file()
        )
    return tuple(sorted(files))


def _runtime_file_snapshot(
    root: Path,
    files: tuple[str, ...],
) -> _RuntimeSourceSnapshot:
    return tuple(
        (
            relative,
            (metadata := (root / relative).stat()).st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_size,
        )
        for relative in files
    )


def _runtime_source_snapshot(root: Path | None = None) -> _RuntimeSourceSnapshot:
    """Return cheap metadata that invalidates the packaged-source hash."""

    source_root = (root or _control_plane_root()).resolve()
    return _runtime_file_snapshot(source_root, _scan_runtime_source_files(source_root))


def _runtime_source_files(root: Path | None = None) -> tuple[str, ...]:
    """Return the packaged source boundary owned by the managed runtime."""

    return tuple(relative for relative, *_metadata in _runtime_source_snapshot(root))


@lru_cache(maxsize=8)
def _runtime_fingerprint_for_snapshot(
    root: str,
    snapshot: _RuntimeSourceSnapshot,
) -> str:
    digest = hashlib.sha256()
    source_root = Path(root)
    for relative, *_metadata in snapshot:
        digest.update(relative.encode("utf-8"))
        digest.update((source_root / relative).read_bytes())
    return digest.hexdigest()


def _runtime_fingerprint() -> str:
    root = _control_plane_root()
    resolved_root = os.fspath(root.resolve())
    try:
        return _runtime_fingerprint_for_snapshot(
            resolved_root,
            _runtime_source_snapshot(root),
        )
    except FileNotFoundError:
        try:
            return _runtime_fingerprint_for_snapshot(
                resolved_root,
                _runtime_source_snapshot(root),
            )
        except FileNotFoundError as exc:
            raise EffectRuntimeStartupError(
                "TypeScript Effect runtime source topology did not stabilize",
                diagnostic_code="packaged_runtime_source_unstable",
            ) from exc


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
    if state is None:
        return _runtime_fingerprint()
    return state.resolve().fingerprint


def _runtime_dir() -> Path:
    owner = str(getattr(os, "getuid", lambda: Path.home())())
    suffix = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"loopx-effect-runtime-{suffix}"


def _runtime_info_path(fingerprint: str) -> Path:
    return _runtime_dir() / f"runtime-{fingerprint[:16]}.json"


def _runtime_server_path() -> Path:
    return _control_plane_root() / "effect_runtime_server.ts"


def _node_executable() -> str:
    status, executable, _version = _probe_node()
    if status != "ready" or executable is None:
        raise EffectRuntimeStartupError(
            f"LoopX Effect runtime requires Node.js {MINIMUM_NODE_VERSION_TEXT} "
            "or newer",
            diagnostic_code="node_unavailable",
        )
    return executable


def _probe_node() -> tuple[str, str | None, str | None]:
    executable = shutil.which("node")
    if executable is None:
        return "missing", None, None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "probe_failed", executable, None
    match = _NODE_VERSION_RE.fullmatch(completed.stdout.strip())
    version = tuple(int(part) for part in match.groups()) if match else None
    if completed.returncode != 0 or version is None:
        return "probe_failed", executable, None
    version_text = ".".join(str(part) for part in version)
    if version < MINIMUM_NODE_VERSION:
        return "unsupported", executable, version_text
    return "ready", executable, version_text


def _pid_is_alive(value: object) -> bool:
    return process_is_alive(value)


def _reap_exited_runtime_child(info: Mapping[str, Any] | None) -> None:
    """Let a dead directly spawned child fail the next non-signaling probe.

    A stopped child can remain a zombie until its Python parent reaps it;
    ``kill(pid, 0)`` still reports that zombie as present. This helper does
    nothing for a live child or a runtime owned by another process.
    """

    if os.name == "nt" or not isinstance(info, Mapping):
        return
    pid = info.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


def _start_lock_holder_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if value > 0 else None


def _read_info(path: Path, *, fingerprint: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    if (
        payload.get("schema_version") != EFFECT_RUNTIME_INFO_SCHEMA_VERSION
        or payload.get("fingerprint") != fingerprint
        or payload.get("host") != "127.0.0.1"
        or not isinstance(payload.get("port"), int)
        or not isinstance(payload.get("token"), str)
        or not _pid_is_alive(payload.get("pid"))
    ):
        return None
    return payload


_RUNTIME_IDENTITY_TEXT_FIELDS = (
    "node_version",
    "sqlite_version",
    "sqlite_source_id",
    "unavailable_reason",
)


def runtime_identity_from_info(info: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Project the serving runtime's Node/SQLite identity from a runtime info file.

    The managed runtime is reused per source revision, so this identity can
    differ from the Node on the caller's PATH; status surfaces must show which
    runtime is actually serving before a qualification failure is diagnosed.
    """

    if not isinstance(info, Mapping):
        return None
    value = info.get("runtime_identity")
    if not isinstance(value, Mapping):
        return None
    identity: dict[str, Any] = {
        "schema_version": value.get("schema_version"),
        "sqlite_available": value.get("sqlite_available") is True,
        "sqlite_authority_qualified": value.get("sqlite_authority_qualified")
        is True,
        "synchronous_statement_finalization": (
            value.get("synchronous_statement_finalization")
            if isinstance(value.get("synchronous_statement_finalization"), bool)
            else None
        ),
    }
    for field in _RUNTIME_IDENTITY_TEXT_FIELDS:
        raw = value.get(field)
        identity[field] = raw if isinstance(raw, str) else None
    return identity


EFFECT_RUNTIME_RESTART_SCHEMA_VERSION = "loopx_effect_runtime_restart_v0"


def _serving_token(path: Path) -> tuple[bool, str | None]:
    """Report whether a runtime is still publishing itself at ``path``.

    The managed runtime removes its info file as part of its shutdown
    handshake, so the file is the authoritative stop signal. The pid is not:
    an exited runtime whose parent has not reaped it still answers a liveness
    probe, which would otherwise report a completed restart as pending.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, None
    except (json.JSONDecodeError, OSError):
        # An unreadable file is not evidence of a stopped runtime; keep waiting
        # until the deadline instead of claiming a restart that did not happen.
        return True, None
    if not isinstance(payload, dict):
        return False, None
    token = payload.get("token")
    return True, token if isinstance(token, str) else None


def restart_effect_runtime(*, timeout: float = 5.0) -> dict[str, Any]:
    """Stop the managed runtime serving this source revision.

    The replacement runtime resolves Node from the PATH of the next request, so
    this is the operator action after installing a qualified Node: a runtime
    started earlier keeps its own Node until it exits.
    """

    fingerprint = _runtime_fingerprint()
    info_path = _runtime_info_path(fingerprint)
    info = _read_info(info_path, fingerprint=fingerprint)
    identity = runtime_identity_from_info(info)
    if info is None:
        return {
            "schema_version": EFFECT_RUNTIME_RESTART_SCHEMA_VERSION,
            "status": "not_running",
            "stopped": False,
            "previous_runtime_identity": identity,
            "info_path": str(info_path),
        }
    pid = info.get("pid")
    serving_token = info.get("token")
    try:
        _request_with_info(
            info,
            request_id=uuid.uuid4().hex,
            method="runtime.shutdown",
            params={},
            timeout=timeout,
        )
    except (
        EffectRuntimeRejected,
        EffectRuntimeRemoteError,
        EffectRuntimeResponseAmbiguous,
        OSError,
    ):
        # A runtime that is already closing must still be reported as pending
        # rather than as a failed restart.
        pass
    deadline = time.monotonic() + timeout
    stopped = False
    while time.monotonic() < deadline:
        published, published_token = _serving_token(info_path)
        if not published or published_token != serving_token:
            stopped = True
            break
        if not _pid_is_alive(pid):
            stopped = True
            break
        time.sleep(0.05)
    return {
        "schema_version": EFFECT_RUNTIME_RESTART_SCHEMA_VERSION,
        "status": "stopped" if stopped else "shutdown_pending",
        "stopped": stopped,
        "previous_runtime_identity": identity,
        "info_path": str(info_path),
    }


def _request_with_info(
    info: Mapping[str, Any],
    *,
    request_id: str,
    method: str,
    params: Mapping[str, Any],
    timeout: float,
    large_local_snapshot: bool = False,
) -> dict[str, Any]:
    request = {
        "schema_version": EFFECT_RUNTIME_REQUEST_SCHEMA_VERSION,
        "token": info["token"],
        "request_id": request_id,
        "method": method,
        "params": dict(params),
    }
    with ExitStack() as stack:
        response_sink: Path | None = None
        encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode()
        if large_local_snapshot:
            if method not in LOCAL_SNAPSHOT_METHODS:
                raise EffectRuntimeRejected(
                    "local snapshot transport is unavailable for this method",
                    diagnostic_code="invalid_request",
                )
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="loopx-effect-")))
            response_sink = directory / "response.json"
            descriptor = os.open(response_sink, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            request["response_sink"] = str(response_sink)
            encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode()
            if len(encoded) > MAX_REQUEST_BYTES:
                params_bytes = json.dumps(dict(params), separators=(",", ":")).encode()
                if len(params_bytes) > MAX_LOCAL_SNAPSHOT_BYTES:
                    raise EffectRuntimeRejected(
                        "TypeScript Effect runtime local snapshot is oversized",
                        diagnostic_code="request_too_large",
                    )
                params_path = directory / "params.json"
                descriptor = os.open(params_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as file:
                    file.write(params_bytes)
                request.pop("params")
                request["params_ref"] = {
                    "schema_version": "loopx_effect_runtime_snapshot_v0",
                    "path": str(params_path), "byte_count": len(params_bytes),
                    "sha256": hashlib.sha256(params_bytes).hexdigest(),
                }
                encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_REQUEST_BYTES:
            raise EffectRuntimeRejected(
                "TypeScript Effect runtime request is oversized",
                diagnostic_code="request_too_large",
            )
        chunks: list[bytes] = []
        size = 0
        with socket.create_connection(
            (str(info["host"]), int(info["port"])), timeout=timeout
        ) as connection:
            try:
                connection.settimeout(timeout)
                # sendall may have delivered a prefix before it raises. From this
                # point onward the caller cannot prove that no effect ran.
                connection.sendall(encoded)
                while True:
                    chunk = connection.recv(64 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RuntimeError("TypeScript Effect runtime response is oversized")
                    if b"\n" in chunk:
                        break
            except (OSError, RuntimeError) as exc:
                raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from exc
        try:
            response = json.loads(b"".join(chunks).split(b"\n", 1)[0])
        except (json.JSONDecodeError, IndexError):
            raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from None
        if isinstance(response, dict) and "result_ref" in response:
            response = _read_local_snapshot_response(
                response, response_sink, method=method, request_id=request_id, timeout=timeout,
            )
    if (
        not isinstance(response, dict)
        or response.get("schema_version") != EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION
        or response.get("request_id") != request_id
    ):
        raise EffectRuntimeResponseAmbiguous(method, timeout=timeout)
    if response.get("ok") is not True:
        raise _remote_runtime_error(response.get("error"))
    return response


def _read_local_snapshot_response(
    envelope: dict[str, Any], sink: Path | None, *, method: str, request_id: str, timeout: float,
) -> dict[str, Any]:
    """Read an exact private response; unverifiable post-dispatch bytes are ambiguous."""
    try:
        ref = envelope["result_ref"]
        if (sink is None or envelope.get("schema_version") != EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION
                or envelope.get("request_id") != request_id or envelope.get("ok") is not True
                or not isinstance(ref, dict)):
            raise ValueError("invalid local snapshot envelope")
        size, digest = ref.get("byte_count"), ref.get("sha256")
        if (not isinstance(size, int) or isinstance(size, bool) or size <= 0
                or size > MAX_LOCAL_SNAPSHOT_BYTES or not isinstance(digest, str)
                or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
            raise ValueError("invalid local snapshot reference")
        descriptor = os.open(sink, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as file:
            metadata = os.fstat(file.fileno())
            if (not os.path.isfile(sink) or metadata.st_size != size
                    or (hasattr(os, "getuid") and
                        (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077))):
                raise ValueError("invalid local snapshot file")
            data = file.read(MAX_LOCAL_SNAPSHOT_BYTES + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("local snapshot digest mismatch")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("invalid local snapshot response")
        return result
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise EffectRuntimeResponseAmbiguous(method, timeout=timeout) from exc


def _remote_runtime_error(value: object) -> EffectRuntimeRemoteError:
    if not isinstance(value, Mapping):
        return EffectRuntimeInternalError(
            "TypeScript Effect runtime returned an invalid error envelope",
            diagnostic_code="invalid_error_envelope",
        )
    kind = value.get("kind")
    code = value.get("code")
    message = value.get("message")
    if (
        not isinstance(kind, str)
        or not kind
        or not isinstance(code, str)
        or not code
    ):
        return EffectRuntimeInternalError(
            "TypeScript Effect runtime returned an invalid error envelope",
            diagnostic_code="invalid_error_envelope",
        )
    rendered = " ".join(
        str(message or "TypeScript Effect runtime request failed").split()
    )
    rendered = rendered[:240] or "TypeScript Effect runtime request failed"
    if kind == "request_rejected":
        return EffectRuntimeRejected(rendered, diagnostic_code=code)
    if kind == "conflict":
        return EffectRuntimeConflict(rendered, diagnostic_code=code)
    if kind == "io_transient":
        return EffectRuntimeTransientIOError(rendered, diagnostic_code=code)
    if kind == "io_permanent":
        return EffectRuntimePermanentIOError(rendered, diagnostic_code=code)
    if kind == "lock_timeout":
        return EffectRuntimeLockTimeout(rendered, diagnostic_code=code)
    if kind == "internal_failure":
        return EffectRuntimeInternalError(rendered, diagnostic_code=code)
    return EffectRuntimeInternalError(
        "TypeScript Effect runtime returned an unsupported error kind",
        diagnostic_code="unsupported_error_kind",
    )


def _read_startup_stderr(capture: IO[bytes]) -> bytes:
    """Return the bounded stderr a managed runtime wrote before it exited."""

    try:
        capture.seek(0)
        return capture.read(MAX_STARTUP_DIAGNOSTIC_BYTES)
    except (OSError, ValueError):
        return b""


def _startup_diagnostic(raw: bytes) -> tuple[str, str] | None:
    """Return the typed diagnostic a rejected managed runtime published.

    A server that rejects its own startup configuration writes one JSON
    envelope to stderr and exits before it listens, so a matching envelope is
    the authoritative configuration error. Any other stderr content, such as a
    Node.js stack trace, is not a typed diagnostic and must not be reported as
    one.
    """

    if not raw:
        return None
    for line in reversed(raw.decode("utf-8", errors="replace").splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("schema_version")
            != EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION
        ):
            continue
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            continue
        rendered = " ".join(str(payload.get("message") or "").split())[:240]
        return code, rendered or (
            "TypeScript Effect runtime rejected its startup configuration"
        )
    return None


def _start_runtime(*, fingerprint: str, info_path: Path) -> dict[str, Any]:
    runtime_dir = info_path.parent
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        runtime_dir.chmod(0o700)
    except OSError:
        pass
    lock = runtime_dir / f"start-{fingerprint[:16]}.lock"
    lock_deadline = time.monotonic() + STARTUP_LOCK_TIMEOUT_SECONDS
    acquired = False
    while time.monotonic() < lock_deadline:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
                lock_file.write(f"{os.getpid()}\n")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            acquired = True
            break
        except FileExistsError:
            existing = _read_info(info_path, fingerprint=fingerprint)
            if existing is not None:
                return existing
            holder_pid = _start_lock_holder_pid(lock)
            if holder_pid is not None and not _pid_is_alive(holder_pid):
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                if time.time() - lock.stat().st_mtime > 10:
                    lock.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(STARTUP_POLL_SECONDS)
    if not acquired:
        existing = _read_info(info_path, fingerprint=fingerprint)
        if existing is not None:
            return existing
        raise EffectRuntimeStartupError(
            "TypeScript Effect runtime startup lock timed out",
            diagnostic_code="startup_lock_timeout",
        )
    try:
        existing = _read_info(info_path, fingerprint=fingerprint)
        if existing is not None:
            return existing
        token = secrets.token_urlsafe(32)
        environment = os.environ.copy()
        environment["LOOPX_EFFECT_RUNTIME_TOKEN"] = token
        # Capture stderr so a rejected startup can publish a typed
        # configuration diagnostic instead of a bare exit status. The capture
        # is an unlinked temporary file, so it cannot deadlock the child on a
        # full pipe and it leaves no stale path behind.
        with tempfile.TemporaryFile() as startup_stderr:
            try:
                process = subprocess.Popen(
                    [
                        _node_executable(),
                        "--no-warnings",
                        "--experimental-strip-types",
                        str(_runtime_server_path()),
                        "--info",
                        str(info_path),
                        "--fingerprint",
                        fingerprint,
                    ],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=startup_stderr,
                    start_new_session=os.name != "nt",
                    close_fds=True,
                )
            except OSError as exc:
                raise EffectRuntimeStartupError(
                    "TypeScript Effect runtime process could not be launched",
                    diagnostic_code="runtime_launch_failed",
                ) from exc
            ready_deadline = time.monotonic() + STARTUP_READY_TIMEOUT_SECONDS
            while time.monotonic() < ready_deadline:
                info = _read_info(info_path, fingerprint=fingerprint)
                if info is not None:
                    return info
                exit_code = process.poll()
                if exit_code is not None:
                    diagnostic = _startup_diagnostic(
                        _read_startup_stderr(startup_stderr)
                    )
                    if diagnostic is not None:
                        code, message = diagnostic
                        raise EffectRuntimeStartupError(
                            message,
                            diagnostic_code=code,
                        )
                    raise EffectRuntimeStartupError(
                        "TypeScript Effect runtime exited before becoming ready "
                        f"(exit_code={exit_code})",
                        diagnostic_code="runtime_exited_before_ready",
                    )
                time.sleep(STARTUP_POLL_SECONDS)
            if process.poll() is None:
                process.terminate()
            raise EffectRuntimeStartupError(
                "TypeScript Effect runtime did not become ready before the startup deadline",
                diagnostic_code="runtime_startup_timeout",
            )
    finally:
        lock.unlink(missing_ok=True)


def effect_runtime_request(
    method: str,
    params: Mapping[str, Any],
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    retry_safe: bool = True,
    large_local_snapshot: bool = False,
) -> dict[str, Any]:
    """Call the managed TS runtime, retrying only idempotent typed effects."""

    fingerprint = _runtime_fingerprint_for_request()
    info_path = _runtime_info_path(fingerprint)
    request_id = str(uuid.uuid4())
    last_error: OSError | RuntimeError | None = None
    for attempt in range(2 if retry_safe else 1):
        info: dict[str, Any] | None = None
        try:
            info = _read_info(info_path, fingerprint=fingerprint)
            if info is None:
                info = _start_runtime(fingerprint=fingerprint, info_path=info_path)
            return _request_with_info(
                info,
                request_id=request_id,
                method=method,
                params=params,
                timeout=timeout,
                large_local_snapshot=large_local_snapshot,
            )
        except (EffectRuntimeRemoteError, EffectRuntimeResponseAmbiguous):
            raise
        except EffectRuntimeStartupError as exc:
            last_error = exc
            if attempt == 0 and retry_safe:
                continue
            raise
        except TimeoutError as exc:
            # A connect timeout is not evidence that an existing runtime died.
            # In particular it must not replace a live server which may still
            # be completing an earlier mutation under the per-Goal lock.
            raise EffectRuntimeStartupError(
                f"TypeScript Effect runtime did not connect for {method} "
                f"within {timeout:g}s",
                diagnostic_code="runtime_request_timeout",
            ) from exc
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if attempt == 0 and retry_safe:
                # Only pre-send connection failures reach this branch. Re-read
                # the locator on retry: reap our own exited child first so a
                # zombie is rejected by _read_info, while a live server may
                # simply be draining.
                # Even a token check followed by unlink would race with a
                # replacement server publishing its own locator.
                _reap_exited_runtime_child(info)
                continue
            break
    if isinstance(last_error, TimeoutError):
        # Name the method and the budget it was given: a caller that sized its
        # own timeout too small cannot repair anything from "request failed".
        raise EffectRuntimeStartupError(
            f"TypeScript Effect runtime did not answer {method} within "
            f"{timeout:g}s",
            diagnostic_code="runtime_request_timeout",
        ) from last_error
    raise EffectRuntimeStartupError(
        f"TypeScript Effect runtime request failed for {method}",
        diagnostic_code="runtime_request_failed",
    ) from last_error


def effect_runtime_result(
    method: str,
    params: Mapping[str, Any],
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    retry_safe: bool = True,
    large_local_snapshot: bool = False,
) -> Any:
    return effect_runtime_request(
        method,
        params,
        timeout=timeout,
        retry_safe=retry_safe,
        large_local_snapshot=large_local_snapshot,
    ).get("result")


def _serving_runtime_identity() -> dict[str, Any] | None:
    """Project the identity of the runtime serving this revision, if any."""

    fingerprint = _runtime_fingerprint()
    info = _read_info(_runtime_info_path(fingerprint), fingerprint=fingerprint)
    return runtime_identity_from_info(info)


def _sqlite_restart_recommendation(identity: Mapping[str, Any] | None) -> str | None:
    """Name the repair for a serving runtime that cannot run SQLite authority."""

    if identity is None or identity.get("sqlite_authority_qualified") is True:
        return None
    # The File path is ready but the SQLite authority lane is not: the serving
    # runtime keeps its own Node, so installing Node alone would not repair it.
    return (
        "The managed Effect runtime serving this revision runs Node "
        f"{identity.get('node_version')} with SQLite "
        f"{identity.get('sqlite_version')}, which lacks the WAL-reset fix. "
        "Install the qualified Node 22.22.3 runtime and run "
        "`loopx doctor --restart-runtime` so the next request starts a new "
        "runtime; the SQLite authority lane stays unavailable until then."
    )


def collect_effect_runtime_readiness(*, deep: bool = False) -> dict[str, object]:
    """Report whether the managed TS Effect runtime can serve control-plane work."""

    status, _executable, version = _probe_node()
    ready = status == "ready"
    runtime_state = "unavailable"
    runtime_diagnostic_code: str | None = None
    runtime_identity: dict[str, Any] | None = None
    if ready:
        try:
            fingerprint = _runtime_fingerprint()
            info = _read_info(
                _runtime_info_path(fingerprint),
                fingerprint=fingerprint,
            )
            runtime_state = "running" if info is not None else "stopped"
            runtime_identity = runtime_identity_from_info(info)
        except (OSError, EffectRuntimeStartupError) as exc:
            ready = False
            status = "package_invalid"
            runtime_diagnostic_code = getattr(
                exc,
                "diagnostic_code",
                "packaged_runtime_source_unreadable",
            )
    runtime_lifecycle: dict[str, object] = {
        "schema_version": "loopx_effect_runtime_lifecycle_v0",
        "management": "on_demand_managed",
        "state": runtime_state,
        "manual_start_required": False,
        "restart_policy": "automatic_on_next_control_plane_request",
        "idle_shutdown": True,
        "diagnostic_code": runtime_diagnostic_code,
    }
    result: dict[str, object] = {
        "schema_version": EFFECT_RUNTIME_READINESS_SCHEMA_VERSION,
        "ready": ready,
        "status": status,
        "required_for": ["control_plane"],
        "default_cli_blocking": True,
        "minimum_node_version": MINIMUM_NODE_VERSION_TEXT,
        "detected_node_version": version,
        "runtime_identity": runtime_identity,
        "semantic_probe": "not_requested" if not deep else "not_run",
        "runtime_lifecycle": runtime_lifecycle,
        "recommended_action": (
            None
            if ready
            else (
                f"Install Node.js {MINIMUM_NODE_VERSION_TEXT} or newer, then "
                "rerun `loopx doctor --deep`."
                if status in {"missing", "unsupported"}
                else "Repair Node.js on PATH, then rerun `loopx doctor --deep`."
            )
        ),
    }
    if ready:
        result["recommended_action"] = (
            _sqlite_restart_recommendation(runtime_identity)
            or result["recommended_action"]
        )
    if not ready or not deep:
        return result
    try:
        ping = effect_runtime_result("runtime.ping", {})
        identity = effect_runtime_result(
            "settlement.identity",
            {
                "goal_id": "doctor-probe",
                "agent_id": "doctor-probe",
                "todo_id": "doctor-probe",
                "turn_instance_id": "doctor-probe",
            },
        )
    except RuntimeError as exc:
        diagnostic_code = getattr(exc, "diagnostic_code", "semantic_probe_failed")
        return {
            **result,
            "ready": False,
            "status": "probe_failed",
            "semantic_probe": "failed",
            "runtime_lifecycle": {
                **runtime_lifecycle,
                "state": "unavailable",
                "diagnostic_code": diagnostic_code,
            },
            "recommended_action": (
                "Run `loopx doctor --deep` again after any concurrent startup "
                "finishes. If the same diagnostic code remains, reinstall LoopX "
                "and verify Node.js before retrying."
            ),
        }
    if (
        not isinstance(ping, Mapping)
        or ping.get("ready") is not True
        or not isinstance(identity, Mapping)
        or identity.get("effect_id")
        != "doctor-probe:doctor-probe:doctor-probe:doctor-probe"
    ):
        return {
            **result,
            "ready": False,
            "status": "probe_failed",
            "semantic_probe": "failed",
            "runtime_lifecycle": {
                **runtime_lifecycle,
                "state": "unavailable",
                "diagnostic_code": "semantic_probe_shape_mismatch",
            },
            "recommended_action": (
                "Reinstall LoopX and verify the packaged TypeScript runtime with "
                "`loopx doctor --deep`."
            ),
        }
    # The probe just started the runtime, so its identity is only readable now;
    # report the Node/SQLite pair that will serve the following requests.
    serving_identity = _serving_runtime_identity() or runtime_identity
    return {
        **result,
        "semantic_probe": "passed",
        "runtime_identity": serving_identity,
        "runtime_lifecycle": {
            **runtime_lifecycle,
            "state": "running",
            "diagnostic_code": None,
        },
        "recommended_action": (
            _sqlite_restart_recommendation(serving_identity)
            or result["recommended_action"]
        ),
    }
