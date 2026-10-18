"""Bounded, public-safe progress on stderr; JSON stdout stays machine readable."""

from __future__ import annotations

import sys
import threading
import time


class UpdateProgress:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.started = time.monotonic()
        self.stage = "Checking installation"
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def report(self, stage: str) -> None:
        if not self.enabled:
            return
        self.stage = stage
        self._write()
        if self.thread is None:
            self.thread = threading.Thread(target=self._tick, daemon=True)
            self.thread.start()

    def _write(self) -> None:
        print(
            f"LoopX: {self.stage} ({int(time.monotonic() - self.started)}s elapsed)",
            file=sys.stderr,
            flush=True,
        )

    def _tick(self) -> None:
        while not self.stopped.wait(10):
            self._write()

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
