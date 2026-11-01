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

    def close(self, *, timeout: float = 2) -> None:
        self.stop.set()
        thread = self.thread
        if thread.ident is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
