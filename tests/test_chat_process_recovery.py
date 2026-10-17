"""A dead Chat process releases execution, not the saved conversation."""

import multiprocessing
from pathlib import Path
import threading
import time
import urllib.request

import pytest

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from test_chat_ordinary_project import ordinary  # noqa: F401


class WaitingAdapter:
    upstream_thread_id = "saved-thread"

    def __init__(self, started) -> None:
        self.started = started

    def capabilities(self):
        return {}

    def healthcheck(self):
        return True

    def interrupt_turn(self, turn_id=None):
        pass

    def close_session(self):
        pass

    def start_turn(self, message, event_sink):
        event_sink("answer.delta", {"delta": "Saved partial answer."})
        self.started.send("started")
        threading.Event().wait(30)
        return {"message": "Late answer."}


class AnswerAdapter:
    upstream_thread_id = "saved-thread"

    def capabilities(self):
        return {}

    def healthcheck(self):
        return True

    def interrupt_turn(self, turn_id=None):
        pass

    def close_session(self):
        pass

    def start_turn(self, message, event_sink):
        return {"message": "Follow-up completed."}


def run_worker(root, sid, tid, started, workspace, codex_bin):
    runtime = ChatRuntimeController(
        store=ChatSessionStore(Path(root)), codex_bin=codex_bin,
        project_contexts=ChatProjectContexts([Path(workspace)] if workspace else [], workspace_grant="workspace_read"),
    )
    runtime._run_turn(
        session_id=sid, turn_id=tid, message="Keep this request.",
        attachments=[], adapter=WaitingAdapter(started),
    )


def session_and_turn(store, *, mode="managed_runtime"):
    session = store.create_session(
        goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="saved-thread", session_mode=mode,
        host_surface="codex_app" if mode == "attached_host" else None,
    )
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="original", message="Keep this request.")
    return sid, turn["turn_id"]


def start_worker(root, sid, tid, *, workspace=None, codex_bin="unused"):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    child = context.Process(target=run_worker, args=(str(root), sid, tid, writer,
                                                    str(workspace) if workspace else None, str(codex_bin)))
    child.start()
    writer.close()
    if not reader.poll(15):
        child.kill()
        child.join(5)
        raise AssertionError("the real managed worker did not start")
    try:
        assert reader.recv() == "started"
    except EOFError:
        child.join(5)
        raise AssertionError(ChatSessionStore(root).load_turn(sid, tid)) from None
    reader.close()
    # Kill only after a real checkpoint: in-memory deltas cannot survive SIGKILL.
    store = ChatSessionStore(root)
    deadline = time.monotonic() + 3
    while not any(row["payload"].get("delta") == "Saved partial answer."
                  for row in store.events_after(sid, tid, None)):
        if time.monotonic() >= deadline:
            child.kill()
            child.join(5)
            raise AssertionError("the partial answer was not durably checkpointed")
        time.sleep(0.01)
    return child


def test_dead_worker_becomes_terminal_without_replay_and_followup_works(tmp_path):
    store = ChatSessionStore(tmp_path)
    sid, tid = session_and_turn(store)
    child = start_worker(tmp_path, sid, tid)
    # A second controller has no adapter, but the other service is still alive.
    restored = ChatRuntimeController(store=store, codex_bin="unused")
    try:
        restored.recover_exited_turn(sid, tid)
        assert store.load_turn(sid, tid)["status"] in {"starting", "running"}
        assert store.load_session(sid)["active_turn_id"] == tid
        child.kill()
        child.join(5)
        assert not child.is_alive()

        restored.recover_exited_turn(sid, tid)
        failed = store.load_turn(sid, tid)
        assert failed["status"] == "failed"
        assert failed["error_code"] == "server_restarted"
        assert failed["message"] == "Keep this request."
        assert "not retried automatically" in failed["error"]
        assert store.load_session(sid)["active_turn_id"] is None
        assert store.load_session(sid)["upstream_thread_id"] == "saved-thread"
        assert store.load_session(sid)["status"] == "ready"
        events = store.events_after(sid, tid, None)
        assert any(row["payload"].get("delta") == "Saved partial answer." for row in events)
        assert sum(row["kind"] == "turn.failed" for row in events) == 1
        assert [row["text"] for row in store.messages(sid) if row["role"] == "user"] == ["Keep this request."]

        restored.recover_exited_turn(sid, tid)
        assert store.events_after(sid, tid, None) == events
        followup, _ = store.create_turn(sid, client_turn_id="followup", message="Continue here.")
        restored._run_turn(session_id=sid, turn_id=followup["turn_id"],
                           message="Continue here.", attachments=[], adapter=AnswerAdapter())
        assert store.load_turn(sid, followup["turn_id"])["status"] == "completed"
        assert store.load_turn(sid, tid) == failed
        assert store.load_session(sid)["active_turn_id"] is None
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        restored.close()


def test_legacy_turn_without_execution_evidence_is_not_guessed_dead(tmp_path):
    store = ChatSessionStore(tmp_path)
    sid, tid = session_and_turn(store)
    store.update_turn(sid, tid, status="running")
    runtime = ChatRuntimeController(store=store, codex_bin="unused")
    try:
        before = store.session_snapshot(sid)
        runtime.recover_exited_turn(sid)
        assert store.session_snapshot(sid) == before
    finally:
        runtime.close()


def test_attached_turn_is_not_recovered_as_local_managed_execution(tmp_path):
    store = ChatSessionStore(tmp_path)
    sid, tid = session_and_turn(store, mode="attached_host")
    child = start_worker(tmp_path, sid, tid)
    runtime = ChatRuntimeController(store=store, codex_bin="unused")
    try:
        child.kill()
        child.join(5)
        before = store.session_snapshot(sid)
        runtime.recover_exited_turn(sid, tid)
        assert store.session_snapshot(sid) == before
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        runtime.close()


@pytest.mark.parametrize("read_events", [True, False])
def test_http_reconnect_or_followup_releases_dead_execution(ordinary, read_events):  # noqa: F811
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    code, opened = request("/api/chat/sessions", {
        "context_kind": "project", "project_ref": contexts.available()[0]["project_ref"],
    })
    assert code == 201
    sid = opened["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="original", message="Keep this request.")
    tid = turn["turn_id"]
    child = start_worker(store.root.parent, sid, tid, workspace=workspace, codex_bin=fake)
    # A second real HTTP endpoint over this same owner-local store, as after
    # the native supervisor replaced the exited Chat service.
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.verbose = False
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        child.kill()
        child.join(5)
        if read_events:
            host, port = server.server_address
            with urllib.request.urlopen(
                f"http://{host}:{port}/api/chat/sessions/{sid}/turns/{tid}/events", timeout=3,
            ) as response:
                events = response.read().decode()
            assert 'event: turn.failed' in events and 'server_restarted' in events
        code, followup = request(f"/api/chat/sessions/{sid}/turns", {
            "client_turn_id": "followup", "message": "Continue here.",
        })
        assert code == 202, followup
        assert runtime.wait_for_turn(session_id=sid, turn_id=followup["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_turn(sid, tid)["error_code"] == "server_restarted"
        assert len(store.list_sessions()) == 1
        assert store.load_session(sid)["active_turn_id"] is None
        import json
        calls = [json.loads(row) for row in capture.read_text().splitlines()]
        assert sum(row.get("method") == "turn/start" for row in calls) == 1
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)
        server.shutdown()
        server.server_close()
        serving.join(2)
        runtime.close()
