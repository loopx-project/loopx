"""Bound external project Chat uses Core Session, queue, and exact stop."""
import time
import json

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401, F811
from test_native_steward_private import steward  # noqa: F401
from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings


def test_explicit_project_write_reaches_native_host_and_resume_without_goal_and_revokes_old_session(ordinary):  # noqa: F811
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore

    store, runtime, contexts, _, capture, fake, workspace = ordinary
    observations = {profile: {"transport_ref": profile, "provider_ref": provider * 24,
        "operator_ref": operator * 24, "verified": True}
        for profile, provider, operator in [("notes-app", "c", "d"), ("other-app", "e", "f")]}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda p: observations[p])
    contexts.conversation_bindings = bindings
    ref = contexts.available()[0]["project_ref"]
    def configure(profile, grant="workspace_read"):
        return bindings.configure(transport_ref=profile, project_ref=ref, executor_endpoint_id="codex", project_grant=grant)
    def open_bound(controller, row):
        return controller.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="untrusted",
            mode="resume_latest", conversation_binding_id=row["binding_id"], source_context={
                "source_ref": "a" * 24, "sender_ref": row["operator_ref"], "private_human_message": True})[0]
    read = configure("notes-app")
    old = open_bound(runtime, read)
    other = open_bound(runtime, configure("other-app"))
    contexts.workspace_grant = "workspace_write"
    write = configure("notes-app", "workspace_write")
    assert write["binding_id"] != read["binding_id"]
    assert configure("notes-app", "workspace_write")["binding_id"] == write["binding_id"]
    with pytest.raises(ValueError, match="no longer authorized"):
        runtime.enqueue_turn(session_id=old["session_id"], client_turn_id="stale", message="edit",
            work_dir=workspace, objective="ignored", origin="lark")
    one = open_bound(runtime, write)
    assert one["session_id"] != old["session_id"] and one["goal_id"] is None
    assert one["project_context"]["grant"] == "workspace_write"
    adapter = runtime.adapters[one["session_id"]]
    assert not adapter.session.execution_mode and adapter.session.runtime_profile == "restricted"
    assert adapter.session.sandbox == "workspace-write"
    turn, _ = runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="edit", message="整理笔记：更新明确指定的文件",
        work_dir=workspace, objective="ignored", origin="lark")
    assert runtime.wait_for_turn(session_id=one["session_id"], turn_id=turn["turn_id"], timeout_sec=10)["status"] == "completed"
    original = one["upstream_thread_id"]
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=str(fake),
        project_contexts=contexts, registry_path=workspace / "no-registry.json")
    try:
        resumed = open_bound(restarted, write)
        assert resumed["session_id"] == one["session_id"] and resumed["upstream_thread_id"] == original
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        starts = [r["params"] for r in requests if r.get("method") == "thread/start"]
        assert [r["sandbox"] for r in starts] == ["read-only", "read-only", "workspace-write"]
        resumes = [r["params"] for r in requests if r.get("method") == "thread/resume"]
        assert resumes[-1]["sandbox"] == "workspace-write" and resumes[-1]["threadId"] == original
        prompts = [r["params"]["input"][0]["text"] for r in requests if r.get("method") == "turn/start"]
        assert "project assistant" in prompts[-1] and "Do not edit files" not in prompts[-1]
        assert "AGENTS.md" in prompts[-1] and "hidden Goal" in prompts[-1]
        assert other["project_context"]["grant"] == "workspace_read"
        downgraded = configure("notes-app")
        assert downgraded["binding_id"] != write["binding_id"]
        with pytest.raises(ValueError, match="no longer authorized"):
            open_bound(restarted, write)
        assert open_bound(restarted, downgraded)["session_id"] != one["session_id"]
        assert store.turn_for_client(old["session_id"], "stale") is None
        assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()
    finally:
        restarted.close()


def test_two_bound_audiences_continue_independently_and_use_core_queue_and_stop(ordinary):  # noqa: F811
    store, runtime, contexts, _, _, _, workspace = ordinary
    observations = {
        "notes-app": {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                      "operator_ref": "d" * 24, "verified": True},
        "other-app": {"transport_ref": "other-app", "provider_ref": "e" * 24,
                      "operator_ref": "f" * 24, "verified": True},
    }
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
                                        observe=lambda profile: observations[profile])
    contexts.conversation_bindings = bindings
    ref = contexts.available()[0]["project_ref"]
    first = bindings.configure(transport_ref="notes-app", project_ref=ref, executor_endpoint_id="codex")
    second = bindings.configure(transport_ref="other-app", project_ref=ref, executor_endpoint_id="codex")
    assert bindings.configure(transport_ref="notes-app", project_ref=ref,
                              executor_endpoint_id="codex")["binding_id"] == first["binding_id"]

    def open_bound(row):
        return runtime.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="ignored",
            mode="resume_latest", conversation_binding_id=row["binding_id"], source_context={
                "source_ref": "a" * 24, "sender_ref": row["operator_ref"], "private_human_message": True})[0]

    try:
        one, two = open_bound(first), open_bound(second)
        assert one["session_id"] != two["session_id"] and one["channel_id"] != two["channel_id"]
        assert one["goal_id"] is None and two["goal_id"] is None
        assert open_bound(first)["session_id"] == one["session_id"]
        blocked, _ = runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="slow", message="wait for interrupt",
            work_dir=workspace, objective="ignored", origin="lark")
        deadline = time.monotonic() + 10
        while store.load_session(one["session_id"])["active_turn_id"] != blocked["turn_id"]:
            assert time.monotonic() < deadline
            time.sleep(.01)
        started = time.monotonic()
        queued, created = runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="later", message="follow-up",
            work_dir=workspace, objective="ignored", origin="lark")
        assert created and time.monotonic() - started < 3
        assert store.load_turn(one["session_id"], queued["turn_id"])["status"] == "queued"
        assert store.turn_for_client(one["session_id"], "later")["turn_id"] == queued["turn_id"]
        assert runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="later", message="follow-up",
            work_dir=workspace, objective="ignored", origin="lark")[1] is False
        independent, _ = runtime.enqueue_turn(session_id=two["session_id"], client_turn_id="independent", message="other audience",
            work_dir=workspace, objective="ignored", origin="lark")
        assert runtime.wait_for_turn(session_id=two["session_id"], turn_id=independent["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_turn(one["session_id"], blocked["turn_id"])["status"] not in {"completed", "interrupted"}
        runtime.interrupt_turn(session_id=one["session_id"], turn_id=blocked["turn_id"])
        assert runtime.wait_for_turn(session_id=one["session_id"], turn_id=queued["turn_id"], timeout_sec=10)["status"] == "completed"
        assert store.load_turn(one["session_id"], blocked["turn_id"])["status"] == "interrupted"
        assert "other audience" not in str(store.messages(one["session_id"]))
        assert "follow-up" not in str(store.messages(two["session_id"]))
        with pytest.raises(ValueError, match="source"):
            runtime.submit_turn(session_id=one["session_id"], client_turn_id="web", message="wrong source",
                                work_dir=workspace, objective="ignored")
        bindings.disconnect(first["binding_id"], expected_revision=bindings.read()["revision"])
        with pytest.raises(ValueError, match="no longer authorized"):
            runtime.enqueue_turn(session_id=one["session_id"], client_turn_id="revoked", message="old grant",
                                 work_dir=workspace, objective="ignored", origin="lark")
        assert store.turn_for_client(one["session_id"], "revoked") is None
    finally:
        runtime.close()


def test_binding_replacement_or_identity_change_cannot_move_existing_context(tmp_path):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.chat_store import ChatSessionStore

    a, b = tmp_path / "notes", tmp_path / "other"
    a.mkdir()
    b.mkdir()
    contexts = ChatProjectContexts([a, b])
    store = ChatSessionStore(tmp_path / "runtime")
    observation = {"transport_ref": "notes-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    old = bindings.configure(transport_ref="notes-app", project_ref=contexts.available()[0]["project_ref"], executor_endpoint_id="codex")
    saved = bindings.resolve(binding_id=old["binding_id"], source_ref="a" * 24,
                             sender_ref=observation["operator_ref"], private_human_message=True)["context"]
    observation["operator_ref"] = "f" * 24
    with pytest.raises(ValueError, match="independently verified"):
        bindings.session_context(saved)
    observation["operator_ref"] = "d" * 24
    new = bindings.configure(transport_ref="notes-app", project_ref=contexts.available()[1]["project_ref"], executor_endpoint_id="codex")
    assert new["binding_id"] != old["binding_id"]
    with pytest.raises(ValueError, match="no longer authorized"):
        bindings.session_context(saved)
    with pytest.raises(ValueError, match="revision"):
        bindings.disconnect(new["binding_id"], expected_revision=0)
    assert bindings.read()["bindings"][0]["binding_id"] == new["binding_id"]
    assert bindings.path.stat().st_mode & 0o777 == 0o600


def test_read_only_bound_project_rejects_write_scoped_executor_before_session_creation(
    ordinary, monkeypatch  # noqa: F811
):
    store, runtime, contexts, _, _, _, workspace = ordinary
    observations = {
        "notes-app": {
            "transport_ref": "notes-app",
            "provider_ref": "c" * 24,
            "operator_ref": "d" * 24,
            "verified": True,
        }
    }
    bindings = ChatConversationBindings(
        root=store.root, project_contexts=contexts, observe=lambda profile: observations[profile]
    )
    contexts.conversation_bindings = bindings
    project_ref = contexts.available()[0]["project_ref"]
    binding = bindings.configure(
        transport_ref="notes-app",
        project_ref=project_ref,
        executor_endpoint_id="kiro-cli",
        project_grant="workspace_read",
    )
    monkeypatch.setattr(
        "loopx.chat_endpoint_catalog.shutil.which",
        lambda executable: executable if executable == "kiro-cli" else None,
    )
    starts = []

    class FakeWriteScopedAdapter:
        upstream_thread_id = "must-not-be-started"

        def close_session(self):
            pass

    start_adapter = runtime._start_adapter
    runtime._start_adapter = lambda **kwargs: starts.append(kwargs) or FakeWriteScopedAdapter()
    source_context = {
        "source_ref": "a" * 24,
        "sender_ref": binding["operator_ref"],
        "private_human_message": True,
    }

    with pytest.raises(ValueError, match="read-only"):
        runtime.open_session(
            goal_id=None,
            agent_id="kiro-cli",
            work_dir=workspace,
            objective="untrusted",
            mode="resume_latest",
            conversation_binding_id=binding["binding_id"],
            source_context=source_context,
        )

    assert starts == []
    assert store.latest_session(
        goal_id=None, agent_id="kiro-cli", channel_id=f"project.{project_ref}"
    ) is None

    # Selecting an actually read-only executor still lets the bound conversation
    # start, complete a request, and replay its idempotency key.
    read_only_binding = bindings.configure(
        transport_ref="notes-app",
        project_ref=project_ref,
        executor_endpoint_id="codex",
        project_grant="workspace_read",
    )
    monkeypatch.setattr(
        "loopx.chat_endpoint_catalog.shutil.which",
        lambda executable: executable
        if executable in {"kiro-cli", runtime.codex_bin}
        else None,
    )
    runtime._start_adapter = start_adapter
    session, resumed = runtime.open_session(
        goal_id=None,
        agent_id="codex",
        work_dir=workspace,
        objective="untrusted",
        mode="resume_latest",
        conversation_binding_id=read_only_binding["binding_id"],
        source_context=source_context,
    )
    assert not resumed
    turn, created = runtime.enqueue_turn(
        session_id=session["session_id"],
        client_turn_id="read-only-bound-project-turn",
        message="summarize the workspace",
        work_dir=workspace,
        objective="untrusted",
        origin="lark",
    )
    assert created
    assert runtime.wait_for_turn(
        session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10
    )["status"] == "completed"
    replay, replayed = runtime.enqueue_turn(
        session_id=session["session_id"],
        client_turn_id="read-only-bound-project-turn",
        message="summarize the workspace",
        work_dir=workspace,
        objective="untrusted",
        origin="lark",
    )
    assert not replayed and replay["turn_id"] == turn["turn_id"]


def test_external_transport_preserves_native_request_identity_and_session(ordinary, monkeypatch):  # noqa: F811
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    store, runtime, contexts, _, capture, fake, workspace = ordinary
    observation = {"transport_ref": "external-owner", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts,
                                        observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    binding = bindings.configure(transport_ref="external-owner", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex")
    core = ChatExternalConversations(runtime)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": observation["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "Explain the current project", "origin": "external"}
    enqueue = runtime.enqueue_turn
    def crash_after_enqueue(**kwargs):
        enqueue(**kwargs)
        raise OSError("crash before request journal settles")
    monkeypatch.setattr(runtime, "enqueue_turn", crash_after_enqueue)
    try:
        with pytest.raises(OSError, match="crash"):
            core.admit(**args)
        prepared = core.read_request(args["request_ref"])
        assert prepared["status"] == "prepared"
        original = store.turn_for_client(prepared["session_id"], "external-" + args["request_ref"])
        turn = runtime.wait_for_turn(session_id=prepared["session_id"], turn_id=original["turn_id"], timeout_sec=10)
        assert turn["status"] == "completed" and turn["origin"] == "external"
        upstream = store.load_session(prepared["session_id"])["upstream_thread_id"]
        runtime.close()
        runtime = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=str(fake),
                                       project_contexts=contexts, registry_path=runtime.registry_path)
        core = ChatExternalConversations(runtime)
        core.recover(request_ref=args["request_ref"])
        admitted = core.read_request(args["request_ref"])
        assert admitted["status"] == "accepted" and admitted["turn_id"] == original["turn_id"]
        assert store.load_session(admitted["session_id"])["upstream_thread_id"] == upstream
        assert core.admit(**args) == admitted
        with pytest.raises(ValueError, match="identity"):
            core.admit(**{**args, "origin": "lark"})
        with pytest.raises(ValueError, match="origin"):
            core.admit(**{**args, "request_ref": "f" * 24, "origin": "web"})
        with pytest.raises(ValueError, match="audience"):
            core.admit(**{**args, "request_ref": "f" * 24,
                "source": {**args["source"], "sender_ref": "b" * 24}})
        assert len(store.list_sessions()) == 1
        assert store.load_session(admitted["session_id"])["project_context"]["binding_id"] == binding["binding_id"]
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len([row for row in requests if row.get("method") == "thread/start"]) == 1
        assert len([row for row in requests if row.get("method") == "turn/start"]) == 1
        assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()
    finally:
        runtime.close()


def test_external_steward_reuses_machine_grant_and_original_delegation(steward, monkeypatch):  # noqa: F811
    from loopx.capabilities.manager_context import authority, deliver, pending
    from test_native_steward_runtime import apply_profile
    store, runtime, _, transport, binding, _, workspace = steward
    apply_profile(store.root.parent, "trusted_owner")
    runtime.registry_path.write_text(json.dumps({"runtime_root": str(store.root.parent), "goals": [
        {"id": "registered-work", "repo": str(workspace), "coordination": {"registered_agents": ["worker"]}}]}))
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **_: None)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": binding["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "Ask the registered worker to inspect the project", "origin": "external"}
    row = transport.core.admit(**args)
    assert row["status"] == "accepted"
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    assert turn["origin"] == "external" and turn["status"] == "queued"
    assert session["manager_runtime_profile"] == "trusted_owner"
    assert session["manager_runtime_sandbox"] == "danger-full-access"
    target = {"goal_id": "registered-work", "agent_id": "worker"}
    assert authority(store.root.parent, runtime.registry_path, session, turn)["targets"] == [target]
    receipt = deliver(store.root.parent, runtime.registry_path, session=session, turn=turn, request=target)
    assert pending(store.root.parent, **target)["items"][0]["message"] == args["message"]
    assert deliver(store.root.parent, runtime.registry_path, session=session, turn=turn, request=target) == {**receipt, "replayed": True}
    assert transport.core.admit(**args)["turn_id"] == row["turn_id"]
    assert not authority(store.root.parent, runtime.registry_path, session, {**turn, "origin": "web"})["targets"]
    assert not authority(store.root.parent, runtime.registry_path, session, {**turn, "message": "changed"})["targets"]
    unbound = {**session, "steward_context": None}
    assert not authority(store.root.parent, runtime.registry_path, unbound, turn)["targets"]
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    with pytest.raises(ValueError, match="no longer authorized"):
        transport.core.admit(**{**args, "request_ref": "f" * 24})


def test_missing_origin_in_legacy_request_retains_lark_identity(ordinary):  # noqa: F811
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    store, runtime, contexts, _, _, _, _ = ordinary
    observation = {"transport_ref": "legacy-app", "provider_ref": "c" * 24,
                   "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: observation)
    contexts.conversation_bindings = bindings
    binding = bindings.configure(transport_ref="legacy-app", project_ref=contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex")
    core = ChatExternalConversations(runtime)
    args = {"binding_id": binding["binding_id"], "source": {"source_ref": "a" * 24,
            "sender_ref": observation["operator_ref"], "private_human_message": True},
            "request_ref": "e" * 24, "message": "/help", "command": "help"}
    try:
        original = core.admit(**args)
        path = core.root / (args["request_ref"] + ".json")
        original.pop("origin")
        path.write_text(json.dumps(original))
        assert core.admit(**args) == original
        with pytest.raises(ValueError, match="identity"):
            core.admit(**args, origin="external")
        assert store.list_sessions() == []
    finally:
        runtime.close()
