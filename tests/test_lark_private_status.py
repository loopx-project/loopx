"""Status observes real native files/queue without creating model work."""
import time

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.extensions.lark.private_conversations import LarkPrivateConversations, _status_text


def test_help_and_empty_steward_status_do_not_open_sessions_or_borrow_scope(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        transport.admit("notes-app", provider.event("notes-app", "help", "/help"))
        assert transport.reconcile() == 1
        message = provider.writes[-1][1]
        assert "角色：普通项目对话" in message and "只读授权" in message
        assert "/new" in message and "设置 → Lark" in message
        assert "/delegate" not in message and "没有自动选用注册 Agent" in message
        assert store.list_sessions() == []
        project = runtime.project_contexts.available()[0]
        transport.bindings.configure(transport_ref="steward-app", project_ref=project["project_ref"],
                                     executor_endpoint_id="codex", context_kind="steward")
        transport.admit("steward-app", provider.event("steward-app", "empty", "/status"))
        assert transport.reconcile() == 1
        assert "角色：长期管家" in provider.writes[-1][1]
        assert "已授权新委托：0" in provider.writes[-1][1]
        assert "执行结束不代表委托验收" in provider.writes[-1][1]
        assert store.list_sessions() == []
        assert all(row["turn_id"] is None for row in transport.core.pending())
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
        assert any("已持久排队：1 条" in text for _, text in provider.writes)
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
        assert "已持久排队：0 条" in provider.writes[-1][1]
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
    assert "暂不可" in text and "会话可继续" not in text
