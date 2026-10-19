"""Durable File/Core journeys with polling held behind an explicit barrier."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService


@contextmanager
def held_poll(service, monkeypatch):
    """A hint can wake work, but elapsed time cannot rescue the assertion."""
    idle, release_fallback = threading.Event(), threading.Event()
    original_closed_wait = service._closed.wait

    def old_poll(_timeout):
        idle.set()
        assert release_fallback.wait(10)
        return service._closed.is_set()

    monkeypatch.setattr(service._closed, "wait", old_poll)
    wakeup = getattr(service, "_delivery_wakeup", None)
    if wakeup is not None:
        original_work_wait = wakeup.wait

        def hinted_poll(_timeout):
            idle.set()
            return original_work_wait(10)

        monkeypatch.setattr(wakeup, "wait", hinted_poll)
    worker = threading.Thread(target=service._reconcile_private)
    worker.start()
    try:
        yield idle
    finally:
        service.close()
        release_fallback.set()
        if wakeup is not None:
            wakeup.set()
        worker.join(10)
        assert not worker.is_alive()
        monkeypatch.setattr(service._closed, "wait", original_closed_wait)


@pytest.mark.parametrize("arrival", ["during_scan", "during_wait"])
def test_existing_listener_wakes_original_journal_without_polling(ordinary, monkeypatch, tmp_path, arrival):  # noqa: F811
    import loopx.extensions.lark.goal_topic_runtime as provider_runtime

    store, runtime, provider, transport = connect(ordinary)
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=store.root.parent,
        runtime_controller=runtime, private_conversations=transport)
    event = provider.event("notes-app", "wake-status", "/status")
    scan_entered, admitted, feed = threading.Event(), threading.Event(), threading.Event()
    returned, sent = threading.Event(), threading.Event()
    original_paths, original_reconcile = transport.pending_delivery_paths, transport.reconcile_request
    scans = 0

    def paths():
        nonlocal scans
        snapshot = original_paths()
        scans += 1
        if arrival == "during_scan" and scans == 1:
            scan_entered.set()
            assert admitted.wait(5)
        return snapshot

    def reconcile(path):
        result = original_reconcile(path)
        if result:
            sent.set()
        return result

    active = True

    def stream(**kwargs):
        nonlocal active
        assert feed.wait(5)
        try:
            result = kwargs["private_admitter"]("notes-app", event)
            assert result["status"] == "command_recorded"
            admitted.set()
            return {"status": "configuration_removed"}
        finally:
            active = False
            kwargs["stop"].set()
            returned.set()

    monkeypatch.setattr(transport, "pending_delivery_paths", paths)
    monkeypatch.setattr(transport, "reconcile_request", reconcile)
    monkeypatch.setattr(provider_runtime, "_active_profile_configs", lambda _:
        {"notes-app": {"cli_bin": "lark-cli", "bot_app_id": "cli_notes_app"}} if active else {})
    monkeypatch.setattr(provider_runtime, "stream_lark_goal_topic_profile", stream)
    # The real App consumer lease is exercised in this fixture's own home.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    listener = threading.Thread(target=service._poll_profile, args=("notes-app", threading.Event()))
    try:
        with held_poll(service, monkeypatch) as idle:
            assert (scan_entered if arrival == "during_scan" else idle).wait(5)
            listener.start()
            feed.set()
            assert admitted.wait(5) and returned.wait(5)
            # The timeout branch remains held. Only admission's wakeup can
            # make this original request reach the existing reply worker.
            assert sent.wait(5)
            assert len(provider.writes) == 1 and provider.writes[0][0] == "notes-app"
            assert "个人助手 · notes" in provider.writes[0][1]
            assert "排队消息：0 条" in provider.writes[0][1]
            assert "只读授权" in provider.writes[0][1]
            assert store.list_sessions() == []
            rows = transport.core.pending()
            assert len(rows) == 1 and rows[0]["status"] == "command_completed"
            before = list(provider.writes)
            assert transport.reconcile() == 0 and provider.writes == before
    finally:
        feed.set()
        admitted.set()
        if listener.ident is not None:
            listener.join(10)
            assert not listener.is_alive()
        runtime.close()
    assert not runtime.turn_release_wakeup._events


def test_native_terminal_release_wakes_final_after_prior_pending_read(ordinary, monkeypatch, tmp_path):  # noqa: F811
    import loopx.extensions.lark.goal_topic_runtime_service as service_module

    store, runtime, provider, transport = connect(ordinary)
    gate = tmp_path / "allow-native-completion"
    fake = ordinary[-2]
    original = "        response = 'Visible runtime response."
    replacement = (f"        while not __import__('pathlib').Path({str(gate)!r}).exists():\n"
                   "            time.sleep(0.01)\n" + original)
    assert original in fake.read_text()
    fake.write_text(fake.read_text().replace(original, replacement, 1))
    event = provider.event("notes-app", "native-wakeup", "Explain this project")
    assert transport.admit("notes-app", event)["status"] == "durably_accepted"
    native = transport.core.pending()[0]
    pending_read_done, delivered = threading.Event(), threading.Event()
    original_reconcile = transport.reconcile_request

    def reconcile(path):
        result = original_reconcile(path)
        if result:
            delivered.set()
        return result

    class ObservedPool(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)

            def finished(done):
                if done.exception() is None and done.result() == 0:
                    pending_read_done.set()

            future.add_done_callback(finished)
            return future

    monkeypatch.setattr(transport, "reconcile_request", reconcile)
    monkeypatch.setattr(service_module, "ThreadPoolExecutor", ObservedPool)
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=store.root.parent,
        runtime_controller=runtime, private_conversations=transport)
    try:
        with held_poll(service, monkeypatch) as idle:
            assert idle.wait(5) and pending_read_done.wait(5)
            assert not delivered.is_set()
            gate.touch()
            turn = runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=5)
            assert turn["status"] == "completed"
            assert delivered.wait(5)
            assert provider.writes[-1] == ("notes-app", turn["response"]["message"])
            assert provider.writes.count(provider.writes[-1]) == 1
            assert len(store.list_sessions()) == 1
            packets = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
            assert sum(p["method"] == "turn/start" for p in packets) == 1
    finally:
        gate.touch()
        runtime.close()
    assert not runtime.turn_release_wakeup._events


def test_preparation_failure_notifies_reader_but_keeps_failed_turn(ordinary):  # noqa: F811
    store, runtime, contexts, request, *_ = ordinary
    _, opened = request("/api/chat/sessions", {"context_kind": "project",
        "project_ref": contexts.available()[0]["project_ref"]})
    sid = opened["session_id"]
    queued, _ = store.create_queued_turn(sid, client_turn_id="owned-prepare-failure", message="Explain")
    wakeup = threading.Event()
    try:
        with runtime.turn_release_wakeup(wakeup):
            with pytest.raises(ValueError, match="already registered"):
                with runtime.turn_release_wakeup(wakeup):
                    pytest.fail("one reader must not register the same wakeup twice")
            runtime._fail_queue_preparation(sid, queued["turn_id"], OSError("fixture runtime unavailable"))
            assert wakeup.is_set()
            assert store.load_turn(sid, queued["turn_id"])["status"] == "failed"
        wakeup.clear()
        runtime.turn_release_wakeup.notify()
        assert not wakeup.is_set() and not runtime.turn_release_wakeup._events
    finally:
        runtime.close()


def test_closed_service_rejects_new_admission_without_dispatch(tmp_path):
    def unexpected(*_args, **_kwargs):
        pytest.fail("closed service must not admit a new input")

    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=SimpleNamespace(admit=unexpected))
    service.close()
    with pytest.raises(RuntimeError, match="service is closed"):
        service._admit_private("notes-app", {})


def test_no_private_transport_registers_no_runtime_wakeup(tmp_path):
    def unexpected(*_args):
        pytest.fail("disabled transport must not register or start a worker")

    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=SimpleNamespace(turn_release_wakeup=unexpected))
    service._reconcile_private()
    service.close()


def test_temporary_discovery_backoff_does_not_starve_later_durable_request(tmp_path, monkeypatch):
    from concurrent.futures import Future
    import loopx.extensions.lark.goal_topic_runtime_service as service_module

    paths = [tmp_path / str(index) for index in range(5)]
    for path in paths:
        path.write_text("durable original request")
    seen, batches = [], []

    class CompletedPool:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def submit(self, operation, path):
            future = Future()
            future.set_result(operation(path))
            return future

    class BackoffTransport:
        polls = 0
        def pending_delivery_paths(self):
            self.polls += 1
            if self.polls == 7:
                service._closed.set()
            # The canonical requests remain present during the hint's backoff.
            return paths if self.polls % 2 and self.polls < 7 else []
        def reconcile_request(self, path):
            seen.append(path)
            batches.append(self.polls)
            return 0  # An unresolved request remains canonical, without effects.

    monkeypatch.setattr(service_module, "ThreadPoolExecutor", CompletedPool)
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=BackoffTransport())
    monkeypatch.setattr(service._delivery_wakeup, "wait", lambda _timeout: None)
    service._reconcile_private()
    assert set(seen) == set(paths)
    assert paths[-1] in seen[4:8]  # The previously skipped request gets the next chance.
    assert all(batches.count(batch) <= 4 for batch in set(batches))
    assert all(path.read_text() == "durable original request" for path in paths)
    assert not service._workers  # Original reply scheduling creates no consumer.
