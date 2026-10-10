from __future__ import annotations

from copy import deepcopy

from loopx.presentation.renderers.quota_markdown import (
    render_quota_markdown as render_quota_markdown_direct,
    render_quota_scheduler_ack_markdown as render_quota_scheduler_ack_markdown_direct,
    render_quota_should_run_markdown as render_quota_should_run_markdown_direct,
)
from loopx.quota import (
    render_quota_markdown,
    render_quota_scheduler_ack_markdown,
    render_quota_should_run_markdown,
)


def test_public_quota_renderers_are_presentation_exports() -> None:
    assert render_quota_markdown is render_quota_markdown_direct
    assert render_quota_should_run_markdown is render_quota_should_run_markdown_direct
    assert render_quota_scheduler_ack_markdown is render_quota_scheduler_ack_markdown_direct


def test_cli_renders_every_shared_requirement_read_and_preserves_historical_fallback() -> None:
    reads = [{"command": f"cat -- '/tmp/goal requirements/source {index}.md'"} for index in range(6)]
    payload = {"required_reads": [{"command": "obsolete-fallback"}],
        "interaction_contract": {"agent_channel": {"required_reads": reads}}}
    original = deepcopy(payload)
    rendered = render_quota_should_run_markdown(payload)
    assert all(read["command"] in rendered for read in reads)
    assert "obsolete-fallback" not in rendered
    assert payload == original
    payload["interaction_contract"]["agent_channel"]["required_reads"] = []
    assert "obsolete-fallback" not in render_quota_should_run_markdown(payload)
    del payload["interaction_contract"]["agent_channel"]["required_reads"]
    assert "obsolete-fallback" in render_quota_should_run_markdown(payload)


def test_quota_rendering_preserves_the_decision_payload() -> None:
    payload = {
        "ok": True,
        "goal_id": "presentation-boundary",
        "decision": "run",
        "should_run": True,
        "interaction_contract": {
            "mode": "bounded_delivery",
            "agent_channel": {"must_attempt": True},
        },
        "quota": {"compute": "medium", "slot_minutes": 15},
    }
    original = deepcopy(payload)

    markdown = render_quota_should_run_markdown(payload)

    assert "# LoopX Quota Should Run" in markdown
    assert "- decision: `run`" in markdown
    assert payload == original


def test_quota_rendering_reuses_the_fallback_projection_contract() -> None:
    payload = {
        "blocked_priority_fallback": {
            "notify_user": False,
            "reason": "higher priority work is blocked",
            "blocked_items": [
                {"index": 1, "text": "repair primary"},
                {"index": 2, "text": "repair secondary"},
            ],
            "selected_executable": {"index": 3, "text": "run safe fallback"},
            "recommended_action": "continue safe work",
        },
        "scoped_user_gate_fallback": {
            "notify_user": True,
            "reason": "another agent owns the gate",
            "blocked_user_gate": {"text": "approve another lane"},
            "blocked_agent_items": [{"index": 4, "text": "wait for approval"}],
            "selected_executable": {"index": 5, "text": "run independent work"},
            "recommended_action": "continue independent work",
        },
    }

    markdown = render_quota_should_run_markdown(payload)

    expected_lines = [
        "- blocked_priority_fallback: notify_user=False blocked_count=2 "
        "selected_index=3 reason=higher priority work is blocked",
        "- blocked_priority_item[1]: repair primary",
        "- blocked_priority_item[2]: repair secondary",
        "- blocked_priority_selected: run safe fallback",
        "- blocked_priority_action: continue safe work",
        "- scoped_user_gate_fallback: notify_user=True blocked_count=1 "
        "selected_index=5 reason=another agent owns the gate",
        "- scoped_user_gate: approve another lane",
        "- scoped_user_gate_blocked_item[4]: wait for approval",
        "- scoped_user_gate_selected: run independent work",
        "- scoped_user_gate_action: continue independent work",
    ]
    assert all(line in markdown for line in expected_lines)
