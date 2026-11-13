"""Bound the private bridge handoff; TypeScript still owns Host cleanup."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys as _sys
import threading
import time
from collections.abc import Callable
from typing import Any

# Keep the historical exports and caller patch points, but select the OS
# transport only when used. Decisions and deadlines remain with the caller.
_PROCESS_RUNTIME_EXPORTS = (
    "force_terminate_process", "write_process_stdin_before_deadline",
)


def __getattr__(name: str) -> Any:
    if name not in _PROCESS_RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ...extensions import process_runtime

    for export in _PROCESS_RUNTIME_EXPORTS:
        globals().setdefault(export, getattr(process_runtime, export))
    return globals()[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PROCESS_RUNTIME_EXPORTS))


def _export(name: str) -> Any:
    return getattr(_sys.modules[__name__], name)


# An already selected owner retains the former import-time binding, including
# owner replacements installed before this module was imported.
if "loopx.extensions.process_runtime" in _sys.modules:
    for _name in _PROCESS_RUNTIME_EXPORTS:
        globals()[_name] = getattr(_sys.modules["loopx.extensions.process_runtime"], _name)


_RUNNING_CHECK_SECONDS = 1.0


class GuardedHostBridge:
    """Keep at most one transient output frame while stdin is backpressured."""

    def __init__(self, process: subprocess.Popen[str], *, deadline: float, cleanup_timeout: float) -> None:
        assert process.stdout is not None
        self.output = process.stdout
        self.process, self.deadline, self.cleanup_timeout = process, deadline, cleanup_timeout
        self.pending: queue.Queue[str | BaseException | None] = queue.Queue(maxsize=1)
        self._stop_reading = threading.Event()
        self.requested = self.cancelled = self.not_launched = False
        self.result: dict[str, Any] | None = None
        self._next_running_check = 0.0
        self.reader = threading.Thread(target=self._read, name="loopx-host-bridge-reader", daemon=True)
        self.reader.start()

    def _publish(self, item: str | BaseException | None) -> bool:
        if self._stop_reading.is_set():
            return False
        self.pending.put(item)
        return not self._stop_reading.is_set()

    def _read(self) -> None:
        try:
            for line in self.output:
                if not self._publish(line):
                    break
        except BaseException as error:
            self._publish(error)
        finally:
            self._publish(None)

    def next(self, *, deadline: float | None = None,
             check_running: Callable[[], None] | None = None) -> dict[str, Any] | None:
        while True:
            read_until = deadline
            if check_running is not None:
                if time.monotonic() >= self._next_running_check:
                    check_running()
                    self._next_running_check = time.monotonic() + _RUNNING_CHECK_SECONDS
                read_until = self._next_running_check
            try:
                item = self.pending.get(timeout=None if read_until is None else max(0, read_until - time.monotonic()))
            except queue.Empty:
                if check_running is None:
                    raise TimeoutError("managed Host bridge launch deadline expired") from None
            else:
                break
        if isinstance(item, BaseException):
            raise item
        if item is None:
            return None
        event = json.loads(item)
        if not isinstance(event, dict):
            raise RuntimeError("invalid managed Host bridge observation")
        return event

    def send(self, frame: str) -> None:
        if self.next(deadline=self.deadline) != {"kind": "ready"}:
            raise RuntimeError("managed Host bridge did not become ready")
        self.requested = True
        _export("write_process_stdin_before_deadline")(self.process, frame, deadline=self.deadline,
            cancel_and_reap=self.cancel)

    def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        deadline = time.monotonic() + self.cleanup_timeout
        try:
            if not self.requested:
                # No request bytes have been sent, so this bridge cannot own a
                # Host. This also handles suspension before JS handlers exist.
                _export("force_terminate_process")(self.process, process_tree_owned=True)
                self.not_launched = True
            elif self.process.poll() is None:
                self.process.terminate()
                if os.name == "posix":
                    # An owned suspended bridge must run its abort handler and
                    # report cleanup before the Goal fence can be released.
                    try:
                        os.kill(self.process.pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
            while (event := self.next(deadline=deadline)) is not None:
                if event.get("kind") == "not_launched":
                    self.not_launched = True
                elif event.get("kind") == "result":
                    self.result = event
            self.process.wait(timeout=max(0, deadline - time.monotonic()))
            self.reader.join(timeout=max(0, deadline - time.monotonic()))
            if (self.reader.is_alive() or (self.requested and
                    (self.process.returncode != 0 or not (self.not_launched or self.result)))):
                raise RuntimeError("managed Host bridge cleanup unconfirmed")
        except BaseException as error:
            # Killing the bridge alone cannot certify an unobserved detached
            # Host. Retain the launching record and fail instead of claiming it.
            if self.process.poll() is None:
                _export("force_terminate_process")(self.process, process_tree_owned=True)
            raise RuntimeError("managed Host bridge cleanup unconfirmed") from error

    def close_output(self) -> None:
        self._stop_reading.set()
        # No caller remains to consume frames after cleanup fails. Release the
        # one blocked producer before joining; this does not certify Host exit.
        try:
            self.pending.get_nowait()
        except queue.Empty:
            pass
        self.reader.join(timeout=self.cleanup_timeout)
        if not self.reader.is_alive():
            self.output.close()


# Star imports retain every former public name, including the lazy reexports.
__all__ = sorted(
    {name for name in globals() if not name.startswith("_")} | set(_PROCESS_RUNTIME_EXPORTS)
)
