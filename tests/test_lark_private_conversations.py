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
        self.reactions = {}
        self.reaction_creates = []
        self.fail_reaction_create = False
        self.fail_reaction_delete = False
        self.profile_apps = {profile: f"cli_{profile.replace('-', '_')}"
                             for profile in ["notes-app", "steward-app"]}

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
        if args[1:] == ["profile", "list"]:
            return {"returncode": 0, "stdout": json.dumps([
                {"name": profile, "appId": app_id}
                for profile, app_id in self.profile_apps.items()]), "stderr": ""}
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
            kind = "post" if "--content" in args else "text"
            content = args[args.index("--content") + 1] if kind == "post" else json.dumps({"text": args[args.index("--text") + 1]})
            text = json.loads(content)["zh_cn"]["content"][0][0]["text"] if kind == "post" else json.loads(content)["text"]
            if "--dry-run" in args:
                data = {"ok": True, "api": [{"body": {"content": content, "msg_type": kind}}]}
            else:
                ref = f"om_out_{len(self.writes)}"
                self.writes.append((profile, text))
                self.messages[ref] = {"message_id": ref, "msg_type": kind, "body": {"content": content}}
                data = {"ok": True, "data": {"message_id": ref}}
        elif "reactions" in args:
            ref = args[args.index("--message-id") + 1]
            if "create" in args:
                if self.fail_reaction_create:
                    return {"returncode": 1, "stdout": '{"ok":false}'}
                emoji = json.loads(args[args.index("--data") + 1])["reaction_type"]["emoji_type"]
                reaction = f"reaction_{len(self.reaction_creates)}"
                self.reaction_creates.append((profile, ref, emoji))
                self.reactions[reaction] = (profile, ref, emoji)
                data = {"ok": True, "data": {"reaction_id": reaction}}
            elif "delete" in args:
                if self.fail_reaction_delete:
                    return {"returncode": 1, "stdout": '{"ok":false}'}
                reaction = args[args.index("--reaction-id") + 1]
                assert self.reactions[reaction][:2] == (profile, ref)
                self.reactions.pop(reaction)
                data = {"ok": True}
            else:
                data = {"ok": True, "data": {"items": [{"reaction_id": key} for key, row in self.reactions.items()
                                                        if row[:2] == (profile, ref)], "has_more": False}}
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
        assert any(profile == "notes-app" and "这条已排队" in text for profile, text in provider.writes)
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


@pytest.mark.parametrize("profile", ["notes-app", "steward-app"])
@pytest.mark.parametrize("info", ["serverOverloaded", "other"])
def test_model_capacity_failure_keeps_session_and_original_delivery(ordinary, profile, info):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    _, _, _, _, capture, fake, _ = ordinary
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        f'"codexErrorInfo": {json.dumps(info)}').replace('private-fixture-upstream-detail',
        'private-fixture Selected model is at capacity serverOverloaded'))
    try:
        event = provider.event(profile, "busy-model", "typed terminal failure")
        assert transport.admit(profile, event)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        sid, tid = row["session_id"], row["turn_id"]
        turn = runtime.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)
        assert turn["status"] == "failed" and turn["response"] is None
        assert turn["error_code"] == ("server_overloaded" if info == "serverOverloaded" else "host_gate")
        thread = store.load_session(sid)["upstream_thread_id"]
        transport.reconcile()
        terminal = provider.writes[-1][1]
        assert ("当前模型繁忙" in terminal) == (info == "serverOverloaded")
        assert "原会话已保留" in terminal
        assert "private-fixture" not in terminal and "Partial answer" not in terminal
        count = len(provider.writes)
        LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli").reconcile()
        assert len(provider.writes) == count
        # Only the human's next input starts work; no retry or model/thread fallback.
        before = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(r["method"] == "turn/start" for r in before) == 1
        followup = provider.event(profile, "after-busy", "continue here")
        transport.admit(profile, followup)
        following = transport.core.pending()[0]
        assert following["session_id"] == sid
        assert runtime.wait_for_turn(session_id=sid, turn_id=following["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_session(sid)["upstream_thread_id"] == thread
        assert len(store.list_sessions()) == 1 and store.load_session(sid)["goal_id"] is None
    finally:
        runtime.close()


def test_source_rejection_unsupported_file_notice_and_ambiguous_reply_readback(ordinary):  # noqa: F811
    _, runtime, provider, transport = connect(ordinary)
    try:
        image = provider.event("notes-app", "file", "", kind="file")
        assert transport.admit("notes-app", {**image, "sender_type": "app"})["status"] == "audience_rejected"
        assert transport.admit("steward-app", image)["status"] == "audience_rejected"
        assert transport.core.pending() == []
        assert transport.admit("notes-app", image)["status"] == "command_recorded"
        provider.verify_replies = False
        assert transport.reconcile() == 0
        assert len(provider.writes) == 1 and "未提交执行" in provider.writes[0][1]
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


@pytest.mark.parametrize("answer,display", [
    ("Author @source_account; primary post unverified.", "Author ＠source_account; primary post unverified."),
    (r"Result\nNext step", "Result\nNext step"),
    ('Untrusted <at id="unknown">label</at>', 'Untrusted ‹at id="unknown">label‹/at>'),
])
def test_private_answer_repairs_presentation_and_recovers_exact_receipt(ordinary, answer, display):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    fake, capture = ordinary[-2], ordinary[-3]
    fake.write_text(fake.read_text().replace('"Runtime response."', json.dumps(answer)))
    try:
        event = provider.event("notes-app", "quoted-source", "Read the source.")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        native = transport.core.pending()[0]
        turn = runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=10)
        assert turn["response"]["message"] == answer
        provider.verify_replies = False
        assert transport.reconcile() == 0
        assert provider.writes[-1] == ("notes-app", display)
        before = list(provider.writes)
        saved = json.loads(next(transport.root.glob("*.json")).read_text())
        assert saved["deliveries"]["terminal"]["text"] == display
        assert saved["deliveries"]["terminal"]["attempt"]
        # Restart only the transport facade: read back the original attempt,
        # without another provider send, model Turn or rewritten Core response.
        recovered = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                             runner=provider, cli_bin="lark-cli")
        provider.verify_replies = True
        assert recovered.reconcile() == 1
        assert recovered.reconcile() == 0
        assert provider.writes == before
        assert store.load_turn(native["session_id"], native["turn_id"])["response"]["message"] == answer
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(row.get("method") == "turn/start" for row in calls) == 1
    finally:
        runtime.close()


def test_private_answer_keeps_preexisting_attempt_text_on_recovery(ordinary, monkeypatch):  # noqa: F811
    from loopx.extensions.lark import private_conversations
    store, runtime, provider, transport = connect(ordinary)
    fake, capture = ordinary[-2], ordinary[-3]
    answer = "Existing\r\nanswer."
    fake.write_text(fake.read_text().replace('"Runtime response."', json.dumps(answer)))
    try:
        event = provider.event("notes-app", "old-reply-intent", "Continue the answer.")
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        native = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=native["session_id"], turn_id=native["turn_id"], timeout_sec=10)
        provider.verify_replies = False
        # Reproduce the pre-change facade: it journals Core text unchanged;
        # the existing Inbox owner independently normalizes the provider wire.
        with monkeypatch.context() as legacy:
            legacy.setattr(private_conversations, "normalize_lark_outbound_text", lambda text, **_: text)
            assert transport.reconcile() == 0
        saved = json.loads(next(transport.root.glob("*.json")).read_text())
        assert saved["deliveries"]["terminal"]["text"] == answer
        assert provider.writes[-1] == ("notes-app", "Existing\nanswer.")
        before = list(provider.writes)
        provider.verify_replies = True
        recovered = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                             runner=provider, cli_bin="lark-cli")
        assert recovered.reconcile() == 1
        assert recovered.reconcile() == 0 and provider.writes == before
        assert store.load_turn(native["session_id"], native["turn_id"])["response"]["message"] == answer
        assert sum(json.loads(line).get("method") == "turn/start" for line in capture.read_text().splitlines()) == 1
    finally:
        runtime.close()


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



def test_revocation_during_source_read_prevents_native_admission(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "revoked", "must not execute")
        binding = transport.bindings.read()["bindings"][0]

        def revoke_after_read(args, cwd=None, timeout=None):
            result = provider(args, cwd, timeout)
            if "+messages-mget" in args:
                transport.bindings.disconnect(binding["binding_id"],
                    expected_revision=transport.bindings.read()["revision"])
            return result

        transport.runner = revoke_after_read
        assert transport.admit("notes-app", event)["status"] == "command_rejected"
        assert transport.core.pending() == []
        assert store.list_sessions() == []
        assert provider.writes == []
    finally:
        runtime.close()



def test_listener_discovery_is_separate_from_current_owner_authorization(ordinary):  # noqa: F811
    _, runtime, _, transport = connect(ordinary)
    original = transport.bindings.observe
    def observe(profile):
        if profile == "steward-app":
            raise ValueError("this App owner is no longer verified")
        return original(profile)
    transport.bindings.observe = observe
    try:
        provider = transport.runner
        provider.calls.clear()
        assert set(transport.profiles()) == {"notes-app", "steward-app"}
        assert provider.calls == [["lark-cli", "profile", "list"]]
        transport.bindings.observe = lambda profile: {**original(profile), "operator_ref": "f" * 24}
        assert set(transport.profiles()) == {"notes-app", "steward-app"}
        transport.bindings.observe = observe
        rejected = {"message_id": "om_unknown", "chat_id": "oc_steward_app", "sender_id": "ou_steward_app",
                    "chat_type": "p2p", "sender_type": "user", "message_type": "text", "content": "unverified"}
        assert transport.admit("steward-app", rejected)["status"] == "audience_rejected"
        assert transport.admit("notes-app", provider.event("notes-app", "healthy", "/status"))["status"] == "command_recorded"
    finally:
        runtime.close()



def test_listener_lease_uses_current_app_and_detects_removal_or_retarget(ordinary):  # noqa: F811
    import hashlib

    _, runtime, provider, transport = connect(ordinary)
    try:
        profiles = transport.profiles()
        assert profiles["notes-app"]["consumer_ref"] == hashlib.sha256(b"cli_notes_app").hexdigest()[:32]
        # A new App under the old profile must not run under the old App's lease.
        provider.profile_apps["notes-app"] = "cli_replacement"
        assert set(transport.profiles()) == {"steward-app"}
        del provider.profile_apps["notes-app"]
        assert set(transport.profiles()) == {"steward-app"}
        provider.profile_apps["notes-app"] = "cli_notes_app"
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        assert set(transport.profiles()) == {"steward-app"}
    finally:
        runtime.close()



@pytest.mark.parametrize("result", [
    {"returncode": 1, "stdout": "", "timed_out": True},
    {"returncode": 0, "stdout": "not JSON"},
    {"returncode": 0, "stdout": "{}"},
])
def test_unreadable_local_profile_inventory_is_unknown_not_removed(ordinary, result):  # noqa: F811
    _, runtime, _, transport = connect(ordinary)
    try:
        transport.runner = lambda *_args: result
        with pytest.raises(OSError, match="configuration could not be read"):
            transport.profiles()
    finally:
        runtime.close()



def test_actual_stream_survives_probe_and_inventory_fault_then_stops_on_disconnect(ordinary):  # noqa: F811
    import threading
    from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile

    _, runtime, provider, transport = connect(ordinary)
    released, listening, fault_seen, recovered = (threading.Event() for _ in range(4))
    result = {}
    fail_inventory = False

    def snapshot():
        if fail_inventory and not fault_seen.is_set():
            saved = transport.runner
            transport.runner = lambda *_args: {"returncode": 1, "stdout": ""}
            try:
                return {"private_profiles": transport.profiles()}
            finally:
                transport.runner = saved
                fault_seen.set()
        profiles = transport.profiles()
        if fault_seen.is_set():
            recovered.set()
        return {"private_profiles": profiles}

    class Lines:
        def __iter__(self):
            yield "[event] ready event_key=im.message.receive_v1\n"
            assert released.wait(10)

    class Consumer:
        stdout = Lines()
        def poll(self):
            return 0 if released.is_set() else None
        def wait(self, timeout=None):
            assert released.wait(timeout or 10)
            return 0
        def terminate(self):
            released.set()
        kill = terminate

    stop = threading.Event()
    def run():
        result.update(stream_lark_goal_topic_profile(profile="notes-app", snapshot_provider=snapshot,
            stop=stop, runtime_root=transport.runtime_root, answer=lambda *_args: "unused",
            private_admitter=transport.admit, process_factory=lambda *_args: Consumer(),
            health_sink=lambda update: listening.set() if update.get("status") == "listening" else None))

    worker = threading.Thread(target=run)
    try:
        worker.start()
        assert listening.wait(3)
        transport.bindings.observe = lambda _profile: (_ for _ in ()).throw(ValueError("verification unavailable"))
        fail_inventory = True
        assert fault_seen.wait(3) and recovered.wait(3)
        assert worker.is_alive() and not released.is_set() and not stop.is_set()
        assert transport.admit("notes-app", provider.event("notes-app", "unverified", "/status"))["status"] == "audience_rejected"
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        worker.join(4)
        assert not worker.is_alive() and stop.is_set()
        assert result["status"] == "configuration_removed"
    finally:
        stop.set()
        released.set()
        worker.join(4)
        runtime.close()



def test_slow_reply_readback_does_not_hold_independent_stop_or_other_app(ordinary, monkeypatch):  # noqa: F811
    import threading
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    store, runtime, provider, transport = connect(ordinary)
    blocked, release = threading.Event(), threading.Event()
    original = Provider.__call__

    def delayed(self, args, cwd=None, timeout=None):
        if "+messages-mget" in args and args[args.index("--message-ids") + 1] == "om_out_0":
            blocked.set()
            assert release.wait(10)
        return original(self, args, cwd, timeout)

    monkeypatch.setattr(Provider, "__call__", delayed)
    transport.admit("notes-app", provider.event("notes-app", "old-status", "/status"))
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=store.root.parent,
        runtime_controller=runtime, private_conversations=transport)
    worker = threading.Thread(target=service._reconcile_private)
    worker.start()
    try:
        assert blocked.wait(10)
        transport.admit("notes-app", provider.event("notes-app", "urgent-stop", "/stop"))
        transport.admit("steward-app", provider.event("steward-app", "other-status", "/status"))
        deadline = time.monotonic() + 5
        while len(provider.writes) < 3:
            assert time.monotonic() < deadline
            time.sleep(.01)
        # The old provider read is still blocked, yet both exact audiences have
        # received feedback. No Session/model was needed for these controls.
        assert not release.is_set()
        assert ("notes-app", "当前没有正在执行的消息。") in provider.writes
        assert any(profile == "steward-app" and "个人助手 · notes" in text
                   for profile, text in provider.writes)
        assert store.list_sessions() == []
    finally:
        release.set()
        service._closed.set()
        worker.join(10)
        runtime.close()
    assert not worker.is_alive()
    writes = list(provider.writes)
    transport.reconcile()
    assert provider.writes == writes  # Recovery does not resend an attempted reply.



def test_private_reply_workers_have_no_executor_backlog_and_stop_scheduling(tmp_path):
    import threading
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    release, full = threading.Event(), threading.Event()
    lock = threading.Lock()
    calls = []

    class Pending:
        def pending_delivery_paths(self):
            return [tmp_path / str(index) for index in range(9)]

        def reconcile_request(self, path):
            with lock:
                calls.append(path)
                if len(calls) == 4:
                    full.set()
            assert release.wait(10)
            return 0

    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=None, private_conversations=Pending())
    worker = threading.Thread(target=service._reconcile_private)
    worker.start()
    try:
        assert full.wait(5)
        assert len(calls) == 4 and len(set(calls)) == 4
        service._closed.set()
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert len(calls) == 4  # Five durable requests remain; none were queued in memory.
    finally:
        service._closed.set()
        release.set()
        worker.join(10)



def test_scoped_recovery_does_not_probe_unrelated_app_and_revocation_blocks_reply(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        for profile in ["notes-app", "steward-app"]:
            transport.admit(profile, provider.event(profile, profile, "/status"))
        first = next(row for row in transport.core.pending()
                     if row["binding_id"] == transport.bindings.read()["bindings"][0]["binding_id"])
        observed = []
        original = transport.bindings.observe
        transport.bindings.observe = lambda profile: (observed.append(profile), original(profile))[1]
        transport.core.recover(request_ref=first["request_ref"])
        assert observed == ["notes-app"]
        binding = transport.bindings.read()
        transport.bindings.disconnect(first["binding_id"], expected_revision=binding["revision"])
        assert transport.reconcile_request(transport.root / f"{first['request_ref']}.json") == 0
        assert provider.writes == [] and store.list_sessions() == []
    finally:
        runtime.close()
