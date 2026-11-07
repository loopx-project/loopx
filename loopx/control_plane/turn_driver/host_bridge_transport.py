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
from typing import Any
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from ...extensions.process_runtime import (
        force_terminate_process as force_terminate_process,
        write_process_stdin_before_deadline as write_process_stdin_before_deadline,
    )

# Preview needs the bridge type; process helpers belong to execution or an
# explicit caller import. Keep their existing identity and patch seams.
_PROCESS_EXPORTS = ("force_terminate_process", "write_process_stdin_before_deadline")


def __getattr__(name: str) -> Any:
    if name not in _PROCESS_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ...extensions import process_runtime

    value = getattr(process_runtime, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PROCESS_EXPORTS))


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

    def next(self, *, deadline: float | None = None) -> dict[str, Any] | None:
        try:
            item = self.pending.get(timeout=None if deadline is None else max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError("managed Host bridge launch deadline expired") from None
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
        write_stdin = getattr(_sys.modules[__name__], "write_process_stdin_before_deadline")
        write_stdin(self.process, frame, deadline=self.deadline, cancel_and_reap=self.cancel)

    def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        deadline = time.monotonic() + self.cleanup_timeout
        try:
            if not self.requested:
                # No request bytes have been sent, so this bridge cannot own a
                # Host. This also handles suspension before JS handlers exist.
                getattr(_sys.modules[__name__], "force_terminate_process")(
                    self.process, process_tree_owned=True)
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
                getattr(_sys.modules[__name__], "force_terminate_process")(
                    self.process, process_tree_owned=True)
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


__all__ = [name for name in globals() if not name.startswith("_")] + list(_PROCESS_EXPORTS)
