from __future__ import annotations

from hashlib import sha256

from loopx.control_plane.quota.selected_todo_projection import (
    selected_todo_projection,
)
from loopx.control_plane.todos.summary_item import todo_text_content_revision


def test_selected_todo_preserves_capability_binding_ref() -> None:
    selected = selected_todo_projection(
        agent_lane_next_action={
            "source": "agent_lane_next_action",
            "todo_id": "todo_binding001",
            "text": "Advance the admitted issue fix.",
            "task_class": "advancement_task",
            "action_kind": "issue_fix_branch_validation",
            "target_key": "issue-fix:owner/repo:issue_42",
            "capability_binding_ref": "issue-fix:feasibility-a1b2c3d4",
        },
        work_lane_contract=None,
    )

    assert selected is not None
    assert selected["capability_binding_ref"] == ("issue-fix:feasibility-a1b2c3d4")


def test_selected_todo_preserves_task_domain() -> None:
    selected = selected_todo_projection(
        agent_lane_next_action={
            "source": "agent_lane_next_action",
            "todo_id": "todo_domain001",
            "task_domain": "validation",
            "text": "Validate the adaptive orchestration contract.",
        },
        work_lane_contract=None,
    )

    assert selected is not None
    assert selected["task_domain"] == "validation"


def test_selected_todo_preserves_current_continuation_hint() -> None:
    selected = selected_todo_projection(
        agent_lane_next_action={
            "source": "agent_lane_next_action",
            "todo_id": "todo_resume001",
            "text": "Advance the current runtime integration.",
            "task_class": "advancement_task",
            "status": "open",
            "continuation_hint": (
                "The implementation is already review-ready; next run the restart canary."
            ),
        },
        work_lane_contract=None,
    )

    assert selected is not None
    assert selected["continuation_hint"] == (
        "The implementation is already review-ready; next run the restart canary."
    )


def test_selected_todo_screens_raw_continuation_hint() -> None:
    selected = selected_todo_projection(
        agent_lane_next_action={
            "source": "agent_lane_next_action",
            "todo_id": "todo_resume_secret",
            "text": "Advance the current runtime integration.",
            "task_class": "advancement_task",
            "status": "open",
            "continuation_hint": ("tok" + "en")
            + "=must-not-project; next run the canary.",
        },
        work_lane_contract=None,
    )

    assert selected is not None
    assert "continuation_hint" not in selected


def test_selected_deferred_resume_adds_missing_content_revision() -> None:
    source_text = "Inspect the ready successor and verify its contract."
    selected = selected_todo_projection(
        agent_lane_next_action=None,
        work_lane_contract=None,
        agent_scope_frontier={
            "action": "successor_replan_required",
            "deferred_resume_candidates": [
                {
                    "todo_id": "todo_resume001",
                    "status": "deferred",
                    "text": source_text,
                }
            ],
        },
    )

    assert selected is not None
    assert selected["content_revision"] == todo_text_content_revision(source_text)

def test_selected_todo_snapshot_hash_covers_unbounded_source_body() -> None:
    full_text = "Preserve the complete acceptance details. " * 12
    action = {
        "todo_id": "todo_long_body",
        "text": full_text,
        "_context_text_sha256": sha256(full_text.encode("utf-8")).hexdigest(),
    }
    selected = selected_todo_projection(
        agent_lane_next_action=action,
        work_lane_contract=None,
    )

    assert selected is not None
    assert selected["text"] != full_text
    assert (
        selected["_context_text_sha256"]
        == sha256(full_text.encode("utf-8")).hexdigest()
    )
