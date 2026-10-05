#!/usr/bin/env python3
"""Smoke-test the Review Packet project-agent handoff context read model."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.handoff.review_packet_context import (  # noqa: E402
    agent_member_from_item,
    agent_member_summary,
    agent_todo_texts_for_handoff,
    project_agent_replan_context,
    project_asset_source,
    project_asset_source_line,
    todo_text_from_project_asset,
)


GOAL_ID = "review-packet-handoff-context"
AGENT_ID = "codex-product-capability"


def build_project_asset_item() -> dict:
    return {
        "goal_id": GOAL_ID,
        "summary": "legacy summary should not become source authority",
        "project_asset": {
            "agent_lane_next_action": {
                "text": "[P1] Continue the active project-agent implementation lane.",
                "claimed_by": AGENT_ID,
            },
            "agent_member": {
                "agent_id": AGENT_ID,
                "role": "product-capability",
                "scope_summary": "handoff contracts",
                "worktree_policy": "clean-worktree",
                "current_claims": ["rp-context", "canary"],
                "handoff_agent": "codex-product-capability",
            },
            "agent_todos": {
                "items": [
                    {
                        "index": 0,
                        "text": "[P0] Monitor owner readiness before broad delivery.",
                        "task_class": "continuous_monitor",
                        "claimed_by": "codex-monitor",
                    },
                    {
                        "index": 1,
                        "text": "[P2] Move handoff context into the handoff bounded context.",
                        "task_class": "advancement_task",
                        "claimed_by": AGENT_ID,
                    },
                    {
                        "index": 2,
                        "text": "[P1] Watch readiness while waiting.",
                    },
                ]
            },
            "user_todos": {
                "next": "Approve the public-safe handoff packet if the owner gate changes."
            },
        },
    }


def assert_project_asset_source_contract(item: dict) -> None:
    source = project_asset_source(item)
    assert source == "project_asset", source
    assert "attention_queue.project_asset" in project_asset_source_line(source)
    assert project_asset_source({"summary": "legacy"}) == "legacy_raw_fallback"
    assert "legacy/raw fallback" in project_asset_source_line("legacy_raw_fallback")
    assert todo_text_from_project_asset(item, "user_todos") == (
        "Approve the public-safe handoff packet if the owner gate changes."
    )


def assert_agent_todo_priority_contract(item: dict) -> None:
    todos = agent_todo_texts_for_handoff(item, limit=3)
    assert todos[0] == (
        "[P1] Continue the active project-agent implementation lane. "
        f"claimed_by={AGENT_ID}"
    ), todos
    assert "Move handoff context" in todos[1], todos
    assert "claimed_by=codex-product-capability" in todos[1], todos
    assert "Monitor owner readiness" in todos[2], todos
    assert "Monitor owner readiness" not in todos[1], todos


def assert_agent_member_contract(item: dict) -> None:
    member = agent_member_from_item(item)
    assert member and member["agent_id"] == AGENT_ID, member
    summary = agent_member_summary(item)
    assert summary is not None, item
    assert "authority=advisory_projection" in summary, summary
    assert "worktree_policy=clean-worktree" in summary, summary
    assert "claims=rp-context,canary" in summary, summary

    context = project_agent_replan_context(
        GOAL_ID, item, {"run_history": {"goals": [{"id": GOAL_ID, "latest_runs": []}]}},
    )
    assert context["schema_version"] == "replan_context_v0", context
    assert context["goal_id"] == GOAL_ID, context
    assert context["agent_id"] == AGENT_ID, context
    assert context["evidence"] == [], context


def main() -> None:
    item = build_project_asset_item()
    assert_project_asset_source_contract(item)
    assert_agent_todo_priority_contract(item)
    assert_agent_member_contract(item)


if __name__ == "__main__":
    main()
