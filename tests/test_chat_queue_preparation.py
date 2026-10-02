"""Accepted requests must settle even when the runtime cannot be prepared."""

import json
import threading
import urllib.request

import pytest

from loopx.chat_agent import CodexChatAgentError
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.extensions.lark.goal_topic_runtime import (
    LarkGoalTopicTurnFailed, answer_lark_goal_topic,
)
from loopx.extensions.lark.manager_context import manager_failure_reply


def session_runtime(tmp_path, *, channel="goal.public-research"):
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="public-research", agent_id="codex", channel_id=channel,
        executor_endpoint_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="public-thread", upstream_mode="chat",
    )
    return store, str(session["session_id"]), ChatRuntimeController(
        store=store, codex_bin="unused-test-codex",
    )


def drain(runtime, session_id, tmp_path):
    runtime.resume_session_queue(session_id=session_id, work_dir=tmp_path, objective="Research")
    # A worker that already retired has completed its queue cleanup.
    with runtime.lock:
        worker = runtime.session_queue_threads.get(session_id)
    if worker:
        worker.join(3)
        assert not worker.is_alive()


@pytest.mark.parametrize("claimed", [False, True])
def test_missing_runtime_asset_settles_accepted_work_without_dispatch(
    tmp_path, monkeypatch, claimed,
):
    store, sid, runtime = session_runtime(tmp_path)
    turns = [store.create_queued_turn(sid, client_turn_id=f"request-{i}", message="Research cash flow")[0]
             for i in range(2)]
    if claimed:
        assert store.claim_next_queued_turn(sid)

    def missing_asset(*args, **kwargs):
        (tmp_path / "removed-release" / "SKILL.md").read_text()

    monkeypatch.setattr(runtime, "_ensure_adapter", missing_asset)
    drain(runtime, sid, tmp_path)
    for turn in turns:
        result = runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=0.1)
        assert result["status"] == "failed"
        assert result["error_code"] == "runtime_unavailable"
        assert result.get("started_at") is None
        assert result.get("upstream_turn_id") is None
        assert str(tmp_path) not in result["error"]
        events = store.events_after(sid, turn["turn_id"], None)
        assert [e["kind"] for e in events].count("turn.failed") == 1
    assert not store.queued_turns(sid)
    assert store.load_session(sid)["active_turn_id"] is None

    class Adapter:
        upstream_thread_id = "public-thread"
        messages = []

        def healthcheck(self):
            return True

        def start_turn(self, message, sink):
            self.messages.append(message)
            return {"message": "Recovered"}

    adapter = Adapter()
    monkeypatch.setattr(runtime, "_ensure_adapter", lambda *a, **kw: adapter)
    retry, created = runtime.enqueue_turn(
        session_id=sid, client_turn_id="request-0", message="Research cash flow",
        work_dir=tmp_path, objective="Research",
    )
    assert not created
    assert retry["status"] == "failed"
    new, _ = runtime.enqueue_turn(
        session_id=sid, client_turn_id="explicit-new-request", message="New request",
        work_dir=tmp_path, objective="Research",
    )
    assert runtime.wait_for_turn(session_id=sid, turn_id=new["turn_id"], timeout_sec=3)["status"] == "completed"
    assert adapter.messages == ["New request"]


def test_stop_wins_over_late_preparation_failure(tmp_path, monkeypatch):
    store, sid, runtime = session_runtime(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def preparation(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        raise OSError("runtime removed")

    monkeypatch.setattr(runtime, "_ensure_adapter", preparation)
    turn, _ = runtime.enqueue_turn(
        session_id=sid, client_turn_id="stop-race", message="Research",
        work_dir=tmp_path, objective="Research",
    )
    assert entered.wait(3)
    assert runtime.interrupt_turn(session_id=sid, turn_id=turn["turn_id"])["status"] == "interrupted"
    release.set()
    drain(runtime, sid, tmp_path)
    assert store.load_turn(sid, turn["turn_id"])["status"] == "interrupted"
    assert not any(e["kind"] == "turn.failed" for e in store.events_after(sid, turn["turn_id"], None))


@pytest.mark.parametrize("delay_stage", ["context", "provider"])
def test_http_stop_remains_effective_after_worker_wait_expires(tmp_path, monkeypatch, delay_stage):
    """A late worker cannot dispatch or hand off after the stop receipt commits."""
    import loopx.chat_coordination as coordination
    from loopx.capabilities import manager_context

    store, sid, runtime = session_runtime(tmp_path)
    entered, release = threading.Event(), threading.Event()
    dispatches, handoffs = [], []

    class Adapter:
        upstream_thread_id = "public-thread"

        def healthcheck(self):
            return True

        def close_session(self):
            pass

        def interrupt_turn(self, *args):
            pass  # Simulate an upstream read that outlives the bounded stop wait.

        def start_turn(self, message, sink):
            dispatches.append(message)
            if delay_stage == "provider" and "Old request" in message:
                entered.set()
                assert release.wait(15)
                return {"message": "Late response", "context_handoff": {"goal_id": "public-research", "agent_id": "worker"}}
            return {"message": "Fresh result"}

    def context(controller, adapter, session, turn_id, sink, **kwargs):
        if delay_stage == "context" and store.load_turn(sid, turn_id)["message"] == "Old request":
            entered.set()
            assert release.wait(15)
        return adapter, {"scope": "owner_goal"}

    monkeypatch.setattr(runtime, "_start_adapter", lambda **kw: Adapter())
    monkeypatch.setattr(coordination, "prepare_turn_context", context)
    monkeypatch.setattr(manager_context, "deliver", lambda *a, **kw: handoffs.append(kw) or {})
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_controller, server.chat_store, server.verbose = runtime, store, False
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        old, _ = runtime.submit_turn(session_id=sid, client_turn_id="old-request", message="Old request", work_dir=tmp_path, objective="Research")
        tid = old["turn_id"]
        assert entered.wait(3)
        with runtime.lock:
            old_worker_done = runtime.turn_done_events[(sid, tid)]
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/chat/sessions/{sid}/turns/{tid}/interrupt",
            data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert json.load(response)["status"] == "interrupted"
        assert not old_worker_done.is_set(), "the late worker must still be blocked"
        assert (sid, tid) not in runtime.cancelled_turns
        fresh, _ = runtime.submit_turn(session_id=sid, client_turn_id="fresh-request", message="Fresh request", work_dir=tmp_path, objective="Research")
        assert runtime.wait_for_turn(session_id=sid, turn_id=fresh["turn_id"], timeout_sec=3)["response"]["message"] == "Fresh result"
        replay, created = runtime.submit_turn(session_id=sid, client_turn_id="old-request", message="Old request", work_dir=tmp_path, objective="Research")
        assert not created and replay["status"] == "interrupted"
        release.set()
        assert old_worker_done.wait(3)
        assert len(dispatches) == (1 if delay_stage == "context" else 2)
        assert handoffs == []
        assert store.load_turn(sid, tid)["status"] == "interrupted"
        assert store.load_session(sid)["status"] == "ready"
        assert [row["kind"] for row in store.events_after(sid, tid, None)].count("turn.interrupted") == 1
        assert not any(row["kind"] in {"turn.completed", "turn.failed"} for row in store.events_after(sid, tid, None))
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(2)


def test_removed_manager_release_fails_before_starting_provider(tmp_path, monkeypatch):
    import loopx.chat_manager as manager

    store, sid, runtime = session_runtime(tmp_path, channel="manager")
    monkeypatch.setattr(manager, "__file__", str(tmp_path / "removed-release" / "chat_manager.py"))
    starts = []
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kw: starts.append(kw))
    turn, _ = runtime.enqueue_turn(
        session_id=sid, client_turn_id="missing-manager-skill", message="Research cash flow",
        work_dir=tmp_path, objective="Research",
    )
    result = runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=3)
    assert result["status"] == "failed"
    assert result["error_code"] == "runtime_unavailable"
    assert result.get("started_at") is None
    assert starts == []
    assert store.load_session(sid)["active_turn_id"] is None


def test_wait_for_preparation_failure_observes_released_session(
    tmp_path,
    monkeypatch,
):
    store, sid, runtime = session_runtime(tmp_path)
    queued, _ = store.create_queued_turn(
        sid,
        client_turn_id="settlement-publication",
        message="Research cash flow",
    )
    claimed = store.claim_next_queued_turn(sid)
    assert claimed is not None
    assert claimed["turn_id"] == queued["turn_id"]

    release_entered = threading.Event()
    allow_release = threading.Event()
    original_release = store.release_active_turn

    def blocked_release(*args, **kwargs):
        release_entered.set()
        assert allow_release.wait(3)
        return original_release(*args, **kwargs)

    monkeypatch.setattr(store, "release_active_turn", blocked_release)
    failure = threading.Thread(
        target=runtime._fail_queue_preparation,
        args=(sid, str(queued["turn_id"]), OSError("runtime removed")),
    )
    failure.start()
    assert release_entered.wait(3)

    observed = []
    waiter = threading.Thread(
        target=lambda: observed.append(
            runtime.wait_for_turn(
                session_id=sid,
                turn_id=str(queued["turn_id"]),
                timeout_sec=3,
            )
        )
    )
    waiter.start()
    waiter.join(0.05)
    assert waiter.is_alive()

    allow_release.set()
    failure.join(3)
    waiter.join(3)
    assert not failure.is_alive()
    assert not waiter.is_alive()
    assert observed[0]["status"] == "failed"
    assert store.load_session(sid)["active_turn_id"] is None


@pytest.mark.parametrize("error,code", [
    (OSError("private installation path"), "runtime_unavailable"),
    (CodexChatAgentError("Cannot restore", error_code="resume_failed", gate=None), "resume_failed"),
])
def test_lark_wait_receives_typed_failure_without_model_or_timeout(tmp_path, monkeypatch, error, code):
    store, sid, runtime = session_runtime(tmp_path)

    def preparation(*args, **kwargs):
        raise error

    monkeypatch.setattr(runtime, "_ensure_adapter", preparation)
    real_wait = runtime.wait_for_turn
    monkeypatch.setattr(runtime, "wait_for_turn", lambda **kw: real_wait(**kw, timeout_sec=3))
    with pytest.raises(LarkGoalTopicTurnFailed) as failure:
        answer_lark_goal_topic(
            route={"goal_id": "public-research", "session_id": sid,
                   "ingress_mode": "session_queue", "message_id": "om_public_question",
                   "topic_root_message_id": "om_public_root"},
            text="Research Microsoft's cash flow", work_dir=tmp_path,
            objective="Research", runtime_controller=runtime,
        )
    assert failure.value.error_code == code
    reply_code, reply = manager_failure_reply(failure.value)
    assert reply_code == code
    assert "修复后重试" in reply
    assert "private installation path" not in reply
    assert not store.queued_turns(sid)
