"""Ordinary browser queue intent through real HTTP, store and native stdio."""

import json
import time

import pytest

from loopx.chat import CHAT_RESPONSE_EXAMPLE_SUFFIX
from test_chat_ordinary_project import ordinary  # noqa: F401


def queue_input_body(action="edit", revision=0, operation="input-1", message="Corrected queued input."):
    return {"action": action, "expected_revision": revision, "operation_id": operation,
            **({"message": message} if action == "edit" else {})}


@pytest.mark.parametrize("delivery", [None, "queue"])
@pytest.mark.parametrize("warm", [False, True])
def test_http_replay_read_failure_preserves_one_dispatch(ordinary, monkeypatch, delivery, warm):  # noqa: F811
    import errno
    from pathlib import Path
    from loopx.chat_store import ChatSessionStore

    store, runtime, contexts, request, capture, *_ = ordinary
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    endpoint = f"/api/chat/sessions/{sid}/turns"
    body = {"message": "Return the original answer", "client_turn_id": "same-request"}
    if delivery:
        body["delivery"] = delivery
    try:
        code, first = request(endpoint, body)
        assert code == 202
        assert runtime.wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=10)["status"] == "completed"
        if warm:
            store.queued_turns(sid, strict=True)
        else:
            store.__dict__.pop("_terminal_turn_discovery", None)
        path, messages = store._turn_path(sid, first["turn_id"]), store.messages(sid)
        original_read, count = Path.read_text, 0

        def fail_once(candidate, *args, **kwargs):
            nonlocal count
            if candidate == path:
                count += 1
                if count == (1 if warm else 2):
                    raise OSError(errno.EIO, "synthetic transient read failure")
            return original_read(candidate, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "read_text", fail_once)
            code, response = request(endpoint, body)
            assert code in {202, 400}
            if code == 202:
                assert response["turn_id"] == first["turn_id"] and not response["created"]
        assert ChatSessionStore(store.root.parent).messages(sid) == messages
        assert request(endpoint, body)[1]["turn_id"] == first["turn_id"]
        assert sum(json.loads(line).get("method") == "turn/start" for line in capture.read_text().splitlines()) == 1
    finally:
        runtime.close()


def test_edit_cancel_and_admission_retries_keep_exact_input_history_and_active_turn(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, original = held_queue(ordinary)
    path = f"/api/chat/sessions/{sid}/queue/{queued}/input"
    before = capture.read_bytes()
    assert request(path)[0] == 404
    assert store.load_turn(sid, queued)["message"] == original["message"]  # GET never mutates.
    body = queue_input_body()
    code, edited = request(path, body)
    assert code == 200 and edited["input_revision"] == 1
    assert request(path, body) == (code, edited)
    assert request(path, queue_input_body(operation="stale"))[1]["error_code"] == "queue_input_revision_conflict"
    # A lost original admission ACK cannot overwrite the explicit edit.
    assert request(f"/api/chat/sessions/{sid}/turns", original)[1]["turn_id"] == queued
    assert store.load_turn(sid, queued)["message"] == body["message"]
    transcript = next(m for m in store.messages(sid) if m.get("turn_id") == queued)
    assert transcript["text"] == body["message"]
    assert transcript["queue_edit_history"] == [{"text": original["message"], "input_revision": 0, "operation_id": "input-1"}]
    cancel = queue_input_body("cancel", 1, "cancel-1")
    result = request(path, cancel)
    assert result[0] == 200 and request(path, cancel) == result
    assert request(path, body) == (code, edited)  # receipt replay, not another edit
    assert store.load_turn(sid, queued)["status"] == "interrupted"
    snapshot = request(f"/api/chat/sessions/{sid}")[1]
    cancelled = next(row for row in snapshot["messages"] if row.get("turn_id") == queued)
    assert cancelled["text"] == body["message"] and cancelled["queue_input_cancelled"] is True
    assert cancelled["queue_input_revision"] == 2
    assert store.load_session(sid)["active_turn_id"] == active
    assert request(f"/api/chat/sessions/{sid}/queue")[1]["turns"] == []
    assert len([e for e in store.events_after(sid, queued, None) if e["kind"] == "turn.interrupted"]) == 1
    assert capture.read_bytes() == before  # edit/cancel never issue native I/O


def test_partial_edit_is_reserved_and_recovers_the_same_operation_after_reopening(ordinary, monkeypatch):  # noqa: F811
    import loopx.chat_store as store_io
    from loopx.chat_store import ChatSessionStore
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    path = f"/api/chat/sessions/{sid}/queue/{queued}/input"
    body = queue_input_body()
    write = store_io._atomic_write_json

    def fail_commit(path, value, **kwargs):
        if value.get("_queue_operations") and not value.get("_queue_mutation"):
            raise OSError("lost commit")
        return write(path, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store_io, "_atomic_write_json", fail_commit)
        assert request(path, body)[0] == 503
    reopened = ChatSessionStore(store.root.parent)
    assert reopened.queued_turns(sid) == []
    assert reopened.queued_turns(sid, include_reserved=True)[0]["_queue_mutation"]["operation_id"] == "input-1"
    assert request(path, queue_input_body(operation="replacement"))[1]["error_code"] == "queue_mutation_in_progress"
    assert request(f"/api/chat/sessions/{sid}/queue/{queued}/steer", {
        "target_turn_id": active, "client_ingress_id": "do-not-send"})[1]["error_code"] == "queue_mutation_in_progress"
    assert request(path, body)[0] == 200
    assert len(next(m for m in store.messages(sid) if m.get("turn_id") == queued)["queue_edit_history"]) == 1
    assert len([e for e in store.events_after(sid, queued, None) if e["kind"] == "turn.input_edited"]) == 1


@pytest.mark.parametrize("action", ["edit", "cancel"])
def test_input_change_and_queue_claim_have_both_serial_orders(ordinary, monkeypatch, action):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import loopx.chat_store as store_io
    store, runtime, contexts, request, capture, *_ = ordinary
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    sid, active, queued, _ = held_queue(ordinary)
    # Stop this isolated native fixture through its real owner; no automatic
    # queue worker is installed, so the following claims have controlled order.
    runtime.interrupt_turn(session_id=sid, turn_id=active)
    runtime.wait_for_turn(session_id=sid, turn_id=active, timeout_sec=10)
    entered, proceed = Event(), Event()
    write = store_io._atomic_write_json

    def pause(path, value, **kwargs):
        if value.get("_queue_mutation"):
            entered.set()
            assert proceed.wait(5)
        return write(path, value, **kwargs)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
        patch.setattr(store_io, "_atomic_write_json", pause)
        changing = pool.submit(request, f"/api/chat/sessions/{sid}/queue/{queued}/input", queue_input_body(action))
        assert entered.wait(5)
        claiming = pool.submit(store.claim_next_queued_turn, sid, host_claim_id="test-host")
        assert not claiming.done()
        proceed.set()
        assert changing.result(timeout=10)[0] == 200
        claimed = claiming.result(timeout=10)
    if action == "edit":
        assert claimed["message"] == "Corrected queued input."
    else:
        assert claimed is None
    other, _ = store.create_queued_turn(sid, client_turn_id="other", message="Already claimed.", origin="web")
    if claimed:
        store.release_active_turn(sid, queued)
    assert store.claim_next_queued_turn(sid, host_claim_id="test-host")["turn_id"] == other["turn_id"]
    before = store.load_turn(sid, other["turn_id"])
    rejected = request(f"/api/chat/sessions/{sid}/queue/{other['turn_id']}/input", queue_input_body(action))
    assert rejected[0] == 409 and rejected[1]["error_code"] == "queue_turn_already_started"
    assert store.load_turn(sid, other["turn_id"]) == before


def test_mutation_respects_revoked_scope_external_source_and_other_session(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    external, _ = store.create_queued_turn(sid, client_turn_id="external", message="Original external request", origin="external")
    assert request(f"/api/chat/sessions/{sid}/queue/{external['turn_id']}/input", queue_input_body())[1]["error_code"] == "queue_mutation_source_unavailable"
    original = store.load_turn(sid, queued)
    other = store.create_session(goal_id="other", agent_id="codex", adapter_kind="codex", upstream_thread_id="other-thread")
    assert request(f"/api/chat/sessions/{other['session_id']}/queue/{queued}/input", queue_input_body())[0] == 404
    runtime.project_contexts = ChatProjectContexts([])
    assert request(f"/api/chat/sessions/{sid}/queue/{queued}/input", queue_input_body())[0] == 400
    assert store.load_turn(sid, queued) == original


def test_edited_input_and_attachments_are_actually_dispatched_once(ordinary):  # noqa: F811
    import base64
    store, runtime, contexts, request, capture, *_ = ordinary
    data = b"# Original source\nKeep this attachment.\n"
    attachment = {"id": "source", "name": "source.md", "mime_type": "text/markdown",
                  "size": len(data), "data_url": "data:text/markdown;base64," + base64.b64encode(data).decode()}
    sid, active, queued, _ = held_queue(ordinary, attachments=[attachment])
    body = queue_input_body()
    assert request(f"/api/chat/sessions/{sid}/queue/{queued}/input", body)[0] == 200
    runtime.steer_active_turn(session_id=sid, expected_turn_id=active, client_ingress_id="finish-current", message="Finish current work.")
    assert runtime.wait_for_turn(session_id=sid, turn_id=queued, timeout_sec=10)["status"] == "completed"
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [c for c in calls if c["method"] == "turn/start"]
    assert len(starts) == 2
    inputs = json.dumps(starts[1]["params"]["input"])
    assert body["message"] in inputs and "Keep this attachment." in inputs
    assert store.load_turn(sid, queued)["attachments"] == [attachment]
    # Rechecking the applied edit after native execution cannot start it again.
    before = capture.read_bytes()
    assert request(f"/api/chat/sessions/{sid}/queue/{queued}/input", body)[0] == 200
    assert capture.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_goal_queue_input_http_with_canonical_goal_providers(ordinary, tmp_path, monkeypatch, provider):  # noqa: F811
    from tests.control_plane.canonical_authority_fixture import promoted_create_fixture, isolate_sqlite_runtime
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    (tmp_path / "provider").mkdir()
    isolate_sqlite_runtime(tmp_path / "provider", monkeypatch)
    registry, root, state = promoted_create_fixture(tmp_path / "goal-source", provider=provider)
    store, runtime, contexts, request, capture, *_ = ordinary
    server = next(c.cell_contents for c in request.__closure__ if hasattr(c.cell_contents, "serve_forever"))
    server.registry_path, runtime.registry_path = registry, registry
    server.runtime_root_override = str(root)
    before = read_canonical_todos_if_promoted(runtime_root=root, goal_id="goal-a")
    session = store.create_session(goal_id="goal-a", agent_id="codex", adapter_kind="codex", upstream_thread_id="goal-thread")
    sid = session["session_id"]
    queued, _ = store.create_queued_turn(sid, client_turn_id="goal-input", message="Original Goal input.", origin="web")
    qid = queued["turn_id"]
    assert request(f"/api/chat/sessions/{sid}/queue")[0] == 200
    path = f"/api/chat/sessions/{sid}/queue/{qid}/input"
    original_turn = store.load_turn(sid, qid)
    original_messages = store.messages(sid)
    original_events = store.events_after(sid, qid, None)
    original_session = store.load_session(sid)
    for index, action in enumerate([
        None, False, 0, {}, [], ["edit"], ["cancel"], [["edit"]], {"toString": "edit"}, "unknown",
    ]):
        invalid = {"action": action, "operation_id": f"invalid-{index}", "expected_revision": 0}
        response = request(path, invalid)
        assert store.load_turn(sid, qid) == original_turn, response
        assert store.messages(sid) == original_messages
        assert store.events_after(sid, qid, None) == original_events
        assert store.load_session(sid) == original_session
        assert not capture.exists()
        assert response[0] == 409, response
    assert request(path, queue_input_body())[0] == 200
    history = request(f"/api/chat/sessions/{sid}?turn_id={qid}&message_limit=200")[1]["messages"]
    assert history[0]["text"] == "Corrected queued input." and history[0]["queue_input_revision"] == 1
    assert request(path, queue_input_body("cancel", 1, "goal-cancel"))[0] == 200
    assert store.load_turn(sid, qid)["status"] == "interrupted"
    after = read_canonical_todos_if_promoted(runtime_root=root, goal_id="goal-a")
    assert after["provider_revision"] == before["provider_revision"]
    assert after["todos"] == before["todos"] and not capture.exists()


def wait_for_active_turn(store, session_id, turn_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        turn = store.load_turn(session_id, turn_id)
        if turn and turn.get("status") == "running" and turn.get("upstream_turn_id"):
            return turn
        time.sleep(0.02)
    raise AssertionError(f"Turn did not reach its native active state: {turn}")


def held_queue(project_fixture, text="Check the second source.", attachments=None):
    store, runtime, contexts, request, capture, *_ = project_fixture
    sid = request("/api/chat/sessions", {"context_kind": "project",
        "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    first = request(f"/api/chat/sessions/{sid}/turns", {
        "message": "wait for steer", "client_turn_id": "held"})[1]
    wait_for_active_turn(store, sid, first["turn_id"])
    body = {"message": text, "client_turn_id": "pending", "delivery": "queue"}
    if attachments:
        body["attachments"] = attachments
    code, queued = request(f"/api/chat/sessions/{sid}/turns", body)
    assert code == 202
    return sid, first["turn_id"], queued["turn_id"], body


def test_queue_read_is_ordered_bounded_read_only_and_full_input_remains_readable(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    text = ("A long original input.\n" * 600).strip()
    sid, active, queued, _ = held_queue(ordinary, text)
    before = capture.read_bytes()
    code, snapshot = request(f"/api/chat/sessions/{sid}/queue")
    assert code == 200 and snapshot["session_id"] == sid
    assert snapshot["active_turn_id"] == active
    assert [(t["turn_id"], t["character_count"], t["preview"]) for t in snapshot["turns"]] == [(queued, len(text), text[:240])]
    assert snapshot["turns"][0]["steering"] is None
    assert store.load_session(sid)["active_turn_id"] == active
    assert capture.read_bytes() == before
    assert request(f"/api/chat/sessions/{sid}?turn_id={queued}&message_limit=200")[1]["messages"][0]["text"] == text
    # Corruption cannot quietly turn an unknown queue into an empty success.
    original = store.load_turn(sid, queued)
    for invalid in [{**original, "status": "invented"}, {**original, "status": []},
                    {**original, "_queue_steering": {"schema_version": "invalid"}}]:
        store._turn_path(sid, queued).write_text(json.dumps(invalid))
        assert request(f"/api/chat/sessions/{sid}/queue")[0] == 503
    store._turn_path(sid, queued).write_text("{")
    assert request(f"/api/chat/sessions/{sid}/queue")[0] == 503


def test_queued_steering_uses_original_input_once_and_replays_after_target_finished(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, original = held_queue(ordinary)
    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    body = {"target_turn_id": active, "client_ingress_id": "insert-original"}
    assert request(path)[0] == 404  # GET never mutates a queue.
    code, result = request(path, body)
    assert code == 200 and result["status"] == "delivered"
    assert runtime.wait_for_turn(session_id=sid, turn_id=active, timeout_sec=10)["status"] == "completed"
    assert request(path, body) == (code, result)
    assert request(f"/api/chat/sessions/{sid}/turns", original)[1]["turn_id"] == queued
    assert store.load_turn(sid, queued)["status"] == "completed"
    assert request(f"/api/chat/sessions/{sid}/queue")[1]["turns"] == []
    rows = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len([r for r in rows if r["method"] == "turn/start"]) == 1
    steers = [r for r in rows if r["method"] == "turn/steer"]
    assert len(steers) == 1 and steers[0]["params"]["input"][0]["text"] == original["message"]
    assert [r["text"] for r in store.messages(sid) if r["role"] == "user"] == ["wait for steer", original["message"]]
    completions = [e for e in store.events_after(sid, queued, None) if e["kind"] == "turn.completed"]
    assert len(completions) == 1
    assert completions[0]["payload"]["steered_into_turn_id"] == active
    assert completions[0]["payload"]["response"]["message"] == ""


def test_unknown_native_delivery_stays_reserved_across_reload_retry_and_queue_claim(ordinary, monkeypatch):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    adapter = runtime.adapters[sid]
    calls = []

    def uncertain(*args):
        calls.append(args)
        raise TimeoutError("native acknowledgement lost")

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "steer_turn", uncertain)
        path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
        body = {"target_turn_id": active, "client_ingress_id": "lost"}
        assert request(path, body)[0] == 409
        assert request(path, body)[0] == 409
        assert request(path, {**body, "client_ingress_id": "new"})[1]["error_code"] == "queue_steering_in_progress"
    assert len(calls) == 1
    monkeypatch.setattr("loopx.chat_ingress.SESSION_QUEUE_MAX_PENDING", 1)
    assert request(f"/api/chat/sessions/{sid}/turns", {
        "message": "No capacity escape", "client_turn_id": "over-capacity", "delivery": "queue"})[0] == 429
    # A fresh store instance sees the same durable pending input and reservation.
    from loopx.chat_store import ChatSessionStore
    fresh = ChatSessionStore(store.root.parent)
    assert fresh.queued_turns(sid) == []
    held = fresh.queued_turns(sid, include_reserved=True)
    assert len(held) == 1 and held[0]["_queue_steering"]["operation_id"] == "lost"
    runtime.steer_active_turn(session_id=sid, expected_turn_id=active,
        client_ingress_id="release-active", message="Complete this check.")
    runtime.wait_for_turn(session_id=sid, turn_id=active, timeout_sec=10)
    assert fresh.claim_next_queued_turn(sid) is None
    assert request(f"/api/chat/sessions/{sid}/queue")[1]["turns"][0]["steering"]["status"] == "pending"


def _lost_steering_ack(runtime, evidence):
    from pathlib import Path

    fake = Path(runtime.codex_bin)
    source = fake.read_text()
    source = source.replace("turn_status = {}", "turn_status = {}\nsteering_inputs = []", 1)
    marker = '    elif method == "turn/steer":'
    assert source.count(marker) == 1
    source = source.replace(marker, '''    elif method == "turn/steer" and "Read the original receipt" in json.dumps(request["params"]):
        assert request["params"]["expectedTurnId"] == active_turn
        steering_inputs.append({"turnId": active_turn, "item": {
            "id": "native-message", "type": "userMessage",
            "clientId": request["params"].get("clientUserMessageId"),
            "content": request["params"]["input"],
        }})
        continue
    elif method == "thread/items/list" and steering_inputs:
        item = json.loads(json.dumps(steering_inputs[0]))
        evidence = EVIDENCE
        if evidence == "wrong-id":
            item["item"]["clientId"] = "another-input"
        elif evidence == "wrong-text":
            item["item"]["content"][0]["text"] = "Different input"
        elif evidence == "wrong-turn":
            item["turnId"] = "another-turn"
        if evidence == "empty":
            result = {"data": [], "nextCursor": None}
        elif evidence == "duplicate":
            result = {"data": [item, item], "nextCursor": None}
        elif evidence == "incomplete":
            result = {"data": [item], "nextCursor": "repeated"}
        elif "cursor" not in request["params"]:
            other = json.loads(json.dumps(item))
            other["item"]["clientId"] = "same-text-different-input"
            result = {"data": [other], "nextCursor": "original"}
        else:
            result = {"data": [item], "nextCursor": None}
''' .replace("EVIDENCE", repr(evidence)) + marker, 1)
    fake.write_text(source)


@pytest.mark.parametrize("evidence", ["confirmed", "empty", "wrong-id", "wrong-text", "wrong-turn", "duplicate", "incomplete"])
def test_check_insertion_reads_every_original_native_page_without_sending_again(ordinary, evidence):  # noqa: F811
    store, runtime, _, request, capture, *_ = ordinary
    _lost_steering_ack(runtime, evidence)
    sid, active, queued, original = held_queue(ordinary, "Read the original receipt.\nKeep every line.")
    runtime.adapters[sid].session.response_timeout_sec = 0.35
    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    body = {"target_turn_id": active, "client_ingress_id": "check-original"}
    assert request(path, body)[0] == 409
    assert store.claim_next_queued_turn(sid) is None
    code, result = request(path, body)
    assert code == (200 if evidence == "confirmed" else 409), result
    rows = [json.loads(row) for row in capture.read_text().splitlines()]
    steers = [row for row in rows if row["method"] == "turn/steer"]
    assert len(steers) == 1
    saved = next((store._session_dir(sid) / "ingress").glob("queue-steer-*.json"))
    receipt = json.loads(saved.read_text())
    assert steers[0]["params"]["clientUserMessageId"] == receipt["native_steering"]["client_message_id"]
    assert receipt["native_steering"]["local_turn_id"] == active
    assert store.load_turn(sid, queued)["message"] == original["message"]
    assert len([row for row in rows if row["method"] == "turn/start"]) == 1
    if evidence == "confirmed":
        assert result["status"] == "delivered"
        assert store.load_turn(sid, queued)["status"] == "completed"
        assert request(path, body)[0] == 200
        assert len([row for row in store.events_after(sid, active, None) if row["kind"] == "turn.steered"]) == 1
    else:
        assert store.load_turn(sid, queued)["_queue_steering"]["status"] == "pending"
        assert store.claim_next_queued_turn(sid) is None


def test_native_readback_can_confirm_after_the_original_turn_has_finished(ordinary):  # noqa: F811
    store, runtime, _, request, capture, *_ = ordinary
    _lost_steering_ack(runtime, "confirmed")
    sid, active, queued, _ = held_queue(ordinary, "Read the original receipt.")
    runtime.adapters[sid].session.response_timeout_sec = 0.35
    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    body = {"target_turn_id": active, "client_ingress_id": "finished-original"}
    assert request(path, body)[0] == 409
    runtime.steer_active_turn(session_id=sid, expected_turn_id=active,
        client_ingress_id="finish", message="Complete the original Turn.")
    runtime.wait_for_turn(session_id=sid, turn_id=active, timeout_sec=5)
    assert store.load_turn(sid, active)["status"] == "completed"
    assert request(path, body)[0] == 200
    assert store.load_turn(sid, queued)["status"] == "completed"
    assert len([row for row in capture.read_text().splitlines() if json.loads(row)["method"] == "turn/steer"]) == 2


@pytest.mark.parametrize("change", ["legacy", "source"])
def test_unidentified_or_rebound_native_input_stays_unknown_without_history_reads(ordinary, change, monkeypatch):  # noqa: F811
    store, runtime, _, request, capture, *_ = ordinary
    _lost_steering_ack(runtime, "confirmed")
    sid, active, queued, _ = held_queue(ordinary, "Read the original receipt.")
    adapter = runtime.adapters[sid]
    adapter.session.response_timeout_sec = 0.35
    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    body = {"target_turn_id": active, "client_ingress_id": "unknown-original"}
    assert request(path, body)[0] == 409
    if change == "legacy":
        receipt = next((store._session_dir(sid) / "ingress").glob("queue-steer-*.json"))
        store.update_ingress_receipt(sid, json.loads(receipt.read_text())["client_ingress_id"], native_steering=None)
    else:
        monkeypatch.setattr(adapter.session, "thread_id", "replacement-thread")
    assert request(path, body)[0] == 409
    rows = [json.loads(row) for row in capture.read_text().splitlines()]
    assert len([row for row in rows if row["method"] == "turn/steer"]) == 1
    assert not [row for row in rows if row["method"] == "thread/items/list"]
    assert store.load_turn(sid, queued)["_queue_steering"]["status"] == "pending"


def test_checking_direct_steering_recovers_partial_transcript_writes_once(ordinary, monkeypatch):  # noqa: F811
    store, runtime, _, request, capture, *_ = ordinary
    _lost_steering_ack(runtime, "confirmed")
    sid, active, _, _ = held_queue(ordinary)
    runtime.adapters[sid].session.response_timeout_sec = 0.35
    path = f"/api/chat/sessions/{sid}/turns/{active}/steer"
    body = {"client_ingress_id": "direct-readback", "message": "Read the original receipt.\nRetain both lines."}
    assert request(path, body)[0] != 200
    original = store.update_ingress_receipt
    failures = []

    def fail_after_transcript(session_id, ingress_id, **changes):
        if changes.get("status") == "delivered" and not failures:
            failures.append(ingress_id)
            raise OSError("fixture receipt write interruption")
        return original(session_id, ingress_id, **changes)

    with monkeypatch.context() as patch:
        patch.setattr(store, "update_ingress_receipt", fail_after_transcript)
        failed_code, failed = request(path, body)
        assert failed_code == 409 and failed["delivery_state"] == "unresolved"
    assert failures == [body["client_ingress_id"]]
    assert request(path, body)[0] == 200
    assert request(path, body)[0] == 200
    assert len([row for row in store.messages(sid) if row["text"] == body["message"]]) == 1
    assert len([row for row in store.events_after(sid, active, None) if row["kind"] == "turn.steered"]) == 1
    assert len([row for row in capture.read_text().splitlines() if json.loads(row)["method"] == "turn/steer"]) == 1


def test_concurrent_direct_recovery_serializes_the_original_send_and_readback(ordinary):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor

    store, runtime, _, request, capture, *_ = ordinary
    _lost_steering_ack(runtime, "confirmed")
    sid, active, _, _ = held_queue(ordinary)
    runtime.adapters[sid].session.response_timeout_sec = 0.35
    path = f"/api/chat/sessions/{sid}/turns/{active}/steer"
    body = {"client_ingress_id": "concurrent-readback", "message": "Read the original receipt."}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: request(path, body), range(2)))
    assert sorted(code == 200 for code, _ in responses) == [False, True]
    assert len([row for row in store.messages(sid) if row["text"] == body["message"]]) == 1
    assert len([row for row in capture.read_text().splitlines() if json.loads(row)["method"] == "turn/steer"]) == 1


def test_proven_non_delivery_preserves_fifo_and_fresh_attempt_can_steer(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    body = {"target_turn_id": active, "client_ingress_id": "not-attached"}
    adapter = runtime.adapters.pop(sid)
    try:
        assert request(path, body)[1]["error_code"] == "live_steering_session_not_attached"
        assert [t["turn_id"] for t in store.queued_turns(sid)] == [queued]
    finally:
        runtime.adapters[sid] = adapter
    assert request(path, {**body, "client_ingress_id": "recovered"})[0] == 200
    assert request(path, body)[0] == 409
    assert store.load_turn(sid, queued)["_queue_steering"]["status"] == "delivered"


def test_queue_steering_revoked_scope_and_wrong_turn_leave_original_unchanged(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    original = store.load_turn(sid, queued)
    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    assert request(path, {"target_turn_id": "wrong", "client_ingress_id": "wrong-target"})[0] == 409
    runtime.project_contexts = ChatProjectContexts([])
    before = capture.read_bytes()
    assert request(path, {"target_turn_id": active, "client_ingress_id": "revoked"})[0] == 400
    assert request(f"/api/chat/sessions/{sid}/queue")[0] == 400
    assert store.load_turn(sid, queued) == original and capture.read_bytes() == before


def test_attachment_queue_is_preserved_when_native_steering_is_text_only(ordinary):  # noqa: F811
    import base64
    store, runtime, contexts, request, capture, *_ = ordinary
    data = b"# Original attachment\n"
    attachment = {"id": "original-note", "name": "note.md", "mime_type": "text/markdown",
        "size": len(data), "data_url": "data:text/markdown;base64," + base64.b64encode(data).decode()}
    sid, active, queued, _ = held_queue(ordinary, attachments=[attachment])
    original = store.load_turn(sid, queued)
    code, error = request(f"/api/chat/sessions/{sid}/queue/{queued}/steer", {
        "target_turn_id": active, "client_ingress_id": "text-only"})
    assert code == 409 and error["error_code"] == "queue_steering_attachments_unavailable"
    assert store.load_turn(sid, queued) == original
    assert store.queued_turns(sid)[0]["attachments"] == [attachment]


def test_peer_queue_cannot_bypass_its_original_collaboration_execution(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    peer, _ = store.create_queued_turn(sid, client_turn_id="peer", message="Original peer work", origin="peer")
    before = capture.read_bytes()
    code, error = request(f"/api/chat/sessions/{sid}/queue/{peer['turn_id']}/steer", {
        "target_turn_id": active, "client_ingress_id": "cannot-adopt-peer"})
    assert code == 409 and error["error_code"] == "queue_steering_source_unavailable"
    assert store.load_turn(sid, peer["turn_id"]) == peer and capture.read_bytes() == before


def test_queue_steering_reservation_fences_concurrent_automatic_claim(ordinary, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    entered, proceed = Event(), Event()
    native = runtime.steer_active_turn

    def pause(**kwargs):
        entered.set()
        assert proceed.wait(5)
        return native(**kwargs)

    monkeypatch.setattr(runtime, "steer_active_turn", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        steering = pool.submit(request, f"/api/chat/sessions/{sid}/queue/{queued}/steer",
            {"target_turn_id": active, "client_ingress_id": "concurrent"})
        assert entered.wait(5)
        consumer = pool.submit(store.claim_next_queued_turn, sid)
        assert not consumer.done()
        proceed.set()
        assert steering.result(timeout=10)[0] == 200
        assert consumer.result(timeout=10) is None
    runtime.wait_for_turn(session_id=sid, turn_id=active, timeout_sec=10)
    assert store.load_turn(sid, queued)["status"] == "completed"
    starts = [json.loads(line) for line in capture.read_text().splitlines() if json.loads(line)["method"] == "turn/start"]
    assert len(starts) == 1


def test_native_ack_survives_queue_settlement_failure_without_resending(ordinary, monkeypatch):  # noqa: F811
    store, runtime, contexts, request, capture, *_ = ordinary
    sid, active, queued, _ = held_queue(ordinary)
    write = store.write_queue_steering

    def fail_commit(sid, turn, plan):
        if plan["action"] == "complete":
            raise OSError("settlement write unavailable")
        return write(sid, turn, plan)

    path = f"/api/chat/sessions/{sid}/queue/{queued}/steer"
    body = {"target_turn_id": active, "client_ingress_id": "recover-commit"}
    with monkeypatch.context() as patch:
        patch.setattr(store, "write_queue_steering", fail_commit)
        assert request(path, body)[0] == 503
    runtime.wait_for_turn(session_id=sid, turn_id=active, timeout_sec=10)
    assert store.queued_turns(sid) == []
    assert request(path, body)[0] == 200
    rows = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len([r for r in rows if r["method"] == "turn/steer"]) == 1
    assert len([r for r in rows if r["method"] == "turn/start"]) == 1


def test_browser_queue_preserves_active_turn_then_delivers_fifo_once(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, _fake, _workspace = ordinary
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    first = request(f"/api/chat/sessions/{sid}/turns",
                    {"message": "wait for steer", "client_turn_id": "first"})[1]
    path = f"/api/chat/sessions/{sid}/turns"
    # Omitting the new intent preserves the original busy rejection.
    code, rejected = request(path, {"message": "not accepted", "client_turn_id": "direct"})
    assert code == 409 and rejected["active_turn_id"] == first["turn_id"]
    body = {"message": "Check the second source.", "client_turn_id": "next", "delivery": "queue"}
    code, queued = request(path, body)
    assert code == 202 and queued["status"] == "queued"
    assert store.load_session(sid)["active_turn_id"] == first["turn_id"]
    replay = request(path, body)[1]
    assert replay["turn_id"] == queued["turn_id"] and not replay["created"]
    third = request(path, {"message": "Check the third source.", "client_turn_id": "third", "delivery": "queue"})[1]
    assert third["status"] == "queued"
    # Release only the synthetic first worker, then observe the real FIFO owner.
    runtime.steer_active_turn(session_id=sid, expected_turn_id=first["turn_id"],
                              client_ingress_id="release-fixture", message="Complete this check.")
    assert runtime.wait_for_turn(session_id=sid, turn_id=first["turn_id"], timeout_sec=10)["status"] == "completed"
    assert runtime.wait_for_turn(session_id=sid, turn_id=queued["turn_id"], timeout_sec=10)["status"] == "completed"
    assert runtime.wait_for_turn(session_id=sid, turn_id=third["turn_id"], timeout_sec=10)["status"] == "completed"
    assert request(path, body)[1]["turn_id"] == queued["turn_id"]
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [row for row in packets if row["method"] == "turn/start"]
    assert len(starts) == 3
    assert starts[1]["params"]["input"][0]["text"].endswith(body["message"] + CHAT_RESPONSE_EXAMPLE_SUFFIX)
    assert starts[2]["params"]["input"][0]["text"].endswith("Check the third source." + CHAT_RESPONSE_EXAMPLE_SUFFIX)
    assert [row["text"] for row in store.messages(sid) if row["role"] == "user"] == [
        "wait for steer", body["message"], "Check the third source.", "Complete this check.",
    ]
    assert len(store.list_sessions()) == 1


@pytest.mark.parametrize("delivery", [True, "steer", "direct", [], {}])
def test_queue_intent_rejects_unknown_delivery_before_effects(ordinary, delivery):  # noqa: F811
    store, _runtime, contexts, request, _capture, _fake, _workspace = ordinary
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    code, rejected = request(f"/api/chat/sessions/{sid}/turns", {
        "message": "Do not dispatch.", "client_turn_id": "invalid", "delivery": delivery,
    })
    assert code == 400 and rejected["delivery_state"] == "not_delivered"
    assert store.messages(sid) == [] and store.queued_turns(sid) == []


def test_queue_capacity_failure_is_a_confirmed_non_delivery(ordinary, monkeypatch):  # noqa: F811
    store, runtime, contexts, request, _capture, _fake, _workspace = ordinary
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]

    def full(**_):
        raise RuntimeError("session_queue_full")

    monkeypatch.setattr(runtime, "enqueue_turn", full)
    code, rejected = request(f"/api/chat/sessions/{sid}/turns", {
        "message": "Keep the draft.", "client_turn_id": "full", "delivery": "queue",
    })
    assert code == 429 and rejected["error_code"] == "session_queue_full"
    assert rejected["delivery_state"] == "not_delivered" and rejected["turn_replay_safe"]
    assert store.messages(sid) == []


def test_web_queue_cannot_reuse_a_revoked_project_grant(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts

    store, runtime, contexts, request, capture, _fake, _workspace = ordinary
    sid = request("/api/chat/sessions", {"context_kind": "project",
                  "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    before = capture.read_bytes()
    runtime.project_contexts = ChatProjectContexts([])
    code, rejected = request(f"/api/chat/sessions/{sid}/turns", {
        "message": "Do not dispatch.", "client_turn_id": "revoked", "delivery": "queue",
    })
    assert code == 400 and rejected["delivery_state"] == "not_delivered"
    assert store.messages(sid) == [] and store.queued_turns(sid) == []
    assert capture.read_bytes() == before
