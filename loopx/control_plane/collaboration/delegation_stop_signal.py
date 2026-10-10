"""Translate worker termination into an explicit delegation stop when requested.

This host adapter owns signal unwinding only. The delegation service persists
ACKs and the typed control plane decides whether execution has drained.
"""
from __future__ import annotations

import os
import signal
from pathlib import Path


class DelegationStopRequested(BaseException):
    """A stop reached the worker that owns this operation; it must acknowledge, not finish.

    A ``BaseException`` like ``KeyboardInterrupt``: a termination request must
    not be swallowed by an ``except Exception`` and turned into further work.
    """

    def __init__(self, source: str) -> None:
        super().__init__(source)
        self.source = source


class DelegationFenced(DelegationStopRequested):
    """A stop this process never acknowledged fences its execution-record write.

    The write is refused before it happens: a late-returning or other-host
    worker records no Turn result, completes no Todo and publishes nothing.
    """

    def __init__(self) -> None:
        super().__init__("fenced")


class WorkerStopSignal:
    """Turn SIGTERM into a stop request only when a stop was written for this operation.

    Without a stop receipt the signal keeps its default meaning, so a shutdown
    still leaves the operation recoverable by ``resume`` instead of stopping it.
    Later signals are absorbed while the acknowledgement is written.
    """

    def __init__(self, stop_path: Path) -> None:
        self.stop_path = stop_path
        self.armed = True

    def __call__(self, signum: int, frame: object) -> None:
        if not self.armed:
            return
        try:
            requested = self.stop_path.exists()
        except OSError:
            requested = False
        if not requested:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        self.armed = False
        raise DelegationStopRequested("SIGTERM")

    def disarm(self) -> None:
        self.armed = False


def install_worker_stop_signal(stop_path: Path) -> WorkerStopSignal | None:
    """Install the detached worker's SIGTERM handler; ``None`` where signals are unsupported."""

    if not hasattr(signal, "SIGTERM"):
        return None
    handler = WorkerStopSignal(stop_path)
    try:
        signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):
        # Not the main thread, or a platform without handler support: the
        # worker still honours stop files at every checkpoint and fenced write.
        return None
    return handler
