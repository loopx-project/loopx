"""Update the real store/activation boundary without stopping a real service."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import threading
from unittest import mock

import pytest

from loopx.chat_store import ChatSessionStore
from loopx.chat_ingress import chat_runtime_activation_lock, chat_runtime_activation_state
from loopx.runtime_activation import restart_services_for_runtime_activation
from loopx.self_update import execute_update_plan, render_update_plan_markdown
from loopx.chat_runtime import ChatRuntimeController
from loopx.file_lock import lock_holder_path, try_exclusive_file_lock


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setattr("loopx.runtime_activation.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("LOOPX_BIN_DIR", raising=False)
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    runtime = tmp_path / "runtime with spaces"
    route = ["loopx", "--runtime-root", str(runtime), "chat"]
    (agents / "com.loopx.chat.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": route})
    )
    (agents / "com.loopx.status.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": ["loopx", "serve-status"]})
    )
    calls = []
    original_run = subprocess.run

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return original_run(args, **kwargs)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", launchctl)
    return runtime, agents, calls


def session(store):
    return store.create_session(
        goal_id="fixture",
        agent_id="fixture",
        adapter_kind="fake",
        upstream_thread_id="fixture",
        upstream_mode="managed",
    )["session_id"]


def activate():
    return restart_services_for_runtime_activation(
        changes_applied=True, runtime_ready=True
    )


def content_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {".json", ".jsonl"}
    }


def lost_execution(store, *, released=False):
    sid = session(store)
    turn, _ = store.create_turn(sid, client_turn_id="original", message="Keep the original request.")
    tid = turn["turn_id"]
    path = store.turn_execution_lock_path(sid, tid)
    with try_exclusive_file_lock(path, agent_id="loopx-chat", operation="execute_chat_turn") as held:
        assert held is not None
        store.update_turn(sid, tid, status="running")
        store.append_event(sid, tid, kind="answer.delta", payload={"delta": "Saved original progress."})
    if not released:
        # Disk exhaustion can also prevent advisory release metadata. The real
        # kernel lock is gone while the owning service PID remains alive.
        record = json.loads(lock_holder_path(path).read_text())
        record.pop("released_at")
        lock_holder_path(path).write_text(json.dumps(record))
    return sid, tid, path


@pytest.mark.parametrize("released", [False, True])
def test_same_process_worker_exit_is_recovered_without_replay(tmp_path, released):
    store = ChatSessionStore(tmp_path)
    sid, tid, path = lost_execution(store, released=released)
    record = lock_holder_path(path).read_bytes()
    assert json.loads(record)["pid"] == os.getpid()
    runtime = ChatRuntimeController(store=store, codex_bin="must-not-execute")
    with mock.patch.object(runtime, "_start_adapter", side_effect=AssertionError("no provider")):
        runtime.recover_exited_turn(sid, tid)
        failed = store.load_turn(sid, tid)
        assert failed["status"] == "failed" and failed["error_code"] == "runtime_error"
        assert "execution worker exited" in failed["error"]
        assert "execution may have occurred" in failed["error"]
        assert "not retried automatically" in failed["error"]
        assert store.load_session(sid)["active_turn_id"] is None
        assert [m["text"] for m in store.messages(sid) if m["role"] == "user"] == ["Keep the original request."]
        events = store.events_after(sid, tid, None)
        assert any(e["kind"] == "answer.delta" for e in events)
        assert sum(e["kind"] == "turn.failed" for e in events) == 1
        runtime.recover_exited_turn(sid, tid)
        assert store.events_after(sid, tid, None) == events
        assert lock_holder_path(path).read_bytes() == record
        assert not runtime.adapters and not runtime.session_queue_threads


@pytest.mark.parametrize("released", [False, True])
def test_recovery_preserves_stop_after_the_live_service_worker_retires(tmp_path, released):
    store = ChatSessionStore(tmp_path)
    sid, tid, path = lost_execution(store, released=released)
    store.begin_turn_interrupt(sid, tid, expected_statuses={"running"})
    before = content_hashes(store.root)
    runtime = ChatRuntimeController(store=store, codex_bin="must-not-execute")
    runtime.recover_exited_turn(sid, tid)
    assert content_hashes(store.root) == before
    assert store.load_turn(sid, tid)["status"] == "interrupting"
    assert store.load_session(sid)["active_turn_id"] == tid
    # A later service crash still belongs to the existing dead-owner recovery.
    holder = json.loads(lock_holder_path(path).read_text())
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    holder["pid"] = process.pid
    lock_holder_path(path).write_text(json.dumps(holder))
    runtime.recover_exited_turn(sid, tid)
    assert store.load_turn(sid, tid)["error_code"] == ("runtime_error" if released else "server_restarted")
    assert store.load_session(sid)["active_turn_id"] is None


@pytest.mark.parametrize("fault", ["absent", "foreign", "pid", "identity", "operation", "queued", "attached"])
def test_unproven_worker_exit_never_releases_accepted_work(tmp_path, fault):
    store = ChatSessionStore(tmp_path)
    if fault == "attached":
        sid = store.create_session(goal_id="fixture", agent_id="fixture", adapter_kind="fake",
            upstream_thread_id="fixture", session_mode="attached_host", host_surface="codex_app")["session_id"]
        turn, _ = store.create_turn(sid, client_turn_id="attached", message="Keep attached work.")
        tid, path = turn["turn_id"], store.turn_execution_lock_path(sid, turn["turn_id"])
        with try_exclusive_file_lock(path, agent_id="loopx-chat", operation="execute_chat_turn"):
            store.update_turn(sid, tid, status="running")
    else:
        sid, tid, path = lost_execution(store)
    record = json.loads(lock_holder_path(path).read_text())
    if fault == "absent":
        lock_holder_path(path).unlink()
    elif fault in {"foreign", "pid", "identity", "operation"}:
        field, value = {"foreign": ("host", "another-host"), "pid": ("pid", "unknown"),
                        "identity": ("lock_id", "another-turn"), "operation": ("operation", "another-operation")}[fault]
        record[field] = value
        lock_holder_path(path).write_text(json.dumps(record))
    elif fault == "queued":
        store.update_turn(sid, tid, status="queued")
    before = content_hashes(store.root)
    ChatRuntimeController(store=store, codex_bin="must-not-execute").recover_exited_turn(sid, tid)
    assert content_hashes(store.root) == before


def test_activation_recovers_only_unowned_execution_and_preserves_queued_input(services):
    root, _agents, calls = services
    store = ChatSessionStore(root)
    sid, tid, _path = lost_execution(store)
    queued, _ = store.create_queued_turn(sid, client_turn_id="saved-followup", message="Original queued follow-up.")
    before = store.load_turn(sid, queued["turn_id"])
    with mock.patch.object(ChatRuntimeController, "_start_adapter", side_effect=AssertionError("no provider")), \
            mock.patch.object(ChatRuntimeController, "resume_session_queue", side_effect=AssertionError("no second consumer")):
        result = activate()
    assert result["activation_pending"] and result["restart_reason"] == "chat_work_pending"
    assert not calls
    assert store.load_turn(sid, tid)["status"] == "failed"
    assert store.load_turn(sid, queued["turn_id"]) == before
    assert [m["text"] for m in store.messages(sid) if m["role"] == "user"] == [
        "Keep the original request.", "Original queued follow-up."]


def test_activation_does_not_recover_a_live_execution_fence(services):
    root, _agents, calls = services
    store = ChatSessionStore(root)
    sid = session(store)
    turn, _ = store.create_turn(sid, client_turn_id="live", message="Still executing.")
    with try_exclusive_file_lock(store.turn_execution_lock_path(sid, turn["turn_id"]),
            agent_id="loopx-chat", operation="execute_chat_turn"):
        store.update_turn(sid, turn["turn_id"], status="running")
        before = content_hashes(store.root)
        result = activate()
        assert result["activation_pending"] and not calls
        assert content_hashes(store.root) == before


@pytest.mark.parametrize("method", ["append_event", "append_message", "release_active_turn"])
def test_worker_exit_partial_terminalization_is_retryable(tmp_path, monkeypatch, method):
    store = ChatSessionStore(tmp_path)
    sid, tid, path = lost_execution(store)
    original_record = lock_holder_path(path).read_bytes()
    runtime = ChatRuntimeController(store=store, codex_bin="must-not-execute")
    original = getattr(store, method)
    with monkeypatch.context() as fault:
        fault.setattr(store, method, mock.Mock(side_effect=OSError(28, "No space left on device")))
        with pytest.raises(OSError):
            runtime.recover_exited_turn(sid, tid)
    assert store.load_turn(sid, tid)["status"] == "failed"
    assert lock_holder_path(path).read_bytes() == original_record
    runtime.recover_exited_turn(sid, tid)
    assert store.load_session(sid)["active_turn_id"] is None
    assert sum(e["kind"] == "turn.failed" for e in store.events_after(sid, tid, None)) == 1
    assert sum(m["role"] == "error" for m in store.messages(sid)) == 1
    assert getattr(store, method) == original


def test_scoped_activation_recovery_skips_unrelated_startup_maintenance(services):
    root, _agents, calls = services
    store = ChatSessionStore(root)
    sid, tid, _ = lost_execution(store)
    with mock.patch.object(ChatSessionStore, "compact_completed_events", side_effect=AssertionError("unrelated history")), \
            mock.patch.object(ChatSessionStore, "_recover_turn_completions", side_effect=AssertionError("unrelated completion")):
        result = activate()
    assert store.load_turn(sid, tid)["status"] == "failed"
    assert not result["activation_pending"] and len(calls) == 2


def test_activation_rechecks_new_managed_and_attached_sessions_in_same_runtime(services):
    root, _agents, calls = services
    assert not activate()["activation_pending"]
    assert len(calls) == 2
    store = ChatSessionStore(root)
    sid, tid, _ = lost_execution(store)
    attached = store.create_session(goal_id="fixture", agent_id="fixture", adapter_kind="fake",
        upstream_thread_id="attached", session_mode="attached_host", host_surface="codex_app")["session_id"]
    turn, _ = store.create_turn(attached, client_turn_id="attached", message="Independent attached work.")
    store.update_turn(attached, turn["turn_id"], status="running")
    original = store.session_snapshot(attached)
    result = activate()
    assert store.load_turn(sid, tid)["status"] == "failed"
    assert store.session_snapshot(attached) == original
    assert result["activation_pending"] and result["restart_reason"] == "chat_work_pending"
    assert len(calls) == 2


@pytest.mark.parametrize("patch", [{"active_turn_id": None}, {"status": "closed"}])
def test_mutable_session_fields_cannot_hide_running_work_from_activation(services, patch):
    root, _agents, calls = services
    store = ChatSessionStore(root)
    sid, tid, _ = lost_execution(store)
    store.update_session(sid, **patch)
    before = content_hashes(store.root)
    result = activate()
    assert result["activation_pending"] and result["restart_reason"] == "chat_work_pending"
    assert not calls and content_hashes(store.root) == before
    assert store.load_turn(sid, tid)["status"] == "running"


def test_original_queue_survives_progress_and_cleanup_disk_failure(tmp_path, monkeypatch):
    from loopx.chat_runtime import _TurnEventBuffer

    store = ChatSessionStore(tmp_path / "runtime")
    sid = session(store)
    first, _ = store.create_queued_turn(sid, client_turn_id="original", message="Original input.")
    second, _ = store.create_queued_turn(sid, client_turn_id="next", message="Saved follow-up.")
    runtime = ChatRuntimeController(store=store, codex_bin="must-not-execute")
    disk_full, failed_cleanup, followup_done = threading.Event(), threading.Event(), threading.Event()
    calls = []
    update, flush, init = store.update_turn, store.flush_events, _TurnEventBuffer.__init__

    def checkpoint(*args, **kwargs):
        if disk_full.is_set() and kwargs.get("status") == "running":
            raise OSError(28, "No space left on device")
        return update(*args, **kwargs)

    def persist(*args, **kwargs):
        if disk_full.is_set():
            failed_cleanup.set()
            raise OSError(28, "No space left on device")
        return flush(*args, **kwargs)

    def buffer_init(self, **kwargs):
        init(self, **kwargs, metadata_checkpoint_interval_sec=0)

    class OriginalAdapter:
        upstream_thread_id = "fixture"

        def healthcheck(self):
            return True

        def start_turn(self, message, sink):
            calls.append(message)
            sink("turn.started", {"upstream_turn_id": "original-native-turn"})
            if message == "Original input.":
                disk_full.set()
                sink("agent.phase", {"label": "Observed original work"})
                raise AssertionError("the failed progress write must escape")
            followup_done.set()
            return {"message": "Original service answered the follow-up."}

        def close_session(self):
            pass

    adapter = OriginalAdapter()
    monkeypatch.setattr(store, "update_turn", checkpoint)
    monkeypatch.setattr(store, "flush_events", persist)
    monkeypatch.setattr(_TurnEventBuffer, "__init__", buffer_init)
    monkeypatch.setattr(runtime, "_ensure_adapter", lambda *a, **k: adapter)
    runtime.adapters[sid] = adapter
    try:
        runtime.resume_session_queue(session_id=sid, work_dir=tmp_path, objective="fixture")
        assert failed_cleanup.wait(5)
        assert runtime.session_queue_threads[sid].is_alive()
        # Restoring storage is the only recovery stimulus: no resubmit, wake,
        # second controller, replacement consumer or second provider input.
        disk_full.clear()
        assert followup_done.wait(5)
        result = runtime.wait_for_turn(session_id=sid, turn_id=second["turn_id"], timeout_sec=5)
        assert result["status"] == "completed", result
        assert store.load_turn(sid, first["turn_id"])["status"] == "failed"
        assert calls == ["Original input.", "Saved follow-up."]
        assert [m["text"] for m in store.messages(sid) if m["role"] == "user"] == calls
        assert store.load_session(sid)["active_turn_id"] is None
    finally:
        disk_full.clear()
        runtime.close()


@pytest.mark.parametrize("state", ["queued", "running", "completed", "unknown"])
def test_desktop_activation_uses_original_manager_and_accepted_work(services, monkeypatch, state):
    from loopx import dashboard_launcher, runtime_activation
    runtime, agents, calls = services
    # A legitimate private bootstrap need not carry the release launcher's
    # Python source text. The loaded manager and its explicit route own it.
    (agents / "com.loopx.chat.plist").write_bytes(plistlib.dumps({"ProgramArguments": [
        "/bin/zsh", "-c", shlex.join(["exec", "private-host", "--runtime-root", str(runtime), "chat"])]}))
    store = ChatSessionStore(runtime)
    sid = session(store)
    turn, _ = store.create_queued_turn(sid, client_turn_id="original", message="retain this input")
    store.update_turn(sid, turn["turn_id"], status=state)
    before = content_hashes(store.root)
    listening, signals = [True], []
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda *_a: "stale")
    monkeypatch.setattr(dashboard_launcher, "_listener_pids", lambda _port: [12345] if listening[0] else [])
    monkeypatch.setattr(dashboard_launcher, "_is_same_user_loopx_chat_process", lambda _pid: True)
    monkeypatch.setattr(runtime_activation, "_managed_service_pid", lambda *_a, **_k: 12345)

    def stop(pid, signal):
        signals.append((pid, signal))
        listening[0] = False
    monkeypatch.setattr(runtime_activation.os, "kill", stop)
    result = runtime_activation.activate_runtime_services(service="chat")
    assert content_hashes(store.root) == before
    assert result["readiness_verified"] is False
    if state == "completed":
        assert result["ok"] and result["status"] == "restart_requested"
        assert len(signals) == 1 and signals[0][0] == 12345
        assert calls == [["launchctl", "kickstart", f"gui/{runtime_activation.os.getuid()}/com.loopx.chat"]]
    else:
        assert not result["ok"] and result["status"] == "deferred"
        assert result["reason"] == ("chat_state_unverified" if state == "unknown" else "chat_work_pending")
        assert not signals and not calls


def test_desktop_activation_rechecks_loaded_manager_before_signalling(services, monkeypatch):
    from loopx import dashboard_launcher, runtime_activation
    runtime, _agents, calls = services
    ChatSessionStore(runtime)
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda *_a: "stale")
    monkeypatch.setattr(dashboard_launcher, "_listener_pids", lambda _port: [12345])
    monkeypatch.setattr(dashboard_launcher, "_is_same_user_loopx_chat_process", lambda _pid: True)
    identities = iter([12345, 54321])
    monkeypatch.setattr(runtime_activation, "_managed_service_pid", lambda *_a, **_k: next(identities))
    with mock.patch.object(runtime_activation.os, "kill") as stop:
        result = runtime_activation.activate_runtime_services(service="chat")
    assert result["reason"] == "chat_state_unverified"
    stop.assert_not_called()
    assert not calls


@pytest.mark.parametrize(
    "state", ["queued", "starting", "running", "completing", "interrupting"]
)
def test_update_never_restarts_accepted_work_or_rewrites_its_input(services, state):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    turn, _ = store.create_turn(
        sid, client_turn_id="original", message="original request"
    )
    store.update_turn(sid, turn["turn_id"], status=state)
    before = content_hashes(store.root)
    result = activate()
    assert (
        result["activation_pending"] and result["restart_reason"] == "chat_work_pending"
    )
    assert result["restarted_services"] == [] and len(result["deferred_services"]) == 2
    assert not calls
    assert content_hashes(store.root) == before


def test_queue_without_an_active_pointer_still_defers_until_original_work_finishes(
    services,
):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    queued, _ = store.create_queued_turn(
        sid, client_turn_id="queued", message="later input"
    )
    assert not store.load_session(sid).get("active_turn_id")
    assert activate()["restart_status"] == "deferred"
    assert not calls
    # The original consumer, represented here by the real store's terminal write,
    # finishes the same Turn. Activation never creates or replays a business Turn.
    store.update_turn(sid, queued["turn_id"], status="completed")
    assert activate()["restart_status"] == "restarted"
    assert len(calls) == 2
    assert store.load_turn(sid, queued["turn_id"])["message"] == "later input"


@pytest.mark.parametrize("mode", ["loopx_queue", "loopx_inbox"])
def test_a_pending_ingress_receipt_alone_does_not_defer_the_restart(services, mode):
    """The guard fences non-terminal Turns, not pending ingress receipts.

    A pending ingress receipt reserves external input before the provider
    delivers it, so it is not a Turn yet. The activation guard reads accepted
    work through the Turn state, so a pending receipt on its own does not
    defer the restart. This is the boundary the guard actually holds, not a
    promise that pending receipts are covered.
    """
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    receipt, created = store.create_ingress_receipt(
        sid,
        client_ingress_id=f"client-{mode}",
        mode=mode,
        message="reserved input",
    )
    assert created is True
    assert receipt["status"] == "pending"
    assert chat_runtime_activation_state(runtime)["nonterminal_turns"] == 0

    result = activate()
    assert result["restart_status"] == "restarted"
    assert len(calls) == 2


def test_a_queued_turn_still_defers_the_restart_with_a_pending_ingress_receipt(
    services,
):
    """The same receipt next to a non-terminal Turn still defers the restart.

    The receipt alone does not defer; a queued Turn does. Recording both
    sides keeps the guard's contract readable: it reads accepted work, and
    only accepted work.
    """
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    receipt, created = store.create_ingress_receipt(
        sid,
        client_ingress_id="client-queued",
        mode="loopx_queue",
        message="reserved input",
    )
    assert created is True
    store.create_queued_turn(
        sid, client_turn_id="queued", message="later input"
    )
    assert chat_runtime_activation_state(runtime)["nonterminal_turns"] == 1

    result = activate()
    assert result["restart_status"] == "deferred"
    assert result["restart_reason"] == "chat_work_pending"
    assert not calls


@pytest.mark.parametrize(
    "damage", ["session", "turn", "active_missing", "unknown_status", "invalid_status"]
)
def test_unreadable_or_inconsistent_work_fails_closed(services, damage):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    turn, _ = store.create_turn(sid, client_turn_id="original", message="preserve me")
    if damage == "session":
        store._session_path(sid).write_text("{broken")
    elif damage == "turn":
        store._turn_path(sid, turn["turn_id"]).write_text("{broken")
    elif damage == "active_missing":
        store._turn_path(sid, turn["turn_id"]).unlink()
    elif damage == "unknown_status":
        store.update_turn(sid, turn["turn_id"], status="unrecognized")
    else:
        store.update_turn(sid, turn["turn_id"], status=[])
    before = content_hashes(store.root)
    result = activate()
    assert result["restart_status"] == "deferred"
    assert result["restart_reason"] == "chat_state_unverified"
    assert not calls and content_hashes(store.root) == before


@pytest.mark.parametrize("wrapper", [False, True])
def test_idle_service_uses_its_explicit_route_including_shell_wrapper(
    services, wrapper
):
    runtime, agents, calls = services
    store = ChatSessionStore(runtime)
    session(store)
    if wrapper:
        command = shlex.join(
            ["/synthetic/loopx", "--runtime-root", str(runtime), "chat"]
        )
        (agents / "com.loopx.chat.plist").write_bytes(
            plistlib.dumps(
                {
                    "ProgramArguments": [
                        "/bin/zsh",
                        "-c",
                        f"export PATH=/synthetic; exec {command}",
                    ],
                }
            )
        )
    assert activate()["activation_pending"] is False
    assert {call[-1].rsplit("/", 1)[-1] for call in calls} == {
        "com.loopx.chat",
        "com.loopx.status",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["loopx", "chat"],
        ["loopx", "--runtime-root", "$ROOT", "chat"],
        ["loopx", "--runtime-root", "relative", "chat"],
    ],
)
def test_unknown_service_route_never_falls_back_to_an_empty_default_store(
    services, arguments
):
    _runtime, agents, calls = services
    (agents / "com.loopx.chat.plist").write_bytes(
        plistlib.dumps({"ProgramArguments": arguments})
    )
    assert activate()["restart_reason"] == "chat_state_unverified"
    assert not calls


@pytest.mark.parametrize("queue", [False, True])
def test_restart_holds_admission_fence_until_process_replacement(
    services, monkeypatch, queue
):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    replacing, release, admitted, attempted = [threading.Event() for _ in range(4)]
    results, errors = [], []
    original_run = subprocess.run

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return original_run(args, **kwargs)
        calls.append(args)
        replacing.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(args, 0, "", "")

    def accept_input():
        attempted.set()
        try:
            method = store.create_queued_turn if queue else store.create_turn
            method(sid, client_turn_id="during-update", message="do not lose input")
            admitted.set()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", launchctl)
    update = threading.Thread(target=lambda: results.append(activate()))
    writer = threading.Thread(target=accept_input)
    update.start()
    try:
        assert replacing.wait(3)
        writer.start()
        assert attempted.wait(2)
        assert not admitted.wait(0.15)
        assert store.turn_for_client(sid, "during-update") is None
    finally:
        release.set()
        update.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not update.is_alive() and not writer.is_alive()
    assert not errors and admitted.is_set()
    assert results[0]["restart_status"] == "restarted"
    assert activate()["restart_reason"] == "chat_work_pending"


def test_contended_admission_is_not_treated_as_idle(services, monkeypatch):
    runtime, _agents, calls = services
    store = ChatSessionStore(runtime)
    session(store)
    original = chat_runtime_activation_lock
    monkeypatch.setattr(
        "loopx.chat_ingress.chat_runtime_activation_lock",
        lambda root, **_kwargs: original(root, timeout_seconds=0),
    )
    with original(runtime):
        assert activate()["restart_reason"] == "chat_state_unverified"
    assert not calls


@pytest.mark.parametrize("unknown", [False, True])
def test_partial_restart_failure_keeps_the_actual_success_and_unknown_outcome(
    services, monkeypatch, unknown
):
    runtime, _agents, calls = services
    ChatSessionStore(runtime)

    def launchctl(args, **_kwargs):
        calls.append(args)
        if len(calls) == 2 and unknown:
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(args, 1 if len(calls) == 2 else 0, "", "")

    monkeypatch.setattr("loopx.runtime_activation.subprocess.run", launchctl)
    result = activate()
    assert result["restart_status"] == "failed" and result["activation_pending"]
    assert result["restarted_services"] == ["com.loopx.chat"]
    assert result["deferred_services"] == ["com.loopx.status"]
    assert len(calls) == 2


@pytest.mark.parametrize("driver", ["archive_snapshot", "python_pip", "python_pipx"])
def test_update_keeps_install_readback_but_does_not_claim_deferred_service_is_serving(
    services, driver
):
    runtime, _agents, _calls = services
    store = ChatSessionStore(runtime)
    sid = session(store)
    store.create_turn(sid, client_turn_id="original", message="original work")
    payload = {
        "source": {"repo": "loopx-project/loopx", "ref": "f" * 40},
        "plan": {},
        "install_lifecycle": {"execution_driver": driver},
    }
    passed = subprocess.CompletedProcess([], 0, json.dumps({"ok": True}), "")
    with (
        mock.patch(
            "loopx.self_update.run_archive_installer", return_value=(passed, {})
        ),
        mock.patch("loopx.self_update.subprocess.run", return_value=passed),
    ):
        result = execute_update_plan(payload)
    assert result["changes_applied"] and not result["ok"]
    assert result["execution"]["doctor_returncode"] == 0
    assert result["execution"]["restart_status"] == "deferred"
    assert result["next_action"]["kind"] == "retry_runtime_activation"
    assert "f" * 40 in result["next_action"]["command"]
    assert "Service activation pending" in render_update_plan_markdown(result)
