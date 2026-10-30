"""Passive native-process evidence for the existing benchmark worker.

The shared stdio transport owns JSON-RPC; the worker owns process cleanup.
These observations grant neither retry authority nor a root-cause verdict.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from ...control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport


def _process_status(process: subprocess.Popen[str]) -> dict[str, Any]:
    code = process.poll()
    return {
        "observed_at_unix": time.time(),
        "return_code": code,
        "signal": -code if os.name == "posix" and code is not None and code < 0 else None,
    }


class NativeCodexProcessObservation:
    """Keep a bounded, content-free snapshot across process and cleanup edges."""

    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self._publish = publish
        self._data: dict[str, Any] = {
            "schema_version": "native_codex_process_observation_v1",
            "pid": None,
            "started_at_unix": None,
            "linux_start_ticks": None,
            "first_failure": None,
            "before_cleanup": None,
            "cleanup_started_at_unix": None,
            "cleanup_trigger": None,
            "scope_error_type": None,
            "cleanup_signals": [],
            "after_cleanup": None,
            "cleanup_error_type": None,
            "persistence_error_type": None,
        }

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def _emit(self) -> None:
        snapshot = self.snapshot()
        try:
            self._publish(snapshot)
        except OSError as exc:
            # Observation failure must neither replace the original exception
            # nor prevent the worker from reaping its owned process group.
            self._data["persistence_error_type"] = type(exc).__name__
            snapshot["persistence_error_type"] = type(exc).__name__

    def started(self, process: subprocess.Popen[str]) -> None:
        self._data.update(pid=process.pid, started_at_unix=time.time())
        try:
            # A missing /proc (or a child that already exited) is unknown. The
            # parent timestamp is not a substitute for a native start identity.
            stat = Path(f"/proc/{process.pid}/stat").read_text()
            self._data["linux_start_ticks"] = int(stat.rsplit(")", 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            pass
        self._emit()

    def failure(self, process: subprocess.Popen[str], operation: str, exc: Exception) -> None:
        if self._data["first_failure"] is not None:
            return
        self._data["first_failure"] = {
            "operation": operation,
            "error_type": type(exc).__name__,
            "error_sha256": sha256(str(exc).encode("utf-8", errors="replace")).hexdigest(),
            "observed_at_unix": time.time(),
            "process": _process_status(process),
        }
        self._emit()

    def before_cleanup(self, process: subprocess.Popen[str], scope_error: str | None) -> None:
        self._data["before_cleanup"] = _process_status(process)
        self._data["cleanup_started_at_unix"] = time.time()
        self._data["cleanup_trigger"] = "scope_exception" if scope_error else "scope_completed"
        self._data["scope_error_type"] = scope_error
        self._emit()

    def signal_requested(self, number: int) -> None:
        self._data["cleanup_signals"].append({"signal": int(number), "requested_at_unix": time.time()})
        self._emit()

    def after_cleanup(self, process: subprocess.Popen[str], error: str | None) -> None:
        self._data["after_cleanup"] = _process_status(process)
        self._data["cleanup_error_type"] = error
        self._emit()


class ObservedStdioNativeGoalTransport(StdioNativeGoalTransport):
    """Observe failures at the shared transport API, without replay or new IO."""

    def __init__(self, process, *, observation, response_timeout_sec=30):
        self.observation = observation
        super().__init__(process, response_timeout_sec=response_timeout_sec)

    def request(self, method, params):
        try:
            return super().request(method, params)
        except Exception as exc:
            self.observation.failure(self.process, "request", exc)
            raise

    def notify(self, method, params):
        try:
            return super().notify(method, params)
        except Exception as exc:
            self.observation.failure(self.process, "notify", exc)
            raise

    def next_event(self, *, timeout_sec):
        try:
            return super().next_event(timeout_sec=timeout_sec)
        except Exception as exc:
            self.observation.failure(self.process, "next_event", exc)
            raise


def write_process_observation(path: Path, observation: dict[str, Any]) -> None:
    """Atomically replace one caller-owned snapshot, retaining every earlier edge."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(observation, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
