"""Public synthetic provider/native Core journeys, never private credentials."""
import json
import time
import http.client
import threading
from types import SimpleNamespace

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.extensions.lark.conversation_identity import identity_ref, lark_private_source, observe_lark_conversation_identity
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.goal_topic_runtime import poll_lark_goal_topic_profile_once


class Provider:
    def __init__(self):
        self.messages = {}
        self.calls = []
        self.writes = []
        self.topic_writes = []
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

    def topic(self, name, text, *, root=None, chat="oc_community", sender="ou_member", addressed=True):
        event = self.event("notes-app", name, text)
        event.update(chat_id=chat, chat_type="group", sender_id=sender)
        if root:
            event.update(root_id=root, parent_id=root, thread_id=f"omt_{root}")
        self.messages[event["message_id"]].update(chat_id=chat,
            sender={"id": sender, "sender_type": "user"},
            **{key: event[key] for key in ["root_id", "parent_id", "thread_id"] if key in event})
        if not root and addressed:
            self.messages[event["message_id"]]["mentions"] = [{"name": "notes-app", "id": {"open_id": "ou_bot"}}]
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
        elif "+chat-list" in args:
            data = {"ok": True, "data": {"chats": [{"chat_id": chat, "name": title}
                for chat, title in [("oc_community", "Community trial"), ("oc_second", "Second trial")]]}}
        elif "+messages-mget" in args:
            ref = args[args.index("--message-ids") + 1]
            message = self.messages[ref]
            if not self.verify_replies and ref.startswith("om_out_"):
                message = {**message, "body": {"content": json.dumps({"text": "not yet visible"})}}
            data = {"ok": True, "data": {"items": [message]}}
        elif "+messages-edit" in args:
            ref = args[args.index("--message-id") + 1]
            content = args[args.index("--content") + 1]
            assert self.messages[ref]["sender"]["id"] == self.profile_apps[profile]
            if "--dry-run" in args:
                data = {"ok": True, "api": [{"body": {"content": content, "msg_type": "post"}}]}
            else:
                self.messages[ref]["body"] = {"content": content}
                data = {"ok": True}
        elif "+messages-send" in args or "+messages-reply" in args:
            kind = "post" if "--content" in args else "text"
            content = args[args.index("--content") + 1] if kind == "post" else json.dumps({"text": args[args.index("--text") + 1]})
            text = json.loads(content)["zh_cn"]["content"][0][0]["text"] if kind == "post" else json.loads(content)["text"]
            if "--dry-run" in args:
                data = {"ok": True, "api": [{"body": {"content": content, "msg_type": kind}}]}
            else:
                ref = f"om_out_{len(self.writes)}"
                self.writes.append((profile, text))
                target = self.messages[args[args.index("--message-id") + 1]] if "+messages-reply" in args else None
                self.messages[ref] = {"message_id": ref, "msg_type": kind, "body": {"content": content},
                    "chat_id": target["chat_id"] if target else args[args.index("--chat-id") + 1],
                    "sender": {"id": self.profile_apps[profile], "sender_type": "app"}}
                if target:
                    assert "--reply-in-thread" in args
                    root = target.get("root_id") or target["message_id"]
                    self.messages[ref].update(root_id=root, parent_id=target["message_id"], thread_id=f"omt_{root}")
                    self.topic_writes.append((target["chat_id"], root, text))
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


def connect_group(fixture):
    store, runtime, provider, transport = connect(fixture)
    runtime.codex_home = fixture[-1].parent / "isolated-native-account"
    runtime.codex_home.mkdir()
    fake = fixture[-2]
    fake.write_text(fake.read_text().replace('"thread": {"id": "durable-thread"},',
        '"thread": {"id": "durable-thread"}, '
        '"activePermissionProfile": {"id": request["params"].get("permissions")}, '
        '"runtimeWorkspaceRoots": [request["params"]["cwd"]],'))
    binding = transport.bindings.read()["bindings"][0]
    refs = [identity_ref(binding["provider_ref"], chat) for chat in ["oc_community", "oc_second"]]
    transport.bindings.configure(transport_ref="notes-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", audience="group", group_refs=refs, available_group_refs=refs)
    return store, runtime, provider, transport


def finish_group_turn(runtime, transport, *, message):
    row = next(row for row in transport.core.pending() if row["message"] == message)
    assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)["status"] == "completed"
    transport.reconcile()
    return row


def test_group_topics_share_members_but_isolate_topics_apps_and_native_host(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("community-root", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        first = finish_group_turn(runtime, transport, message=root["content"])
        continuation = provider.topic("community-follow", "Compare its configuration", root=root["message_id"], sender="ou_other_member")
        assert transport.admit("notes-app", continuation)["status"] == "durably_accepted"
        second = finish_group_turn(runtime, transport, message=continuation["content"])
        assert second["session_id"] == first["session_id"]
        assert transport.admit("notes-app", {**continuation, "event_id": "redelivered"})["status"] == "durably_accepted"
        for name, chat in [("different-topic", "oc_community"), ("different-group", "oc_second")]:
            event = provider.topic(name, name, chat=chat)
            assert transport.admit("notes-app", event)["status"] == "durably_accepted"
            row = finish_group_turn(runtime, transport, message=name)
            assert row["session_id"] != first["session_id"]
        assert len(store.list_sessions()) == 3
        context = store.load_session(first["session_id"])["project_context"]
        assert context["audience"] == "bound_group" and context["filesystem_scope"] == "workspace_only"
        assert all(row["goal_id"] is None for row in store.list_sessions())
        assert provider.topic_writes and all(chat in {"oc_community", "oc_second"} for chat, _, _ in provider.topic_writes)
        assert any(root_id == root["message_id"] and text == "Runtime response." for _, root_id, text in provider.topic_writes)
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        starts = [r["params"] for r in requests if r["method"] == "thread/start"]
        assert len(starts) == 3 and all(r["permissions"] == "loopx_workspace_only_read" for r in starts)
        assert all(not r.get("dynamicTools") for r in starts)
        assert "Fresh Core evidence" not in ordinary[4].read_text()
        # Original private project still uses the default host and grant.
        assert runtime.project_contexts.available()[0].get("filesystem_scope") is None
        assert transport._binding("steward-app").get("audience") is None
        runtime.close()
        from loopx.chat_runtime import ChatRuntimeController
        restarted = ChatRuntimeController(store=store, codex_bin=str(ordinary[-2]), project_contexts=runtime.project_contexts)
        restarted.codex_home = runtime.codex_home
        try:
            reopened, resumed = restarted.open_session(goal_id=None, agent_id="codex", work_dir=ordinary[-1],
                objective="continue", conversation_binding_id=context["binding_id"],
                source_context=lark_private_source(provider_ref=context["provider_ref"], event=root),
                mode="resume_latest")
            assert resumed and reopened["session_id"] == first["session_id"]
        finally:
            restarted.close()
    finally:
        runtime.close()


def test_group_model_handoff_cannot_read_or_dispatch_to_a_private_goal(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_runtime import CodexAppServerAdapter
    store, runtime, provider, transport = connect_group(ordinary)
    original = CodexAppServerAdapter.start_turn

    def untrusted_response(adapter, *args, **kwargs):
        return {**original(adapter, *args, **kwargs), "context_handoff": {
            "goal_id": "private-work", "agent_id": "private-agent", "brief": "read private state"}}

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", untrusted_response)
    monkeypatch.setattr("loopx.chat_runtime.apply_context_handoff",
        lambda *_, **__: pytest.fail("workspace Chat must reject before any private Goal read or dispatch"))
    try:
        root = provider.topic("untrusted-handoff", "Explain this public project")
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        row = transport.core.pending()[0]
        turn = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert turn["status"] == "failed"
        assert "cannot hand off" in json.dumps(turn)
        assert not (store.root.parent / "manager-context").exists()
    finally:
        runtime.close()


def test_group_progress_queue_and_stop_remain_in_the_original_topic(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        roots = [provider.topic(f"running-{i}", "wait for interrupt") for i in range(2)]
        for root in roots:
            assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        rows = [next(row for row in transport.core.pending() if row["source"] == lark_private_source(
            provider_ref=transport._binding("notes-app")["provider_ref"], event=root)) for root in roots]
        for row in rows:
            deadline = time.monotonic() + 10
            while store.load_turn(row["session_id"], row["turn_id"])["status"] != "running":
                assert time.monotonic() < deadline
                time.sleep(.01)
        queued = provider.topic("queued-in-first-topic", "Continue this public check", root=roots[0]["message_id"])
        assert transport.admit("notes-app", queued)["status"] == "durably_accepted"
        assert len(store.queued_turns(rows[0]["session_id"])) == 1
        store.append_event(rows[0]["session_id"], rows[0]["turn_id"], kind="answer.delta", payload={"text": "Public progress"})
        store.append_event(rows[0]["session_id"], rows[0]["turn_id"], kind="agent.phase", payload={"method": "item/reasoning/textDelta", "label": "private reasoning"})
        transport.reconcile()
        assert any(root == roots[0]["message_id"] and "Public progress" in text for _, root, text in provider.topic_writes)
        assert all("private reasoning" not in text for _, _, text in provider.topic_writes)
        stop = provider.topic("stop-first-topic", "/stop", root=roots[0]["message_id"])
        assert transport.admit("notes-app", stop)["status"] == "command_recorded"
        assert runtime.wait_for_turn(session_id=rows[0]["session_id"], turn_id=rows[0]["turn_id"], timeout_sec=10)["status"] == "interrupted"
        assert store.load_turn(rows[1]["session_id"], rows[1]["turn_id"])["status"] == "running"
        finish_group_turn(runtime, transport, message=queued["content"])
        assert any("+messages-edit" in call for call in provider.calls)
        assert all(root in {row["message_id"] for row in roots} for _, root, _ in provider.topic_writes)
    finally:
        runtime.close()


def test_group_provenance_and_revocation_prevent_private_or_wrong_topic_delivery(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        for event in [provider.event("notes-app", "private", "private query"),
                      provider.topic("foreign", "foreign", chat="oc_not_selected"),
                      provider.topic("unaddressed", "ordinary community conversation", addressed=False)]:
            assert transport.admit("notes-app", event)["status"] in {"audience_rejected", "source_verification_failed"}
        root = provider.topic("root", "topic one")
        forged = provider.topic("forged", "wrong root", root=root["message_id"])
        provider.messages[forged["message_id"]]["root_id"] = "om_other_root"
        assert transport.admit("notes-app", forged)["status"] == "source_verification_failed"
        assert not store.list_sessions() and not list(transport.root.glob("*.json"))
        assert transport.admit("notes-app", root)["status"] == "durably_accepted"
        row = next(row for row in transport.core.pending() if row["message"] == root["content"])
        assert runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)["status"] == "completed"
        binding = transport._binding("notes-app")
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        transport.reconcile()
        assert provider.writes == []  # Even a completed result needs current source authority.
        assert transport.admit("notes-app", root)["status"] == "audience_rejected"
    finally:
        runtime.close()


def test_group_status_and_recipient_commands_cannot_expose_private_sessions(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect_group(ordinary)
    try:
        root = provider.topic("status-root", "/status")
        assert transport.admit("notes-app", root)["status"] == "command_recorded"
        help_event = provider.topic("help", "/help", root=root["message_id"])
        assert transport.admit("notes-app", help_event)["status"] == "command_recorded"
        for name, command in [("agents", "/agents"), ("agent", "/agent secret"), ("project", "/project")]:
            assert transport.admit("notes-app", provider.topic(name, command, root=root["message_id"]))["status"] == "command_recorded"
        transport.reconcile()
        assert not store.list_sessions()
        replies = "\n".join(text for _, text in provider.writes)
        assert str(ordinary[-1]) not in replies and "此 Bot" in replies
        assert "不能选择个人 Agent" in replies
        assert all(root_id == root["message_id"] for _, root_id, _ in provider.topic_writes)
    finally:
        runtime.close()


def test_http_group_setup_observes_membership_reads_back_selection_and_rejects_widening(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    import loopx.chat_lark_api as api

    store, runtime, provider, transport = connect(ordinary)
    monkeypatch.setattr(api, "build_lark_goal_topic_runtime_snapshot", lambda **_: {"goals": []})
    monkeypatch.setattr(ChatRequestHandler, "_lark_runner", lambda _: provider)
    refreshes = []
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.verbose = False
    server.registry_path, server.runtime_root_override = runtime.registry_path, store.root.parent
    server.lark_cli_resolution = LarkCliResolution(command="lark-cli", available=True, source="explicit", version="fixture", error_code=None)
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(refresh=lambda: refreshes.append(True), health_snapshot=lambda: {}, close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    path = "/api/chat/lark/private-conversations"
    body = {"app_ref": "notes-app", "project_ref": runtime.project_contexts.available()[0]["project_ref"],
        "executor_endpoint_id": "codex", "audience": "group", "group_ids": ["oc_community"]}
    try:
        original = transport.bindings.read()
        for bad in [{"group_ids": ["oc_not_selected"]}, {"group_ids": []},
                    {"group_ids": ["oc_community", "oc_community"]}, {"context_kind": "steward"},
                    {"executor_endpoint_id": "claude-code"}]:
            assert request(path, {**body, **bad})[0] == 400
            assert transport.bindings.read() == original and not refreshes
        status, response = request(path, body)
        assert status == 200, response
        saved = next(row for row in response["connections"] if row["app_ref"] == "notes-app")
        assert saved["audience"] == "group" and saved["group_count"] == 1
        assert saved["agent_candidates"] == saved["agent_targets"] == []
        assert str(ordinary[-1]) not in json.dumps(response)
        selected = request("/api/chat/lark/chats?app_ref=notes-app")[1]["chats"]
        assert [(row["chat_id"], row["selected"]) for row in selected] == [("oc_community", True), ("oc_second", False)]
        assert request(path, body)[1]["revision"] == response["revision"]
        assert refreshes == [True, True]
        assert not store.list_sessions() and not provider.writes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()


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
            legacy.setattr(private_conversations, "_presentation_text", lambda text: text)
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


def test_core_admission_verifies_once_inside_both_fences_including_replay(ordinary, monkeypatch):  # noqa: F811
    from contextlib import contextmanager
    from loopx.capabilities.native_chat import external_conversations

    store, runtime, _, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    original_lock = external_conversations.exclusive_file_lock
    active = set()

    @contextmanager
    def track_lock(path, *, operation):
        with original_lock(path, operation=operation):
            active.add(operation)
            try:
                yield
            finally:
                active.remove(operation)

    original_observe = transport.bindings.observe
    observed = []

    def observe(profile):
        assert active == {"route_external_chat_request", "admit_external_chat_request"}
        observed.append(profile)
        return original_observe(profile)

    monkeypatch.setattr(external_conversations, "exclusive_file_lock", track_lock)
    transport.bindings.observe = observe
    try:
        args = {"binding_id": binding["binding_id"], "source": source,
                "request_ref": "b" * 24, "message": "/help", "command": "help"}
        first = transport.core.admit(**args)
        assert first["status"] == "command_completed"
        assert observed == ["notes-app"]
        assert transport.core.admit(**args) == first
        assert observed == ["notes-app", "notes-app"]
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
        with pytest.raises(ValueError):
            transport.core.admit(**args)
        assert store.list_sessions() == []
    finally:
        runtime.close()


@pytest.mark.parametrize("field", ["binding_id", "source_ref"])
@pytest.mark.parametrize("value", ["../../outside", "", "not-an-opaque-reference", None])
def test_invalid_source_lock_path_is_rejected_before_file_access(ordinary, monkeypatch, field, value):  # noqa: F811
    from loopx.capabilities.native_chat import external_conversations

    _, runtime, _, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    binding_id = binding["binding_id"]
    if field == "binding_id":
        binding_id = value
    else:
        source[field] = value

    def unexpected_lock(*args, **kwargs):
        pytest.fail("invalid references must not reach filesystem locks")

    monkeypatch.setattr(external_conversations, "exclusive_file_lock", unexpected_lock)
    try:
        with pytest.raises(ValueError, match="invalid external conversation source reference"):
            transport.core.admit(binding_id=binding_id, source=source,
                                 request_ref="b" * 24, message="must not execute")
        assert not transport.core.root.exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["disconnect", "owner_change"])
def test_authority_changed_while_waiting_for_source_fence_blocks_admission(ordinary, monkeypatch, change):  # noqa: F811
    from contextlib import contextmanager
    from loopx.capabilities.native_chat import external_conversations

    store, runtime, _, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    source = {"source_ref": "a" * 24, "sender_ref": binding["operator_ref"], "private_human_message": True}
    original_lock = external_conversations.exclusive_file_lock
    original_observe = transport.bindings.observe

    @contextmanager
    def change_before_acquiring_source_fence(path, *, operation):
        if operation == "route_external_chat_request":
            if change == "disconnect":
                transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
            else:
                transport.bindings.observe = lambda profile: {**original_observe(profile), "operator_ref": "f" * 24}
        with original_lock(path, operation=operation):
            yield

    monkeypatch.setattr(external_conversations, "exclusive_file_lock", change_before_acquiring_source_fence)
    try:
        with pytest.raises(ValueError):
            transport.core.admit(binding_id=binding["binding_id"], source=source,
                                 request_ref="b" * 24, message="must not execute")
        assert transport.core.pending() == []
        assert store.list_sessions() == []
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
