from __future__ import annotations

import shlex

from loopx.cli import build_parser
from loopx.cli_commands.status import attach_agent_lane_next_actions
from loopx.control_plane.agents.agent_lane_recommendation import (
    scope_status_item_to_agent_lane,
    selected_recommended_action_from_work_lane,
)
from loopx.control_plane.quota.monitor_poll import build_quota_monitor_poll_event
from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.quota import build_quota_should_run

GOAL_ID = "monitor-replan-agent-scope-fixture"
AGENT_ID = "codex-quality-agent"
PEER_AGENT_ID = "codex-delivery-peer"
SECOND_PEER_AGENT_ID = "codex-main-control"


def _guard(
    *,
    current_agent_advancement: bool = False,
    interleaved_monitors: bool = False,
) -> dict:
    monitor_items = [
        quota_todo_item(
            todo_id="todo_stalled_monitor",
            index=1,
            title="Watch the release qualification PR.",
            task_class="continuous_monitor",
            claimed_by=AGENT_ID,
            target_key="github-pr-123",
            consecutive_no_change="5",
            cadence="30m",
            next_due_at="2099-01-01T00:00:00+00:00",
        ),
    ]
    if interleaved_monitors:
        monitor_items = [
            quota_todo_item(
                todo_id="todo_unchanged_once",
                index=1,
                title="Watch the scheduler qualification PR.",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                target_key="github-pr-123",
                consecutive_no_change="1",
                cadence="30m",
                next_due_at="2099-01-01T00:00:00+00:00",
            ),
            quota_todo_item(
                todo_id="todo_second_unchanged_once",
                index=2,
                title="Watch the quota qualification PR.",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                target_key="github-pr-234",
                consecutive_no_change="1",
                cadence="45m",
                next_due_at="2099-01-01T00:00:00+00:00",
            ),
            quota_todo_item(
                todo_id="todo_unchanged_twice",
                index=3,
                title="Watch the control-plane qualification PR.",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                target_key="github-pr-456",
                consecutive_no_change="5",
                cadence="1h",
                next_due_at="2099-01-01T00:00:00+00:00",
            ),
            quota_todo_item(
                todo_id="todo_peer_monitor",
                index=4,
                title="Watch an independent peer PR.",
                task_class="continuous_monitor",
                claimed_by=PEER_AGENT_ID,
                target_key="github-pr-789",
                consecutive_no_change="5",
                cadence="15m",
                next_due_at="2099-01-01T00:00:00+00:00",
            ),
        ]
    items = [
        *monitor_items,
        quota_todo_item(
            todo_id="todo_peer_advancement",
            index=5 if interleaved_monitors else 4,
            title="Advance an independent peer delivery.",
            task_class="advancement_task",
            claimed_by=PEER_AGENT_ID,
        ),
    ]
    if current_agent_advancement:
        items.append(
            quota_todo_item(
                todo_id="todo_current_advancement",
                index=3,
                title="Advance the current quality lane.",
                task_class="advancement_task",
                claimed_by=AGENT_ID,
            )
        )
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Wait for material monitor evidence.",
        agent_todos=quota_todo_summary(items),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID, PEER_AGENT_ID],
        },
    )
    return build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )


def test_peer_advancement_does_not_suppress_current_monitor_streak_replan() -> None:
    guard = _guard()

    assert guard["decision"] == "autonomous_replan_required"
    assert guard["effective_action"] == "autonomous_replan_required"
    assert guard["should_run"] is True
    obligation = guard["autonomous_replan_obligation"]
    assert obligation["agent_id"] == AGENT_ID
    trigger = obligation["triggers"][0]
    assert trigger["kind"] == "monitor_no_change_streak"
    assert trigger["todo_id"] == "todo_stalled_monitor"
    assert trigger["run_count"] == 5
    assert trigger["threshold"] == 5

    frontier = guard["goal_frontier_projection"]
    assert frontier["monitor_only_lanes"]["present"] is True
    assert frontier["replan_required"] is True
    assert frontier["remaining_advancement_frontier"] == {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 1,
    }


def test_current_streak_keeps_eligibility_target_order_and_public_shape() -> None:
    from copy import deepcopy
    from loopx.control_plane.goals.goal_frontier import _monitor_no_change_streak_trigger

    def item(todo_id, target, **fields):
        return quota_todo_item(todo_id=todo_id, title="Observe a finite source.",
            task_class="continuous_monitor", claimed_by=AGENT_ID,
            target_key=target, consecutive_no_change="8", expires_at="2099-01-01T00:00:00Z",
            **fields)

    current = [item("todo_z", "z"), item("todo_a", "a")]
    ignored = [
        {**current[0], "todo_id": "todo_peer", "claimed_by": PEER_AGENT_ID, "consecutive_no_change": "99"},
        {**current[0], "todo_id": "todo_unclaimed", "claimed_by": None, "consecutive_no_change": "99"},
        {**current[0], "watch_only": "true", "consecutive_no_change": "99"},
        {**current[0], "status": "blocked", "consecutive_no_change": "99"},
        {**current[0], "done": True, "consecutive_no_change": "99"},
        {**current[0], "task_class": "advancement_task", "consecutive_no_change": "99"},
    ]
    source = {"monitor_open_items": ignored + current}
    before = deepcopy(source)
    assert _monitor_no_change_streak_trigger(source, agent_id=AGENT_ID) == {
        "kind": "monitor_no_change_streak", "section": "agent_todo_summary.monitor_open_items",
        "text": "monitor a recorded 8 consecutive unchanged polls without selectable advancement",
        "todo_id": "todo_a", "monitor_target_id": "a", "run_count": 8, "threshold": 5,
        "agent_id": AGENT_ID,
    }
    assert source == before


def test_watch_only_monitor_streak_does_not_create_replan_obligation() -> None:
    monitor = quota_todo_item(
        todo_id="todo_watch_only_monitor",
        index=1,
        title="Observe public release liveness.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="github-pr-123",
        consecutive_no_change="50",
        cadence="30m",
        next_due_at="2099-01-01T00:00:00+00:00",
        watch_only="true",
    )
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Observe liveness quietly.",
        agent_todos=quota_todo_summary([monitor]),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
    )

    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )

    assert guard["decision"] != "autonomous_replan_required"
    assert guard.get("autonomous_replan_obligation") is None
    assert guard["goal_frontier_projection"]["monitor_only_lanes"]["present"] is False


def test_rejected_replan_ack_reason_is_visible_in_quota_feedback() -> None:
    monitor = quota_todo_item(
        todo_id="todo_stalled_monitor",
        index=1,
        title="Watch bounded qualification evidence.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="qualification",
        consecutive_no_change="5",
        cadence="30m",
        next_due_at="2099-01-01T00:00:00+00:00",
        expires_at="2099-01-02T00:00:00+00:00",
    )
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Replan the stalled qualification lane.",
        agent_todos=quota_todo_summary([monitor]),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        latest_runs=[
            {
                "generated_at": "2026-08-11T00:00:00Z",
                "agent_id": AGENT_ID,
                "classification": "replan_noop",
                "replan_ack_feedback": {
                    "schema_version": "replan_ack_feedback_v0",
                    "generated_at": "2026-08-11T00:00:00Z",
                    "agent_id": AGENT_ID,
                    "classification": "replan_noop",
                    "rejected_claims": [
                        {
                            "kind": "watch_lane_continuation",
                            "reason": "monitor is missing expires_at or resume_when",
                        }
                    ],
                },
            }
        ],
    )

    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )

    obligation = guard["autonomous_replan_obligation"]
    assert obligation["replan_ack_feedback"]["rejected_claims"][0]["kind"] == (
        "watch_lane_continuation"
    )
    assert "previous replan ACK was rejected" in obligation["recommended_action"]
    assert guard["heartbeat_recommendation"]["replan_obligation"][
        "replan_ack_feedback"
    ] == obligation["replan_ack_feedback"]


def test_current_agent_advancement_still_preempts_monitor_streak_replan() -> None:
    guard = _guard(current_agent_advancement=True)

    assert guard["decision"] == "run"
    assert guard["effective_action"] == "normal_run"
    assert guard["work_lane_contract"]["lane"] == "advancement_task"
    assert guard.get("autonomous_replan_obligation") is None


def test_interleaved_monitors_keep_independent_no_change_streaks() -> None:
    guard = _guard(interleaved_monitors=True)

    assert guard["decision"] == "autonomous_replan_required"
    trigger = guard["autonomous_replan_obligation"]["triggers"][0]
    assert trigger == {
        "kind": "monitor_no_change_streak",
        "section": "agent_todo_summary.monitor_open_items",
        "text": (
            "monitor github-pr-456 recorded 5 consecutive unchanged polls "
            "without selectable advancement"
        ),
        "todo_id": "todo_unchanged_twice",
        "monitor_target_id": "github-pr-456",
        "run_count": 5,
        "threshold": 5,
        "agent_id": AGENT_ID,
    }
    assert [
        item["todo_id"] for item in guard["agent_todo_summary"]["monitor_open_items"]
    ] == ["todo_unchanged_once", "todo_second_unchanged_once"]
    assert guard["agent_todo_summary"]["payload_compaction"]["compacted_lanes"][
        "monitor_open_items"
    ] == {"shown": 2, "total": 3}


def test_three_peer_status_quota_and_monitor_recommendations_stay_agent_scoped() -> None:
    current_action = "Wait for the quality lane's own material evidence."
    peer_action = "Advance the delivery peer's unrelated adapter."
    main_action = "Continue the main-control provider rollout."
    monitor = quota_todo_item(
        todo_id="todo_quality_monitor",
        index=1,
        title="Watch the quality qualification evidence.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="quality-evidence",
        consecutive_no_change="0",
        cadence="30m",
        next_due_at="2099-01-01T00:00:00+00:00",
    )
    blocker = quota_todo_item(
        todo_id="todo_quality_prerequisite",
        index=2,
        title="Complete the quality baseline prerequisite.",
        task_class="advancement_task",
        claimed_by=SECOND_PEER_AGENT_ID,
        successor_todo_ids=["todo_quality_baseline"],
    )
    blocked_successor = quota_todo_item(
        todo_id="todo_quality_baseline",
        index=3,
        status="deferred",
        title="Run the release outcome baseline.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        resume_when="todo_done:todo_quality_prerequisite",
    )
    peer_recommendation = {
        "schema_version": "agent_lane_recommendation_v0",
        "progress_scope": "agent_lane",
        "agent_id": PEER_AGENT_ID,
        "recommended_action": peer_action,
        "generated_at": "2026-07-24T03:20:00+00:00",
    }
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action=main_action,
        agent_todos=quota_todo_summary([monitor, blocker, blocked_successor]),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [
                AGENT_ID,
                PEER_AGENT_ID,
                SECOND_PEER_AGENT_ID,
            ],
        },
        latest_runs=[
            {
                "classification": "main_control_progress",
                "generated_at": "2026-07-24T03:30:00+00:00",
                "agent_id": SECOND_PEER_AGENT_ID,
                "progress_scope": "goal",
                "recommended_action": main_action,
                "delivery_outcome": "outcome_progress",
            },
            {
                "classification": "delivery_peer_progress",
                "generated_at": "2026-07-24T03:20:00+00:00",
                "agent_id": PEER_AGENT_ID,
                "progress_scope": "agent_lane",
                "recommended_action": peer_action,
                "delivery_outcome": "outcome_progress",
            },
            {
                "classification": "quality_lane_progress",
                "generated_at": "2026-07-24T03:10:00+00:00",
                "agent_id": AGENT_ID,
                "progress_scope": "agent_lane",
                "recommended_action": current_action,
                "delivery_outcome": "outcome_progress",
                "agent_vision": {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "agent_id": AGENT_ID,
                    "state": "vision_active",
                    "todo_delta": ["activate:todo_quality_prerequisite"],
                    "vision_patch": {
                        "acceptance_summary": (
                            "Keep quality qualification advancing until closed."
                        ),
                        "advancement_policy": "repeat_until_closed",
                        "replan_trigger_summary": (
                            "Replan when the quality baseline remains unavailable."
                        ),
                    },
                },
            },
        ],
        item_extra={
            "agent_lane_recommendation": peer_recommendation,
            "latest_run_recommended_action": main_action,
            "latest_run_recommended_action_source": "latest_status_run",
        },
        project_asset_extra={
            "agent_lane_recommendation": peer_recommendation,
            "latest_run_recommended_action": main_action,
            "latest_run_recommended_action_source": "latest_status_run",
        },
    )

    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )

    assert guard["effective_action"] == "monitor_quiet_skip"
    assert guard["recommended_action"] == current_action
    assert guard["latest_run_recommended_action"] == current_action
    event = build_quota_monitor_poll_event(guard)
    assert event["agent_id"] == AGENT_ID
    assert event["recommended_action"] == current_action

    attach_agent_lane_next_actions(payload, agent_id=AGENT_ID)
    item = payload["attention_queue"]["items"][0]
    assert item["recommended_action"] == main_action
    assert item["latest_run_recommended_action"] == current_action
    assert item["agent_lane_recommendation"]["agent_id"] == AGENT_ID
    assert item["project_asset"]["next_action"] == main_action
    assert item["project_asset"]["latest_run_recommended_action"] == current_action


def test_monitor_quiet_without_own_lane_run_drops_peer_recommendation() -> None:
    peer_action = "Advance the delivery peer's unrelated adapter."
    monitor_action = "wait quietly for material monitor evidence"
    item, project_asset, recommendation = scope_status_item_to_agent_lane(
        {
            "recommended_action": peer_action,
            "agent_lane_recommendation": {
                "agent_id": PEER_AGENT_ID,
                "recommended_action": peer_action,
            },
            "latest_run_recommended_action": peer_action,
            "project_asset": {
                "agent_lane_recommendation": {
                    "agent_id": PEER_AGENT_ID,
                    "recommended_action": peer_action,
                },
                "latest_run_recommended_action": peer_action,
            },
        },
        latest_runs=[
            {
                "agent_id": PEER_AGENT_ID,
                "progress_scope": "agent_lane",
                "recommended_action": peer_action,
            }
        ],
        agent_id=AGENT_ID,
        public_safe_compact_text=lambda value, **_: str(value) if value else None,
    )

    assert recommendation is None
    assert "agent_lane_recommendation" not in item
    assert "latest_run_recommended_action" not in item
    assert "agent_lane_recommendation" not in project_asset
    assert (
        selected_recommended_action_from_work_lane(
            item,
            agent_todo_summary=None,
            work_lane_contract={"action": monitor_action},
            agent_lane_recommendation=recommendation,
            prefer_agent_lane_recommendation=True,
        )
        == monitor_action
    )


def _combined_user_frontier_guard(*, user_task_class: str) -> dict:
    monitor = quota_todo_item(
        todo_id="todo_stalled_monitor",
        index=1,
        title="Watch the release qualification PR.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="github-pr-123",
        consecutive_no_change="5",
        cadence="30m",
        next_due_at="2099-01-01T00:00:00+00:00",
    )
    blocked_successor = quota_todo_item(
        todo_id="todo_blocked_release_baseline",
        index=2,
        status="deferred",
        title="Run the release outcome baseline.",
        task_class="advancement_task",
        action_kind="release_evidence_qualification",
        claimed_by=AGENT_ID,
        required_capabilities=["benchmark_runner"],
        resume_when="capacity_available:benchmark_runner",
    )
    user_item = quota_todo_item(
        todo_id="todo_user_frontier",
        role="user",
        status="open",
        title="Review the public-safe qualification evidence.",
        task_class=user_task_class,
        blocks_agent=AGENT_ID if user_task_class == "user_gate" else None,
        bound_agent=AGENT_ID if user_task_class == "user_action" else None,
    )
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Wait for material monitor evidence.",
        agent_todos=quota_todo_summary([monitor, blocked_successor]),
        user_todos=quota_todo_summary([user_item], role="user"),
        quota_state="operator_gate" if user_task_class == "user_gate" else "eligible",
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID, PEER_AGENT_ID],
        },
        latest_runs=[
            {
                "classification": "quality_vision_fixture",
                "generated_at": "2026-07-19T00:00:00+00:00",
                "agent_id": AGENT_ID,
                "progress_scope": "agent_lane",
                "agent_vision": {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "agent_id": AGENT_ID,
                    "state": "vision_active",
                    "todo_delta": [
                        "activate:todo_blocked_release_baseline"
                    ],
                    "vision_patch": {
                        "acceptance_summary": (
                            "Keep release qualification advancing until closed."
                        ),
                        "advancement_policy": "repeat_until_closed",
                        "replan_trigger_summary": (
                            "Replan after five unchanged monitor observations."
                        ),
                    },
                },
            }
        ],
    )
    return build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )


def test_nonblocking_user_action_does_not_suppress_stalled_monitor_replan() -> None:
    guard = _combined_user_frontier_guard(user_task_class="user_action")

    assert guard["decision"] == "autonomous_replan_required"
    assert guard["goal_frontier_projection"]["vision_wait_state"][
        "selected_todo_id"
    ] == "todo_blocked_release_baseline"
    trigger = guard["autonomous_replan_obligation"]["triggers"][0]
    assert trigger["kind"] == "monitor_no_change_streak"
    assert trigger["todo_id"] == "todo_stalled_monitor"
    assert guard["interaction_contract"]["user_channel"] == {
        "action_required": False,
        "notify": "NOTIFY",
        "max_items": 3,
        "actions": ["[P0] Review the public-safe qualification evidence."],
        "non_blocking": True,
        "reason": (
            "open non-blocking user action should be surfaced while independent "
            "agent work continues"
        ),
    }
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True


def test_nonblocking_user_action_does_not_suppress_empty_frontier_replan() -> None:
    user_action = quota_todo_item(
        todo_id="todo_review_reminder",
        role="user",
        status="open",
        title="Review the experiment integration PR.",
        task_class="user_action",
        bound_agent=AGENT_ID,
    )
    replan_obligation = {
        "schema_version": "autonomous_replan_obligation_v0",
        "required": True,
        "stall_threshold": 2,
        "trigger_count": 1,
        "triggers": [
            {
                "kind": "run_history_no_progress_repeat",
                "section": "run_history",
                "text": "two stalled turns left no runnable agent todo",
                "agent_id": AGENT_ID,
            }
        ],
        "todo_actions": [
            {
                "action": "add",
                "role": "agent",
                "priority": "P1",
                "text": "create the next runnable experiment slice",
            }
        ],
        "stop_condition": "stop on owner-only authority",
    }
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        waiting_on="codex",
        recommended_action="Replan the empty agent frontier.",
        agent_todos=quota_todo_summary([], role="agent"),
        user_todos=quota_todo_summary([user_action], role="user"),
        project_asset_extra={
            "autonomous_replan_obligation": replan_obligation,
        },
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID, PEER_AGENT_ID],
        },
    )
    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )

    assert guard["decision"] == "autonomous_replan_required", guard
    assert guard["state"] == "eligible", guard
    assert guard["requires_user_action"] is False, guard
    assert guard["execution_obligation"] == {
        "must_attempt_work": True,
        "kind": "autonomous_replan_required",
        "minimum": "one_bounded_replan_with_typed_outcome",
        "notify_is_execution_gate": False,
        "stall_threshold": 2,
        "contract_obligation": (
            "apply autonomous_replan_obligation; create a typed concrete runnable "
            "agent todo only when an executable target is known, otherwise record "
            "an accepted typed semantic or coverage-backed terminal outcome"
        ),
        "reason": (
            "autonomous_replan_obligation is a machine execution contract; "
            "quiet no-op is not allowed until the replan slice is validated or blocked"
        ),
    }, guard
    contract = guard["interaction_contract"]
    assert contract["mode"] == "autonomous_replan", contract
    assert contract["user_channel"]["non_blocking"] is True, contract
    assert contract["agent_channel"]["must_attempt"] is True, contract
    cli_actions = contract["cli_channel"]["next_cli_actions"]
    refresh_action = next(action for action in cli_actions if "refresh-state" in action)
    assert "--progress-surface-id <surface-id>" in refresh_action
    assert "--progress-hypothesis-id <hypothesis-id>" in refresh_action
    assert "--progress-probe-kind <probe-kind>" in refresh_action
    assert "--progress-evidence-id <evidence-id>" in refresh_action
    assert "typed-progress-identifiers-and-evidence" not in refresh_action
    assert refresh_action.startswith("loopx --format json refresh-state ")
    assert "--delivery-batch-scale" not in refresh_action
    assert "--delivery-outcome" not in refresh_action
    rendered_action = (
        refresh_action.replace(
            "<advanced|blocked|exploration_exhausted|no_followup>",
            "advanced",
        )
        .replace("<surface-id>", "surface-new")
        .replace("<hypothesis-id>", "hypothesis-new")
        .replace("<probe-kind>", "probe-new")
        .replace("<evidence-id>", "evidence-new")
    )
    parsed = build_parser().parse_args(shlex.split(rendered_action)[1:])
    assert parsed.command == "refresh-state"
    assert parsed.progress_result_class == "advanced"
    assert not any("todo add" in action for action in cli_actions), cli_actions


def test_blocking_user_gate_still_precedes_stalled_monitor_replan() -> None:
    guard = _combined_user_frontier_guard(user_task_class="user_gate")

    assert guard["decision"] == "skip"
    assert guard["interaction_contract"]["mode"] == "user_gate"
    assert guard["interaction_contract"]["user_channel"]["action_required"] is True
    assert guard.get("autonomous_replan_obligation") is None
