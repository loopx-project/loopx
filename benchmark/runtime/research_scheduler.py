"""Let durable research feedback interrupt only the outer scheduler's sleep.

The event is a hint to read current quota again. It grants no model admission,
changes no Turn or native operation identity, and cannot reset a phase budget.
The ordinary scheduler still owns its state route, dispatch and stop handling.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import signal
import time

from .layout import _CONTROL


class FeedbackEventSleep:
    """Bounded local file observation while the ordinary worker is asleep."""

    def __init__(self, path: Path, *, clock=time.monotonic, pause=time.sleep):
        self.path, self.clock, self.pause = path, clock, pause
        # Capture before the first quota read, not on entry into sleep: a
        # consumer may publish between the decision and its eventual sleep.
        self.revision = self._revision()

    def _revision(self):
        try:
            with self.path.open("r", encoding="utf-8") as source:
                value = json.loads(source.read(4097))
            revision = value.get("revision") if isinstance(value, dict) else None
            return revision if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{64}", revision) else None
        except (OSError, ValueError):
            return None  # Unavailable/partial signals never substitute for state.

    def __call__(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("scheduler sleep requires a finite nonnegative duration")
        deadline = self.clock() + seconds
        while True:
            current = self._revision()
            if current is not None and current != self.revision:
                self.revision = current
                return
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            self.pause(min(1.0, remaining))


def main(argv=None):
    if os.environ.get("LOOPX_RESEARCH_LOOP") != "1":
        raise ValueError("research feedback scheduler requires the explicit research-loop treatment")
    from scripts.external_scheduler_worker import main as run_scheduler

    root = Path(os.environ.get("LOOPX_RESEARCH_FEEDBACK_ROOT", _CONTROL + "/native-feedback"))
    return run_scheduler(argv, sleep=FeedbackEventSleep(root / "ingestion" / "wake.json"))


if __name__ == "__main__":
    def cancelled(signum, frame):
        raise KeyboardInterrupt("scheduler cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    raise SystemExit(main())
