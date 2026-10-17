"""Ordinary browser queue intent through real HTTP, store and native stdio."""

import json

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401


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
