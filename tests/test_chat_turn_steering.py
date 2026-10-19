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
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_native_steward_private import steward  # noqa: F401


def install_transport_failure(fake, *, failures=1, info=None, item_type="userMessage", unreadable=False,
                              readback_info=None, fail_each_request=False, error_message=None,
                              readback_message=None, user_only=False):
    """A native stdio peer, including paginated terminal/item control RPCs."""
    info = info if info is not None else {"responseStreamDisconnected": {"httpStatusCode": None}}
    error_payload = {"codexErrorInfo": info}
    if error_message is not None:
        error_payload["message"] = error_message
    source = fake.read_text().replace("turn_status = {}", "turn_status = {}\ntransport_errors = {}\ntransport_failures = 0\ntransport_attempts = {}")
    count = 'transport_attempts.get(json.dumps(request["params"]["input"]), 0)' if fail_each_request else "transport_failures"
    source = source.replace('        if "typed terminal failure"',
        '        if "transport recovery" in json.dumps(request.get("params") or {}) and ' + count + ' < '
        + repr(failures) + ':\n'
        '            transport_failures += 1\n'
        '            transport_attempts[json.dumps(request["params"]["input"])] = ' + count + ' + 1\n'
        '            turn_status[active_turn] = "failed"\n'
        '            transport_errors[active_turn] = ' + repr(error_payload) + '\n'
        '            print(json.dumps({"method": "turn/completed", "params": {"threadId": "durable-thread", '
        '"turn": {"id": active_turn, "status": "failed", "error": transport_errors[active_turn]}}}), flush=True)\n'
        '            continue\n'
        '        if "typed terminal failure"')
    error = repr({"codexErrorInfo": readback_info}) if readback_info is not None else "transport_errors.get(turn_id)"
    if readback_message is not None:
        error = repr({"codexErrorInfo": info if readback_info is None else readback_info,
                      "message": readback_message})
    source = source.replace('{"id": turn_id, "status": status}',
        '{"id": turn_id, "status": status, "error": ' + error + '}')
    source = source.replace('    elif method in {"thread/items/list", "thread/backgroundTerminals/list"}:',
        '    elif method == "thread/items/list":\n'
        '        selected = request["params"]["turnId"]\n'
        '        cursor = request["params"].get("cursor")\n'
        '        result = {"data": [{"turnId": selected, "item": {"id": "answer" if cursor else "request", '
        '"type": "reasoning" if cursor else ' + repr(item_type) + '}}], "nextCursor": None if cursor or '
        + repr(user_only) + ' else "second"}\n'
        + ('        result.pop("data")\n' if unreadable else '')
        + '    elif method in {"thread/backgroundTerminals/list"}:')
    fake.write_text(source)


@pytest.mark.parametrize("failure,expected_starts,expected_status", [
    ({}, 2, "completed"), ({"failures": 2}, 3, "completed"),
    ({"failures": 3}, 4, "completed"), ({"failures": 4}, 4, "failed"),
    ({"info": "serverOverloaded"}, 2, "completed"),
    ({"info": "serverOverloaded", "failures": 2}, 3, "completed"),
    ({"info": "serverOverloaded", "failures": 3}, 4, "completed"),
    ({"info": "serverOverloaded", "failures": 4}, 4, "failed"),
    ({"info": "serverOverloaded", "item_type": "commandExecution"}, 1, "failed"),
    ({"info": "serverOverloaded", "readback_info": "other"}, 1, "failed"),
    ({"info": "server overloaded"}, 1, "failed"),
    ({"info": "other"}, 1, "failed"), ({"info": "cyberPolicy"}, 1, "failed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out", "user_only": True}, 2, "completed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out", "user_only": True, "failures": 3}, 4, "completed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out", "user_only": True, "failures": 4}, 4, "failed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out"}, 1, "failed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out", "user_only": True, "item_type": "commandExecution"}, 1, "failed"),
    ({"info": "other", "error_message": "prefix workspace routing discovery timed out", "user_only": True}, 1, "failed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out private detail", "user_only": True}, 1, "failed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out", "readback_message": "a different error", "user_only": True}, 1, "failed"),
    ({"info": "cyberPolicy", "error_message": "workspace routing discovery timed out", "user_only": True}, 1, "failed"),
    ({"info": "usageLimitExceeded", "error_message": "workspace routing discovery timed out", "user_only": True}, 1, "failed"),
    ({"info": "other", "error_message": "workspace routing discovery timed out", "user_only": True, "unreadable": True}, 1, "failed"),
    ({"info": "usageLimitExceeded"}, 1, "failed"), ({"info": "unauthorized"}, 1, "failed"),
    ({"item_type": "commandExecution"}, 1, "failed"), ({"unreadable": True}, 1, "failed"),
    ({"readback_info": {"responseStreamDisconnected": {"httpStatusCode": 503}}}, 1, "failed"),
])
def test_core_recovers_bounded_effect_free_transient_attempts_without_reaccepting_request(
    ordinary, failure, expected_starts, expected_status, monkeypatch,  # noqa: F811
):
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    install_transport_failure(fake, **failure)
    monkeypatch.setattr(runtime.closed, "wait", lambda _: False)
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    body = {"message": "transport recovery", "client_turn_id": "original-request"}
    accepted = request(f"/api/chat/sessions/{sid}/turns", body)[1]
    tid = accepted["turn_id"]
    completed = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
    assert completed["status"] == expected_status
    duplicate = request(f"/api/chat/sessions/{sid}/turns", body)[1]
    assert duplicate["turn_id"] == tid and not duplicate["created"]
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [row["params"] for row in packets if row["method"] == "turn/start"]
    assert len(starts) == expected_starts
    assert all(row == starts[0] for row in starts), "same message, thread, model, permission and config"
    assert len(store.list_sessions()) == 1 and len(list(store._session_dir(sid).glob("turns/*.json"))) == 1
    assert [row["text"] for row in store.messages(sid) if row["role"] == "user"] == [body["message"]]
    agents = [row for row in store.messages(sid) if row["role"] == "agent"]
    assert len(agents) == (1 if expected_status == "completed" else 0)
    planned = [row for row in store.events_after(sid, tid, None) if row["kind"] == "transport.retry_planned"]
    assert len(planned) == expected_starts - 1
    if planned:
        if failure.get("info") == "other":
            assert all(row["payload"]["readback"]["pre_execution_routing_timeout"] is True for row in planned)
            assert all(row["payload"]["readback"]["item_count"] == 1 for row in planned)
        assert "workspace routing discovery timed out" not in json.dumps(planned)
        assert planned[0]["payload"]["upstream_turn_id"] == "durable-turn-1"
        assert [row["payload"]["attempt"] for row in planned] == list(range(1, len(planned) + 1))
        assert completed["upstream_turn_id"] == f"durable-turn-{expected_starts}"
        phases = [row["payload"] for row in store.events_after(sid, tid, None)
                  if row["kind"] == "agent.phase" and row["payload"].get("phase") == "retrying"]
        assert len(phases) == len(planned)
        assert all(("模型暂时繁忙" in row["label"]) == (failure.get("info") == "serverOverloaded")
                   for row in phases)
        assert [row["label"].rsplit("（第 ", 1)[1].split(" 次重试）", 1)[0] for row in phases] == [
            f"{attempt}/3" for attempt in range(1, len(planned) + 1)
        ]
    runtime.close()


@pytest.mark.parametrize("profile", ["restricted", "trusted_owner"])
@pytest.mark.parametrize("info", [None, "serverOverloaded", "other"])
def test_steward_transport_retry_preserves_input_and_next_request_refreshes_context(
    steward, monkeypatch, profile, info,  # noqa: F811
):
    from test_native_steward_runtime import apply_profile

    store, runtime, provider, transport, binding, capture, workspace = steward
    fake = Path(runtime.codex_bin)
    install_transport_failure(fake, info=info, user_only=info == "other",
        error_message="workspace routing discovery timed out" if info == "other" else None)
    apply_profile(store.root.parent, profile)
    monkeypatch.setattr(runtime.closed, "wait", lambda _: False)

    def admit(identity, text):
        transport.admit("steward-app", provider.event("steward-app", identity, text))
        row = next(item for item in transport.core.pending() if item["message"] == text)
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)["status"] == "completed"
        return row

    first = admit("first", "transport recovery")
    second = admit("next", "Read fresh evidence for this next request.")
    assert first["session_id"] == second["session_id"]
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [row["params"] for row in packets if row["method"] == "turn/start"]
    assert len(starts) == 3 and starts[0] == starts[1]
    # Scope/evidence collection still runs for both attempts; its new snapshot
    # is not substituted into the existing request. A new user request gets it.
    contexts = [row["payload"] for row in store.events_after(first["session_id"], first["turn_id"], None)
                if row["kind"] == "manager.context"]
    assert len(contexts) == 2 and contexts[0]["collection_completed_at"] != contexts[1]["collection_completed_at"]
    assert contexts[0]["collection_completed_at"] in starts[0]["input"][0]["text"]
    assert contexts[1]["collection_completed_at"] not in starts[1]["input"][0]["text"]
    assert contexts[0]["collection_completed_at"] not in starts[2]["input"][0]["text"]
    assert [row["text"] for row in store.messages(first["session_id"]) if row["role"] == "user"] == [
        "transport recovery", "Read fresh evidence for this next request.",
    ]
    assert transport.reconcile() == 2
    for source in [first, second]:
        record = json.loads((transport.root / f"{source['request_ref']}.json").read_text())
        terminal = record["deliveries"]["terminal"]
        actual = provider.messages[terminal["attempt"]["message_ref"]]
        wire = json.loads(actual["body"]["content"])
        assert terminal["verified"] and record["status"] == "delivered"
        assert actual["sender"]["id"] == provider.profile_apps["steward-app"]
        assert actual["chat_id"] == record["event"]["chat_id"]
        assert wire["zh_cn"]["content"][0][0]["text"] == terminal["text"]
        assert terminal["text"] == store.load_turn(source["session_id"], source["turn_id"])["response"]["message"]
    writes = list(provider.writes)
    assert transport.reconcile() == 0 and provider.writes == writes


@pytest.mark.parametrize("change", ["grant", "scope"])
@pytest.mark.parametrize("info", [None, "serverOverloaded", "other"])
def test_steward_transport_retry_still_stops_when_authority_changes(steward, monkeypatch, change, info):  # noqa: F811
    from test_native_steward_runtime import apply_profile

    store, runtime, provider, transport, binding, capture, workspace = steward
    install_transport_failure(Path(runtime.codex_bin), info=info, user_only=info == "other",
        error_message="workspace routing discovery timed out" if info == "other" else None)
    apply_profile(store.root.parent, "trusted_owner")

    def during_backoff(_):
        if change == "grant":
            apply_profile(store.root.parent, "restricted")
        else:
            runtime.registry_path.write_text(json.dumps({"schema_version": "0.1", "goals": [
                {"id": "new-project", "repo": str(workspace)}]}))
        return False

    monkeypatch.setattr(runtime.closed, "wait", during_backoff)
    transport.admit("steward-app", provider.event("steward-app", "fenced", "transport recovery"))
    row = transport.core.pending()[0]
    final = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
    assert final["status"] == "failed"
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(packet["method"] == "turn/start" for packet in packets) == 1


def test_real_backoff_keeps_fifo_queue_and_gives_each_request_its_bounded_retry(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    install_transport_failure(fake, fail_each_request=True)
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    first = request(f"/api/chat/sessions/{sid}/turns",
                    {"message": "transport recovery", "client_turn_id": "first"})[1]
    second, created = runtime.enqueue_turn(session_id=sid, client_turn_id="second",
        message="transport recovery queued", work_dir=workspace, objective="continue", origin="web")
    assert created
    for tid in [first["turn_id"], second["turn_id"]]:
        assert runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=15)["status"] == "completed"
        events = store.events_after(sid, tid, None)
        plan = next(row for row in events if row["kind"] == "transport.retry_planned")
        starts = [row for row in events if row["kind"] == "turn.started"]
        from datetime import datetime
        waited = datetime.fromisoformat(starts[1]["created_at"]) - datetime.fromisoformat(plan["created_at"])
        assert waited.total_seconds() >= 2.0
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [row["params"] for row in packets if row["method"] == "turn/start"]
    assert len(starts) == 4 and starts[0] == starts[1] and starts[2] == starts[3]
    assert [row["text"] for row in store.messages(sid) if row["role"] == "user"] == [
        "transport recovery", "transport recovery queued",
    ]
    assert len([row for row in store.messages(sid) if row["role"] == "agent"]) == 2
    runtime.close()


@pytest.mark.parametrize("change", ["stop", "grant", "model", "session", "claim"])
@pytest.mark.parametrize("info", [None, "serverOverloaded", "other"])
def test_transport_backoff_observes_stop_and_authority_or_configuration_changes(ordinary, monkeypatch, change, info):  # noqa: F811
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    install_transport_failure(fake, info=info, user_only=info == "other",
        error_message="workspace routing discovery timed out" if info == "other" else None)
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]

    def during_backoff(_):
        tid = store.load_session(sid)["active_turn_id"]
        if change == "stop":
            store.update_turn(sid, tid, status="interrupting")
        elif change == "grant":
            runtime.project_contexts = type(contexts)([])
        elif change == "model":
            runtime.adapters[sid].session.model = "different-model"
        elif change == "session":
            store.update_session(sid, manager_authorization_scope_id="changed")
        else:
            store.update_turn(sid, tid, host_claim_id="different-claim")
        return False

    original_wait = runtime.closed.wait
    monkeypatch.setattr(runtime.closed, "wait", during_backoff)
    accepted = request(f"/api/chat/sessions/{sid}/turns", {"message": "transport recovery", "client_turn_id": "fenced"})[1]
    done = runtime.turn_done_events.get((sid, accepted["turn_id"]))
    if done:
        assert done.wait(10)
    monkeypatch.setattr(runtime.closed, "wait", original_wait)
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(row["method"] == "turn/start" for row in packets) == 1
    assert store.load_turn(sid, accepted["turn_id"])["status"] == ("interrupting" if change == "stop" else "failed")
    runtime.close()


@pytest.mark.parametrize("info", [None, "serverOverloaded", "other"])
def test_crash_after_retry_reservation_never_replays_the_uncertain_native_attempt(ordinary, monkeypatch, info):  # noqa: F811
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    install_transport_failure(fake, info=info, user_only=info == "other",
        error_message="workspace routing discovery timed out" if info == "other" else None)
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    dispatch = {}

    def retain_dispatch(**kwargs):
        dispatch.update(kwargs)
        return True

    class WorkerLost(BaseException):
        pass

    monkeypatch.setattr(runtime, "_start_accepted_turn_worker", retain_dispatch)
    accepted = request(f"/api/chat/sessions/{sid}/turns", {"message": "transport recovery", "client_turn_id": "crash"})[1]
    original_wait = runtime.closed.wait

    def crash(_):
        raise WorkerLost()

    monkeypatch.setattr(runtime.closed, "wait", crash)
    with pytest.raises(WorkerLost):
        runtime._run_turn(**dispatch)
    monkeypatch.setattr(runtime.closed, "wait", original_wait)
    tid = accepted["turn_id"]
    assert len([row for row in store.events_after(sid, tid, None) if row["kind"] == "transport.retry_planned"]) == 1
    assert store.load_turn(sid, tid)["status"] in {"starting", "running"}
    runtime.close()
    restored = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=str(fake), project_contexts=contexts)
    try:
        restored.resume_session(session_id=sid, work_dir=workspace, objective="continue")
        replay, created = restored.submit_turn(session_id=sid, client_turn_id="crash", message="transport recovery",
                                               work_dir=workspace, objective="continue")
        assert not created and replay["turn_id"] == tid and replay["status"] == "failed"
        assert replay["error_code"] == "server_restarted"
        packets = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(row["method"] == "turn/start" for row in packets) == 1
        assert len([row for row in restored.store.events_after(sid, tid, None)
                    if row["kind"] == "transport.retry_planned"]) == 1
    finally:
        restored.close()


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
