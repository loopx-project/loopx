"""Status is a view of canonical facts, not inferred progress or permission."""
from types import MappingProxyType

import pytest

from loopx.extensions.lark.private_conversations import _status_text
from loopx.presentation.renderers.conversation_status_markdown import render_conversation_status


def snapshot(**changes):
    return {"context_kind": "project", "workspace_path": "/workspace/example-notes",
            "executor_endpoint_id": "codex", "grant": "workspace_read",
            "observed_at": "2026-10-01T12:00:00+00:00", "session_status": "ready",
            "active_turn_status": None, "active_turn_observation_available": True,
            "queued_count": 0, "authorized_commission_count": 0, **changes}


def test_short_status_keeps_facts_and_moves_diagnostics_to_help():
    facts = snapshot(queued_count=3)
    rendered = _status_text(facts, help_requested=False)
    assert rendered.startswith("🟢 可以继续对话。")
    assert "个人助手 · example-notes" in rendered
    assert "排队消息：3 条" in rendered and "只读授权" in rendered
    assert "/help" in rendered and "/workspace" not in rendered
    assert "观察于 " in rendered and facts["observed_at"] not in rendered
    help_text = _status_text(facts, help_requested=True)
    for exact_fact in [facts["workspace_path"], facts["executor_endpoint_id"], facts["observed_at"]]:
        assert exact_fact in help_text
    assert "/agents" in help_text and "图片/文件目前未交给模型" in help_text


@pytest.mark.parametrize("changes", [
    {"session_status": "resume_failed"},
    {"active_turn_status": "completed", "active_turn_observation_available": False},
    {"session_status": "unknown_state"},
    {"active_turn_status": "unknown_state"},
])
def test_missing_or_failed_observations_never_claim_ready(changes):
    rendered = render_conversation_status(snapshot(**changes))
    assert rendered.startswith("⚠️") and "可以继续对话" not in rendered
    assert "本机检查原会话" in rendered


def test_execution_end_is_not_steward_acceptance():
    facts = MappingProxyType(snapshot(context_kind="steward", active_turn_status="completed",
                                      authorized_commission_count=2))
    rendered = render_conversation_status(facts)
    assert rendered.startswith("⚪ 本次执行已结束。")
    assert "长期管家" in rendered and "已授权委托：2 个" in rendered
    assert "仍需验收" in rendered and "✅" not in rendered


@pytest.mark.parametrize("grant,expected", [
    ("workspace_write", "当前工作区可读写。"),
    ("workspace_read", "当前工作区仅有只读授权。"),
    ("unknown", "工作区权限暂不可判定"),
    (None, "工作区权限暂不可判定"),
])
def test_writes_require_the_existing_explicit_grant(grant, expected):
    assert expected in render_conversation_status(snapshot(grant=grant))


def test_attached_host_help_does_not_advertise_unsupported_controls():
    rendered = _status_text(snapshot(recipient_agent_id="authorized-worker",
                                     recipient_goal_id="authorized-goal"), help_requested=True)
    assert "已选 Agent：authorized-worker" in rendered
    assert "原宿主" in rendered and "/project" in rendered
    assert "/stop " not in rendered and "/new " not in rendered


def test_timeout_is_actionable_and_does_not_recommend_blind_retry():
    rendered = render_conversation_status(snapshot(active_turn_status="timed_out"))
    assert "检查原会话后再决定是否重试" in rendered
    assert "可以继续对话" not in rendered


def test_delayed_status_preserves_the_original_observation_time():
    rendered = render_conversation_status(snapshot(observed_at="2001-07-01T12:00:00Z"))
    assert "观察于 2001-" in rendered
    unknown = render_conversation_status(snapshot(observed_at="not-an-observation"))
    assert "观察时间暂不可读" in unknown and "观察于 " not in unknown
    ambiguous = render_conversation_status(snapshot(observed_at="2001-07-01T12:00:00"))
    assert "观察时间暂不可读" in ambiguous and "观察于 " not in ambiguous
