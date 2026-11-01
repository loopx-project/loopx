"""Process-scoped lifecycle IO for opted-in background service owners.

No second persisted configuration, job queue or effect authority lives here.
Owners re-read their grants on reconciliation; TypeScript projects health from
passive observations. The process supervisor remains the App/OS service host.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time

from .background_runtime import HostedWorker
from ..control_plane.effect_runtime import effect_runtime_result


@dataclass(frozen=True)
class BackgroundTask:
    task_id: str
    observe: Callable[[], dict]


@dataclass(frozen=True)
class BackgroundService:
    service_id: str
    tasks: tuple[BackgroundTask, ...]
    reconcile: Callable[[], None]
    close: Callable[[], None]


def worker_observation(worker: HostedWorker, *, enabled: bool = True) -> dict:
    return {"available": True, "enabled": enabled, "worker": worker.snapshot()}


class BackgroundServices:
    """One registry supervises existing owners; it never retries their effects."""

    def __init__(self) -> None:
        self._services: dict[str, BackgroundService] = {}
        self._errors: set[str] = set()
        self._lifecycle = threading.RLock()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self._started = False
        self._supervisor = HostedWorker(name="loopx-background-services", stop=self._stop,
            target=self._run, retry_seconds=15)

    def register(self, service: BackgroundService) -> None:
        with self._lifecycle, self._state_lock:
            ids = [task.task_id for item in self._services.values() for task in item.tasks]
            incoming = [task.task_id for task in service.tasks]
            if (self._started or self._stop.is_set() or service.service_id in self._services or not incoming
                    or len(set(incoming)) != len(incoming) or set(ids) & set(incoming)):
                raise ValueError("Duplicate or closed background registration")
            self._services[service.service_id] = service

    def start(self) -> None:
        with self._state_lock:
            self._started = True
        self._supervisor.start()

    def _run(self) -> None:
        self._supervisor.repeat(self.reconcile, interval_seconds=15)

    def reconcile(self) -> None:
        with self._lifecycle:
            for service in self._services.values():
                if self._stop.is_set():
                    return
                try:
                    # Each owner performs its own fresh permission/intent read.
                    # start() is idempotent; live or completed work is not reset.
                    service.reconcile()
                    with self._state_lock:
                        self._errors.discard(service.service_id)
                except Exception:
                    # Isolate owners and withhold private provider diagnostics.
                    with self._state_lock:
                        self._errors.add(service.service_id)
                self._supervisor.checkpoint()

    def snapshot(self) -> dict:
        # Copy the registry, then observe outside lifecycle mutation. A GET must
        # neither repair a stopped task nor manufacture a progress heartbeat.
        with self._state_lock:
            services, errors = tuple(self._services.values()), self._errors.copy()
        rows = []
        for service in services:
            for task in service.tasks:
                try:
                    observation = task.observe()
                except Exception:
                    observation = {"available": False, "enabled": None}
                rows.append({**observation, "id": task.task_id, "service_id": service.service_id,
                             "reconcile_failed": service.service_id in errors})
        return effect_runtime_result("runtime.background.project", {
            "now": time.time(), "tasks": rows,
            "supervisor": worker_observation(self._supervisor),
        })

    def close(self) -> None:
        # Fence before joining; even a concurrent recheck cannot resurrect work.
        self._stop.set()
        self._supervisor.close()
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
            for service in reversed(tuple(self._services.values())):
                try:
                    service.close()
                except Exception:
                    with self._state_lock:
                        self._errors.add(service.service_id)
