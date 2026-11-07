"""Python worker hosting only; providers retain permission and effect ownership.

A live thread is not proof of provider health. Retry is explicit and applies to
the supplied host loop, never to an individual external/model request.
"""
from __future__ import annotations

from collections.abc import Callable
import logging
import math
import threading
import time


class HostedWorker:
    def __init__(self, *, name: str, target: Callable[[], None],
                 stop: threading.Event, retry_seconds: float | None = None,
                 progress_deadline_seconds: float = 300):
        if retry_seconds is not None and (not math.isfinite(retry_seconds) or retry_seconds <= 0):
            raise ValueError("Worker retry delay must be positive and finite")
        self.name, self.target = name, target
        self.stop, self.retry_seconds = stop, retry_seconds
        if not math.isfinite(progress_deadline_seconds) or progress_deadline_seconds <= 0:
            raise ValueError("Worker progress deadline must be positive and finite")
        # A diagnostic deadline, never cancellation or permission to replay.
        self.progress_deadline_seconds = progress_deadline_seconds
        self._lock = threading.Lock()
        self._terminal = False
        self._repeating = False
        self._progress = {"started_at": None, "last_success_at": None,
                          "last_failure_at": None, "next_check_at": None,
                          "last_progress_at": None, "deadline_at": None,
                          "failure_count": 0, "executing": False}
        self.thread = self._new_thread()

    def snapshot(self) -> dict:
        """Passive facts; reading never advances progress or starts work."""
        with self._lock:
            return {**self._progress, "alive": self.thread.is_alive(),
                    "stop_requested": self.stop.is_set(), "terminal": self._terminal}

    def checkpoint(self) -> None:
        """Report a completed unit inside a longer scan without declaring success."""
        with self._lock:
            if self._progress["executing"]:
                self._progress["last_progress_at"] = time.time()
                self._progress["deadline_at"] = time.time() + self.progress_deadline_seconds

    def _begin(self) -> None:
        with self._lock:
            self._progress.update(started_at=time.time(), last_progress_at=time.time(), executing=True, next_check_at=None,
                                  deadline_at=time.time() + self.progress_deadline_seconds)

    def _settle(self, *, failed: bool, delay: float | None) -> None:
        now = time.time()
        with self._lock:
            self._progress.update(executing=False, last_progress_at=now,
                next_check_at=now + delay if delay is not None else None,
                deadline_at=now + delay + 15 if delay is not None else None)
            if failed:
                self._progress.update(last_failure_at=now, failure_count=self._progress["failure_count"] + 1)
            else:
                self._progress.update(last_success_at=now, failure_count=0)

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
                self._begin()
                self.target()
                # Normal completion, including a one-shot discovery, is not a
                # failure and must not be automatically replayed.
                with self._lock:
                    self._terminal = True
                if not self._repeating:
                    self._settle(failed=False, delay=None)
                return
            except Exception:
                self._settle(failed=True, delay=self.retry_seconds)
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
        Progress records actual step completion. A live thread alone never
        establishes a successful check or provider/request health.
        """
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("Worker interval must be positive and finite")
        self._repeating = True
        while not self.stop.is_set():
            failed = False
            try:
                self._begin()
                requested = step()
                delay = interval_seconds if requested is None else requested
                if not math.isfinite(delay) or delay <= 0:
                    raise ValueError("Worker step delay must be positive and finite")
            except Exception:
                self._settle(failed=True, delay=self.retry_seconds)
                if on_error is not None:
                    on_error()
                else:
                    logging.getLogger(__name__).warning("Background step failed; provider details withheld")
                if self.retry_seconds is None:
                    return
                delay = self.retry_seconds
                failed = True
            if not failed:
                self._settle(failed=False, delay=delay)
            if not self.stop.is_set():
                # A hint may advance a normal scan, never bypass error backoff.
                (self.stop.wait if failed else (wait or self.stop.wait))(delay)

    def close(self, *, timeout: float = 2) -> None:
        self.stop.set()
        thread = self.thread
        if thread.ident is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
