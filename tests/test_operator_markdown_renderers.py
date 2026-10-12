from __future__ import annotations

from loopx.presentation.renderers.trajectory_hygiene_markdown import (
    render_trajectory_hygiene_markdown,
)
from loopx.presentation.renderers.turn_envelope_markdown import (
    render_turn_envelope_markdown,
)


def test_turn_envelope_markdown_keeps_hot_path_contract() -> None:
    markdown = render_turn_envelope_markdown(
        {
            "ok": True,
            "goal_id": "loopx-meta",
            "agent_id": "codex-product-capability",
            "decision": "run",
            "should_run": True,
            "effective_action": "normal_run",
            "action": {"primary_action": "todo_fixture: advance quality"},
            "user": {"action_required": False},
            "writeback": {"spend_policy": "spend_after_validation"},
            "scheduler": {"action": "run_now"},
            "compaction": {"envelope_json_bytes": 3902, "within_budget": True},
        }
    )

    assert "# LoopX Turn Envelope" in markdown
    assert "- action: todo_fixture: advance quality" in markdown
    assert "- user_action_required: `False`" in markdown
    assert "- spend_policy: spend_after_validation" in markdown
    assert "- envelope_bytes: `3902`" in markdown
    assert "- within_budget: `True`" in markdown


def test_turn_envelope_markdown_compacts_reference_only_work_context() -> None:
    markdown = render_turn_envelope_markdown(
        {
            "ok": True,
            "work_context": {
                "complete": True,
                "selected_todo_ref": "selected_todo",
                "selected_todo_authority": "markdown_active_state@sha256:fixture",
                "instruction": "Read sources before work.",
            },
        }
    )

    assert (
        "- work_context: complete=True selected_todo_ref=selected_todo selected_todo_authority=markdown_active_state@sha256:fixture"
        in markdown
    )
    assert "- work_context_instruction: Read sources before work." in markdown
    assert "## Current work context" not in markdown


def test_turn_envelope_markdown_tolerates_malformed_nested_values() -> None:
    markdown = render_turn_envelope_markdown(
        {
            "ok": True,
            "action": None,
            "user": "not-a-mapping",
            "writeback": [],
            "scheduler": 1,
            "compaction": False,
        }
    )

    assert "- action: " in markdown
    assert "- user_action_required: `None`" in markdown
    assert "- scheduler: `None`" in markdown
    assert "- within_budget: `None`" in markdown


def test_trajectory_hygiene_markdown_renders_boundary_metrics_and_channels() -> None:
    markdown = render_trajectory_hygiene_markdown(
        {
            "ok": True,
            "schema_version": "trajectory_hygiene_summary_v0",
            "goal_filter": "fixture",
            "sample": {
                "compact_history_row_count": 5,
                "source": "public_safe_compact_run_index",
            },
            "metrics": {"controller_event_ratio": 0.4},
            "training_boundary": {
                "seed_model_training_eligible": False,
                "reason": "compact history is audit evidence only",
            },
            "channel_counts": {"controller": 2, "outcome": 3},
        }
    )

    assert "# LoopX Trajectory Hygiene" in markdown
    assert "- compact_history_rows: `5`" in markdown
    assert "- seed_model_training_eligible: `False`" in markdown
    assert "| `controller_event_ratio` | `0.4` |" in markdown
    assert "- `controller`: `2`" in markdown
    assert "- `outcome`: `3`" in markdown


def test_trajectory_hygiene_markdown_fails_closed() -> None:
    markdown = render_trajectory_hygiene_markdown(
        {"ok": False, "error": "compact index unavailable"}
    )

    assert markdown == (
        "# LoopX Trajectory Hygiene\n\n"
        "- ok: `False`\n"
        "- error: compact index unavailable"
    )


def test_trajectory_hygiene_markdown_tolerates_malformed_nested_values() -> None:
    markdown = render_trajectory_hygiene_markdown(
        {
            "ok": True,
            "sample": None,
            "metrics": "not-a-mapping",
            "training_boundary": [],
            "channel_counts": 1,
        }
    )

    assert "- compact_history_rows: `None`" in markdown
    assert "| metric | value |" in markdown
    assert markdown.endswith("## Channels")
