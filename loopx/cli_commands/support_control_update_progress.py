"""Observable update progress; stdout remains a single final JSON document."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

from .update_artifact import atomic_json


class UpdateProgress:
    def __init__(self, enabled: bool = True, *, json_output: bool = False,
                 operation_id: str | None = None, status_path: Path | None = None,
                 pending: bool = False) -> None:
        self.enabled = enabled
        self.json_output = json_output
        self.operation_id = None if pending else (operation_id or os.environ.get("LOOPX_UPDATE_OPERATION_ID") or uuid4().hex)
        self.status_path = status_path or (Path(os.environ["LOOPX_UPDATE_STATUS_PATH"])
            if os.environ.get("LOOPX_UPDATE_STATUS_PATH") else None)
        self.started = time.monotonic()
        self.changed = self.started
        self.stage_started = self.started
        self.stage = "resolve"
        self.received: int | None = None
        self.total: int | None = None
        self.rate = 0.0
        self.last_sample = (self.started, 0)
        self.durations: dict[str, float] = {}
        self.target: dict | None = None
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None

    def report(self, event: str | dict) -> None:
        with self.lock:
            now = time.monotonic()
            details = event if isinstance(event, dict) else {"stage": event}
            if isinstance(details.get("target"), dict):
                self.target = details["target"]
            stage = str(details.get("stage", self.stage))
            if stage != self.stage:
                self.durations[self.stage] = self.durations.get(self.stage, 0) + now - self.stage_started
                self.stage, self.stage_started = stage, now
                self.received, self.total, self.rate = None, None, 0
                self.last_sample = (now, 0)
                self.changed = now
            received = details.get("received")
            if type(received) is int and received >= 0:
                if received != self.received:
                    self.changed = now
                previous_time, previous_bytes = self.last_sample
                self.rate = max(0, received - previous_bytes) / max(.001, now - previous_time)
                self.received = received
                self.last_sample = (now, received)
            total = details.get("total")
            if type(total) is int and total > 0:
                self.total = total
            self._write()
            if self.thread is None and not self.stopped.is_set():
                self.thread = threading.Thread(target=self._tick, daemon=True)
                self.thread.start()

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "schema_version": "loopx_update_progress_v1", "operation_id": self.operation_id,
            "pid": os.getpid(), "stage": self.stage, "status": "running",
            "target": self.target,
            "received": self.received, "total": self.total,
            "bytes_per_second": round(self.rate, 1) if now - self.last_sample[0] < 5 else 0,
            "elapsed_seconds": round(now - self.started, 1),
            "stage_seconds": round(now - self.stage_started, 1),
            "last_progress_at": round(time.time() - (now - self.changed), 3),
            "updated_at": time.time(), "stalled": now - self.changed >= 10,
            "stage_durations": {key: round(value, 3) for key, value in {
                **self.durations, self.stage: self.durations.get(self.stage, 0) + now - self.stage_started}.items()},
        }

    def _write(self) -> None:
        event = self.snapshot()
        if self.status_path:
            atomic_json(self.status_path, event)
        if not self.enabled:
            return
        try:
            if self.json_output:
                print(json.dumps(event), file=sys.stderr, flush=True)
            else:
                amount = ""
                if self.received is not None:
                    total = f"{self.total / 1048576:.1f} MiB" if self.total else "unknown total"
                    amount = f" · {self.received / 1048576:.1f}/{total} · {event['bytes_per_second'] / 1024:.0f} KiB/s"
                waiting = " · no new progress for 10s" if event["stalled"] else ""
                identity = f" [{self.operation_id[:8]}]" if self.operation_id else ""
                print(f"LoopX{identity}: {self.stage}{amount} ({event['elapsed_seconds']}s){waiting}", file=sys.stderr, flush=True)
        except (BrokenPipeError, OSError):
            self.enabled = False

    def _tick(self) -> None:
        while not self.stopped.wait(5):
            with self.lock:
                self._write()

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
