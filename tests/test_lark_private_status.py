"""Status observes real native files/queue without creating model work."""
import time

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.extensions.lark.private_conversations import LarkPrivateConversations, _status_text
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_agent import CodexChatAgentError


def test_help_and_empty_steward_status_do_not_open_sessions_or_borrow_scope(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "help", "/help"))
        assert transport.reconcile() == 1
        message = provider.writes[-1][1]
        assert "个人助手 · notes" in message and "只读授权" in message
        assert "/new" in message and "设置 → Lark" in message
        assert "/delegate" not in message
        assert transport.core.pending()[0]["status_snapshot"]["grant"] == "workspace_read"
        assert store.list_sessions() == []
        project = runtime.project_contexts.available()[0]
        transport.bindings.configure(transport_ref="steward-app", project_ref=project["project_ref"],
                                     executor_endpoint_id="codex", context_kind="steward")
        transport.admit("steward-app", provider.event("steward-app", "empty", "/status"))
        assert transport.reconcile() == 1
        assert "长期管家 · notes" in provider.writes[-1][1]
        assert "已授权委托：0" in provider.writes[-1][1]
        assert "仍需验收" in provider.writes[-1][1]
        assert store.list_sessions() == []
        assert all(row["turn_id"] is None for row in transport.core.pending())
    finally:
        runtime.close()


def test_unselected_project_status_does_not_read_or_expose_agent_cadence(  # noqa: F811
    ordinary, monkeypatch  # noqa: F811
):
    store, runtime, provider, transport = connect(ordinary)
    from loopx.control_plane import effect_runtime

    actual = effect_runtime.effect_runtime_result

    def observe(method, params, **kwargs):
        if method == "quota.automation_cadence.manage":
            pytest.fail("project status must not read an Agent cadence owner")
        return actual(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observe)
    try:
        transport.admit(
            "notes-app", provider.event("notes-app", "project-status", "/status")
        )
        native = next(
            row for row in transport.core.pending() if row["command"] == "status"
        )
        assert "automation_cadence" not in native["status_snapshot"]
        assert transport.reconcile() == 1
        assert "自动执行" not in provider.writes[-1][1]
        assert store.list_sessions() == []
    finally:
        runtime.close()


def test_pending_status_readback_recovers_from_files_without_resending(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        provider.verify_replies = False
        transport.admit("notes-app", provider.event("notes-app", "status", "/status"))
        snapshot = transport.core.pending()[0]["status_snapshot"]
        assert transport.reconcile() == 0
        assert len(provider.writes) == 1
        assert transport.health()[transport.bindings.read()["bindings"][0]["binding_id"]]["recovery_count"] == 1
        # A new production transport instance observes the original durable
        # intent and native request; it neither opens a Session nor resends.
        restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
                                            runner=provider, cli_bin="lark-cli")
        provider.verify_replies = True
        assert restarted.reconcile() == 1
        assert len(provider.writes) == 1
        assert restarted.core.pending()[0]["status_snapshot"] == snapshot
        assert store.list_sessions() == []
    finally:
        runtime.close()


@pytest.mark.parametrize("resume_error", [False, True])
def test_status_and_help_observe_original_session_after_actual_upstream_resume(ordinary, resume_error):  # noqa: F811
    import json
    store, original_runtime, provider, transport = connect(ordinary)
    _, _, contexts, _, capture, fake, workspace = ordinary
    restarted = None
    try:
        transport.admit("notes-app", provider.event("notes-app", "initial", "initial"))
        original = transport.core.pending()[0]
        sid = original["session_id"]
        original_runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=10)
        assert transport.reconcile() == 1
        upstream = store.load_session(sid)["upstream_thread_id"]
        original_runtime.close()
        if resume_error:
            fake.write_text(fake.read_text().replace(
                '    elif method in {"thread/start", "thread/resume"}:',
                '    elif method == "thread/resume":\n'
                '        print(json.dumps({"id": request_id, "error": {"code": -32000, "message": "Original thread unavailable"}}), flush=True)\n'
                '        continue\n'
                '    elif method in {"thread/start", "thread/resume"}:'))
        restarted = ChatRuntimeController(store=store, codex_bin=str(fake), project_contexts=contexts,
                                          registry_path=original_runtime.registry_path)
        def resume():
            return restarted.open_session(goal_id=None, agent_id="codex", work_dir=workspace, objective="",
                mode="resume_latest", conversation_binding_id=original["binding_id"], source_context=original["source"])
        if resume_error:
            with pytest.raises(CodexChatAgentError, match="could not be restored"):
                resume()
        else:
            assert resume()[0]["session_id"] == sid
        before_observation = capture.read_text().splitlines()
        assert any(json.loads(line).get("method") == "thread/resume" for line in before_observation)
        transport = LarkPrivateConversations(controller=restarted, runtime_root=store.root.parent,
                                             runner=provider, cli_bin="lark-cli")
        for command in ["status", "help"]:
            transport.admit("notes-app", provider.event("notes-app", command, f"/{command}"))
            row = next(row for row in transport.core.pending() if row["command"] == command)
            assert row["session_id"] == sid
            assert row["status_snapshot"]["session_status"] == ("resume_failed" if resume_error else "ready")
            transport.reconcile()
            assert ("会话恢复失败" if resume_error else "可以继续对话") in provider.writes[-1][1]
            assert "尚无会话" not in provider.writes[-1][1]
        assert len(store.list_sessions()) == 1
        assert store.load_session(sid)["upstream_thread_id"] == upstream
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len([row for row in requests if row.get("method") == "thread/start"]) == 1
        assert capture.read_text().splitlines() == before_observation
    finally:
        if restarted:
            restarted.close()
        original_runtime.close()


def test_status_after_new_observes_closed_original_without_reopening_it(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "initial", "initial"))
        first = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)
        transport.admit("notes-app", provider.event("notes-app", "new", "/new"))
        transport.admit("notes-app", provider.event("notes-app", "status", "/status"))
        snapshot = next(row for row in transport.core.pending() if row["command"] == "status")["status_snapshot"]
        assert snapshot["session_status"] == "closed"
        transport.reconcile()
        assert any("会话已关闭" in text for _, text in provider.writes)
        assert len(store.list_sessions()) == 1
        transport.admit("notes-app", provider.event("notes-app", "next", "next"))
        second = next(row for row in transport.core.pending() if row["message"] == "next")
        assert second["session_id"] != first["session_id"]
        runtime.wait_for_turn(session_id=second["session_id"], turn_id=second["turn_id"], timeout_sec=10)
    finally:
        runtime.close()


def test_busy_status_reads_durable_queue_and_duplicate_retains_original_snapshot(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "slow", "wait for interrupt"))
        first = transport.core.pending()[0]
        sid = first["session_id"]
        deadline = time.monotonic() + 10
        while store.load_session(sid).get("active_turn_id") != first["turn_id"]:
            assert time.monotonic() < deadline
            time.sleep(.01)
        transport.admit("notes-app", provider.event("notes-app", "queued", "follow-up"))
        status = provider.event("notes-app", "status", "/status")
        transport.admit("notes-app", status)
        original = next(row for row in transport.core.pending() if row["command"] == "status")
        assert original["status_snapshot"]["queued_count"] == 1
        assert original["status_snapshot"]["active_turn_status"] in {"starting", "running"}
        transport.reconcile()
        assert any("排队消息：1 条" in text for _, text in provider.writes)
        transport.admit("notes-app", provider.event("notes-app", "stop", "/stop"))
        queued = next(row for row in transport.core.pending() if row["message"] == "follow-up")
        runtime.wait_for_turn(session_id=sid, turn_id=queued["turn_id"], timeout_sec=10)
        transport.reconcile()
        before = len(provider.writes)
        transport.admit("notes-app", {**status, "event_id": "redelivery"})
        transport.reconcile()
        assert len(provider.writes) == before
        assert transport.core.read_request(original["request_ref"])["status_snapshot"] == original["status_snapshot"]
        transport.admit("notes-app", provider.event("notes-app", "current", "/status"))
        transport.reconcile()
        assert "排队消息：0 条" in provider.writes[-1][1]
        assert len(store.list_sessions()) == 1
        assert store.load_session(sid)["goal_id"] is None
        assert not any("source_ref" in text or "operator_ref" in text for _, text in provider.writes)
    finally:
        runtime.close()


@pytest.mark.parametrize("unknown", ["session", "turn", "missing"])
def test_unavailable_execution_evidence_is_not_presented_as_ready(unknown):
    snapshot = {"context_kind": "project", "observed_at": "2026-01-01T10:00:00Z",
        "workspace_path": "/authorized/notes", "executor_endpoint_id": "codex", "grant": "workspace_read",
        "queued_count": 0, "authorized_commission_count": 0, "session_status": "future_state" if unknown == "session" else "busy",
        "active_turn_status": "future_state" if unknown == "turn" else None,
        "active_turn_observation_available": unknown != "missing"}
    text = _status_text(snapshot, help_requested=False)
    assert "暂不可" in text and "可以继续对话" not in text


def test_explicit_write_status_matches_binding_without_opening_a_model_session(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        runtime.project_contexts.workspace_grant = "workspace_write"
        transport.bindings.configure(transport_ref="notes-app", project_ref=runtime.project_contexts.available()[0]["project_ref"],
            executor_endpoint_id="codex", project_grant="workspace_write")
        transport.admit("notes-app", provider.event("notes-app", "write-status", "/help"))
        assert transport.reconcile() == 1
        assert "当前工作区可读写" in provider.writes[-1][1]
        assert "只读授权" not in provider.writes[-1][1]
        assert "项目规则和 skills" in provider.writes[-1][1]
        assert store.list_sessions() == []
    finally:
        runtime.close()
