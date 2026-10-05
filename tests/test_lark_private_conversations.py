"""Public synthetic provider/native Core journeys, never private credentials."""
import json
import time

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.extensions.lark.conversation_identity import observe_lark_conversation_identity
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.goal_topic_runtime import poll_lark_goal_topic_profile_once


class Provider:
    def __init__(self):
        self.messages = {}
        self.calls = []
        self.writes = []
        self.verify_replies = True

    def event(self, profile, name, text, kind="text"):
        event = {"schema_version": "lark_event_inbox_event_v0", "event_id": f"event_{name}",
            "message_id": f"om_{name}", "chat_id": f"oc_{profile.replace('-', '_')}", "chat_type": "p2p",
            "sender_type": "user", "sender_id": f"ou_{profile.replace('-', '_')}", "message_type": kind,
            "content": text}
        self.messages[event["message_id"]] = {"message_id": event["message_id"], "chat_id": event["chat_id"],
            "sender": {"id": event["sender_id"], "sender_type": "user"}, "msg_type": kind, "content": text}
        return event

    def __call__(self, args, cwd=None, timeout=None):
        self.calls.append(list(args))
        profile = args[args.index("--profile") + 1]
        if "auth" in args:
            data = {"ok": True, "appId": f"cli_{profile.replace('-', '_')}", "identities": {
                "bot": {"available": True, "verified": True, "appName": profile},
                "user": {"available": True, "verified": True, "openId": f"ou_{profile.replace('-', '_')}"}}}
        elif "+messages-mget" in args:
            ref = args[args.index("--message-ids") + 1]
            message = self.messages[ref]
            if not self.verify_replies and ref.startswith("om_out_"):
                message = {**message, "body": {"content": json.dumps({"text": "not yet visible"})}}
            data = {"ok": True, "data": {"items": [message]}}
        elif "+messages-send" in args:
            text = args[args.index("--text") + 1]
            content = json.dumps({"text": text})
            if "--dry-run" in args:
                data = {"ok": True, "api": [{"body": {"content": content}}]}
            else:
                ref = f"om_out_{len(self.writes)}"
                self.writes.append((profile, text))
                self.messages[ref] = {"message_id": ref, "body": {"content": content}}
                data = {"ok": True, "data": {"message_id": ref}}
        else:
            pytest.fail(f"unnecessary provider operation: {args[2:5]}")
        return {"returncode": 0, "stdout": json.dumps(data), "stderr": ""}


def connect(fixture):
    store, runtime, contexts, _, _, _, _ = fixture
    provider = Provider()
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
        observe=lambda profile: observe_lark_conversation_identity(profile=profile, runner=provider, cli_bin="lark-cli"))
    contexts.conversation_bindings = bindings
    for profile in ["notes-app", "steward-app"]:
        bindings.configure(transport_ref=profile, project_ref=contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
    return store, runtime, provider, LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                                                             runner=provider, cli_bin="lark-cli")


def test_native_private_admission_queue_other_app_stop_and_verified_delivery(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        first = provider.event("notes-app", "slow", "wait for interrupt")
        assert transport.admit("notes-app", first)["status"] == "durably_accepted"
        canonical = ChatExternalConversations(runtime).pending()[0]
        sid = canonical["session_id"]
        deadline = time.monotonic() + 10
        while store.load_session(sid).get("active_turn_id") != canonical["turn_id"]:
            assert time.monotonic() < deadline
            time.sleep(.01)
        queued = provider.event("notes-app", "queued", "follow-up")
        started = time.monotonic()
        assert transport.admit("notes-app", queued)["status"] == "durably_accepted"
        assert time.monotonic() - started < 3
        assert len(store.queued_turns(sid)) == 1
        # Status uses the canonical running Turn/queue, without another model call.
        status_event = provider.event("notes-app", "current-status", "/status")
        assert transport.admit("notes-app", status_event)["status"] == "command_recorded"
        status_snapshot = next(row["status_snapshot"] for row in transport.core.pending()
                               if row["command"] == "status")
        assert status_snapshot["active_turn_status"] in {"starting", "running"}
        transport.reconcile()
        status_reply = next(text for profile, text in provider.writes
                            if profile == "notes-app" and "排队消息：1 条" in text)
        phase = "正在启动" if status_snapshot["active_turn_status"] == "starting" else "正在执行"
        assert phase in status_reply and "个人助手 · notes" in status_reply
        assert str(ordinary[-1]) not in status_reply and "/help" in status_reply
        assert len(store.queued_turns(sid)) == 1
        assert transport.admit("notes-app", {**queued, "event_id": "redelivery"})["status"] == "durably_accepted"
        assert len(store.queued_turns(sid)) == 1
        other = provider.event("steward-app", "other", "different audience")
        assert transport.admit("steward-app", other)["status"] == "durably_accepted"
        other_row = next(row for row in transport.core.pending() if row["message"] == "different audience")
        assert runtime.wait_for_turn(session_id=other_row["session_id"], turn_id=other_row["turn_id"], timeout_sec=10)["status"] == "completed"
        transport.reconcile()
        assert any(profile == "steward-app" and text == "Runtime response." for profile, text in provider.writes)
        assert any(profile == "notes-app" and "持久受理" in text for profile, text in provider.writes)
        stop = provider.event("notes-app", "stop", "/stop")
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        queued_row = next(row for row in transport.core.pending() if row["message"] == "follow-up")
        assert runtime.wait_for_turn(session_id=sid, turn_id=queued_row["turn_id"], timeout_sec=10)["status"] == "completed"
        transport.reconcile()
        before = len(provider.writes)
        transport.reconcile()
        assert len(provider.writes) == before
        assert "different audience" not in str(store.messages(sid))
        assert not any("chats" in call for call in provider.calls)
        assert not any("schema_version" in text for _, text in provider.writes)
        assert all(row["goal_id"] is None for row in store.list_sessions())
    finally:
        runtime.close()


def test_source_rejection_attachment_notice_and_ambiguous_reply_readback(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect(ordinary)
    try:
        image = provider.event("notes-app", "image", "", kind="image")
        assert transport.admit("notes-app", {**image, "sender_type": "app"})["status"] == "audience_rejected"
        assert transport.admit("steward-app", image)["status"] == "audience_rejected"
        assert transport.core.pending() == []
        assert transport.admit("notes-app", image)["status"] == "command_recorded"
        provider.verify_replies = False
        assert transport.reconcile() == 0
        assert len(provider.writes) == 1 and "图片或文件没有交给模型" in provider.writes[0][1]
        assert transport.reconcile() == 0 and len(provider.writes) == 1
        provider.verify_replies = True
        assert transport.reconcile() == 1 and len(provider.writes) == 1
        assert runtime.store.list_sessions() == []
        with pytest.raises(ValueError, match="non-default"):
            observe_lark_conversation_identity(profile="default", runner=provider, cli_bin="lark-cli")
    finally:
        runtime.close()


def test_existing_consumer_dispatches_private_admission_without_waiting_for_model():
    event = {"chat_type": "p2p", "message_type": "text", "message_id": "om_synthetic"}
    admitted = []
    result = poll_lark_goal_topic_profile_once(profile="notes-app", snapshot={"private_profiles": {
        "notes-app": {"cli_bin": "lark-cli", "provider_ref": "a" * 24}}}, runtime_root=".",
        answer=lambda *_: pytest.fail("ordinary private Chat must not call a Goal answer"),
        consume_runner=lambda args: {"returncode": 0, "stdout": json.dumps(event)},
        private_admitter=lambda profile, event: admitted.append((profile, event)) or {"status": "durably_accepted"})
    assert result["event_statuses"] == ["durably_accepted"]
    assert admitted == [("notes-app", event)]


def test_new_session_replay_cannot_close_a_later_session(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "first", "first"))
        first = next(row for row in transport.core.pending() if row["message"] == "first")
        runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)
        new = provider.event("notes-app", "new", "/new")
        transport.admit("notes-app", new)
        transport.admit("notes-app", provider.event("notes-app", "next", "next"))
        second = next(row for row in transport.core.pending() if row["message"] == "next")
        assert first["session_id"] != second["session_id"]
        transport.admit("notes-app", new)
        assert store.load_session(second["session_id"])["status"] != "closed"
        # Simulate a lost correlation response after original Core admission.
        # Its stable Turn must remain in the closed original Session.
        original_path = transport.core.root / f"{first['request_ref']}.json"
        original = json.loads(original_path.read_text())
        original["status"] = "prepared"
        original_path.write_text(json.dumps(original))
        transport.core.recover()
        recovered = json.loads(original_path.read_text())
        assert recovered["session_id"] == first["session_id"] and recovered["turn_id"] == first["turn_id"]
        assert store.turn_for_client(second["session_id"], f"external-{first['request_ref']}") is None
        runtime.wait_for_turn(session_id=second["session_id"], turn_id=second["turn_id"], timeout_sec=10)
    finally:
        runtime.close()


def test_private_setup_companion_verifies_each_app_and_redacts_subjects(ordinary, tmp_path):  # noqa: F811
    from types import SimpleNamespace
    from loopx.extensions.lark.private_conversation_api import PrivateConversationRequestMixin
    _, runtime, _, transport = connect(ordinary)
    registry = tmp_path / "fresh-registry.json"
    registry.write_text(json.dumps({"schema_version": "0.1", "goals": []}))
    updates = []
    server = SimpleNamespace(runtime_controller=runtime, registry_path=registry, runtime_root_override=tmp_path,
        lark_private_conversations=transport, lark_goal_topic_runtime=SimpleNamespace(
            refresh=lambda: updates.append(True), health_snapshot=lambda: {}))

    class Handler(PrivateConversationRequestMixin):
        def __init__(self):
            self.server = server
            self.body = {}
            self.result = None
        def _read_json(self):
            return self.body
        def _send_json(self, value):
            self.result = value
        def _send_error(self, value, **kwargs):
            self.result = {"error": value, **kwargs}

    handler = Handler()
    try:
        handler._private_conversations()
        assert len(handler.result["connections"]) == 2
        assert all(row["context_available"] for row in handler.result["connections"])
        encoded = json.dumps(handler.result)
        assert all(marker not in encoded for marker in ["operator_ref", "provider_ref", "workspace_path", "ou_notes", "cli_notes"])
        handler.body = {"app_ref": "notes-app", "project_ref": "f" * 24, "executor_endpoint_id": "codex"}
        handler._private_conversation_connect()
        assert handler.result["status"] == 400 and not updates
        handler.body = {"binding_id": transport.bindings.read()["bindings"][0]["binding_id"], "revision": 0}
        handler._private_conversation_disconnect()
        assert handler.result["status"] == 400 and not updates
        handler.body["revision"] = transport.bindings.read()["revision"]
        handler._private_conversation_disconnect()
        assert handler.result["ok"] and len(handler.result["connections"]) == 1 and updates == [True]
    finally:
        runtime.close()


def test_rendered_cli_text_is_preserved_and_source_type_must_agree(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        literal = '{"text":"this is literal user text"}'
        event = provider.event("notes-app", "rendered", literal)
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        assert transport.core.pending()[0]["message"] == literal
        mismatched = provider.event("notes-app", "wrong_type", "not a text message")
        provider.messages[mismatched["message_id"]]["msg_type"] = "image"
        assert transport.admit("notes-app", mismatched)["status"] == "source_conflict"
        assert len(transport.core.pending()) == 1
    finally:
        runtime.close()


def test_private_app_alias_guard_rejects_unknown_or_unverified_identity():
    from loopx.chat_lark_api import _app_identity_for_private_guard
    provider = Provider()
    assert _app_identity_for_private_guard("notes-app", provider, "lark-cli") == "cli_notes_app"
    for data in [{}, {"appId": "cli_notes_app"}, {"appId": "malformed", "identities": {"bot": {
        "available": True, "verified": True}}}, {"appId": "cli_notes_app", "identities": {"bot": {
        "available": True, "verified": False}}}]:
        def unverified(*_args, **_kwargs):
            return {"returncode": 0, "stdout": json.dumps(data), "stderr": ""}
        with pytest.raises(ValueError, match="could not be verified"):
            _app_identity_for_private_guard("notes-app", unverified, "lark-cli")


def test_one_expired_app_identity_does_not_hide_the_other_listener(ordinary):  # noqa: F811
    _, runtime, _, transport = connect(ordinary)
    original = transport.bindings.observe
    def observe(profile):
        if profile == "steward-app":
            raise ValueError("this App owner is no longer verified")
        return original(profile)
    transport.bindings.observe = observe
    try:
        assert set(transport.profiles()) == {"notes-app"}
        transport.bindings.observe = lambda profile: {**original(profile), "operator_ref": "f" * 24}
        assert transport.profiles() == {}
        transport.bindings.observe = observe
        rejected = {"message_id": "om_unknown", "chat_id": "oc_steward_app", "sender_id": "ou_steward_app",
                    "chat_type": "p2p", "sender_type": "user", "message_type": "text", "content": "unverified"}
        assert transport.admit("steward-app", rejected)["status"] == "audience_rejected"
    finally:
        runtime.close()
