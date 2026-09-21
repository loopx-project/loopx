from __future__ import annotations

from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.work_items.work_lane import (
    preserve_heartbeat_receipt_bound_work_lane,
)
from loopx.control_plane.work_items.work_lane_context import (
    item_progress_scope,
    latest_run_progress_scope,
)
from loopx.quota import build_quota_should_run

GOAL_ID = "work-lane-policy-fixture"
PAST_DUE_AT = "2000-01-01T00:00:00+00:00"


def _status(
    *,
    agent_todo_items: list[dict],
    status: str = "monitor_backlog_fairness",
    next_action: str = "Observe dependency state and then advance backlog if unchanged.",
    latest_runs: list[dict] | None = None,
) -> dict:
    return quota_status_payload(
        goal_id=GOAL_ID,
        status=status,
        agent_todo_items=agent_todo_items,
        recommended_action=next_action,
        next_action=next_action,
        latest_runs=latest_runs,
    )


def _monitor_and_advancement() -> list[dict]:
    return [
        {
            "index": 1,
            "text": "[P0] Monitor one overdue dependency.",
            "role": "agent",
            "status": "open",
            "priority": "P0",
            "task_class": "continuous_monitor",
            "action_kind": "monitor",
            "next_due_at": PAST_DUE_AT,
        },
        {
            "index": 2,
            "text": "[P1] Advance the bounded product slice.",
            "role": "agent",
            "status": "open",
            "priority": "P1",
            "task_class": "advancement_task",
        },
    ]


def test_unchanged_monitor_attempt_yields_to_advancement() -> None:
    unchanged_poll = {
        "classification": "quota_monitor_poll",
        "agent_id": "codex-fixture",
        "health_check": "due monitor observation unchanged; no quota spend",
        "monitor_event": {
            "monitor_mode": "due_monitor_observed_without_material_transition",
            "material_change": False,
        },
    }
    payload = _status(
        agent_todo_items=_monitor_and_advancement(),
        next_action="Advance the bounded product slice.",
        latest_runs=[unchanged_poll],
    )
    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]

    assert lane["lane"] == "advancement_task"
    assert lane["reason_codes"] == [
        "open_agent_todo",
        "due_monitor_context",
        "monitor_attempt_already_recorded",
    ]
    assert guard["recommended_action"] == "[P1] Advance the bounded product slice."


def test_due_monitor_preempts_lower_priority_advancement() -> None:
    payload = _status(agent_todo_items=_monitor_and_advancement())
    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]

    assert lane["lane"] == "continuous_monitor"
    assert lane["monitor_due_count"] == 1
    assert lane["selected_todo_id"]
    assert guard["recommended_action"] == "[P0] Monitor one overdue dependency."


def test_due_watch_only_monitor_is_an_auxiliary_no_spend_route() -> None:
    items = _monitor_and_advancement()
    items[0].update(
        {
            "todo_id": "todo_watch_due",
            "watch_only": "true",
        }
    )
    items[1].update(
        {
            "todo_id": "todo_advancement",
        }
    )
    payload = _status(
        agent_todo_items=items,
        next_action="Advance the bounded product slice.",
    )
    turn_instance_id = "turn-watch-only-auxiliary"
    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        turn_instance_id=turn_instance_id,
    )

    assert guard["recommended_action"] == "[P1] Advance the bounded product slice."
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task"
    assert lane["auxiliary_monitor_poll"] == {
        "schema_version": "auxiliary_monitor_poll_v0",
        "required": False,
        "preempts_advancement": False,
        "spend_policy": "no_spend",
        "continuation": "advancement_remains_primary",
        "monitor_due_count": 1,
        "monitor_due_items": [
            {
                "index": 1,
                "text": "[P0] Monitor one overdue dependency.",
                "todo_id": "todo_watch_due",
                "status": "open",
                "priority": "P0",
                "task_class": "continuous_monitor",
                "action_kind": "monitor",
                "next_due_at": PAST_DUE_AT,
                "watch_only": "true",
            }
        ],
        "selected_todo_id": "todo_watch_due",
        "selected_next_due_at": PAST_DUE_AT,
    }
    summary = guard["agent_todo_summary"]
    assert summary["watch_only_monitor_due_count"] == 1
    assert summary["watch_only_monitor_due_items"][0]["todo_id"] == (
        "todo_watch_due"
    )
    interaction = guard["interaction_contract"]
    assert interaction["agent_channel"]["auxiliary_monitor_poll"][
        "required"
    ] is False
    auxiliary_cli = interaction["cli_channel"]["auxiliary_monitor_poll"]
    assert auxiliary_cli["schema_version"] == "auxiliary_monitor_poll_cli_v0"
    assert auxiliary_cli["availability"] == "ready"
    assert auxiliary_cli["turn_instance_id"] == turn_instance_id
    assert auxiliary_cli["spend_policy"] == "no_spend"
    assert f"--turn-instance-id {turn_instance_id}" in auxiliary_cli["command"]
    assert "--todo-id todo_watch_due" in auxiliary_cli["command"]
    assert '--result-hash "${LOOPX_MONITOR_RESULT_HASH:?}"' in auxiliary_cli[
        "command"
    ]
    assert auxiliary_cli["command"].endswith("--execute")
    assert "--material-change --execute" in auxiliary_cli[
        "material_change_command"
    ]
    assert auxiliary_cli["input_contract"] == {
        "schema_version": "auxiliary_monitor_observation_input_v0",
        "result_hash": {
            "required": True,
            "environment_variable": "LOOPX_MONITOR_RESULT_HASH",
            "source": "fresh_external_observation_digest",
        },
        "material_change": {
            "required": True,
            "unchanged_command_key": "command",
            "changed_command_key": "material_change_command",
        },
    }


def test_due_watch_only_monitor_without_turn_binding_is_not_executable() -> None:
    items = _monitor_and_advancement()
    items[0].update({"todo_id": "todo_watch_due", "watch_only": "true"})
    items[1]["todo_id"] = "todo_advancement"

    guard = build_quota_should_run(
        _status(agent_todo_items=items),
        goal_id=GOAL_ID,
    )

    auxiliary_cli = guard["interaction_contract"]["cli_channel"][
        "auxiliary_monitor_poll"
    ]
    assert auxiliary_cli["availability"] == "turn_binding_required"
    assert auxiliary_cli["reason_code"] == (
        "auxiliary_monitor_turn_instance_id_missing"
    )
    assert "command" not in auxiliary_cli
    assert "material_change_command" not in auxiliary_cli


def test_watch_only_priority_cannot_hide_an_ordinary_due_monitor() -> None:
    items = _monitor_and_advancement()
    items[0].update(
        {
            "todo_id": "todo_watch_due",
            "watch_only": "true",
        }
    )
    items.insert(
        1,
        {
            "index": 3,
            "todo_id": "todo_ordinary_due",
            "text": "[P1] Poll the ordinary due dependency.",
            "role": "agent",
            "status": "open",
            "priority": "P1",
            "task_class": "continuous_monitor",
            "action_kind": "monitor",
            "next_due_at": PAST_DUE_AT,
        },
    )
    items[-1].update(
        {
            "priority": "P2",
            "text": "[P2] Advance the bounded product slice.",
        }
    )
    payload = _status(agent_todo_items=items)

    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]

    assert lane["lane"] == "continuous_monitor"
    assert lane["selected_todo_id"] == "todo_ordinary_due"
    assert lane["monitor_due_items"][0]["todo_id"] == "todo_ordinary_due"
    assert lane.get("auxiliary_monitor_poll") is None


def test_receipt_bound_advancement_retains_auxiliary_due_monitor_context() -> None:
    due_monitor = {
        "todo_id": "todo_due_monitor",
        "task_class": "continuous_monitor",
        "status": "open",
    }
    preserved = preserve_heartbeat_receipt_bound_work_lane(
        {
            "schema_version": "work_lane_contract_v1",
            "lane": "continuous_monitor",
            "obligation": "attempt_due_monitor",
            "must_attempt_work": True,
            "monitor_kind": "todo_monitor_due",
            "monitor_due_count": 1,
            "monitor_due_items": [due_monitor],
        },
        selected_todo={
            "todo_id": "todo_bound_advancement",
            "task_class": "advancement_task",
            "selection_binding": "heartbeat_receipt",
        },
    )

    assert preserved is not None
    assert preserved["selected_todo_id"] == "todo_bound_advancement"
    assert preserved["monitor_due_items"] == [due_monitor]
    assert "auxiliary_monitor_observation_allowed" in preserved["reason_codes"]
    assert preserved["monitor_policy"] == (
        "auxiliary_no_spend_observation_then_continue_bound_todo"
    )


def test_quiet_monitor_explains_blocked_non_monitor_todos() -> None:
    payload = _status(
        agent_todo_items=[
            {
                "index": 1,
                "text": "[P0] Wait for the blocked product dependency.",
                "role": "agent",
                "status": "blocked",
                "priority": "P0",
                "task_class": "advancement_task",
            },
            {
                "index": 2,
                "text": "[P1] Preserve the explicit blocker.",
                "role": "agent",
                "status": "blocked",
                "priority": "P1",
                "task_class": "blocker",
            },
            {
                "index": 3,
                "text": "[P3] Check the dependency next week.",
                "role": "agent",
                "status": "open",
                "priority": "P3",
                "task_class": "continuous_monitor",
                "action_kind": "monitor",
                "next_due_at": "2999-01-01T00:00:00+00:00",
            },
        ],
        next_action="Wait for a material dependency transition.",
    )

    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]

    assert guard["effective_action"] == "monitor_quiet_skip"
    assert lane["non_runnable_non_monitor_count"] == 2
    assert lane["reason_codes"] == ["non_runnable_non_monitor_todos_present"]
    assert "no executable advancement todo is runnable" in guard["reason"]
    assert "all visible open agent todos are monitor-class" not in guard["reason"]


def test_true_monitor_only_lane_keeps_existing_reason_code() -> None:
    payload = _status(
        agent_todo_items=[
            {
                "index": 1,
                "text": "[P3] Check the dependency next week.",
                "role": "agent",
                "status": "open",
                "priority": "P3",
                "task_class": "continuous_monitor",
                "action_kind": "monitor",
                "next_due_at": "2999-01-01T00:00:00+00:00",
            },
        ],
        next_action="Wait for a material dependency transition.",
    )

    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]

    assert guard["effective_action"] == "monitor_quiet_skip"
    assert lane["non_runnable_non_monitor_count"] == 0
    assert lane["reason_codes"] == ["monitor_todo_only"]


def test_work_lane_context_progress_scope_sources() -> None:
    payload = _status(
        agent_todo_items=_monitor_and_advancement(),
        status="side_bypass_dependency_observation",
        next_action="Observe dependency state and then advance backlog if unchanged.",
    )
    item = payload["attention_queue"]["items"][0]

    assert item_progress_scope(item) == "dependency_observation"
    assert latest_run_progress_scope(
        {"classification": "runner_dependency_observed"}
    ) == "dependency_observation"
    assert latest_run_progress_scope(
        {
            "classification": "runner_dependency_observed",
            "progress_scope": "primary_goal",
        }
    ) == "primary_goal"
