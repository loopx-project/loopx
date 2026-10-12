#!/usr/bin/env python3
"""Smoke-test the quota-facing todo summary read model."""

from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.todos.quota_summary import (  # noqa: E402
    compact_quota_todo_summary_for_payload,
    select_quota_todo_summary,
    summarize_project_asset_todos_for_quota,
    summarize_user_todos_for_quota,
)
from loopx.control_plane.todos.user_gate import is_user_gate_todo_item  # noqa: E402


AGENT_IDENTITY = {"agent_id": "codex-product-capability"}


def canonical_todo_summary() -> dict:
    return {
        "schema_version": "todo_summary_v0",
        "source_section": "agent todo",
        "total_count": 6,
        "open_count": 5,
        "done_count": 1,
        "monitor_writeback": {"supported": True, "source": "active_state"},
        "items": [
            {
                "index": 1,
                "todo_id": "todo_gate_current",
                "text": "Owner approval before product capability write",
                "task_class": "user_gate",
                "action_kind": "approval",
                "blocks_agent": "codex-product-capability",
            },
            {
                "index": 2,
                "todo_id": "todo_gate_other",
                "text": "Owner approval before main-control write",
                "task_class": "user_gate",
                "action_kind": "approval",
                "blocks_agent": "codex-main-control",
            },
            {
                "index": 3,
                "todo_id": "todo_monitor_due",
                "text": "Monitor scheduler transition",
                "task_class": "continuous_monitor",
                "claimed_by": "codex-product-capability",
                "next_due_at": "2000-01-01T00:00:00+00:00",
                "target_key": "scheduler_transition",
            },
            {
                "index": 4,
                "todo_id": "todo_monitor_gap",
                "text": "Monitor missing schedule metadata",
                "task_class": "continuous_monitor",
                "claimed_by": "codex-product-capability",
                "target_key": "schedule_gap",
            },
            {
                "index": 5,
                "todo_id": "todo_refactor_next",
                "text": "[P1] Continue quota/todo state-machine refactor",
                "task_class": "advancement_task",
                "claimed_by": "codex-product-capability",
            },
        ],
        "active_next_action_items": [
            {
                "index": 5,
                "todo_id": "todo_refactor_next",
                "text": "[P1] Continue quota/todo state-machine refactor",
                "task_class": "advancement_task",
                "claimed_by": "codex-product-capability",
            },
            {
                "index": 6,
                "todo_id": "todo_other_agent_next",
                "text": "[P1] Other agent active-next item",
                "task_class": "advancement_task",
                "claimed_by": "codex-main-control",
            },
        ],
        "todo_succession_warning": {
            "schema_version": "todo_succession_warning_v0",
            "reason_code": "completed_advancement_without_successor",
            "count": 1,
            "items": [
                {
                    "index": 7,
                    "todo_id": "todo_done_without_successor",
                    "text": "[P1] Finished state-machine canary without successor",
                    "status": "done",
                    "done": True,
                    "task_class": "advancement_task",
                    "action_kind": "state_machine_canary_refactor",
                    "claimed_by": "codex-product-capability",
                }
            ],
        },
    }


def assert_agent_scoped_user_gate_and_monitor_state() -> None:
    summary = summarize_user_todos_for_quota(
        canonical_todo_summary(),
        agent_identity=AGENT_IDENTITY,
        filter_user_gate_blocks_agent=True,
    )
    assert summary is not None
    assert summary["open_count"] == 4, summary
    assert summary["all_open_count"] == 5, summary
    assert summary["user_action_open_count"] == 3, summary
    assert summary["other_agent_scoped_open_count"] == 1, summary
    assert summary["gate_open_items"][0]["todo_id"] == "todo_gate_current", summary
    assert {
        item.get("todo_id")
        for item in summary["other_agent_scoped_items"]
    } == {"todo_gate_other"}, summary
    assert {
        item.get("todo_id")
        for item in summary["user_action_items"]
    } == {
        "todo_monitor_due",
        "todo_monitor_gap",
        "todo_refactor_next",
    }, summary
    assert summary["other_agent_bound_user_action_open_count"] == 1, summary
    assert {
        item.get("todo_id")
        for item in summary["other_agent_bound_user_action_items"]
    } == {"todo_other_agent_next"}, summary

    assert summary["monitor_due_count"] == 0, summary
    assert summary["monitor_due_items"] == [], summary
    assert summary["monitor_schedule_gap_count"] == 0, summary
    assert summary["monitor_schedule_gap_items"] == [], summary
    assert summary["active_next_action_items"][0]["todo_id"] == "todo_refactor_next", summary
    assert all(
        item.get("todo_id") != "todo_other_agent_next"
        for item in summary["active_next_action_items"]
    ), summary
    assert summary["todo_succession_warning"]["count"] == 1, summary


def assert_project_asset_fallback_and_canonical_precedence() -> None:
    raw_canonical = {
        "source_section": "raw legacy queue",
        "items": [
            {
                "index": 1,
                "text": "raw fallback should not outrank project asset",
            }
        ],
    }
    project_asset = {
        "next": "[P1] Project asset selected todo",
        "next_index": 9,
        "next_claimed_by": "codex-product-capability",
    }
    selected = select_quota_todo_summary(
        raw_canonical,
        project_asset,
        agent_identity=AGENT_IDENTITY,
    )
    assert selected is not None
    assert selected["source_section"] == "project_asset", selected
    assert selected["first_open_items"][0]["index"] == 9, selected
    assert "Project asset selected todo" in selected["first_open_items"][0]["text"], selected

    canonical = canonical_todo_summary()
    selected = select_quota_todo_summary(
        canonical,
        {"next": "[P1] Project asset should lose"},
        agent_identity=AGENT_IDENTITY,
    )
    assert selected is not None
    assert selected["source_section"] == "agent todo", selected
    assert any(
        item.get("todo_id") == "todo_gate_current"
        for item in selected["gate_open_items"]
    ), selected
    assert selected["first_executable_items"][0]["todo_id"] == "todo_refactor_next", selected


def assert_project_asset_summary_reuses_canonical_shape() -> None:
    canonical_shape = canonical_todo_summary()
    summary = summarize_project_asset_todos_for_quota(
        canonical_shape,
        agent_identity=AGENT_IDENTITY,
        filter_user_gate_blocks_agent=True,
    )
    assert summary is not None
    assert summary["source_section"] == "agent todo", summary
    assert summary["monitor_due_count"] == 0, summary
    assert summary["other_agent_scoped_open_count"] == 1, summary


def assert_agent_due_monitor_survives_claimed_lane_compaction() -> None:
    other_agent_monitors = [
        {
            "index": index,
            "todo_id": f"todo_other_monitor_{index:02d}",
            "text": f"[P2] Observe other agent monitor {index}.",
            "status": "open",
            "task_class": "continuous_monitor",
            "claimed_by": "codex-main-control",
            "target_key": f"other-monitor-{index}",
            "next_due_at": "2999-01-01T00:00:00+00:00",
        }
        for index in range(16)
    ]
    side_agent_due = {
        "index": 17,
        "todo_id": "todo_side_monitor_due_after_compaction",
        "text": "[P1] Observe the due side-agent monitor.",
        "status": "open",
        "task_class": "continuous_monitor",
        "claimed_by": "codex-side-bypass",
        "target_key": "side-monitor-due-after-compaction",
        "next_due_at": "2000-01-01T00:00:00+00:00",
    }
    canonical = {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "total_count": 17,
        "open_count": 17,
        "done_count": 0,
        "monitor_writeback": {"supported": True, "source": "active_state"},
        "claimed_monitor_open_items": other_agent_monitors,
        "monitor_open_items": [*other_agent_monitors, side_agent_due],
    }

    selected = select_quota_todo_summary(
        canonical,
        None,
        agent_identity={"agent_id": "codex-side-bypass"},
    )
    assert selected is not None
    assert selected["open_count"] == 1, selected
    assert selected["monitor_due_count"] == 1, selected
    assert (
        selected["monitor_due_items"][0]["todo_id"] == side_agent_due["todo_id"]
    ), selected


def assert_legacy_and_canonical_shared_lanes_stay_in_parity() -> None:
    items = [
        {
            "index": 1,
            "todo_id": "todo_shared_monitor",
            "text": "[P1] Observe the shared due monitor.",
            "status": "open",
            "task_class": "continuous_monitor",
            "claimed_by": "codex-product-capability",
            "target_key": "shared-monitor",
            "next_due_at": "2000-01-01T00:00:00+00:00",
        },
        {
            "index": 2,
            "todo_id": "todo_shared_advancement",
            "text": "[P1] Continue the shared advancement lane.",
            "status": "open",
            "task_class": "advancement_task",
            "claimed_by": "codex-product-capability",
        },
    ]
    active_next = [items[1]]
    monitor_writeback = {"supported": True, "source": "active_state"}
    canonical = summarize_user_todos_for_quota(
        {
            "schema_version": "todo_summary_v0",
            "source_section": "Agent Todo",
            "total_count": 2,
            "open_count": 2,
            "done_count": 0,
            "items": items,
            "active_next_action_items": active_next,
            "monitor_writeback": monitor_writeback,
        },
        agent_identity=AGENT_IDENTITY,
    )
    legacy = summarize_project_asset_todos_for_quota(
        {
            "source_section": "project_asset",
            "open": 2,
            "done": 0,
            "first_open_items": items,
            "active_next_action_items": active_next,
            "monitor_writeback": monitor_writeback,
        },
        agent_identity=AGENT_IDENTITY,
    )
    assert canonical is not None and legacy is not None
    shared_keys = {
        "open_count",
        "first_open_items",
        "first_executable_items",
        "monitor_open_items",
        "monitor_due_count",
        "monitor_due_items",
        "active_next_action_items",
        "active_next_action_executable_items",
        "backlog_items",
        "executable_backlog_items",
        "claimed_open_count",
        "unclaimed_open_count",
        "claimed_open_items",
        "claimed_advancement_open_items",
        "claimed_monitor_open_items",
        "current_agent_claimed_open_items",
        "current_agent_claimed_advancement_items",
        "current_agent_claimed_monitor_items",
    }
    for key in shared_keys:
        assert canonical.get(key) == legacy.get(key), (key, canonical, legacy)


def assert_user_gate_hint_detection_is_preserved() -> None:
    assert is_user_gate_todo_item(
        {
            "text": "Credential decision",
            "action_kind": "credential_rotation",
        }
    )
    assert not is_user_gate_todo_item(
        {
            "text": "Credential decision",
            "task_class": "advancement_task",
            "action_kind": "credential_rotation",
        }
    )
    assert is_user_gate_todo_item(
        {
            "text": "Public claim decision",
            "action_kind": "public_claim_review",
        }
    )


def assert_quota_payload_summary_compacts_hot_path_lanes() -> None:
    long_text = "[P2] " + "inspect projection consistency " * 30
    source = {
        "schema_version": "todo_summary_v0",
        "source_section": "agent todo",
        "open_count": 12,
        "claimed_open_count": 12,
        "claimed_monitor_open_count": 12,
        "claimed_open_items": [
            {
                "index": index,
                "todo_id": f"todo_payload_{index:03d}",
                "text": long_text,
                "title": long_text,
                "status": "open",
                "task_class": "continuous_monitor",
                "claimed_by": "codex-main-control",
                "handoff_note": {
                    "schema_version": "handoff_note_v0",
                    "body": "cold path detail " * 200,
                },
            }
            for index in range(12)
        ],
        "user_action_open_count": 4,
        "user_action_items": [
            {
                "index": index,
                "todo_id": f"todo_user_action_{index:03d}",
                "text": f"Review user action {index}",
                "status": "open",
                "task_class": "user_action",
            }
            for index in range(4)
        ],
        "claim_scope": {
            "schema_version": "agent_claim_scope_v0",
            "agent_id": "codex-product-capability",
            "other_agent_claimed_open_count": 12,
            "other_agent_claimed_items": [
                {
                    "index": index,
                    "todo_id": f"todo_other_{index:03d}",
                    "text": long_text,
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                }
                for index in range(12)
            ],
        },
    }
    compact = compact_quota_todo_summary_for_payload(source)
    assert compact["open_count"] == 12, compact
    assert compact["claimed_open_count"] == 12, compact
    assert len(compact["claimed_open_items"]) == 2, compact
    assert len(compact["user_action_items"]) == 3, compact
    assert len(compact["claim_scope"]["other_agent_claimed_items"]) == 2, compact
    first = compact["claimed_open_items"][0]
    assert first["todo_id"] == "todo_payload_000", compact
    assert first["text"].endswith("..."), first
    assert len(first["text"]) <= 180, first
    assert first["title"].endswith("..."), first
    assert len(first["title"]) <= 180, first
    assert "handoff_note" not in first, first
    compaction = compact["payload_compaction"]
    assert compaction["schema_version"] == "quota_todo_summary_payload_compaction_v0", compaction
    assert compaction["compacted_lanes"]["claimed_open_items"] == {
        "shown": 2,
        "total": 12,
    }, compaction
    assert compaction["compacted_lanes"]["user_action_items"] == {
        "shown": 3,
        "total": 4,
    }, compaction
    assert len(json.dumps(compact, ensure_ascii=False, sort_keys=True)) < 5000, compact


def main() -> None:
    assert_agent_scoped_user_gate_and_monitor_state()
    assert_project_asset_fallback_and_canonical_precedence()
    assert_project_asset_summary_reuses_canonical_shape()
    assert_agent_due_monitor_survives_claimed_lane_compaction()
    assert_legacy_and_canonical_shared_lanes_stay_in_parity()
    assert_user_gate_hint_detection_is_preserved()
    assert_quota_payload_summary_compacts_hot_path_lanes()
    print("quota todo summary read model smoke passed")


if __name__ == "__main__":
    main()
