"""Python worker hosting only; providers retain permission and effect ownership.

A live thread is not proof of provider health. Retry is explicit and applies to
the supplied host loop, never to an individual external/model request.
"""
from __future__ import annotations

from collections.abc import Callable
import logging
import math
import threading


class HostedWorker:
    def __init__(self, *, name: str, target: Callable[[], None],
                 stop: threading.Event, retry_seconds: float | None = None):
        if retry_seconds is not None and (not math.isfinite(retry_seconds) or retry_seconds <= 0):
            raise ValueError("Worker retry delay must be positive and finite")
        self.name, self.target = name, target
        self.stop, self.retry_seconds = stop, retry_seconds
        self._lock = threading.Lock()
        self._terminal = False
        self.thread = self._new_thread()

    def _new_thread(self) -> threading.Thread:
        return threading.Thread(target=self._run, daemon=True, name=self.name)

    def start(self):
        with self._lock:
            if self.stop.is_set() or self._terminal or self.thread.is_alive():
                return self
            if self.thread.ident is not None:
                self.thread = self._new_thread()
            self.thread.start()
        return self

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                self.target()
                # Normal completion, including a one-shot discovery, is not a
                # failure and must not be automatically replayed.
                with self._lock:
                    self._terminal = True
                return
            except Exception:
                logging.getLogger(__name__).warning("Background worker failed; provider details withheld")
                if self.retry_seconds is None:
                    with self._lock:
                        self._terminal = True
                    return
                if self.stop.wait(self.retry_seconds):
                    return

    def repeat(self, step: Callable[[], float | None], *, interval_seconds: float,
               on_error: Callable[[], None] | None = None,
               wait: Callable[[float], object] | None = None) -> None:
        """Drive an opted-in reconciliation step without rebuilding its scope.

        The caller keeps its lease, pool, cursors and receipts around this call.
        A returned delay selects the next observation, not a request retry.
        Health hooks belong in the step/on_error and observe actual work; this
        runner never publishes healthy merely because its thread is alive.
        """
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("Worker interval must be positive and finite")
        while not self.stop.is_set():
            failed = False
            try:
                requested = step()
                delay = interval_seconds if requested is None else requested
                if not math.isfinite(delay) or delay <= 0:
                    raise ValueError("Worker step delay must be positive and finite")
            except Exception:
                if on_error is not None:
                    on_error()
                else:
                    logging.getLogger(__name__).warning("Background step failed; provider details withheld")
                if self.retry_seconds is None:
                    return
                delay = self.retry_seconds
                failed = True
            if not self.stop.is_set():
                # A hint may advance a normal scan, never bypass error backoff.
                (self.stop.wait if failed else (wait or self.stop.wait))(delay)

    def close(self, *, timeout: float = 2) -> None:
        self.stop.set()
        thread = self.thread
        if thread.ident is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
