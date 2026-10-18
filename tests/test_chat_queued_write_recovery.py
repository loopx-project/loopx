from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
import loopx.chat_store as chat_store
from loopx.chat_runtime import ChatRuntimeController
from loopx.control_plane.effect_runtime import restart_effect_runtime
from test_chat_loopx_mode import mode, project  # noqa: F401
from test_file_lock_windows_atomic import reader_without_delete_share


@pytest.fixture(autouse=True)
def cleanup_runtime(monkeypatch):
    yield
    restart_effect_runtime(timeout=5)


def finish(store, sid, tid):
    store.update_turn(sid, tid, status="completed", response={"text": "finished"})
    store.release_active_turn(sid, tid, last_activity_at="2026-10-15T00:00:00Z", last_error_code=None)


def request(service, sid, repo, operation, **body):
    return service.apply(sid, {"operation": operation, "operation_id": "original", **body},
                         work_dir=repo, objective="Original bounded request")


@contextmanager
def transient_reader(path, monkeypatch):
    observed = threading.Event()
    replace = os.replace
    attempts = []

    def observed_replace(src, dst):
        if Path(dst) == path:
            attempts.append(1)
            observed.set()
        return replace(src, dst)

    with reader_without_delete_share(path) as close:
        def release():
            assert observed.wait(2)
            time.sleep(0.1)
            close()
        worker = threading.Thread(target=release)
        worker.start()
        monkeypatch.setattr(chat_store.os, "replace", observed_replace)
        try:
            yield attempts
        finally:
            observed.set()
            worker.join(3)
            assert not worker.is_alive()


def test_transient_windows_reader_preserves_complete_atomic_chat_state(tmp_path, monkeypatch):
    path = tmp_path / "turn.json"
    path.write_text('{"status":"queued"}')
    with transient_reader(path, monkeypatch) as attempts:
        chat_store._atomic_write_json(path, {"status": "starting"}, preserve_mode=True)
    assert len(attempts) >= 2
    assert json.loads(path.read_text()) == {"status": "starting"}
    assert not list(tmp_path.glob("*.tmp"))


def test_persistent_windows_reader_keeps_original_and_cleans_temporary(tmp_path):
    path = tmp_path / "turn.json"
    path.write_text('{"status":"queued"}')
    before = path.read_bytes()
    with reader_without_delete_share(path):
        started = time.monotonic()
        with pytest.raises(OSError) as failure:
            chat_store._atomic_write_json(path, {"status": "starting"}, preserve_mode=True)
        assert failure.value.winerror == 5
        assert time.monotonic() - started < 1.2
        assert path.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))


def test_unrelated_replace_failure_propagates_without_retry(tmp_path, monkeypatch):
    attempts = []
    def fail(src, dst):
        attempts.append(1)
        raise FileNotFoundError(errno.ENOENT, "missing source")
    monkeypatch.setattr(chat_store.os, "replace", fail)
    with pytest.raises(FileNotFoundError):
        chat_store._atomic_write_json(tmp_path / "turn.json", {"status": "queued"})
    assert attempts == [1] and not list(tmp_path.glob("*.tmp"))


def test_posix_permission_error_is_not_a_windows_retry(tmp_path, monkeypatch):
    attempts = []
    def fail(src, dst):
        attempts.append(1)
        error = PermissionError(errno.EACCES, "denied")
        error.winerror = 5
        raise error
    monkeypatch.setattr(chat_store, "os", SimpleNamespace(name="posix", fsync=os.fsync, chmod=os.chmod, replace=fail))
    with pytest.raises(PermissionError):
        chat_store._atomic_write_json(tmp_path / "turn.json", {"status": "queued"})
    assert attempts == [1] and not list(tmp_path.glob("*.tmp"))


def test_successful_chat_write_preserves_permissions_without_sleep(tmp_path, monkeypatch):
    path = tmp_path / "turn.json"
    path.write_text('{}')
    before = path.stat().st_mode
    monkeypatch.setattr(time, "sleep", lambda _: pytest.fail("successful write must not wait"))
    chat_store._atomic_write_json(path, {"status": "queued"}, preserve_mode=True)
    assert path.stat().st_mode == before
    assert json.loads(path.read_text()) == {"status": "queued"}


def test_json_serialization_failure_cleans_temporary(tmp_path):
    path = tmp_path / "turn.json"
    path.write_text('{}')
    with pytest.raises(TypeError):
        chat_store._atomic_write_json(path, {"bad": object()}, preserve_mode=True)
    assert path.read_text() == '{}' and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("operation", ["start", "resume"])
@pytest.mark.parametrize("cold", [False, True], ids=["same-controller", "cold-controller"])
def test_original_operation_recovers_queued_after_failed_start_fact(mode, monkeypatch, operation, cold):  # noqa: F811 - imported pytest fixture
    service, sid, repo, settings, _ = mode
    store = service.store
    # The real failing HTTP Session already had its LoopX tools activated.
    store.update_session(sid, loopx_tools=True)
    controller = service.controller
    # Persist the real request capsule/display message; hold only scheduling.
    monkeypatch.setattr(controller, "submit_turn", ChatRuntimeController.submit_turn.__get__(controller))
    monkeypatch.setattr(controller, "_check_codex_home", lambda _: None)
    monkeypatch.setattr(controller, "_ensure_adapter_locked", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(controller, "_start_accepted_turn_worker", lambda **k: False)
    if operation == "resume":
        store.update_session(sid, native_goal={"status": "paused", "tokensUsed": 400})
    first = request(service, sid, repo, operation, settings=settings)
    tid = first["turn_id"]
    original = store.load_turn(sid, tid)
    messages = store.messages(sid)
    events = store.events_after(sid, tid, None)
    assert original["status"] == "queued" and original["upstream_turn_id"] is None
    controller = service.controller
    done = threading.Event()
    controller.turn_done_events[(sid, tid)] = done
    with reader_without_delete_share(store._turn_path(sid, tid)):
        with pytest.raises(OSError) as failure:
            controller._run_turn(session_id=sid, turn_id=tid, message=original["message"],
                                 attachments=[], adapter=SimpleNamespace(), done_event=done, loopx_execution=True)
    assert failure.value.winerror == 5 and done.is_set()
    assert (sid, tid) not in controller.turn_done_events
    assert store.load_turn(sid, tid) == original
    if cold:
        controller = ChatRuntimeController(store=type(store)(store.root.parent), codex_bin="unused", registry_path=controller.registry_path)
        service = controller.loopx_mode
        store = service.store
    monkeypatch.setattr(controller, "submit_turn", ChatRuntimeController.submit_turn.__get__(controller))
    monkeypatch.setattr(controller, "_check_codex_home", lambda _: None)
    monkeypatch.setattr(controller, "_ensure_adapter_locked", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(controller, "_start_accepted_turn_worker", ChatRuntimeController._start_accepted_turn_worker.__get__(controller))
    dispatches = []
    def provider(**kwargs):
        dispatches.append((kwargs["session_id"], kwargs["turn_id"], kwargs["message"]))
        finish(store, sid, tid)
    monkeypatch.setattr(controller, "_run_started_turn", provider)
    replay = request(service, sid, repo, operation)
    current = controller.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=5)
    assert replay["turn_id"] == tid and current["status"] == "completed", current
    assert dispatches == [(sid, tid, original["message"])]
    assert store.messages(sid) == messages
    assert [e for e in store.events_after(sid, tid, None) if e["kind"] == "turn.queued"] == [e for e in events if e["kind"] == "turn.queued"]
    assert current["loopx_request"] == original["loopx_request"]
    assert request(service, sid, repo, operation)["turn_id"] == tid
    assert len(dispatches) == 1
    controller.close()


@pytest.mark.parametrize("operation", ["start", "resume"])
def test_changed_settings_cannot_dispatch_original_queued_request(mode, operation):  # noqa: F811 - imported pytest fixture
    service, sid, repo, settings, calls = mode
    if operation == "resume":
        service.store.update_session(sid, native_goal={"status": "paused", "tokensUsed": 400})
    first = request(service, sid, repo, operation, settings=settings)
    before = service.store.load_turn(sid, first["turn_id"])
    with pytest.raises(ValueError, match="identity conflict"):
        request(service, sid, repo, operation, settings={**settings, "token_budget": settings["token_budget"] + 1})
    assert len(calls) == 1 and service.store.load_turn(sid, first["turn_id"]) == before


def test_stopped_goal_cannot_dispatch_previously_queued_operation(mode):  # noqa: F811 - imported pytest fixture
    service, sid, repo, settings, calls = mode
    first = request(service, sid, repo, "start", settings=settings)
    registry = service.controller.registry_path
    data = json.loads(registry.read_text())
    goal = next(g for g in data["goals"] if g["id"] == "research")
    goal["activation"] = {"schema_version": "loopx_goal_activation_v1", "state": "stopped", "updated_at": "2026-10-15T00:00:00Z", "reason": "Owner stopped"}
    registry.write_text(json.dumps(data))
    with pytest.raises(Exception, match="stopped"):
        request(service, sid, repo, "start")
    assert len(calls) == 1 and service.store.load_turn(sid, first["turn_id"])["status"] == "queued"


def test_terminal_original_operation_replay_never_dispatches(mode):  # noqa: F811 - imported pytest fixture
    service, sid, repo, settings, calls = mode
    first = request(service, sid, repo, "start", settings=settings)
    finish(service.store, sid, first["turn_id"])
    assert request(service, sid, repo, "start")["turn_id"] == first["turn_id"]
    assert len(calls) == 1
