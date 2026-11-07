"""A common lifecycle must preserve owner authority and diagnose real progress."""
import threading
import time

from loopx.extensions.background_runtime import HostedWorker
from loopx.extensions.background_services import (
    BackgroundService, BackgroundServices, BackgroundTask, worker_observation,
)


def test_passive_reads_report_stalled_live_work_without_replay_or_fake_success(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked():
        calls.append(True)
        entered.set()
        release.wait(3)

    worker = HostedWorker(name="synthetic-blocked", target=blocked, stop=threading.Event(),
                          progress_deadline_seconds=20)
    runtime = BackgroundServices()
    runtime.register(BackgroundService("fixture", (
        BackgroundTask("blocked", lambda: worker_observation(worker)),
    ), worker.start, worker.close))
    try:
        runtime.reconcile()
        assert entered.wait(1)
        assert runtime.snapshot()["tasks"][0]["state"] == "checking"
        before = worker.snapshot()
        now[0] += 21
        assert runtime.snapshot()["tasks"][0]["state"] == "overdue"
        runtime.reconcile()
        assert calls == [True] and worker.snapshot() == before
        assert runtime.snapshot()["tasks"][0]["last_success_at"] is None
    finally:
        release.set()
        runtime.close()
    runtime.reconcile()
    assert calls == [True]


def test_automatic_reconciliation_isolated_failure_and_shutdown_fence(monkeypatch):
    from loopx.extensions import background_services as module
    period = HostedWorker.repeat
    # Shorten only the common observation cadence, keeping the real thread,
    # repetition, error isolation and owner callbacks in this test.
    monkeypatch.setattr(module.HostedWorker, "repeat", lambda self, step, **kwargs:
                        period(self, step, interval_seconds=.01))
    recovered, stop = threading.Event(), threading.Event()
    attempts, closed = [], []
    runtime = BackgroundServices()

    def reconcile():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("private diagnostics must not escape")
        recovered.set()

    runtime.register(BackgroundService("failed-once", (
        BackgroundTask("task", lambda: {"available": True, "enabled": False}),
    ), reconcile, lambda: closed.append(True)))
    runtime.register(BackgroundService("other", (
        BackgroundTask("other-task", lambda: {"available": True, "enabled": False}),
    ), stop.set, lambda: None))
    runtime.start()
    assert recovered.wait(2) and stop.is_set(), "One failing owner must not starve another"
    runtime.close()
    count = len(attempts)
    runtime.close()
    runtime.reconcile()
    assert len(attempts) == count and closed == [True]


def test_owner_rechecks_revocation_before_effect_and_completed_work_stays_terminal():
    grant, calls = [False], []
    worker = HostedWorker(name="synthetic-once", target=lambda: calls.append(True), stop=threading.Event())
    runtime = BackgroundServices()
    runtime.register(BackgroundService("fixture", (
        BackgroundTask("task", lambda: worker_observation(worker, enabled=grant[0])),
    ), lambda: worker.start() if grant[0] else None, worker.close))
    runtime.reconcile()
    assert runtime.snapshot()["tasks"][0]["state"] == "disabled" and calls == []
    grant[0] = True
    runtime.reconcile()
    worker.thread.join(2)
    assert runtime.snapshot()["tasks"][0]["state"] == "completed"
    runtime.reconcile()
    assert calls == [True]
    runtime.close()


def test_a_stuck_reconciler_cannot_block_its_own_health_readback():
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    runtime = BackgroundServices()

    def reconcile():
        entered.set()
        release.wait(3)

    runtime.register(BackgroundService("fixture", (
        BackgroundTask("task", lambda: {"available": True, "enabled": False}),
    ), reconcile, lambda: None))
    runtime.start()
    assert entered.wait(1)
    reader = threading.Thread(target=lambda: (runtime.snapshot(), observed.set()))
    try:
        reader.start()
        assert observed.wait(1), "Diagnostics must not share the blocked mutation lock"
    finally:
        release.set()
        reader.join(2)
        runtime.close()
