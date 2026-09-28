"""A receiver must never observe delegated work without its exact return route."""

import json

import pytest

import loopx.capabilities.manager_context as delivery
import loopx.capabilities.manager_context.roundtrip as roundtrip
from loopx.chat_store import ChatSessionStore


@pytest.fixture(params=["manager", "goal.research", "manager.external.test"])
def conversation(tmp_path, request):
    root = tmp_path
    registry = root / "registry.json"
    registry.write_text(json.dumps({"goals": [{
        "id": "research", "repo": str(root),
        "coordination": {"registered_agents": ["worker"]},
    }]}))
    channel = request.param
    external = channel.startswith("manager.external.")
    store = ChatSessionStore(root)
    session = store.create_session(
        goal_id="research" if channel == "goal.research" else "loopx-manager",
        agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="fixture", channel_id=channel,
    )
    turn, _ = store.create_turn(
        session["session_id"], client_turn_id="owner-request",
        message="Ask the research owner for a Chinese draft. Do not publish it.",
        origin="lark" if external else "web",
    )
    target = {"goal_id": "research", "agent_id": "worker"}
    if external:
        delivery._write(delivery._root(root) / "policy.json", {
            "schema_version": delivery.POLICY_SCHEMA,
            "sources": {channel: {"sender_ids": ["owner"], "targets": [target]}},
        })
        delivery.register_ingress(
            root, session_id=session["session_id"],
            client_turn_id=turn["client_turn_id"], channel=channel,
            sender_id="owner", message=turn["message"], source_id="lark:request",
        )
    return root, registry, store, session, turn, target


@pytest.mark.parametrize("failure", ["route_write", "route_readback", "entry_write"])
def test_failed_preparation_never_exposes_work_and_exact_retry_recovers(
    conversation, monkeypatch, failure,
):
    root, registry, store, session, turn, target = conversation
    original_read = roundtrip._read

    def fail_route_write(path, value):
        raise OSError("return storage unavailable")

    def fail_route_readback(path):
        raise OSError("return readback unavailable")

    def fail_entry_write(path, value):
        raise OSError("inbox storage unavailable")

    with monkeypatch.context() as patch:
        if failure == "route_write":
            patch.setattr(roundtrip, "_write", fail_route_write)
        elif failure == "route_readback":
            patch.setattr(roundtrip, "_read", fail_route_readback)
        else:
            patch.setattr(delivery, "_write", fail_entry_write)
        with pytest.raises(OSError):
            delivery.deliver(root, registry, session=session, turn=turn, request=target)
        assert delivery.pending(root, **target)["items"] == []

    receipt = delivery.deliver(root, registry, session=session, turn=turn, request=target)
    assert not receipt["replayed"]
    assert len(delivery.pending(root, **target)["items"]) == 1
    assert delivery.deliver(root, registry, session=session, turn=turn, request=target) == {
        **receipt, "replayed": True,
    }
    route = original_read(delivery._root(root) / "roundtrips" / (receipt["request_id"] + ".json"))
    assert route["session_id"] == session["session_id"]
    assert route["client_turn_id"] == turn["client_turn_id"]
    assert route["channel_id"] == session["channel_id"]


def test_receiver_can_report_at_publication_before_sender_gets_receipt(
    conversation, monkeypatch,
):
    root, registry, store, session, turn, target = conversation
    original_write = delivery._write
    returned_text = "已完成中文草稿，保留原约束，尚未发布。"

    def receive_immediately(path, value):
        original_write(path, value)
        # The atomic entry rename is the publication boundary. An independently
        # scheduled receiver can read here, before deliver() returns to Chat.
        entry, = delivery.pending(root, **target)["items"]
        delivery.acknowledge(root, **target, request_id=entry["request_id"],
                             decision="adopt", reason="Keep the no-publication constraint")
        roundtrip.report(root, "research", "worker", entry["request_id"],
                         "conclusion", returned_text)

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_write", receive_immediately)
        receipt = delivery.deliver(root, registry, session=session, turn=turn, request=target)
    store.update_turn(session["session_id"], turn["turn_id"], status="completed",
                      response={"message": "Delivered", "context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    sends = []

    def sender(*args, **kwargs):
        sends.append((args, kwargs))
        return {"sent": True}

    # App readback survives restarting the store and needs no second model Turn.
    if not session["channel_id"].startswith("manager.external."):
        for _ in range(2):
            roundtrip.drain(root, registry, ChatSessionStore(root), sender)
        returned = [row for row in store.messages(session["session_id"])
                    if row.get("origin") == "manager_followup"]
        assert len(returned) == 1 and returned_text in returned[0]["text"]
        assert returned[0]["turn_id"] == turn["turn_id"]
        assert sends == []
    else:
        assert roundtrip.reply_status(root, receipt)[0]["status"] == "queued"


def test_lost_acknowledgement_leaves_a_returnable_request_and_replays_once(
    conversation, monkeypatch,
):
    root, registry, _, session, turn, target = conversation
    original_write = delivery._write

    def lose_ack(path, value):
        original_write(path, value)
        raise OSError("publication acknowledgement lost")

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "_write", lose_ack)
        with pytest.raises(OSError):
            delivery.deliver(root, registry, session=session, turn=turn, request=target)
    entry, = delivery.pending(root, **target)["items"]
    route = roundtrip._route(root, entry)
    assert route["session_id"] == session["session_id"]
    receipt = delivery.deliver(root, registry, session=session, turn=turn, request=target)
    assert receipt["replayed"] and receipt["request_id"] == entry["request_id"]
    assert len(delivery.pending(root, **target)["items"]) == 1


def test_conflicting_return_route_cannot_publish_work(conversation, monkeypatch):
    root, registry, _, session, turn, target = conversation
    original_write = roundtrip._write

    def wrong_destination(path, value):
        original_write(path, {**value, "session_id": "unrelated-conversation"})

    with monkeypatch.context() as patch:
        patch.setattr(roundtrip, "_write", wrong_destination)
        with pytest.raises(ValueError, match="return route readback failed"):
            delivery.deliver(root, registry, session=session, turn=turn, request=target)
    assert delivery.pending(root, **target)["items"] == []
    with pytest.raises(ValueError, match="return route conflict"):
        delivery.deliver(root, registry, session=session, turn=turn, request=target)
    assert delivery.pending(root, **target)["items"] == []
