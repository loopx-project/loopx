from __future__ import annotations

import json

import pytest

from loopx.control_plane.quota.cli_projection import (
    QUOTA_CLI_MONITOR_POLL_DECISION_DETAIL_COMMAND,
    compact_quota_monitor_poll_cli_payload,
)

MONITOR_POLL_PROJECTION_BUDGET = 6_000


def _decision(*, effective_action: str, should_run: bool) -> dict[str, object]:
    return {
        "should_run": should_run,
        "normal_delivery_allowed": should_run,
        "recovery_delivery_allowed": False,
        "effective_action": effective_action,
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "state": "active",
        "safe_bypass_allowed": False,
        "safe_bypass_kind": None,
        "blocked_action_scope": None,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 30,
            "spent_slots": 2,
            "allowed_slots": 48,
        },
        "selected_todo": {
            "schema_version": "quota_selected_todo_v0",
            "source": "capability_gate.runnable_candidates",
            "todo_id": "todo_monitor",
            "index": 7,
            "role": "agent",
            "priority": "P1",
            "status": "open",
            "task_class": "continuous_monitor",
            "action_kind": "pr_review_monitor",
            "task_repository": "git:github.com/example/loopx",
            "claimed_by": "agent-a",
            "unblocks_todo_id": "todo_parent",
            "content_revision": "sha256:" + "a" * 64,
            "agent_id": "agent-a",
            "selected_by": "current_agent_claimed_todo",
            "text": "Monitor a verbose external target without copying this text.",
        },
        "work_lane_contract": {
            "obligation": "attempt_due_monitor",
            "diagnostic": "x" * 60_000,
        },
        "interaction_contract": {
            "schema_version": "interaction_contract_v0",
            "mode": "NOTIFY",
            "user_channel": {
                "action_required": False,
                "notify": "NOTIFY",
                "max_items": 3,
                "actions": ["Review the updated public contract."],
                "non_blocking": True,
                "reason": "This explanation remains on the cold path.",
                "diagnostic": "u" * 60_000,
            },
            "agent_channel": {
                "must_attempt": should_run,
                "delivery_allowed": should_run,
                "quiet_noop_allowed": not should_run,
                "primary_action": "Continue." if should_run else "Wait.",
                "resolution_trace": {"summary": "source=agent_lane"},
                "diagnostic": "a" * 60_000,
            },
            "cli_channel": {
                "spend_allowed_now": False,
                "spend_after_validation": should_run,
                "spend_policy": "spend once" if should_run else "do not spend",
                "delivery_workspace_causality": {
                    "schema_version": "delivery_workspace_causality_v0",
                    "refresh": "delivery_workspace",
                    "spend": "recorded_delivery_workspace",
                    "mismatch": "fail_closed",
                },
                "next_cli_actions": ["verbose cold-path command"],
                "diagnostic": "c" * 60_000,
            },
            "response_plan": {
                "schema_version": "interaction_response_plan_v0",
                "kind": "surface_user_gate",
                "decision": "ask_user",
                "action_sequence": ["notify", "wait"],
                "silent_wait_allowed": False,
                "diagnostic": "r" * 60_000,
            },
            "diagnostic": "y" * 60_000,
        },
    }


@pytest.mark.parametrize(
    ("dry_run", "replayed", "ok", "has_after"),
    [
        (True, False, True, True),
        (False, False, True, True),
        (False, True, True, True),
        (True, False, False, False),
    ],
    ids=("dry-run", "execute", "replay", "failure"),
)
def test_monitor_poll_default_projection_is_bounded_and_semantically_aligned(
    dry_run: bool,
    replayed: bool,
    ok: bool,
    has_after: bool,
) -> None:
    before = _decision(effective_action="monitor_quiet_skip", should_run=False)
    after = (
        _decision(effective_action="autonomous_replan_required", should_run=True)
        if has_after
        else None
    )
    payload = {
        "ok": ok,
        "mode": "monitor-poll",
        "dry_run": dry_run,
        "replayed": replayed,
        "decision_summary": {"before": {}, "after": None},
        "before": before,
        "after": after,
    }

    projected = compact_quota_monitor_poll_cli_payload(payload)

    assert projected["before"] == projected["decision_summary"]["before"]
    assert projected["before"]["effective_action"] == "monitor_quiet_skip"
    assert projected["before"]["selected_todo"] == {
        "schema_version": "quota_selected_todo_v0",
        "source": "capability_gate.runnable_candidates",
        "todo_id": "todo_monitor",
        "index": 7,
        "role": "agent",
        "priority": "P1",
        "status": "open",
        "task_class": "continuous_monitor",
        "action_kind": "pr_review_monitor",
        "task_repository": "git:github.com/example/loopx",
        "claimed_by": "agent-a",
        "unblocks_todo_id": "todo_parent",
        "agent_id": "agent-a",
        "selected_by": "current_agent_claimed_todo",
    }
    assert "work_lane_contract" not in projected["before"]
    assert "interaction_contract" not in projected["before"]
    if has_after:
        assert projected["after"] != projected["decision_summary"]["after"]
        assert projected["decision_summary"]["after"] == {
            "should_run": True,
            "effective_action": "autonomous_replan_required",
            "state": "active",
        }
        assert projected["after"]["effective_action"] == "autonomous_replan_required"
        assert projected["after"]["interaction_contract"] == {
            "schema_version": "interaction_contract_v0",
            "mode": "NOTIFY",
            "user_channel": {
                "action_required": False,
                "notify": "NOTIFY",
                "max_items": 3,
                "actions": ["Review the updated public contract."],
                "non_blocking": True,
            },
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "primary_action": "Continue.",
            },
            "cli_channel": {
                "spend_allowed_now": False,
                "spend_after_validation": True,
                "spend_policy": "spend once",
                "delivery_workspace_causality": {
                    "schema_version": "delivery_workspace_causality_v0",
                    "refresh": "delivery_workspace",
                    "spend": "recorded_delivery_workspace",
                    "mismatch": "fail_closed",
                },
            },
            "response_plan": {
                "schema_version": "interaction_response_plan_v0",
                "kind": "surface_user_gate",
                "decision": "ask_user",
                "action_sequence": ["notify", "wait"],
                "silent_wait_allowed": False,
            },
        }
        compact_contract = projected["after"]["interaction_contract"]
        assert "reason" not in compact_contract["user_channel"]
        assert "resolution_trace" not in compact_contract["agent_channel"]
        assert "next_cli_actions" not in compact_contract["cli_channel"]
    else:
        assert projected["after"] is None
        assert projected["decision_summary"]["after"] is None
    detail_ref = projected["payload_compaction"]["decisions"]["detail_ref"]
    assert detail_ref["request"] == QUOTA_CLI_MONITOR_POLL_DECISION_DETAIL_COMMAND
    assert (
        len(json.dumps(projected, ensure_ascii=False, indent=2))
        < MONITOR_POLL_PROJECTION_BUDGET
    )
    assert payload["before"] is before
    assert "work_lane_contract" in payload["before"]


def test_monitor_poll_projection_keeps_nested_event_decision_aligned() -> None:
    before = _decision(effective_action="monitor_quiet_skip", should_run=False)
    monitor_event = {
        "source": "heartbeat",
        "before": {"effective_action": "stale"},
    }
    payload = {
        "ok": True,
        "mode": "monitor-poll",
        "monitor_event": monitor_event,
        "decision_summary": {"before": {}, "after": None},
        "before": before,
        "after": None,
    }

    projected = compact_quota_monitor_poll_cli_payload(payload)

    assert projected["before"] == projected["decision_summary"]["before"]
    assert projected["before"] == projected["monitor_event"]["before"]
    assert projected["monitor_event"]["source"] == "heartbeat"
    assert payload["monitor_event"] is monitor_event
    assert monitor_event["before"] == {"effective_action": "stale"}


def test_monitor_poll_explicit_cold_path_preserves_full_decisions() -> None:
    before = _decision(effective_action="monitor_quiet_skip", should_run=False)
    after = _decision(effective_action="normal_run", should_run=True)
    payload = {
        "ok": True,
        "mode": "monitor-poll",
        "decision_summary": {"before": {}, "after": {}},
        "before": before,
        "after": after,
    }

    projected = compact_quota_monitor_poll_cli_payload(
        payload,
        include_decision_detail=True,
    )

    assert projected is payload
    assert projected["before"]["work_lane_contract"]["diagnostic"] == "x" * 60_000
    assert projected["after"]["interaction_contract"]["diagnostic"] == "y" * 60_000
