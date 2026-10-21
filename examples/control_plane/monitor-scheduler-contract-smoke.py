#!/usr/bin/env python3
"""Smoke-test scheduled monitor lane routing without growing the large lane smoke."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.quota_fixtures import quota_status_payload  # noqa: E402
from loopx.control_plane.scheduler import monitor_todo as monitor_todo_module  # noqa: E402
from loopx.control_plane.scheduler import scheduler_hint as scheduler_hint_module  # noqa: E402
from loopx.control_plane.scheduler import time as scheduler_time  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.runtime import time as runtime_time  # noqa: E402
from loopx.quota import build_quota_should_run, render_quota_should_run_markdown  # noqa: E402


GOAL_ID = "monitor-scheduler-fixture"
AGENT_ID = "codex-product-capability"
PAST_DUE_AT = "2000-01-01T00:00:00+00:00"
FUTURE_DUE_AT = "2999-01-01T00:00:00+00:00"
EXPIRED_AT = "2000-01-01T00:05:00+00:00"
FROZEN_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)
FRONTIER_REPLAN_ACK_RUNS = [
    {
        "classification": "monitor_scheduler_replan_ack",
        "agent_id": AGENT_ID,
        "progress_scope": "agent_lane",
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "delta_contract": {
                "schema_version": "repair_delta_contract_v0",
                "delta_present": True,
                "delta_kinds": ["watch_lane_continuation"],
            },
        },
    }
]


def status_payload(
    *,
    agent_todo_items: list[dict],
    status: str = "monitor_scheduler_fixture",
    recommended_action: str = "Route scheduled monitor todos from structured metadata.",
    coordination: dict | None = None,
    latest_runs: list[dict] | None = None,
) -> dict:
    return quota_status_payload(
        goal_id=GOAL_ID,
        status=status,
        agent_todo_items=agent_todo_items,
        recommended_action=recommended_action,
        next_action=recommended_action,
        coordination=coordination
        or {
            "registered_agents": [AGENT_ID],
            "agent_model": "peer_v1",
        },
        latest_runs=latest_runs
        if latest_runs is not None
        else FRONTIER_REPLAN_ACK_RUNS,
    )


def monitor_item(
    *,
    index: int,
    todo_id: str,
    priority: str,
    target_key: str,
    next_due_at: str | None = None,
    cadence: str | None = "15m",
    claimed_by: str | None = AGENT_ID,
    expires_at: str | None = None,
    last_checked_at: str | None = None,
    required_capabilities: list[str] | None = None,
) -> dict:
    item = {
        "index": index,
        "text": f"[{priority}] Monitor {target_key} and write back only material transitions.",
        "todo_id": todo_id,
        "role": "agent",
        "status": "open",
        "priority": priority,
        "task_class": "continuous_monitor",
        "action_kind": "monitor",
        "target_key": target_key,
    }
    if claimed_by:
        item["claimed_by"] = claimed_by
    if cadence:
        item["cadence"] = cadence
    if next_due_at:
        item["next_due_at"] = next_due_at
    if expires_at:
        item["expires_at"] = expires_at
    if last_checked_at:
        item["last_checked_at"] = last_checked_at
    if required_capabilities:
        item["required_capabilities"] = required_capabilities
    return item


def frozen_timestamp_after(minutes: int) -> str:
    return (FROZEN_NOW + timedelta(minutes=minutes)).isoformat()


def frozen_timestamp_before(minutes: int) -> str:
    return (FROZEN_NOW - timedelta(minutes=minutes)).isoformat()


def blocking_handoff_item(*, index: int, todo_id: str = "todo_primary_handoff") -> dict:
    return {
        "index": index,
        "text": "[P1] Primary review gate that blocks the product-capability lane.",
        "todo_id": todo_id,
        "role": "agent",
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
        "action_kind": "pr_review_merge",
        "claimed_by": "codex-main-control",
        "blocks_agent": AGENT_ID,
        "unblocks_todo_id": "todo_after_handoff",
    }


def advancement_item(
    *,
    index: int,
    priority: str = "P1",
    claimed_by: str = AGENT_ID,
    required_capabilities: list[str] | None = None,
) -> dict:
    item = {
        "index": index,
        "text": f"[{priority}] Advance the runtime contract slice with validation.",
        "todo_id": f"todo_adv_{index}",
        "role": "agent",
        "status": "open",
        "priority": priority,
        "task_class": "advancement_task",
        "claimed_by": claimed_by,
    }
    if required_capabilities:
        item["required_capabilities"] = required_capabilities
    return item


def guard_for(
    items: list[dict],
    *,
    agent_id: str = AGENT_ID,
    recommended_action: str = "Route scheduled monitor todos from structured metadata.",
    coordination: dict | None = None,
    latest_runs: list[dict] | None = None,
    include_scheduler_detail: bool = False,
    now: datetime = FROZEN_NOW,
    available_capabilities: list[str] | None = None,
) -> dict:
    original_scheduler_now = scheduler_hint_module.now_utc
    original_monitor_now = monitor_todo_module.now_utc
    scheduler_hint_module.now_utc = lambda: now
    monitor_todo_module.now_utc = lambda: now
    try:
        return build_quota_should_run(
            status_payload(
                agent_todo_items=items,
                recommended_action=recommended_action,
                coordination=coordination,
                latest_runs=latest_runs,
            ),
            goal_id=GOAL_ID,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
            include_scheduler_detail=include_scheduler_detail,
            scheduler_execution_context=APP_SCHEDULER_CONTEXT,
        )
    finally:
        scheduler_hint_module.now_utc = original_scheduler_now
        monitor_todo_module.now_utc = original_monitor_now


def assert_scheduler_timestamp_parser_is_shared_and_utc_normalized() -> None:
    assert scheduler_time.parse_timestamp is runtime_time.parse_timestamp
    assert monitor_todo_module.parse_monitor_timestamp is scheduler_time.parse_scheduler_timestamp
    assert scheduler_hint_module.parse_timestamp is runtime_time.parse_timestamp
    assert scheduler_hint_module.parse_timestamp("2026-01-01T08:00:00+08:00").isoformat() == (
        "2026-01-01T00:00:00+00:00"
    )
    assert monitor_todo_module.parse_monitor_timestamp(" 2026-01-01T00:00:00Z ").isoformat() == (
        "2026-01-01T00:00:00+00:00"
    )


def assert_not_due_monitor_waits_quietly() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_wait",
                priority="P1",
                next_due_at=FUTURE_DUE_AT,
                target_key="update-note-draft-pr",
            )
        ]
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "skip", guard
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert lane["obligation"] == "quiet_until_material_monitor_transition", lane
    assert lane["must_attempt_work"] is False, lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 0, guard


def assert_not_due_monitor_scheduler_uses_due_horizon_before_cadence() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_wait_fast",
                priority="P0",
                cadence="3m",
                next_due_at=FUTURE_DUE_AT,
                target_key="auto-research-vision-monitor",
            )
        ]
    )
    scheduler = guard["scheduler_hint"]
    codex_app = scheduler["codex_app"]
    stateful = codex_app["stateful_backoff"]
    assert guard["decision"] == "skip", guard
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert scheduler["cadence_class"] == "monitor_wait", scheduler
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=15", scheduler
    assert codex_app["example_progression_minutes"] == [15, 30, 60], scheduler
    assert stateful["current_rrule"] == "FREQ=MINUTELY;INTERVAL=15", scheduler


def assert_monitor_scheduler_far_window_uses_coarse_backoff() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_far_window",
                priority="P1",
                cadence="3m",
                next_due_at=frozen_timestamp_after(90),
                expires_at=frozen_timestamp_after(150),
                target_key="far-window-watch",
            )
        ],
        include_scheduler_detail=True,
    )
    scheduler = guard["scheduler_hint"]
    codex_app = scheduler["codex_app"]
    context = scheduler["cold_path_detail"]["cadence_context"]
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert context["phase"] == "far_window", context
    assert context["cap_minutes"] == 90, context
    assert codex_app["example_progression_minutes"] == [15, 30, 60], scheduler
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=15", scheduler


def assert_monitor_scheduler_due_horizon_can_break_host_floor() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_due_within_host_floor",
                priority="P0",
                cadence=None,
                next_due_at=frozen_timestamp_after(7),
                target_key="deadline-sensitive-watch",
            )
        ],
        include_scheduler_detail=True,
    )
    scheduler = guard["scheduler_hint"]
    codex_app = scheduler["codex_app"]
    context = scheduler["cold_path_detail"]["cadence_context"]
    assert context["phase"] == "near_window", context
    assert context["host_floor_minutes"] == 7, context
    assert context["cap_minutes"] == 7, context
    assert codex_app["example_progression_minutes"] == [7], scheduler
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=7", scheduler


def assert_monitor_scheduler_near_window_caps_without_breaking_floor() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_near_window",
                priority="P1",
                cadence="3m",
                next_due_at=frozen_timestamp_after(37),
                expires_at=frozen_timestamp_after(100),
                target_key="near-window-watch",
            )
        ],
        include_scheduler_detail=True,
    )
    scheduler = guard["scheduler_hint"]
    codex_app = scheduler["codex_app"]
    context = scheduler["cold_path_detail"]["cadence_context"]
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert context["phase"] == "near_window", context
    assert context["host_floor_minutes"] == 15, context
    assert context["cap_minutes"] == 37, context
    assert codex_app["example_progression_minutes"] == [15, 30], scheduler


def assert_monitor_scheduler_near_window_reset_identity_is_stable() -> None:
    item = monitor_item(
        index=1,
        todo_id="todo_near_window_stable_identity",
        priority="P1",
        cadence="3m",
        next_due_at=frozen_timestamp_after(37),
        expires_at=frozen_timestamp_after(100),
        target_key="near-window-stable-watch",
    )
    first = guard_for([item], include_scheduler_detail=True)
    same_bucket = guard_for(
        [item],
        include_scheduler_detail=True,
        now=FROZEN_NOW + timedelta(minutes=1),
    )
    next_bucket = guard_for(
        [item],
        include_scheduler_detail=True,
        now=FROZEN_NOW + timedelta(minutes=8),
    )
    first_scheduler = first["scheduler_hint"]
    same_bucket_scheduler = same_bucket["scheduler_hint"]
    next_bucket_scheduler = next_bucket["scheduler_hint"]
    first_context = first_scheduler["cold_path_detail"]["cadence_context"]
    same_bucket_context = same_bucket_scheduler["cold_path_detail"]["cadence_context"]
    next_bucket_context = next_bucket_scheduler["cold_path_detail"]["cadence_context"]
    assert first_context["phase"] == "near_window", first_context
    assert same_bucket_context["phase"] == "near_window", same_bucket_context
    assert next_bucket_context["phase"] == "near_window", next_bucket_context
    assert first_context["cap_minutes"] == 37, first_context
    assert same_bucket_context["cap_minutes"] == 36, same_bucket_context
    assert next_bucket_context["cap_minutes"] == 29, next_bucket_context
    assert first_scheduler["codex_app"]["example_progression_minutes"] == [15, 30], first_scheduler
    assert same_bucket_scheduler["codex_app"]["example_progression_minutes"] == [15, 30], (
        same_bucket_scheduler
    )
    assert next_bucket_scheduler["codex_app"]["example_progression_minutes"] == [15], (
        next_bucket_scheduler
    )
    assert first_scheduler["reset_policy"]["reset_token"] == same_bucket_scheduler[
        "reset_policy"
    ]["reset_token"], (
        first_scheduler,
        same_bucket_scheduler,
    )
    assert first_scheduler["cold_path_detail"]["reset_policy_detail"][
        "reset_profile_signature"
    ] == same_bucket_scheduler["cold_path_detail"]["reset_policy_detail"][
        "reset_profile_signature"
    ], same_bucket_scheduler


def assert_monitor_scheduler_active_window_honors_tighter_cadence() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_active_window",
                priority="P0",
                cadence="3m",
                next_due_at=frozen_timestamp_after(3),
                expires_at=frozen_timestamp_after(40),
                last_checked_at=frozen_timestamp_before(5),
                target_key="active-window-watch",
            )
        ],
        include_scheduler_detail=True,
    )
    scheduler = guard["scheduler_hint"]
    codex_app = scheduler["codex_app"]
    context = scheduler["cold_path_detail"]["cadence_context"]
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert context["phase"] == "active_window", context
    assert context["cadence_minutes"] == 3, context
    assert context["host_floor_minutes"] == 3, context
    assert codex_app["example_progression_minutes"] == [3], scheduler
    local_scheduler = scheduler["cold_path_detail"]["local_scheduler"]
    assert local_scheduler["example_progression_minutes"] == [3], scheduler
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler


def assert_unscheduled_monitor_requires_metadata_repair() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_unscheduled",
                priority="P1",
                target_key="update-note-draft-pr",
                cadence=None,
                next_due_at=None,
            )
        ]
    )
    lane = guard["work_lane_contract"]
    summary = guard["agent_todo_summary"]
    assert guard["decision"] == "run", guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["monitor_kind"] == "todo_monitor_schedule_gap", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["selected_todo_id"] == "todo_monitor_unscheduled", lane
    assert summary["monitor_due_count"] == 0, summary
    assert summary["monitor_schedule_gap_count"] == 1, summary
    assert summary["monitor_schedule_gap_items"][0]["todo_id"] == "todo_monitor_unscheduled", summary
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, guard
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, guard
    assert guard["scheduler_hint"]["cadence_class"] == "active_work", guard


def assert_unscheduled_monitor_repair_survives_handoff_gates() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_unscheduled",
                priority="P1",
                target_key="update-note-draft-pr",
                cadence=None,
                next_due_at=None,
                claimed_by=None,
            ),
            blocking_handoff_item(index=2),
        ],
        coordination={
            "registered_agents": ["codex-main-control", AGENT_ID],
            "agent_model": "peer_v1",
        },
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["effective_action"] == "normal_run", guard
    assert "agent_scope_frontier" not in guard, guard
    frontier_hint = guard.get("agent_lane_frontier_hint", {})
    assert frontier_hint.get("decision") != "quiet_noop_blocker", guard
    assert lane["monitor_kind"] == "todo_monitor_schedule_gap", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, guard


def assert_due_monitor_requires_explicit_attempt() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_due",
                priority="P1",
                next_due_at=PAST_DUE_AT,
                target_key="update-note-draft-pr",
            )
        ]
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["monitor_kind"] == "todo_monitor_due", lane
    assert lane["obligation"] == "attempt_due_monitor", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["selected_todo_id"] == "todo_monitor_due", lane
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, guard
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, guard


def assert_due_monitor_requires_available_capabilities() -> None:
    item = monitor_item(
        index=1,
        todo_id="todo_private_read_monitor_due",
        priority="P0",
        next_due_at=PAST_DUE_AT,
        target_key="private-source-watch",
        required_capabilities=["private_read"],
    )
    blocked_guard = guard_for([item])
    blocked_summary = blocked_guard["agent_todo_summary"]
    blocked_lane = blocked_guard.get("work_lane_contract") or {}
    assert blocked_guard["decision"] == "repair_bridge", blocked_guard
    assert blocked_guard["effective_action"] == "capability_bridge_repair", blocked_guard
    assert blocked_guard["capability_gate"]["action"] == "repair_bridge", blocked_guard
    assert blocked_guard["capability_gate"]["decision_owner"] == "agent", blocked_guard
    assert blocked_guard["capability_gate"]["missing"] == ["private_read"], blocked_guard
    assert blocked_lane["reason_codes"][0] == "monitor_todo_only", blocked_lane
    assert blocked_summary["monitor_due_count"] == 0, blocked_summary
    assert blocked_summary["monitor_open_items"][0]["todo_id"] == item["todo_id"], blocked_summary
    assert blocked_summary["monitor_capability_blocked_due_count"] == 1, blocked_summary
    blocked_item = blocked_summary["monitor_capability_blocked_due_items"][0]
    assert blocked_item["todo_id"] == item["todo_id"], blocked_item
    assert blocked_item["required_capabilities"] == ["private_read"], blocked_item
    assert blocked_item["missing_capabilities"] == ["private_read"], blocked_item
    assert blocked_lane.get("selected_todo_id") != item["todo_id"], blocked_lane
    assert blocked_guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, blocked_guard
    assert blocked_guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, blocked_guard

    excluded_guard = guard_for([{**item, "excluded_agents": [AGENT_ID]}])
    excluded_summary = excluded_guard["agent_todo_summary"]
    excluded_scope = excluded_summary["claim_scope"]
    assert excluded_summary["monitor_due_count"] == 0, excluded_summary
    assert excluded_summary["monitor_capability_blocked_due_count"] == 0, excluded_summary
    assert excluded_scope["executor_excluded_self_count"] == 1, excluded_scope
    assert excluded_scope["executor_excluded_self_items"][0]["todo_id"] == item["todo_id"], excluded_scope

    runnable_guard = guard_for([item], available_capabilities=["private_read"])
    runnable_lane = runnable_guard["work_lane_contract"]
    assert runnable_guard["decision"] == "run", runnable_guard
    assert runnable_guard["effective_action"] == "normal_run", runnable_guard
    assert runnable_guard["agent_todo_summary"]["monitor_due_count"] == 1, runnable_guard
    assert runnable_guard["agent_todo_summary"]["monitor_capability_blocked_due_count"] == 0, runnable_guard
    assert runnable_lane["selected_todo_id"] == item["todo_id"], runnable_lane
    assert runnable_lane["obligation"] == "attempt_due_monitor", runnable_lane


def assert_runnable_due_monitor_survives_blocked_due_monitor() -> None:
    blocked = monitor_item(
        index=1,
        todo_id="todo_private_read_monitor_due",
        priority="P1",
        next_due_at=PAST_DUE_AT,
        target_key="private-source-watch",
        required_capabilities=["private_read"],
    )
    runnable = monitor_item(
        index=2,
        todo_id="todo_network_monitor_due",
        priority="P2",
        next_due_at=PAST_DUE_AT,
        target_key="public-network-watch",
        required_capabilities=["network"],
    )
    guard = guard_for([blocked, runnable], available_capabilities=["network"])
    summary = guard["agent_todo_summary"]
    gate = guard["capability_gate"]
    lane = guard["work_lane_contract"]

    assert guard["decision"] == "run", guard
    assert guard["effective_action"] == "normal_run", guard
    assert gate["action"] == "run", gate
    assert gate["runnable_candidates"][0]["todo_id"] == runnable["todo_id"], gate
    assert gate["blocked_candidates"][0]["todo_id"] == blocked["todo_id"], gate
    assert summary["monitor_due_count"] == 1, summary
    assert summary["monitor_capability_blocked_due_count"] == 1, summary
    assert lane["monitor_due_count"] == 1, lane
    assert lane["selected_todo_id"] == runnable["todo_id"], lane
    assert lane["monitor_due_items"][0]["todo_id"] == runnable["todo_id"], lane
    assert guard["selected_todo"]["todo_id"] == runnable["todo_id"], guard
    assert "capability_monitor_fallback" not in guard, guard
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, guard
    assert runnable["todo_id"] in guard["interaction_contract"]["agent_channel"]["primary_action"], guard


def assert_capability_blocked_due_monitor_does_not_hide_external_signal() -> None:
    blocked_due = monitor_item(
        index=1,
        todo_id="todo_private_read_monitor_due",
        priority="P0",
        next_due_at=PAST_DUE_AT,
        target_key="private-source-watch",
        required_capabilities=["private_read"],
    )
    external_monitor = monitor_item(
        index=2,
        todo_id="todo_pr_monitor_future",
        priority="P1",
        next_due_at=FUTURE_DUE_AT,
        target_key="pr_merged:example",
    )
    observe_action = "Observe compact result marker from the launched PR monitor."

    blocked_guard = guard_for(
        [blocked_due, external_monitor],
        recommended_action=observe_action,
    )
    blocked_lane = blocked_guard["work_lane_contract"]
    blocked_gate = blocked_guard["capability_gate"]
    assert blocked_gate["selection_policy"] == "no_runnable_candidate", blocked_gate
    assert blocked_gate["runnable_count"] == 0, blocked_gate
    assert blocked_lane["must_attempt_work"] is True, blocked_lane
    assert blocked_guard["decision"] == "observe", blocked_guard
    assert blocked_guard["should_run"] is True, blocked_guard
    assert blocked_guard["effective_action"] == "external_evidence_observe", blocked_guard
    assert blocked_guard["external_evidence_observation"]["required"] is True, blocked_guard
    assert "selected_todo" not in blocked_guard, blocked_guard
    assert blocked_guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert blocked_guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False

    observable_guard = guard_for(
        [external_monitor],
        recommended_action=observe_action,
    )
    assert observable_guard["decision"] == "observe", observable_guard
    assert observable_guard["effective_action"] == "external_evidence_observe", observable_guard
    assert observable_guard["external_evidence_observation"]["required"] is True
    assert observable_guard["work_lane_contract"]["must_attempt_work"] is True


def assert_due_monitor_capability_resolution_is_preserved() -> None:
    network_guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_network_monitor_due",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="public-network-watch",
                required_capabilities=["network"],
            )
        ]
    )
    network_gate = network_guard["capability_gate"]
    assert network_gate["action"] == "repair_bridge", network_guard
    assert network_gate["owner_missing"] == [], network_gate
    assert network_gate["repair_missing"] == ["network"], network_gate
    assert network_guard["heartbeat_recommendation"]["notify"] == "DONT_NOTIFY", network_guard
    assert network_guard["interaction_contract"]["user_channel"]["action_required"] is False
    assert network_guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert network_guard["requires_user_action"] is False, network_guard

    owner_guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_credentials_monitor_due",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="protected-watch",
                required_capabilities=["credentials"],
            )
        ]
    )
    owner_gate = owner_guard["capability_gate"]
    assert owner_gate["action"] == "ask_owner", owner_guard
    assert owner_gate["owner_missing"] == ["credentials"], owner_gate
    assert owner_guard["heartbeat_recommendation"]["notify"] == "NOTIFY", owner_guard
    assert owner_guard["interaction_contract"]["user_channel"]["action_required"] is True, owner_guard
    assert owner_guard["interaction_contract"]["agent_channel"]["must_attempt"] is False, owner_guard
    assert owner_guard["requires_user_action"] is True, owner_guard
    owner_markdown = render_quota_should_run_markdown(owner_guard)
    assert "capability_gate: action=ask_owner" in owner_markdown, owner_markdown
    assert "missing=['credentials']" in owner_markdown, owner_markdown

    repair_guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_bridge_monitor_due",
                priority="P1",
                next_due_at=PAST_DUE_AT,
                target_key="runner-bridge-watch",
                required_capabilities=["benchmark_runner"],
            )
        ]
    )
    repair_gate = repair_guard["capability_gate"]
    assert repair_gate["action"] == "repair_bridge", repair_guard
    assert repair_gate["repair_missing"] == ["benchmark_runner"], repair_gate
    assert repair_guard["capability_repair_allowed"] is True, repair_guard
    assert repair_guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, repair_guard


def assert_due_monitor_capability_resolution_uses_full_lane() -> None:
    items = [
        monitor_item(
            index=index,
            todo_id=f"todo_private_monitor_due_{index}",
            priority="P0",
            next_due_at=PAST_DUE_AT,
            target_key=f"private-source-watch-{index}",
            required_capabilities=["private_read"],
        )
        for index in (1, 2)
    ]
    items.append(
        monitor_item(
            index=3,
            todo_id="todo_network_monitor_due_after_display_limit",
            priority="P0",
            next_due_at=PAST_DUE_AT,
            target_key="public-network-watch-after-display-limit",
            required_capabilities=["network"],
        )
    )
    guard = guard_for(items)
    summary = guard["agent_todo_summary"]
    gate = guard["capability_gate"]
    assert summary["monitor_capability_blocked_due_count"] == 3, summary
    assert len(summary["monitor_capability_blocked_due_items"]) == 2, summary
    compaction = summary["payload_compaction"]["compacted_lanes"]
    assert compaction["monitor_capability_blocked_due_items"] == {
        "shown": 2,
        "total": 3,
    }, compaction
    assert gate["action"] == "repair_bridge", gate
    assert gate["owner_missing"] == [], gate
    assert gate["repair_missing"] == ["private_read", "network"], gate
    assert gate["unsupported_missing"] == [], gate
    assert "private_read" in gate["missing"], gate
    assert "network" in gate["missing"], gate
    assert guard["heartbeat_recommendation"]["notify"] == "DONT_NOTIFY", guard


def assert_expired_monitor_does_not_catch_up() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_expired",
                priority="P1",
                next_due_at=PAST_DUE_AT,
                expires_at=EXPIRED_AT,
                target_key="expired-publish-window",
            )
        ],
        include_scheduler_detail=True,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "skip", guard
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert lane["obligation"] == "quiet_until_material_monitor_transition", lane
    assert lane["must_attempt_work"] is False, lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 0, guard
    monitor_items = guard["agent_todo_summary"]["monitor_open_items"]
    assert monitor_items[0]["todo_id"] == "todo_monitor_expired", monitor_items
    assert monitor_items[0]["expires_at"] == EXPIRED_AT, monitor_items
    scheduler = guard["scheduler_hint"]
    codex_app = scheduler["codex_app"]
    context = scheduler["cold_path_detail"]["cadence_context"]
    assert scheduler["cadence_class"] == "monitor_wait", scheduler
    assert context["phase"] == "expired", context
    assert context["expired_monitor_count"] == 1, context
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=15", scheduler
    assert codex_app["example_progression_minutes"] == [15, 30, 60], scheduler


def assert_due_monitor_priority_does_not_steal_advancement_lane() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_monitor_due_p2",
                priority="P2",
                next_due_at=PAST_DUE_AT,
                target_key="low-priority-watch",
            ),
            advancement_item(index=2, priority="P1"),
        ]
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["reason_codes"] == ["open_agent_todo", "due_monitor_context"], lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 1, guard


def assert_capability_repair_precedes_monitor_schedule_repair() -> None:
    guard = guard_for(
        [
            advancement_item(
                index=1,
                priority="P1",
                required_capabilities=["private_read"],
            ),
            monitor_item(
                index=2,
                todo_id="todo_capability_skip_monitor_gap",
                priority="P0",
                target_key="skillsbench-live-batch-watch",
                cadence=None,
                next_due_at=None,
            ),
        ]
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "repair_bridge", guard
    assert guard["effective_action"] == "capability_bridge_repair", guard
    assert guard["capability_gate"]["action"] == "repair_bridge", guard
    assert "capability_monitor_fallback" not in guard, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["must_attempt_work"] is True, lane
    assert "todo_capability_skip_monitor_gap" in str(
        guard["agent_todo_summary"].get("monitor_open_items")
    ), guard
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "repair_capability_bridge", guard
    assert guard["execution_obligation"]["contract"] == "capability_gate", guard
    primary_action = guard["interaction_contract"]["agent_channel"]["primary_action"]
    assert primary_action.startswith(
        "execute interaction_contract.agent_channel.next_task_action.operation once"
    ), guard
    assert "on success execute cli_channel.next_cli_actions[0]" in primary_action, guard
    next_task_action = guard["interaction_contract"]["agent_channel"]["next_task_action"]
    assert next_task_action["kind"] == "capability_verification", guard
    assert next_task_action["capability"] == "private_read", guard
    assert next_task_action["todo_id"] == "todo_adv_1", guard
    assert guard["scheduler_hint"]["cadence_class"] == "active_work", guard


def assert_capability_repair_precedes_scheduled_monitor_wait() -> None:
    guard = guard_for(
        [
            advancement_item(
                index=1,
                priority="P1",
                required_capabilities=["private_read"],
            ),
            monitor_item(
                index=2,
                todo_id="todo_capability_skip_monitor_wait",
                priority="P0",
                target_key="skillsbench-live-batch-watch",
                cadence="15m",
                next_due_at=FUTURE_DUE_AT,
            ),
        ]
    )
    scheduler = guard["scheduler_hint"]
    assert guard["decision"] == "repair_bridge", guard
    assert guard["effective_action"] == "capability_bridge_repair", guard
    assert guard["capability_gate"]["action"] == "repair_bridge", guard
    assert "capability_monitor_fallback" not in guard, guard
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "repair_capability_bridge", guard
    assert scheduler["cadence_class"] == "active_work", scheduler
    assert scheduler["codex_app"]["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler


def assert_read_only_projected_due_monitor_does_not_force_writeback() -> None:
    status = status_payload(
        agent_todo_items=[
            monitor_item(
                index=1,
                todo_id="todo_projected_due_monitor",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="event-projected-watch",
            ),
            advancement_item(index=2, priority="P1"),
        ]
    )
    agent_todos = status["attention_queue"]["items"][0]["project_asset"]["agent_todos"]
    agent_todos["monitor_writeback"] = {
        "schema_version": "monitor_writeback_contract_v0",
        "supported": False,
        "source": "event_projection_read_model",
    }

    guard = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert lane.get("obligation") != "attempt_due_monitor", lane
    summary = guard["agent_todo_summary"]
    assert summary["monitor_due_count"] == 0, summary
    assert summary["monitor_open_items"][0]["todo_id"] == "todo_projected_due_monitor", summary
    assert summary["monitor_writeback"]["supported"] is False, summary


def assert_due_monitor_is_not_overridden_by_side_agent_scope_wait() -> None:
    other_agent = "codex-main-control"
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_side_due_monitor",
                priority="P1",
                next_due_at=PAST_DUE_AT,
                target_key="side-agent-due-watch",
            ),
            advancement_item(index=2, priority="P1", claimed_by=other_agent),
        ],
        coordination={
            "registered_agents": [other_agent, AGENT_ID],
            "agent_model": "peer_v1",
        },
    )
    lane = guard["work_lane_contract"]
    assert guard["agent_identity"]["agent_model"] == "peer_v1", guard
    assert "role" not in guard["agent_identity"], guard
    assert guard["decision"] == "run", guard
    assert guard["effective_action"] == "normal_run", guard
    assert guard["should_run"] is True, guard
    assert guard["normal_delivery_allowed"] is True, guard
    assert lane["monitor_kind"] == "todo_monitor_due", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["selected_todo_id"] == "todo_side_due_monitor", lane
    assert "agent_scope_frontier" not in guard, guard
    assert "agent_lane_frontier_hint" not in guard, guard
    claim_scope = guard["agent_todo_summary"]["claim_scope"]
    assert claim_scope["other_agent_claimed_open_count"] == 1, claim_scope
    assert claim_scope["other_agent_claimed_weight"] == "diagnostic_only", claim_scope
    contract = guard["interaction_contract"]
    assert contract["agent_channel"]["must_attempt"] is True, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is False, contract


def assert_known_due_monitor_survives_incomplete_work_counts() -> None:
    status = status_payload(
        agent_todo_items=[
            monitor_item(
                index=1,
                todo_id="todo_incomplete_source_due_monitor",
                priority="P1",
                next_due_at=PAST_DUE_AT,
                target_key="incomplete-source-due-watch",
            ),
            {
                "index": 2,
                "text": "[P1] Deferred independent branch work.",
                "todo_id": "todo_deferred_branch_successor",
                "role": "agent",
                "status": "deferred",
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
                "resume_when": "capacity_available:project_branch",
            },
        ]
    )
    agent_todos = status["attention_queue"]["items"][0]["project_asset"]["agent_todos"]
    # A legacy/partial source cannot certify monitor-only scheduling, but its
    # explicitly projected due Todo remains an executable candidate.
    agent_todos["work_counts"]["complete"] = False

    guard = build_quota_should_run(status, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert guard["agent_todo_summary"]["work_counts"]["complete"] is False, guard
    assert guard["agent_todo_summary"]["monitor_due_count"] == 1, guard
    assert guard["decision"] == "run", guard
    assert guard["work_lane_contract"]["selected_todo_id"] == "todo_incomplete_source_due_monitor", guard
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, guard
    assert "agent_scope_frontier" not in guard, guard


def assert_multiple_due_monitor_cap_and_order() -> None:
    guard = guard_for(
        [
            monitor_item(
                index=5,
                todo_id="todo_monitor_due_p1",
                priority="P1",
                next_due_at=PAST_DUE_AT,
                target_key="p1-watch",
            ),
            monitor_item(
                index=9,
                todo_id="todo_monitor_due_p0_late",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="p0-late-watch",
            ),
            monitor_item(
                index=3,
                todo_id="todo_monitor_due_p0_first",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="p0-first-watch",
            ),
        ]
    )
    lane = guard["work_lane_contract"]
    assert lane["obligation"] == "attempt_due_monitor", lane
    assert lane["monitor_due_count"] == 3, lane
    assert len(lane["monitor_due_items"]) == 1, lane
    assert lane["monitor_due_items"][0]["todo_id"] == "todo_monitor_due_p0_first", lane
    assert lane["selected_todo_id"] == "todo_monitor_due_p0_first", lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 3, guard
    assert len(guard["agent_todo_summary"]["monitor_due_items"]) == 1, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "work_lane_monitor_due: count=3" in markdown, markdown


def assert_other_agent_due_monitor_does_not_preempt_current_agent_lane() -> None:
    other_agent = "codex-main-control"
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_other_due_monitor",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="other-agent-watch",
                claimed_by=other_agent,
            ),
            advancement_item(index=2, priority="P1"),
        ],
        coordination={
            "registered_agents": [other_agent, AGENT_ID],
            "agent_model": "peer_v1",
        },
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert lane.get("monitor_due_count", 0) == 0, lane
    assert "monitor_due_items" not in lane, lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 0, guard
    claim_scope = guard["agent_todo_summary"]["claim_scope"]
    assert claim_scope["other_agent_claimed_open_count"] == 1, claim_scope
    assert claim_scope["other_agent_claimed_items"][0]["todo_id"] == "todo_other_due_monitor", claim_scope
    assert guard["recommended_action"] == advancement_item(index=2, priority="P1")["text"], guard


def assert_other_agent_claimed_work_stays_diagnostic_when_no_current_lane() -> None:
    other_agent = "codex-main-control"
    guard = guard_for(
        [
            monitor_item(
                index=1,
                todo_id="todo_other_due_monitor",
                priority="P0",
                next_due_at=PAST_DUE_AT,
                target_key="other-agent-watch",
                claimed_by=other_agent,
            ),
            advancement_item(index=2, priority="P0", claimed_by=other_agent),
        ],
        coordination={
            "registered_agents": [other_agent, AGENT_ID],
            "agent_model": "peer_v1",
        },
    )
    summary = guard["agent_todo_summary"]
    lane = guard.get("work_lane_contract") or {}
    assert summary["open_count"] == 0, summary
    assert summary["first_executable_items"] == [], summary
    assert summary["monitor_due_count"] == 0, summary
    assert lane.get("must_attempt_work") is not True, lane
    claim_scope = summary["claim_scope"]
    assert claim_scope["selectable_open_count"] == 0, claim_scope
    assert claim_scope["other_agent_claimed_open_count"] == 2, claim_scope
    assert claim_scope["other_agent_claimed_weight"] == "diagnostic_only", claim_scope
    assert guard["recommended_action"] != advancement_item(index=2, priority="P0", claimed_by=other_agent)["text"], guard


def main() -> int:
    assert_scheduler_timestamp_parser_is_shared_and_utc_normalized()
    assert_not_due_monitor_waits_quietly()
    assert_not_due_monitor_scheduler_uses_due_horizon_before_cadence()
    assert_monitor_scheduler_far_window_uses_coarse_backoff()
    assert_monitor_scheduler_due_horizon_can_break_host_floor()
    assert_monitor_scheduler_near_window_caps_without_breaking_floor()
    assert_monitor_scheduler_near_window_reset_identity_is_stable()
    assert_monitor_scheduler_active_window_honors_tighter_cadence()
    assert_unscheduled_monitor_requires_metadata_repair()
    assert_unscheduled_monitor_repair_survives_handoff_gates()
    assert_due_monitor_requires_explicit_attempt()
    assert_due_monitor_requires_available_capabilities()
    assert_runnable_due_monitor_survives_blocked_due_monitor()
    assert_capability_blocked_due_monitor_does_not_hide_external_signal()
    assert_due_monitor_capability_resolution_is_preserved()
    assert_due_monitor_capability_resolution_uses_full_lane()
    assert_expired_monitor_does_not_catch_up()
    assert_due_monitor_priority_does_not_steal_advancement_lane()
    assert_capability_repair_precedes_monitor_schedule_repair()
    assert_capability_repair_precedes_scheduled_monitor_wait()
    assert_read_only_projected_due_monitor_does_not_force_writeback()
    assert_due_monitor_is_not_overridden_by_side_agent_scope_wait()
    assert_known_due_monitor_survives_incomplete_work_counts()
    assert_multiple_due_monitor_cap_and_order()
    assert_other_agent_due_monitor_does_not_preempt_current_agent_lane()
    assert_other_agent_claimed_work_stays_diagnostic_when_no_current_lane()
    print("monitor-scheduler-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
