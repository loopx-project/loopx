"""Original-topic stop after a final answer; synthetic wire, real child processes."""
import subprocess
import sys

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect_group, finish_group_turn
from loopx.chat_agent import CodexChatAgentError
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result


class BackgroundWire:
    def __init__(self, session, turn_id, monkeypatch, *, children=None):
        self.session, self.turn_id = session, turn_id
        self.calls, self.failure = [], None
        self.children = children if children is not None else {
            name: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            for name in ("owned", "unowned")}
        original = session._write

        def write(packet):
            method, params = packet["method"], packet["params"]
            self.calls.append((method, params))
            if method == self.failure:
                session.messages.put({"id": packet["id"], "error": {"message": "control unavailable"}})
                return
            if method == "thread/backgroundTerminals/list":
                result = {"data": [{"itemId": name + "-command", "processId": str(child.pid)}
                                   for name, child in self.children.items() if child.poll() is None]}
            elif method == "thread/items/list":
                result = {"data": [{"turnId": turn_id, "item": {
                    "type": "commandExecution", "id": "owned-command", "status": "completed"}}]
                    if params["turnId"] == turn_id else []}
            elif method == "thread/turns/list":
                result = {"data": [{"id": turn_id, "status": "completed"}]}
            elif method == "thread/backgroundTerminals/terminate":
                child = next(p for p in self.children.values() if str(p.pid) == params["processId"])
                assert child is self.children["owned"], "foreign terminal must survive"
                child.terminate()
                child.wait(timeout=5)
                result = {"terminated": True}
            else:
                return original(packet)
            session.messages.put({"id": packet["id"], "result": result})

        monkeypatch.setattr(session, "_write", write)

    def close(self):
        for child in self.children.values():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


def _completed_with_later_answer(fixture, monkeypatch):
    store, runtime, provider, transport = connect_group(fixture)
    root = provider.topic("background-root", "Start bounded background work")
    assert transport.admit("notes-app", root)["status"] == "durably_accepted"
    first = finish_group_turn(runtime, transport, message=root["content"])
    sid, tid = first["session_id"], first["turn_id"]
    native_id = store.load_turn(sid, tid)["upstream_turn_id"]
    wire = BackgroundWire(runtime.adapters[sid].session, native_id, monkeypatch)
    later = provider.topic("later-answer", "Keep the corrected filename", root=root["message_id"])
    assert transport.admit("notes-app", later)["status"] == "durably_accepted"
    finish_group_turn(runtime, transport, message=later["content"])
    assert store.load_session(sid)["active_turn_id"] is None
    return store, runtime, provider, transport, root, first, wire


def test_idle_topic_stop_finds_earlier_completed_commands_and_keeps_receipts_and_peers(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(ordinary, monkeypatch)
    sid, tid = first["session_id"], first["turn_id"]
    before = store.load_turn(sid, tid)
    peer = provider.topic("independent-topic", "Independent answer")
    try:
        transport.admit("notes-app", peer)
        other = finish_group_turn(runtime, transport, message=peer["content"])
        peer_before = store.load_session(other["session_id"])
        stop = provider.topic("stop-background", "/stop", root=root["message_id"])
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        assert wire.children["owned"].poll() is not None
        assert wire.children["unowned"].poll() is None
        row = next(r for r in transport.core.pending() if r["request_ref"] != first["request_ref"]
                   and r["command"] == "stop")
        assert row["background_turn_ids"] == [tid] and row["turn_id"] is None
        assert row["response_code"] == "stop_requested"
        assert store.load_turn(sid, tid) == before  # completion is not rewritten as interruption
        assert store.load_session(other["session_id"]) == peer_before
        transport.reconcile()
        assert any(topic == root["message_id"] and "停止" in text for _, topic, text in provider.topic_writes)
        effects = sum(method.endswith("/terminate") for method, _ in wire.calls)
        transport.admit("notes-app", stop)
        assert sum(method.endswith("/terminate") for method, _ in wire.calls) == effects
    finally:
        wire.close()
        runtime.close()


@pytest.mark.parametrize("failure", ["thread/backgroundTerminals/list", "thread/items/list",
                                    "thread/backgroundTerminals/terminate"])
def test_failed_background_stop_preserves_frozen_targets_and_never_confirms_delivery(ordinary, monkeypatch, failure):  # noqa: F811
    store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(ordinary, monkeypatch)
    stop = provider.topic("retry-stop", "/stop", root=root["message_id"])
    wire.failure = failure
    try:
        with pytest.raises(CodexChatAgentError, match="rejected"):
            transport.admit("notes-app", stop)
        row = next(r for r in transport.core.pending() if r["command"] == "stop")
        if failure.endswith("/terminate"):
            assert row["target_recorded"] and row["background_turn_ids"] == [first["turn_id"]]
        else:
            assert not row.get("target_recorded")  # unavailable observation is not an empty inventory
        assert row["status"] != "command_completed" and wire.children["owned"].poll() is None
        assert not any("停止" in text for _, _, text in provider.topic_writes)
        wire.failure = None
        transport.admit("notes-app", stop)
        assert wire.children["owned"].poll() is not None and wire.children["unowned"].poll() is None
    finally:
        wire.close()
        runtime.close()


def test_completed_background_stop_resumes_original_transport_without_starting_another_turn(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(ordinary, monkeypatch)
    sid = first["session_id"]
    native_id = store.load_turn(sid, first["turn_id"])["upstream_turn_id"]
    thread_id = store.load_session(sid)["upstream_thread_id"]
    original_start = runtime._start_adapter
    resumed = []

    def start(**kwargs):
        assert kwargs["resume_thread_id"] == thread_id
        adapter = original_start(**kwargs)
        resumed.append(BackgroundWire(adapter.session, native_id, monkeypatch, children=wire.children))
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start)
    runtime.adapters.pop(sid).close_session()
    before = store.messages(sid)
    try:
        stop = provider.topic("stop-after-transport-close", "/stop", root=root["message_id"])
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        assert len(resumed) == 1 and wire.children["owned"].poll() is not None
        assert wire.children["unowned"].poll() is None
        assert store.messages(sid) == before and store.load_session(sid)["upstream_thread_id"] == thread_id
        assert not any(method == "turn/start" for method, _ in resumed[0].calls)
    finally:
        wire.close()
        runtime.close()


@pytest.mark.parametrize("background", [[""], ["same", "same"], "not-an-array", [False]])
def test_core_rejects_unbound_or_ambiguous_background_targets(background):
    with pytest.raises(EffectRuntimeRejected):
        effect_runtime_result("collaboration.conversation.request", {
            "request": {"request_ref": "a" * 24, "command": "stop"},
            "binding": {"audience": "group"}, "current_session": {"session_id": "original", "active_turn_id": None},
            "background_turn_ids": background})


def test_core_replay_keeps_original_background_targets_instead_of_new_observation():
    result = effect_runtime_result("collaboration.conversation.request", {
        "request": {"request_ref": "a" * 24, "command": "stop", "target_recorded": True,
                    "session_id": "original", "turn_id": None, "background_turn_ids": ["old"]},
        "binding": {"audience": "group"}, "current_session": {"session_id": "new", "active_turn_id": "new-active"},
        "background_turn_ids": ["new-background"]})
    assert result == {"operation": "stop", "session_id": "original", "turn_id": None,
                      "background_turn_ids": ["old"], "response_code": "stop_requested"}
