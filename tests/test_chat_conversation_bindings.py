"""Bound external project Chat uses Core Session, queue, and exact stop."""
import time
import json

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401, F811
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
