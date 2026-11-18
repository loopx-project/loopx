#!/usr/bin/env python3
"""Smoke-test derived terminal no-follow-up shutdown across quota and scheduler."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.goals.goal_frontier import (  # noqa: E402
    goal_frontier_is_terminal_no_followup,
)
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import compact_todo_group  # noqa: E402


GOAL_ID = "terminal-no-followup-fixture"
AGENT_ID = "codex-main-control"
APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


def completed_todos(*, role: str, count: int = 1) -> dict:
    summary = compact_todo_group(
        [
            {
                "index": index,
                "todo_id": f"todo_completed_{role}_{index}",
                "text": "Complete the bounded fixture work.",
                "role": role,
                "status": "done",
                "done": True,
                "priority": "P1",
                "task_class": "advancement_task" if role == "agent" else "user_action",
                "no_followup": True,
                "claimed_by": AGENT_ID if role == "agent" else None,
            }
            for index in range(1, count + 1)
        ],
        source_section="Agent Todo" if role == "agent" else "User Todo",
        role=role,
    )
    assert summary is not None
    return summary


def status_payload() -> dict:
    user_todos = completed_todos(role="user")
    agent_todos = completed_todos(role="agent")
    project_asset = {
        "next_action": "No further action is required.",
    }
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": [AGENT_ID],
    }
    quota = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 10,
        "spent_slots": 0,
        "state": "eligible",
        "reason": "eligible fixture",
    }
    return {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "active",
                    "waiting_on": "codex",
                    "severity": "active",
                    "source": "active_state",
                    "recommended_action": "No further action is required.",
                    "quota": quota,
                    "user_todos": user_todos,
                    "agent_todos": agent_todos,
                    "project_asset": project_asset,
                    "coordination": coordination,
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "registry_member": True,
                    "status": "active",
                    "adapter_kind": "harness_self_improvement",
                    "adapter_status": "connected-read-only",
                    "quota": quota,
                    "coordination": coordination,
                    "latest_runs": [],
                }
            ]
        },
    }


def assert_terminal_guard_stops_recurring_automation() -> None:
    guard = build_quota_should_run(
        status_payload(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert guard["status"] == "active", guard
    assert guard["state"] == "terminal_no_followup", guard
    assert guard["quota"]["state"] == "terminal_no_followup", guard
    assert guard["should_run"] is False, guard
    assert guard["normal_delivery_allowed"] is False, guard
    assert guard["effective_action"] == "terminal_no_followup", guard
    assert guard["decision"] == "skip", guard
    frontier = guard["goal_frontier_projection"]
    assert frontier["terminal_state"] == {
        "schema_version": "goal_terminal_state_v0",
        "kind": "no_followup",
        "derived": True,
        "source": "validated_goal_closure",
    }, guard
    assert frontier["source_completeness"]["user_todos"] == "valid", guard
    assert frontier["source_completeness"]["agent_todos"] == "valid", guard
    assert guard["execution_obligation"]["must_attempt_work"] is False, guard
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is False, guard
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is True, guard

    liveness = guard["automation_liveness"]
    assert liveness["automation_action"] == "stop_terminal_no_followup", guard
    assert liveness["keep_active"] is False, guard
    assert liveness["pause_allowed"] is True, guard

    scheduler = guard["scheduler_hint"]
    assert scheduler["action"] == "stop_until_explicit_resume", guard
    assert "codex_app" not in scheduler, scheduler
    assert scheduler["app_automation"]["apply"] == "pause_or_delete_current_heartbeat_if_possible", guard
    assert scheduler["app_automation"]["host_action_required"] is True, guard
    assert scheduler["app_automation"]["attempt_limit"] == 1, guard
    assert scheduler["app_automation"]["verify_host_result"] is True, guard
    assert scheduler["app_automation"]["ack_required"] is False, guard
    assert scheduler["unchanged_poll"]["codex_cli_tui"] == "exit", guard


def assert_truncated_display_uses_full_source_proof() -> None:
    payload = status_payload()
    agent_todos = completed_todos(role="agent", count=13)
    assert len(agent_todos["items"]) < agent_todos["total_count"], agent_todos
    payload["attention_queue"]["items"][0]["agent_todos"] = agent_todos
    guard = build_quota_should_run(payload, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert guard["effective_action"] == "terminal_no_followup", guard


def assert_not_terminal(payload: dict, *, label: str) -> None:
    guard = build_quota_should_run(payload, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert guard.get("effective_action") != "terminal_no_followup", (label, guard)
    assert guard.get("automation_liveness", {}).get("automation_action") != "stop_terminal_no_followup", (label, guard)
    assert guard.get("scheduler_hint", {}).get("action") != "stop_until_explicit_resume", (label, guard)


def assert_missing_or_malformed_sources_fail_closed() -> None:
    for field in ("user_todos", "agent_todos"):
        payload = status_payload()
        del payload["attention_queue"]["items"][0][field]
        assert_not_terminal(payload, label=f"missing-{field}")

    for malformed in (None, "bad", True, -1):
        payload = status_payload()
        payload["attention_queue"]["items"][0]["agent_todos"]["open_count"] = malformed
        assert_not_terminal(payload, label=f"malformed-open-count-{malformed!r}")


def assert_contradictory_source_contracts_fail_closed() -> None:
    for field in ("schema_version", "items", "terminal_closure_proof"):
        payload = status_payload()
        del payload["attention_queue"]["items"][0]["agent_todos"][field]
        assert_not_terminal(payload, label=f"missing-agent-{field}")

    payload = status_payload()
    payload["attention_queue"]["items"][0]["agent_todos"]["monitor_open_items"] = "bad"
    assert_not_terminal(payload, label="malformed-monitor-open-items")

    payload = status_payload()
    payload["attention_queue"]["items"][0]["agent_todos"]["items"][0]["status"] = "open"
    payload["attention_queue"]["items"][0]["agent_todos"]["items"][0]["done"] = False
    assert_not_terminal(payload, label="open-item-behind-closed-counts")

    payload = status_payload()
    payload["attention_queue"]["items"][0]["agent_todos"]["items"][0][
        "status"
    ] = "deferred"
    assert_not_terminal(payload, label="deferred-item-behind-closed-counts")

    payload = status_payload()
    agent_todos = payload["attention_queue"]["items"][0]["agent_todos"]
    agent_todos["completed_without_successor_count"] = 1
    agent_todos["completed_without_successor_items"] = []
    assert_not_terminal(payload, label="inconsistent-successor-gap")

    payload = status_payload()
    payload["attention_queue"]["items"][0]["agent_todos"][
        "route_continuation_replan_count"
    ] = 1
    assert_not_terminal(payload, label="inconsistent-route-replan")


def assert_semantic_successor_and_replan_gaps_fail_closed() -> None:
    payload = status_payload()
    agent_todos = compact_todo_group(
        [
            {
                "index": 1,
                "todo_id": "todo_closed_slice",
                "text": "Close one bounded fixture slice.",
                "status": "done",
                "done": True,
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
                "no_followup": True,
            },
            {
                "index": 2,
                "todo_id": "todo_successor_gap",
                "text": "Finish tracked work without recording a successor.",
                "status": "done",
                "done": True,
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
            },
        ],
        source_section="Agent Todo",
        role="agent",
    )
    assert agent_todos is not None
    assert "terminal_closure_proof" not in agent_todos, agent_todos
    payload["attention_queue"]["items"][0]["agent_todos"] = agent_todos
    assert_not_terminal(payload, label="semantic-successor-gap")

    payload = status_payload()
    agent_todos = compact_todo_group(
        [
            {
                "index": 1,
                "todo_id": "todo_route_replan",
                "text": "Replan the stale route before closeout.",
                "status": "done",
                "done": True,
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
                "no_followup": True,
                "route_continuation_replan_required": True,
            }
        ],
        source_section="Agent Todo",
        role="agent",
    )
    assert agent_todos is not None
    assert "terminal_closure_proof" not in agent_todos, agent_todos
    payload["attention_queue"]["items"][0]["agent_todos"] = agent_todos
    assert_not_terminal(payload, label="semantic-route-replan")


def assert_open_work_and_frontier_dimensions_fail_closed() -> None:
    payload = status_payload()
    payload["attention_queue"]["items"][0]["agent_todos"] = compact_todo_group(
        [{
            "index": 1,
            "todo_id": "todo_open_work",
            "text": "Continue bounded fixture work.",
            "role": "agent",
            "status": "open",
            "priority": "P1",
            "task_class": "advancement_task",
            "claimed_by": AGENT_ID,
        }],
        source_section="Agent Todo",
        role="agent",
    )
    assert_not_terminal(payload, label="open-agent-todo")

    for label, item in (
        (
            "standing-monitor",
            {
                "index": 1,
                "todo_id": "todo_monitor",
                "text": "Monitor a fixture signal.",
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "continuous_monitor",
                "claimed_by": AGENT_ID,
                "target_key": "fixture-signal",
                "cadence": "1h",
            },
        ),
        (
            "deferred-successor",
            {
                "index": 1,
                "todo_id": "todo_deferred_successor",
                "text": "Resume the deferred fixture successor.",
                "role": "agent",
                "status": "deferred",
                "priority": "P1",
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
                "resume_when": "todo_done:todo_prerequisite",
            },
        ),
    ):
        payload = status_payload()
        payload["attention_queue"]["items"][0]["agent_todos"] = compact_todo_group(
            [item], source_section="Agent Todo", role="agent"
        )
        assert_not_terminal(payload, label=label)

    payload = status_payload()
    payload["attention_queue"]["items"][0]["project_asset"]["autonomous_replan_obligation"] = {
        "schema_version": "autonomous_replan_obligation_v0",
        "required": True,
        "triggers": [{"kind": "fixture_replan"}],
        "recommended_action": "Create a successor fixture todo.",
    }
    assert_not_terminal(payload, label="replan-obligation")

    payload = status_payload()
    payload["run_history"]["goals"][0]["latest_runs"] = [{
        "classification": "state_refreshed",
        "generated_at": "2026-07-15T00:00:00+00:00",
        "agent_id": AGENT_ID,
        "progress_scope": "agent_lane",
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "agent_id": AGENT_ID,
            "state": "vision_drift_detected",
            "vision_patch": {
                "acceptance_summary": "Close the fixture acceptance gap.",
                "replan_trigger_summary": "A fixture acceptance gap remains.",
            },
            "todo_delta": [],
            "vision_budget": {"schema_version": "goal_vision_budget_v0", "status": "ok"},
        },
    }]
    assert_not_terminal(payload, label="acceptance-gap")

    guard = build_quota_should_run(status_payload(), goal_id=GOAL_ID, agent_id=AGENT_ID)
    terminal_projection = guard["goal_frontier_projection"]
    mutations = {
        "monitor": ("normalized_progress", "agent_monitor_open_count", 1),
        "ready-successor": ("deferred_successors", "ready_count", 1),
        "blocked-successor": ("deferred_successors", "blocked_count", 1),
        "replan": (None, "replan_required", True),
        "acceptance-gap": (None, "acceptance_gaps", [{"kind": "fixture_gap"}]),
        "autonomy-blocker": (None, "autonomy_blockers", ["fixture_blocker"]),
    }
    for label, (section, key, value) in mutations.items():
        projection = deepcopy(terminal_projection)
        if section:
            projection[section][key] = value
        else:
            projection[key] = value
        assert goal_frontier_is_terminal_no_followup(projection=projection) is False, label

    for malformed in (None, "bad", True, -1):
        projection = deepcopy(terminal_projection)
        projection["normalized_progress"]["agent_open_count"] = malformed
        assert goal_frontier_is_terminal_no_followup(projection=projection) is False, malformed


def main() -> int:
    assert_terminal_guard_stops_recurring_automation()
    assert_truncated_display_uses_full_source_proof()
    assert_missing_or_malformed_sources_fail_closed()
    assert_contradictory_source_contracts_fail_closed()
    assert_semantic_successor_and_replan_gaps_fail_closed()
    assert_open_work_and_frontier_dimensions_fail_closed()
    print("quota-terminal-no-followup-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
