"""Turn-bound steering over HTTP, the file store and a protocol subprocess."""

import json
import runpy
import stat
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.chat_agent import CodexChatAgentSession
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def conversation(tmp_path):
    fixture = runpy.run_path(str(Path(__file__).parents[1] / "examples/loopx-chat-runtime-smoke.py"))
    executable = tmp_path / "codex"
    executable.write_text(fixture["FAKE_CODEX"], encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin=str(executable))
    session, _ = runtime.open_session(
        goal_id="fixture", agent_id="codex", work_dir=tmp_path,
        objective="Inspect a bounded task.", mode="resume_latest",
    )
    session_id = session["session_id"]
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_controller = runtime
    server.chat_store = store
    server.verbose = False
    server.goal_subagent_configuration_enabled = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def start(identity):
        turn, _ = runtime.submit_turn(
            session_id=session_id, client_turn_id=identity, message="wait for steer",
            work_dir=tmp_path, objective="Inspect a bounded task.",
        )
        fixture["wait_for_active_turn"](store, session_id=session_id, turn_id=turn["turn_id"], require_upstream=True)
        return turn["turn_id"]

    def post(turn_id, identity="adjust-one", text="Check the dependency first.", **extra):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/chat/sessions/{session_id}/turns/{turn_id}/steer",
            data=json.dumps({"client_ingress_id": identity, "message": text, **extra}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    yield store, runtime, session_id, start, post
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize(
    "execution_mode,runtime_profile",
    [(False, "restricted"), (False, "trusted_owner"), (True, "restricted")],
)
def test_steering_updates_the_existing_task_without_replaying_start_prompt(
    conversation, monkeypatch, execution_mode, runtime_profile,
):
    store, runtime, session_id, start, post = conversation
    session = runtime.adapters[session_id].session
    session.execution_mode = execution_mode
    session.runtime_profile = runtime_profile
    writes = []
    original_write = CodexChatAgentSession._write

    def record_write(self, payload):
        writes.append(json.loads(json.dumps(payload)))
        return original_write(self, payload)

    monkeypatch.setattr(CodexChatAgentSession, "_write", record_write)
    turn_id = start("initial-task")
    upstream_turn_id = store.load_turn(session_id, turn_id)["upstream_turn_id"]
    correction = "  Keep the comparison.\n- Use the current releases.\n- Preserve this example:\n    value = 1\n  "
    assert post(turn_id, text=correction)[0] == 200
    completed = runtime.wait_for_turn(session_id=session_id, turn_id=turn_id, timeout_sec=5)
    assert completed["status"] == "completed"
    assert post(turn_id, text=correction)[1]["created"] is False

    starts = [row["params"] for row in writes if row.get("method") == "turn/start"]
    assert len(starts) == 1
    initial_text = starts[0]["input"][0]["text"]
    assert "LoopX context (supporting context only):" in initial_text
    assert "Inspect a bounded task." in initial_text
    assert initial_text.endswith("Current user message: wait for steer")
    updates = [row["params"] for row in writes if row.get("method") == "turn/steer"]
    assert updates == [{
        "threadId": session.thread_id,
        "expectedTurnId": upstream_turn_id,
        "input": [{"type": "text", "text": correction.strip()}],
    }]
    users = [row["text"] for row in store.messages(session_id) if row["role"] == "user"]
    assert users == ["wait for steer", correction]
    assert len([row for row in store.messages(session_id) if row["role"] == "agent"]) == 1


def test_http_steering_deduplicates_and_never_retargets(conversation):
    store, runtime, session_id, start, post = conversation
    first = start("first")
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: post(first), range(2)))
    assert any(status == 200 and row["created"] for status, row in replies)
    # A concurrent duplicate can be unresolved until the first delivery commits.
    assert all(status in {200, 409} for status, _ in replies)
    assert all(row.get("delivery_state") == "unresolved" for status, row in replies if status == 409)
    completed = runtime.wait_for_turn(session_id=session_id, turn_id=first, timeout_sec=5)
    assert completed["response"]["message"] == "Steered response."
    second = start("second")
    status, receipt = post(first)
    assert status == 200 and receipt["created"] is False
    assert receipt["turn_id"] == first and receipt["status"] == "delivered"
    assert post(second)[0] == 400, "an operation id cannot move to a different turn"
    assert post(first, identity="late-adjustment")[0] == 409
    assert post(first, text="Changed request")[0] == 400
    assert store.load_session(session_id)["active_turn_id"] == second
    assert len([row for row in store.messages(session_id) if row["text"] == "Check the dependency first."]) == 1
    runtime.interrupt_turn(session_id=session_id, turn_id=second)


@pytest.mark.parametrize("text,extra", [("", {}), (123, {}), ("x" * 12001, {}), ("hello", {"unexpected": True})])
def test_http_steering_rejects_invalid_input_before_delivery(conversation, text, extra):
    store, _, session_id, start, post = conversation
    turn_id = start("validation")
    assert post(turn_id, text=text, **extra)[0] == 400
    assert store.load_session(session_id)["active_turn_id"] == turn_id
    assert not list((store.sessions_root / session_id / "ingress").glob("*.json"))


def test_unsupported_adapter_does_not_queue_or_start_a_turn(conversation):
    store, runtime, session_id, start, post = conversation
    turn_id = start("unsupported")
    adapter = runtime.adapters.pop(session_id)
    try:
        status, receipt = post(turn_id)
        assert status == 409
        assert receipt["error_code"] == "live_steering_session_not_attached"
        assert receipt["delivery_state"] == "not_delivered"
        repeated_status, repeated_receipt = post(turn_id)
        assert repeated_status == 409
        assert repeated_receipt["error_code"] == receipt["error_code"]
        assert repeated_receipt["delivery_state"] == "not_delivered"
        assert store.load_session(session_id)["active_turn_id"] == turn_id
        assert len(store.messages(session_id)) == 1
    finally:
        runtime.adapters[session_id] = adapter
    status, delivered = post(turn_id, identity="adjust-after-recovery")
    assert status == 200 and delivered["created"] is True
    assert delivered["turn_id"] == turn_id
    assert runtime.wait_for_turn(session_id=session_id, turn_id=turn_id, timeout_sec=5)["status"] == "completed"
    assert len([row for row in store.messages(session_id) if row["text"] == "Check the dependency first."]) == 1


@pytest.mark.parametrize("status", ["interrupting", "completing", "completed"])
def test_turn_closing_does_not_accept_steering(conversation, status):
    store, _, session_id, start, post = conversation
    turn_id = start("closing")
    store.update_turn(session_id, turn_id, status=status)
    code, receipt = post(turn_id)
    assert code == 409 and receipt["error_code"] == "live_steering_turn_mismatch"
    assert len(store.messages(session_id)) == 1


def test_legacy_unbound_ingress_stays_compatible(conversation):
    store, runtime, session_id, start, _ = conversation
    turn_id = start("legacy")
    turn, created = runtime.steer_active_turn(session_id=session_id, client_ingress_id="legacy", message="Keep the task.")
    assert created and turn["turn_id"] == turn_id
    duplicate, created = runtime.steer_active_turn(session_id=session_id, client_ingress_id="legacy", message="Keep the task.")
    assert not created and duplicate["turn_id"] == turn_id
