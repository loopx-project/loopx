"""Source-bound send/recovery journeys through the real native File/Core owner."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import (  # noqa: F401
    completed_group_stop, connect, connect_group,
)

from loopx.extensions.lark.inbox_reply import reply_lark_event_inbox


def _deliver(transport, record, *, phase="admission"):
    path = transport.root / f"{record['request_ref']}.json"
    # Each test prepares this Inbox before counting the send/recovery itself.
    # Metadata preparation is separate from its fresh source authority check.
    config = transport.runtime_root / ".loopx/config/private-chat" / f"{record['source']['source_ref']}.json"
    assert config.exists()
    return transport._deliver(path, record, phase, "Original source result", inbox=lambda: config)


def test_send_then_lost_readback_recovers_with_one_fresh_source_authority_each_time(completed_group_stop):  # noqa: F811
    _, provider, transport, root, _, record = completed_group_stop
    transport._inbox(record)
    provider.calls.clear()
    provider.verify_replies = False
    assert not _deliver(transport, record)
    assert len(provider.writes) == 1
    before_send = next(i for i, args in enumerate(provider.calls)
                       if "+messages-reply" in args and "--dry-run" not in args)
    assert sum("auth" in args for args in provider.calls[:before_send]) == 1
    assert record["deliveries"]["admission"]["attempt"]["message_ref"] == "om_out_0"
    provider.verify_replies = True
    provider.calls.clear()
    assert _deliver(transport, record)
    assert sum("auth" in args for args in provider.calls) == 1
    assert len(provider.writes) == 1
    assert provider.topic_writes == [("oc_community", root["message_id"], "Original source result")]
    # A successful previous operation is not authority for this readback.
    record["deliveries"]["admission"]["verified"] = False
    provider.messages[root["message_id"]]["mentions"] = []
    provider.calls.clear()
    assert not _deliver(transport, record)
    assert sum("auth" in args for args in provider.calls) == 1
    assert len(provider.writes) == 1


@pytest.mark.parametrize("private", [False, True])
def test_ordinary_result_uses_the_same_fresh_app_proof_for_sender_and_source(ordinary, private):  # noqa: F811
    _, runtime, provider, transport = connect(ordinary) if private else connect_group(ordinary)
    try:
        root = provider.event("notes-app", "status-root", "/status") if private else provider.topic("status-root", "/status")
        assert transport.admit("notes-app", root)["status"] == "command_recorded"
        record = next(json.loads(p.read_text()) for p in transport.root.glob("*.json"))
        transport._inbox(record)
        provider.calls.clear()
        assert _deliver(transport, record)
        assert sum("auth" in args for args in provider.calls) == 1
        assert provider.writes == [("notes-app", "Original source result")]
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["app", "bot", "owner", "revoke", "root", "sender", "chat", "parent", "thread"])
def test_current_source_denial_never_becomes_a_send_or_receipt_intent(completed_group_stop, change):  # noqa: F811
    _, provider, transport, root, event, record = completed_group_stop
    transport._inbox(record)
    if change == "app":
        provider.profile_apps["notes-app"] = "cli_other"
    elif change == "bot":
        provider.bot_ready = False
    elif change == "owner":
        # Group authority is App-scoped, independent of a private owner login.
        provider.owner_ready = False
        assert _deliver(transport, record)
        return
    elif change == "revoke":
        transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif change == "root":
        provider.messages[root["message_id"]]["mentions"] = []
    else:
        field, value = {"sender": ("sender", {"id": "ou_other", "sender_type": "user"}),
            "chat": ("chat_id", "oc_other"), "parent": ("parent_id", "om_other"),
            "thread": ("thread_id", "omt_other")}[change]
        provider.messages[event["message_id"]][field] = value
    assert not _deliver(transport, record)
    assert not provider.writes and not provider.reaction_creates
    assert not record["deliveries"]["admission"].get("started")
    if change in {"root", "sender", "chat", "parent", "thread"}:
        # A verified App with a changed source is not an App-name mismatch.
        # Keeping that distinction also avoids an unrelated metadata refresh.
        assert record["deliveries"]["admission"]["blocker"] == "lark_inbox_reply_sender_not_in_configured_chat"


def test_ordinary_source_read_must_join_before_late_revocation_can_authorize_a_send(ordinary, monkeypatch):  # noqa: F811
    _, runtime, provider, transport = connect_group(ordinary)
    entered, release = threading.Event(), threading.Event()
    try:
        root = provider.topic("late-root", "/status")
        event = provider.topic("late-reply", "/status", root=root["message_id"])
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        record = next(json.loads(p.read_text()) for p in transport.root.glob("*.json"))
        config = transport._inbox(record)
        original = transport._group_source_message

        def read(profile, message_id):
            result = original(profile, message_id)
            if message_id == root["message_id"]:
                entered.set()
                assert release.wait(3)
            return result

        monkeypatch.setattr(transport, "_group_source_message", read)
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(transport._deliver, transport.root / f"{record['request_ref']}.json",
                                    record, "admission", "Original source result", inbox=lambda: config)
            try:
                assert entered.wait(2)
                transport.bindings.disconnect(record["binding_id"], expected_revision=transport.bindings.read()["revision"])
                assert not provider.writes
            finally:
                release.set()
            assert not pending.result(timeout=3)
        assert not provider.writes and not record["deliveries"]["admission"].get("started")
    finally:
        release.set()
        runtime.close()


@pytest.mark.parametrize("field", ["profile", "chat", "message"])
def test_a_foreign_inbox_route_cannot_borrow_the_original_source_proof(completed_group_stop, field):  # noqa: F811
    _, provider, transport, _, event, record = completed_group_stop
    args = ["notes-app", event["chat_id"], event["message_id"]]
    args[["profile", "chat", "message"].index(field)] = "foreign"
    provider.calls.clear()
    assert transport._source_authority(record, *args) == (None, False)
    assert not provider.calls and not provider.writes


def test_legacy_membership_callback_keeps_its_own_sender_identity_gate(completed_group_stop):  # noqa: F811
    _, provider, transport, _, event, record = completed_group_stop
    config = transport._inbox(record)
    provider.bot_ready = False
    calls = []
    result = reply_lark_event_inbox(project=transport.runtime_root, config_path=config,
        message_id=event["message_id"], text="Original source result", execute=True,
        runner=transport._reply_runner, source_membership_verifier=lambda: calls.append(True) or True)
    assert not result["ok"] and not result["sender_identity_verified"]
    assert not calls and not provider.writes


def test_a_boolean_is_not_a_verified_source_identity(completed_group_stop):  # noqa: F811
    _, provider, transport, _, event, record = completed_group_stop
    config = transport._inbox(record)
    provider.calls.clear()
    result = reply_lark_event_inbox(project=transport.runtime_root, config_path=config,
        message_id=event["message_id"], text="Original source result", execute=True,
        runner=transport._reply_runner, source_authority_verifier=lambda *_: True)
    assert not result["ok"] and not result["sender_identity_verified"]
    assert not provider.calls and not provider.writes
