#!/usr/bin/env python3
"""Smoke-test the shared user-gate todo read model."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.todos.quota_summary import (  # noqa: E402
    summarize_user_todos_for_quota,
)
from loopx.control_plane.todos.user_gate import (  # noqa: E402
    build_gate_prompt,
    has_open_user_gate_todo,
    is_user_gate_todo_item,
    open_todo_count,
    open_user_gate_todo_items,
    should_notify_user_on_open_todo,
    user_gate_todo_notify_reason,
)


AGENT_IDENTITY = {"agent_id": "codex-product-capability"}


def raw_todos() -> dict:
    return {
        "schema_version": "todo_summary_v0",
        "open_count": 6,
        "items": [
            {
                "index": 1,
                "todo_id": "todo_gate_current",
                "text": "Approve product capability runtime write.",
                "task_class": "user_gate",
                "action_kind": "approval",
                "blocks_agent": "codex-product-capability",
            },
            {
                "index": 2,
                "todo_id": "todo_gate_other",
                "text": "Approve main-control production rollout.",
                "task_class": "user_gate",
                "action_kind": "production_rollout",
                "blocks_agent": "codex-main-control",
            },
            {
                "index": 3,
                "todo_id": "todo_next",
                "text": "Continue non-benchmark control-plane refactor.",
                "task_class": "user_action",
                "bound_agent": "codex-product-capability",
            },
            {
                "index": 4,
                "todo_id": "todo_action_other",
                "text": "Review main-control follow-up notes.",
                "task_class": "user_action",
                "bound_agent": "codex-main-control",
            },
            {
                "index": 5,
                "todo_id": "todo_action_goal",
                "text": "Review the goal-wide follow-up note.",
                "task_class": "user_action",
                "goal_bound": True,
            },
            {
                "index": 6,
                "todo_id": "todo_action_legacy_other",
                "text": "Review a legacy main-control follow-up note.",
                "task_class": "user_action",
                "claimed_by": "codex-main-control",
            },
        ],
    }


def assert_shared_gate_detection() -> None:
    assert is_user_gate_todo_item(
        {
            "action_kind": "public_claim_review",
            "text": "Review the public claim.",
        }
    )
    assert not is_user_gate_todo_item(
        {
            "task_class": "advancement_task",
            "action_kind": "public_claim_review",
            "text": "Review the public claim.",
        }
    )
    assert is_user_gate_todo_item(
        {
            "task_class": "user_gate",
            "action_kind": "public_claim_review",
            "text": "Review the public claim.",
        }
    )
    assert not is_user_gate_todo_item(
        {
            "task_class": "user_action",
            "action_kind": "approval",
            "text": "Non-blocking user follow-up.",
        }
    )
    summary = summarize_user_todos_for_quota(
        raw_todos(),
        agent_identity=AGENT_IDENTITY,
        filter_user_gate_blocks_agent=True,
    )
    assert summary is not None
    assert summary["open_count"] == 3, summary
    assert summary["all_open_count"] == 6, summary
    assert summary["other_agent_scoped_open_count"] == 1, summary
    assert summary["user_action_open_count"] == 2, summary
    assert summary["other_agent_bound_user_action_open_count"] == 2, summary
    assert [
        item["todo_id"]
        for item in summary["other_agent_bound_user_action_items"]
    ] == ["todo_action_legacy_other", "todo_action_other"], summary

    with_duplicate = {
        "open_count": "2",
        "gate_open_items": [summary["gate_open_items"][0]],
        "first_open_items": [
            summary["gate_open_items"][0],
            summary["user_action_items"][0],
        ],
    }
    gate_items = open_user_gate_todo_items(with_duplicate)
    assert [item["todo_id"] for item in gate_items] == ["todo_gate_current"], gate_items
    assert open_todo_count(with_duplicate) == 2
    assert has_open_user_gate_todo(with_duplicate)
    assert user_gate_todo_notify_reason(with_duplicate) == (
        "open user_gate todo requires owner decision before approval"
    )


def assert_notification_policy() -> None:
    summary = {"open_count": 1, "first_open_items": [{"text": "Need owner decision"}]}
    assert should_notify_user_on_open_todo(
        state="waiting",
        waiting_on="codex",
        user_todo_summary=summary,
    )
    assert should_notify_user_on_open_todo(
        state="eligible",
        waiting_on="external_evidence",
        user_todo_summary=summary,
    )
    assert not should_notify_user_on_open_todo(
        state="operator_gate",
        waiting_on="controller",
        user_todo_summary=summary,
    )
    assert not should_notify_user_on_open_todo(
        state="eligible",
        waiting_on="codex",
        user_todo_summary={"open_count": 0},
    )


def assert_operator_gate_prompt() -> None:
    prompt = build_gate_prompt(
        {
            "operator_question": "Can the side agent self-merge this refactor?",
            "recommended_action": "Approve after premerge canary.",
            "next_handoff_condition": "PR merged and successor todo linked.",
            "missing_gates": ["owner_approval"],
        },
        user_todo_summary={
            "open_count": 1,
            "first_open_items": [
                {
                    "index": 1,
                    "todo_id": "todo_gate_current",
                    "text": "Approve the scoped refactor.",
                    "task_class": "user_gate",
                }
            ],
        },
    )
    assert prompt is not None
    assert "Can the side agent self-merge this refactor?" in prompt, prompt
    assert "owner_approval" in prompt, prompt
    assert "Approve the scoped refactor." in prompt, prompt

    other_agent_prompt = build_gate_prompt(
        {},
        user_todo_summary={
            "open_count": 0,
            "other_agent_scoped_open_count": 1,
            "all_open_count": 2,
            "other_agent_scoped_items": [
                {
                    "index": 7,
                    "todo_id": "todo_other",
                    "text": "Approve another agent boundary.",
                }
            ],
        },
    )
    assert other_agent_prompt is not None
    assert "其他 agent/global 用户待办" in other_agent_prompt, other_agent_prompt


def main() -> None:
    assert_shared_gate_detection()
    assert_notification_policy()
    assert_operator_gate_prompt()
    print("todo user-gate read model smoke passed")


if __name__ == "__main__":
    main()
