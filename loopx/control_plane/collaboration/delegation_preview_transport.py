"""Single-worker Python IO bridge for TS-supervised read-only Turn previews.

Module reuse is scoped to one interpreter/release/environment/workspace/binding.
All decisions and file/authority reads still execute through the original CLI.
"""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
import weakref
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any

from ..effect_runtime import _node_executable


BRIDGE_CLOSE_TIMEOUT_SECONDS = 5.0


def _source_snapshot(release: Path) -> tuple:
    """Loaded-code identity only; authority/configuration is read per request."""
    files = []
    pending = [("loopx", os.fspath(release / "loopx"))]
    while pending:
        relative, directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                entries = sorted(entries, key=lambda item: item.name)
        except OSError:
            continue  # Match os.walk's directory-error behavior.
        children = []
        for entry in entries:
            name = os.path.join(relative, entry.name)
            try:
                is_directory = entry.is_dir()
            except OSError:
                is_directory = False  # os.walk classifies the same entry as a file.
            if is_directory:
                if not entry.is_symlink():
                    children.append((name, entry.path))
            elif entry.name.endswith((".py", ".ts", ".json")):
                metadata = entry.stat()
                files.append((name, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size))
        pending.extend(reversed(children))
    return tuple(files)


def _terminate_bridge(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        try:
            process.send_signal(signal.SIGCONT)
        except ProcessLookupError:
            return
    process.terminate()


def _kill_bridge(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _close_bridge(
    process: subprocess.Popen,
    *,
    force: bool = False,
    cleanup_confirmed: bool = False,
) -> bool:
    # Parent EOF cancels the TS-owned group; give its cleanup fence time to run.
    if force:
        _terminate_bridge(process)
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass  # A crashed/retired supervisor may already have closed its pipe.
    try:
        process.wait(timeout=BRIDGE_CLOSE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if not force:
            # SIGTERM asks the supervisor to clean, not to abandon its worker.
            _terminate_bridge(process)
            try:
                process.wait(timeout=BRIDGE_CLOSE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _kill_bridge(process)
                process.wait(timeout=BRIDGE_CLOSE_TIMEOUT_SECONDS)
        else:
            _kill_bridge(process)
            process.wait(timeout=BRIDGE_CLOSE_TIMEOUT_SECONDS)
    finally:
        if process.stdout is not None:
            process.stdout.close()
    # SIGTERM can win before the bridge installs its handlers, before it can
    # spawn a worker. Once initialized, normal exit follows Host group cleanup.
    # A SIGKILLed supervisor provides neither guarantee.
    return cleanup_confirmed or process.returncode in (0, -signal.SIGTERM)


class DelegationPreviewTransport:
    """No pool or background scheduler: at most one owned fixed-cwd worker."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._partition: tuple | None = None
        self._finalizer: weakref.finalize | None = None
        self._sequence = 0

    def _close(
        self, *, force: bool = False, cleanup_confirmed: bool = False
    ) -> bool:
        process, finalizer = self._process, self._finalizer
        if process is not None and not _close_bridge(
            process,
            force=force,
            cleanup_confirmed=cleanup_confirmed,
        ):
            return False
        self._process = self._partition = self._finalizer = None
        self._sequence = 0
        if finalizer is not None:
            finalizer.detach()
        return True

    def close(self) -> None:
        with self._lock:
            self._close()

    def preview(self, *, command: list[str], workspace: Path, release: Path,
                environment: dict[str, str], registry: Path, runtime_root: Path,
                goal_id: str, agent_id: str, todo_id: str,
                argv: tuple[str, ...], timeout: float) -> dict[str, Any]:
        started = time.monotonic()
        if not self._lock.acquire(timeout=timeout):
            raise subprocess.TimeoutExpired(["delegation-preview"], timeout)
        try:
            # Source metadata, not authority-result reuse. Include Python
            # modules as well as the TS owner; changes retire the loaded code.
            snapshot = _source_snapshot(release)
            metadata = workspace.stat()
            partition = (tuple(command), str(workspace.resolve()), metadata.st_dev,
                         metadata.st_ino, str(registry), str(runtime_root), goal_id,
                         agent_id, todo_id, snapshot,
                         hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).digest())
            for replacement in (False, True):
                if (self._partition != partition or self._process is None
                        or self._process.poll() is not None or self._sequence >= 128):
                    if not self._close():
                        raise ValueError(
                            "delegation preview cleanup remains unconfirmed"
                        )
                    bridge = Path(__file__).with_name("delegation_preview_bridge.ts")
                    self._process = subprocess.Popen(
                        [_node_executable(), "--no-warnings", "--experimental-strip-types", str(bridge)],
                        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                        start_new_session=True,
                    )
                    self._finalizer = weakref.finalize(self, _close_bridge, self._process)
                    self._partition = partition
                    self._send({"kind": "start", "request": {
                        "argv": [*command, "--registry", str(registry), "--runtime-root",
                                 str(runtime_root), "--goal-id", goal_id, "--agent-id",
                                 agent_id, "--todo-id", todo_id],
                        "cwd": str(workspace), "input": "", "timeout_ms": 60000,
                        "drain_timeout_ms": 2000, "stdout_limit_bytes": 1_048_576,
                    }}, started + timeout, timeout)
                    if self._read(started + timeout, timeout) != {"kind": "ready"}:
                        raise ValueError("delegation preview did not start")
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(["delegation-preview"], timeout)
                self._sequence += 1
                try:
                    self._send({"kind": "request", "id": self._sequence,
                                "argv": list(argv), "timeout_ms": min(remaining, 60) * 1000},
                               started + timeout, timeout)
                except BrokenPipeError:
                    # EOF alone is not permission to retry. A completed idle
                    # retirement may have left its cleanup fence in stdout.
                    pass
                response = self._read(started + timeout, timeout)
                if (not replacement and response == {"kind": "retired", "last_id": self._sequence - 1}
                        and type(response["last_id"]) is int):
                    # The TS owner confirms this request was not accepted and
                    # its old group stopped. Reuse the original deadline/binding.
                    self._close(cleanup_confirmed=True)
                    continue
                if response.get("kind") == "failure" and response.get("outcome") == "timeout":
                    raise subprocess.TimeoutExpired(["delegation-preview"], timeout)
                if response.get("kind") != "preview" or response.get("id") != self._sequence:
                    raise ValueError("delegation preview returned no structured result")
                if _source_snapshot(release) != snapshot:
                    raise ValueError("delegation preview source changed during inspection")
                return response["value"]
            raise ValueError("delegation preview retirement did not complete")
        except BaseException:
            self._close(force=True)
            raise
        finally:
            self._lock.release()

    def _send(self, value: dict, deadline: float, timeout: float) -> None:
        assert self._process is not None and self._process.stdin is not None
        stream = self._process.stdin
        data = (json.dumps(value) + "\n").encode("utf-8")

        if os.name == "posix":
            # A blocking write in another thread can retain the pipe after
            # close(), or finish a frame after the caller's deadline. This
            # transport exclusively owns stdin; keep its writes nonblocking
            # so the original owner can observe EOF during timeout cleanup.
            descriptor = stream.fileno()
            os.set_blocking(descriptor, False)
            remaining = memoryview(data)
            with selectors.DefaultSelector() as writable:
                writable.register(descriptor, selectors.EVENT_WRITE)
                while remaining:
                    budget = deadline - time.monotonic()
                    if budget <= 0 or not writable.select(budget):
                        raise subprocess.TimeoutExpired(["delegation-preview"], timeout)
                    try:
                        written = os.write(descriptor, remaining)
                    except BlockingIOError:
                        continue
                    if written <= 0:
                        raise OSError("preview input closed")
                    remaining = remaining[written:]
            return

        # Keep the existing Windows pipe writer path. Host cleanup remains
        # owned by the same supervisor on every platform.
        result: Queue = Queue(maxsize=1)

        def write() -> None:
            try:
                remaining = memoryview(data)
                while remaining:
                    written = os.write(stream.fileno(), remaining)
                    if written <= 0:
                        raise OSError("preview input closed")
                    remaining = remaining[written:]
                result.put(None)
            except Exception as error:
                result.put(error)

        # Raw pipe IO avoids a buffered-stream lock that would also block the
        # cleanup close while a writer waits for capacity. No decision runs in
        # this thread. Timeout closes the same control pipe and waits for the
        # original TS owner; uncertain cleanup still forbids a replacement.
        Thread(target=write, daemon=True).start()
        try:
            error = result.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            raise subprocess.TimeoutExpired(["delegation-preview"], timeout) from None
        if error is not None:
            raise error

    def _read(self, deadline: float, timeout: float) -> dict:
        assert self._process is not None and self._process.stdout is not None
        # A partial frame or a stalled supervisor startup must not turn a pipe
        # readline into an unbounded wait. This thread only reads bytes; timeout
        # closes parent stdin and asks the original TS Host owner to clean up.
        stream = self._process.stdout
        result: Queue = Queue(maxsize=1)

        def read() -> None:
            try:
                result.put((stream.readline(1_048_577), None))
            except Exception as error:
                result.put((None, error))

        Thread(target=read, daemon=True).start()
        try:
            line, error = result.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            raise subprocess.TimeoutExpired(["delegation-preview"], timeout) from None
        if error is not None:
            raise error
        if not line.endswith("\n") or len(line) > 1_048_576:
            raise ValueError("delegation preview transport unavailable")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("delegation preview observation is invalid")
        return value
