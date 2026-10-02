"""Existing notification transport tests inject synthesis, never call a live model."""
import json
import pytest


@pytest.fixture(autouse=True)
def notification_transport_synthesis(request, monkeypatch):
    if request.path.name not in {
        "test_lark_goal_channel.py", "test_lark_goal_channel_lifecycle.py",
        "test_lark_goal_channel_blocked_notice.py",
        "test_lark_goal_channel_blocked_notice_receipts.py",
    }:
        return
    from loopx.extensions.lark import goal_channel_runtime, goal_channel_blocked_notice
    from loopx.capabilities.manager_context.goal_notice import validated_notice

    def render(**kwargs):
        facts = kwargs["facts"]
        # A content-carrying test double qualifies transport, not model judgment.
        return validated_notice(kwargs.get("cached_text") or json.dumps(facts, ensure_ascii=False), facts)

    monkeypatch.setattr(goal_channel_runtime, "render_channel_notice", render)
    monkeypatch.setattr(goal_channel_blocked_notice, "render_channel_notice", render)
