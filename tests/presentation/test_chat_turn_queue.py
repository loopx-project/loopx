"""Ordinary browser queue intent through real HTTP, store and native stdio."""

import json

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401


def held_queue(ordinary, text="Check the second source.", attachments=None):
    store, runtime, contexts, request, capture, *_ = ordinary
    sid = request("/api/chat/sessions", {"context_kind": "project",
        "project_ref": contexts.available()[0]["project_ref"]})[1]["session_id"]
    first = request(f"/api/chat/sessions/{sid}/turns", {
        "message": "wait for steer", "client_turn_id": "held"})[1]
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
    assert starts[1]["params"]["input"][0]["text"].endswith(body["message"])
    assert starts[2]["params"]["input"][0]["text"].endswith("Check the third source.")
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
