"""Original-topic stop after a final answer; synthetic wire, real child processes."""
import subprocess
import sys
import time

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect_group, finish_group_turn
from loopx.chat_agent import CodexChatAgentError
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from loopx.extensions.lark.conversation_identity import identity_ref


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
                    if params.get("turnId") in (None, turn_id) else []}
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


def _completed_with_later_answer(fixture, monkeypatch, *, history_size=2):
    store, runtime, provider, transport = connect_group(fixture)
    root = provider.topic("background-root", "Start bounded background work")
    assert transport.admit("notes-app", root)["status"] == "durably_accepted"
    first = finish_group_turn(runtime, transport, message=root["content"])
    sid, tid = first["session_id"], first["turn_id"]
    native_id = store.load_turn(sid, tid)["upstream_turn_id"]
    wire = BackgroundWire(runtime.adapters[sid].session, native_id, monkeypatch)
    for index in range(history_size - 1):
        later = provider.topic(f"later-answer-{index}", f"Keep the corrected filename {index}", root=root["message_id"])
        assert transport.admit("notes-app", later)["status"] == "durably_accepted"
        finish_group_turn(runtime, transport, message=later["content"])
    assert store.load_session(sid)["active_turn_id"] is None
    return store, runtime, provider, transport, root, first, wire


def test_long_completed_history_stop_reaches_original_provider_with_one_thread_scan(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(
        ordinary, monkeypatch, history_size=23)
    from loopx.capabilities.native_chat.codex_background_control import completed_turn_ids

    sid = first["session_id"]
    frozen = completed_turn_ids(runtime, sid)
    before = {turn: store.load_turn(sid, turn) for turn in frozen}
    assert len(frozen) == 23
    wire.calls.clear()
    stop = provider.topic("long-history-stop", "/stop", root=root["message_id"])
    try:
        # Exercise the listener's durable admission and existing reply worker
        # boundary, not only the inventory helper or a replacement controller.
        assert transport.admit("notes-app", stop, defer_background_stop=True)["status"] == "command_recorded"
        row = next(r for r in transport.core.pending() if r["command"] == "stop")
        assert row["status"] == "prepared" and set(row["background_turn_ids"]) == set(frozen)
        assert not wire.calls and wire.children["owned"].poll() is None
        transport.reconcile()
        delivered = transport.core.read_request(row["request_ref"])
        assert delivered["status"] == "command_completed" and delivered["delivery_verified"]
        assert delivered["background_turn_ids"] == [first["turn_id"]]
        assert wire.children["owned"].poll() is not None and wire.children["unowned"].poll() is None
        assert all(store.load_turn(sid, turn) == original for turn, original in before.items())
        reads = [params for method, params in wire.calls if method == "thread/items/list"]
        assert len([params for params in reads if "turnId" not in params]) == 1
        assert len(reads) == 2  # Discovery plus fresh exact-Turn cancellation proof.
        assert sum(topic == root["message_id"] and "停止" in text
                   for _, topic, text in provider.topic_writes) == 1
        calls, writes = list(wire.calls), list(provider.writes)
        transport.reconcile()
        assert wire.calls == calls and provider.writes == writes
    finally:
        wire.close()
        runtime.close()


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


def test_active_topic_stop_is_not_gated_by_unrelated_completed_history(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    root = provider.topic("completed-history", "First completed answer")
    transport.admit("notes-app", root)
    first = finish_group_turn(runtime, transport, message=root["content"])
    sid = first["session_id"]
    completed = store.load_turn(sid, first["turn_id"])
    old_native = completed["upstream_turn_id"]
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    session = runtime.adapters[sid].session
    original, calls = session._write, []

    def write(packet):
        method, params = packet["method"], packet["params"]
        calls.append((method, params))
        if method == "thread/backgroundTerminals/list":
            result = {"data": [{"itemId": "foreign-command", "processId": str(foreign.pid)}]}
        elif method == "thread/items/list" and params.get("turnId") == old_native:
            session.messages.put({"id": packet["id"], "error": {"message": "old history unavailable"}})
            return
        else:
            return original(packet)
        session.messages.put({"id": packet["id"], "result": result})

    monkeypatch.setattr(session, "_write", write)
    try:
        active = provider.topic("active-after-history", "wait for interrupt", root=root["message_id"])
        transport.admit("notes-app", active)
        row = transport.core.read_request(identity_ref(transport._binding("notes-app")["provider_ref"], active["message_id"]))
        deadline = time.monotonic() + 10
        while not (store.load_turn(sid, row["turn_id"])["status"] == "running"
                   and store.load_turn(sid, row["turn_id"]).get("upstream_turn_id")):
            assert time.monotonic() < deadline
            time.sleep(.01)
        native = store.load_turn(sid, row["turn_id"])["upstream_turn_id"]
        stop = provider.topic("stop-active-with-history", "/stop", root=root["message_id"])
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        assert runtime.wait_for_turn(session_id=sid, turn_id=row["turn_id"], timeout_sec=10)["status"] == "interrupted"
        assert any(m == "turn/interrupt" and p["turnId"] == native for m, p in calls)
        assert not any(m == "thread/items/list" and p["turnId"] == old_native for m, p in calls)
        assert foreign.poll() is None and store.load_turn(sid, first["turn_id"]) == completed
    finally:
        monkeypatch.setattr(session, "_write", original)
        runtime.close()
        if foreign.poll() is None:
            foreign.terminate()
        foreign.wait(timeout=5)


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
            assert row["target_recorded"] and row["session_id"] == first["session_id"]
            assert first["turn_id"] in row["background_turn_ids"]
            # Unknown inventory preserves canonical candidates, never idle.
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


@pytest.mark.parametrize("failure", ["thread/items/list", "thread/backgroundTerminals/list"])
@pytest.mark.parametrize("later", ["active", "completed", "new_session", "resume"])
@pytest.mark.parametrize("legacy", [False, True])
def test_first_inventory_failure_cannot_expand_retried_stop(ordinary, monkeypatch, failure, later, legacy):  # noqa: F811
    store, runtime, provider, transport, root, first, wire = _completed_with_later_answer(ordinary, monkeypatch)
    sid, tid = first["session_id"], first["turn_id"]
    completed_before = {m["turn_id"]: store.load_turn(sid, m["turn_id"])
                        for m in store.messages(sid) if m.get("turn_id")}
    native = completed_before[tid]["upstream_turn_id"]
    stop = provider.topic("stop-before-inventory-fault", "/stop", root=root["message_id"])
    original_start = runtime._start_adapter
    resumed = []
    interrupted_native = set()

    def install_history(session):
        original = session._write

        def write(packet):
            method, params = packet["method"], packet["params"]
            if method == "thread/turns/list":
                # Include ALL original and later native Turns, not a fixture
                # that could hide a wrongly selected newer active target.
                turns = {store.load_turn(sid, m["turn_id"])["upstream_turn_id"]:
                         store.load_turn(sid, m["turn_id"])
                         for m in store.messages(sid) if m.get("turn_id")}
                result = {"data": [{"id": key, "status": "interrupted" if key in interrupted_native
                                   else "inProgress" if value["status"] in {"running", "interrupting"}
                                   else value["status"]} for key, value in turns.items()]}
            elif method == "thread/items/list" and params.get("turnId") in (None, newer_native):
                result = {"data": [{"turnId": newer_native, "item": {
                    "type": "commandExecution", "id": "future-command", "status": "completed"}}]}
                if "turnId" not in params:
                    result["data"].append({"turnId": native, "item": {
                        "type": "commandExecution", "id": "owned-command", "status": "completed"}})
            else:
                if method == "turn/interrupt":
                    interrupted_native.add(params["turnId"])
                return original(packet)
            session.messages.put({"id": packet["id"], "result": result})

        monkeypatch.setattr(session, "_write", write)

    def start(**kwargs):
        adapter = original_start(**kwargs)
        if kwargs.get("resume_thread_id") == store.load_session(sid)["upstream_thread_id"]:
            resumed.append(BackgroundWire(adapter.session, native, monkeypatch, children=wire.children))
            install_history(adapter.session)
        return adapter

    try:
        wire.failure = failure
        with pytest.raises(CodexChatAgentError):
            transport.admit("notes-app", stop)
        prepared = next(r for r in transport.core.pending() if r["command"] == "stop")
        assert prepared["status"] == "prepared" and wire.children["owned"].poll() is None
        if legacy:
            from loopx.chat_store import _atomic_write_json
            # Persist the exact old schema's pre-inventory failure shape. An
            # upgrade has no original targets to recover from this journal.
            prepared.update(session_id=None, turn_id=None)
            prepared.pop("target_recorded", None)
            prepared.pop("background_turn_ids", None)
            _atomic_write_json(transport.core.root / f"{prepared['request_ref']}.json", prepared)
        if later == "new_session":
            transport.admit("notes-app", provider.topic("new-after-stop-fault", "/new", root=root["message_id"]))
        message = provider.topic("later-work-after-stop-fault",
                                 "wait for interrupt" if later in {"active", "new_session"} else "Another answer",
                                 root=root["message_id"])
        transport.admit("notes-app", message)
        newer = transport.core.read_request(identity_ref(transport._binding("notes-app")["provider_ref"], message["message_id"]))
        newer_sid, newer_tid = newer["session_id"], newer["turn_id"]
        if later in {"completed", "resume"}:
            # Keep the failed control request pending until the explicit retry
            # below; normal delivery reconciliation also retries such requests.
            assert runtime.wait_for_turn(session_id=newer_sid, turn_id=newer_tid, timeout_sec=10)["status"] == "completed"
        deadline = time.monotonic() + 10
        while not ((newer_turn := store.load_turn(newer_sid, newer_tid)).get("upstream_turn_id")
                   and newer_turn["status"] in {"running", "completed"}):
            assert time.monotonic() < deadline
            time.sleep(.01)
        newer_native = newer_turn["upstream_turn_id"]
        wire.children["future"] = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        if sid in runtime.adapters:
            install_history(runtime.adapters[sid].session)
        if later == "resume":
            runtime.adapters.pop(sid).close_session()
        monkeypatch.setattr(runtime, "_start_adapter", start)
        wire.failure = None
        if legacy:
            with pytest.raises(EffectRuntimeRejected, match="original stop scope was not recorded"):
                transport.admit("notes-app", stop)
            row = next(r for r in transport.core.pending() if r["request_ref"] == prepared["request_ref"])
            assert row["status"] == "prepared" and not row.get("target_recorded")
            assert row["session_id"] is None and row["turn_id"] is None
            assert all(child.poll() is None for child in wire.children.values())
            assert store.load_turn(newer_sid, newer_tid) == newer_turn
            assert not any("停止" in text for _, _, text in provider.topic_writes)
            return
        if later == "new_session":
            # /new deliberately closed the original Session. Its pending stop
            # remains unconfirmed; cancellation cannot reopen it or move to the
            # newer Session just to turn this failure into a successful reply.
            with pytest.raises(KeyError, match="chat session was not found"):
                transport.admit("notes-app", stop)
            row = next(r for r in transport.core.pending() if r["request_ref"] == prepared["request_ref"])
            assert row["status"] == "prepared" and row["session_id"] == sid and row["turn_id"] is None
            assert row["background_turn_ids"] == prepared["background_turn_ids"]
            assert wire.children["owned"].poll() is None
            assert wire.children["future"].poll() is None and wire.children["unowned"].poll() is None
            assert store.load_turn(newer_sid, newer_tid) == newer_turn
            assert not any("停止" in text for _, _, text in provider.topic_writes)
            return
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        row = next(r for r in transport.core.pending() if r["request_ref"] == prepared["request_ref"])
        assert row["session_id"] == sid and row["turn_id"] is None
        assert row["background_turn_ids"] == [tid]
        assert wire.children["owned"].poll() is not None
        assert wire.children["future"].poll() is None and wire.children["unowned"].poll() is None
        assert store.load_turn(newer_sid, newer_tid) == newer_turn
        assert all(store.load_turn(sid, key) == value for key, value in completed_before.items())
        assert prepared["target_recorded"] and prepared["session_id"] == sid
        assert set(prepared["background_turn_ids"]) == set(completed_before)
        if later == "resume":
            assert len(resumed) == 1
        transport.reconcile()
        assert any(topic == root["message_id"] and "停止" in text for _, topic, text in provider.topic_writes)
    finally:
        wire.close()
        runtime.close()
