"""Thread hosting must not imply provider health, replay or execution authority."""
import threading

from loopx.extensions.background_runtime import HostedWorker


def test_start_is_idempotent_and_close_preserves_a_cooperative_wait():
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def target():
        calls.append(True)
        entered.set()
        release.wait(3)

    worker = HostedWorker(name="synthetic-wait", target=target, stop=stop)
    worker.start()
    worker.start()
    assert entered.wait(1) and len(calls) == 1
    worker.close(timeout=.01)
    assert stop.is_set() and worker.thread.is_alive(), "A live drain is not a completed shutdown"
    release.set()
    worker.thread.join(timeout=2)
    worker.start()
    assert len(calls) == 1 and not worker.thread.is_alive(), "Explicit stop cannot restart"


def test_close_before_start_has_no_effect():
    called = threading.Event()
    worker = HostedWorker(name="synthetic-off", target=called.set, stop=threading.Event())
    worker.close()
    worker.start()
    assert not called.is_set()


def test_only_explicit_retry_restarts_a_failed_host_loop(caplog):
    recovered = threading.Event()
    calls = []

    def target():
        calls.append(True)
        if len(calls) == 1:
            raise AssertionError("private provider detail")
        recovered.set()

    worker = HostedWorker(name="synthetic-recovery", target=target, stop=threading.Event(), retry_seconds=.01)
    worker.start()
    assert recovered.wait(2)
    worker.thread.join(timeout=2)
    assert len(calls) == 2, "Normal completion must not replay even with retry enabled"
    assert "private provider detail" not in caplog.text
    worker.close()


def test_failure_does_not_replay_without_explicit_retry():
    calls = []

    def target():
        calls.append(True)
        raise RuntimeError("synthetic failure")

    worker = HostedWorker(name="synthetic-once", target=target, stop=threading.Event())
    worker.start()
    worker.thread.join(timeout=2)
    worker.start()
    assert calls == [True]
    worker.close()


def test_completed_work_is_not_replayed_by_another_start():
    calls = []
    worker = HostedWorker(name="synthetic-completed", target=lambda: calls.append(True), stop=threading.Event())
    worker.start()
    worker.thread.join(timeout=2)
    worker.start()
    assert calls == [True]
    worker.close()


def test_periodic_steps_share_wait_retry_and_keep_their_live_scope(caplog, monkeypatch):
    stop = threading.Event()
    calls, waits, failures = [], [], []
    scope = object()

    def step():
        calls.append(scope)
        if len(calls) == 2:
            raise RuntimeError("private synthetic detail")
        return 2 if len(calls) == 1 else None

    def wait(delay):
        waits.append(delay)
        if len(waits) == 3:
            stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    worker = HostedWorker(name="synthetic-periodic", stop=stop, retry_seconds=.25,
        target=lambda: worker.repeat(step, interval_seconds=4, wait=wait,
                                     on_error=lambda: failures.append(True)))
    worker.start()
    worker.thread.join(2)
    assert not worker.thread.is_alive()
    assert calls == [scope, scope, scope] and waits == [2, .25, 4]
    assert failures == [True] and "private synthetic detail" not in caplog.text


def test_periodic_step_failure_is_terminal_without_explicit_retry():
    calls = []

    def step():
        calls.append(True)
        raise RuntimeError("synthetic stop")

    worker = HostedWorker(name="synthetic-periodic-once", stop=threading.Event(),
        target=lambda: worker.repeat(step, interval_seconds=1))
    worker.start()
    worker.thread.join(2)
    worker.start()
    assert not worker.thread.is_alive() and calls == [True]


def test_invalid_step_delay_uses_failure_backoff_instead_of_spinning(monkeypatch):
    stop, waits, failures = threading.Event(), [], []

    def wait(delay):
        waits.append(delay)
        stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    worker = HostedWorker(name="synthetic-invalid-delay", stop=stop, retry_seconds=3,
        target=lambda: worker.repeat(lambda: float("nan"), interval_seconds=1,
                                     wait=wait, on_error=lambda: failures.append(True)))
    worker.start()
    worker.thread.join(2)
    assert waits == [3] and failures == [True]


def test_wakeup_hint_cannot_bypass_failure_backoff(monkeypatch):
    stop, waits, hints = threading.Event(), [], []

    def step():
        raise OSError("synthetic scanner failure")

    def backoff(delay):
        waits.append(delay)
        stop.set()

    monkeypatch.setattr(stop, "wait", backoff)
    worker = HostedWorker(name="synthetic-hint", stop=stop, retry_seconds=3,
        target=lambda: worker.repeat(step, interval_seconds=1,
                                     wait=lambda delay: hints.append(delay)))
    worker.start()
    worker.thread.join(2)
    assert waits == [3] and hints == []
