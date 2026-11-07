#!/usr/bin/env python3
"""Smoke-test quota routing for open todos gated by resume_when."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import (  # noqa: E402
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import parse_active_state_todos  # noqa: E402


GOAL_ID = "resume-gated-open-todo-fixture"
AGENT_ID = "codex-product-capability"
PRIMARY_AGENT = "codex-main-control"
BLOCKING_TODO_ID = "todo_projection_refresh"
GATED_TODO_ID = "todo_projection_wording"
FALLBACK_TODO_ID = "todo_catalog_canary"
ARCHIVED_DONE_TODO_ID = "todo_archived_pr_merge"
ARCHIVE_GATED_MONITOR_ID = "todo_archive_gated_monitor"
READY_DEFERRED_ID = "todo_ready_deferred_p0"
MATERIAL_MONITOR_ID = "todo_material_monitor"
MATERIAL_WAIT_ID = "todo_material_wait"
GATED_ACTION = "[P0] Review refreshed projection wording."
FALLBACK_ACTION = "[P1] Continue catalog-driven product canary coverage."
ARCHIVE_MONITOR_ACTION = "[P1] Monitor product refactor/catalog canary continuation."
APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


def build_agent_todos(*, prerequisite_status: str) -> dict:
    agent_todos = quota_todo_summary(
        [
            quota_todo_item(
                todo_id=BLOCKING_TODO_ID,
                index=1,
                text="[P0] Refresh the projection prerequisite.",
                status=prerequisite_status,
                task_class="continuous_monitor",
                claimed_by="codex-side-bypass",
            ),
            quota_todo_item(
                todo_id=GATED_TODO_ID,
                index=2,
                text=GATED_ACTION,
                claimed_by=AGENT_ID,
                required_capabilities=["shell"],
                resume_when=f"todo_done:{BLOCKING_TODO_ID}",
            ),
            quota_todo_item(
                todo_id=FALLBACK_TODO_ID,
                index=3,
                priority="P1",
                text=FALLBACK_ACTION,
                claimed_by=AGENT_ID,
                required_capabilities=["shell"],
            ),
        ],
        role="agent",
    )
    return agent_todos


def status_payload(
    agent_todos: dict,
    *,
    next_action: str,
    user_todos: dict | None = None,
) -> dict:
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": [PRIMARY_AGENT, "codex-side-bypass", AGENT_ID],
    }
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="resume_gated_open_todo_fixture",
        recommended_action=next_action,
        next_action=next_action,
        agent_todos=agent_todos,
        user_todos=user_todos,
        coordination=coordination,
    )


def selected_todo_id(payload: dict) -> str:
    return payload["agent_lane_next_action"]["todo_id"]


def runnable_todo_ids(payload: dict) -> list[str]:
    return [
        item["todo_id"]
        for item in payload["capability_gate"]["runnable_candidates"]
    ]


def assert_not_ready_open_resume_todo_is_not_executable() -> None:
    agent_todos = build_agent_todos(prerequisite_status="open")
    gated = next(
        item for item in agent_todos["backlog_items"] if item["todo_id"] == GATED_TODO_ID
    )
    assert gated["resume_ready"] is False, gated
    executable_ids = {
        item["todo_id"] for item in agent_todos["executable_backlog_items"]
    }
    assert GATED_TODO_ID not in executable_ids, agent_todos
    assert FALLBACK_TODO_ID in executable_ids, agent_todos

    quota_payload = build_quota_should_run(
        status_payload(agent_todos, next_action=GATED_ACTION),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["normal_delivery_allowed"] is True, quota_payload
    assert selected_todo_id(quota_payload) == FALLBACK_TODO_ID, quota_payload
    assert GATED_TODO_ID not in runnable_todo_ids(quota_payload), quota_payload
    assert quota_payload["recommended_action"] == FALLBACK_ACTION, quota_payload


def assert_ready_open_resume_todo_can_run() -> None:
    agent_todos = build_agent_todos(prerequisite_status="done")
    gated = next(
        item for item in agent_todos["backlog_items"] if item["todo_id"] == GATED_TODO_ID
    )
    assert gated["resume_ready"] is True, gated
    executable_ids = {
        item["todo_id"] for item in agent_todos["executable_backlog_items"]
    }
    assert GATED_TODO_ID in executable_ids, agent_todos

    quota_payload = build_quota_should_run(
        status_payload(agent_todos, next_action=GATED_ACTION),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert quota_payload["should_run"] is True, quota_payload
    assert selected_todo_id(quota_payload) == GATED_TODO_ID, quota_payload
    assert runnable_todo_ids(quota_payload)[0] == GATED_TODO_ID, quota_payload
    assert quota_payload["recommended_action"] == GATED_ACTION, quota_payload


def assert_monitor_generation_resumes_without_boolean_or_replay_drift() -> None:
    def summary(generation: int, *, material_change: bool) -> dict:
        return quota_todo_summary(
            [
                quota_todo_item(
                    todo_id=MATERIAL_MONITOR_ID,
                    index=1,
                    text="[P0] Poll the external review lifecycle.",
                    task_class="continuous_monitor",
                    claimed_by=AGENT_ID,
                    material_change=material_change,
                    material_change_generation=generation,
                    watch_only=True,
                ),
                quota_todo_item(
                    todo_id=MATERIAL_WAIT_ID,
                    index=2,
                    text="[P0] Resume the externally reviewed slice.",
                    claimed_by=AGENT_ID,
                    required_capabilities=["shell"],
                    resume_when=f"monitor_changed:{MATERIAL_MONITOR_ID}",
                    resume_monitor_generation=4,
                ),
                quota_todo_item(
                    todo_id=FALLBACK_TODO_ID,
                    index=3,
                    priority="P1",
                    text=FALLBACK_ACTION,
                    claimed_by=AGENT_ID,
                    required_capabilities=["shell"],
                ),
            ],
            role="agent",
        )

    same_generation = summary(4, material_change=True)
    waiting = next(
        item
        for item in same_generation["backlog_items"]
        if item["todo_id"] == MATERIAL_WAIT_ID
    )
    assert waiting["resume_ready"] is False, waiting
    assert waiting["resume_condition"]["generation_fence"] == (
        "strictly_greater_than_baseline"
    ), waiting
    same_quota = build_quota_should_run(
        status_payload(same_generation, next_action=waiting["text"]),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert selected_todo_id(same_quota) == FALLBACK_TODO_ID, same_quota

    next_generation = summary(5, material_change=False)
    resumed = next(
        item
        for item in next_generation["backlog_items"]
        if item["todo_id"] == MATERIAL_WAIT_ID
    )
    assert resumed["resume_ready"] is True, resumed
    resumed_quota = build_quota_should_run(
        status_payload(next_generation, next_action=resumed["text"]),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert selected_todo_id(resumed_quota) == MATERIAL_WAIT_ID, resumed_quota


def assert_scoped_monitor_generation_ignores_unrelated_case_changes() -> None:
    scope = "case-a/run-1"
    scope_digest = hashlib.sha256(scope.encode()).hexdigest()[:24]

    def summary(scoped_generation: int, global_generation: int) -> dict:
        return quota_todo_summary(
            [
                quota_todo_item(
                    todo_id=MATERIAL_MONITOR_ID,
                    index=1,
                    text="[P0] Poll the benchmark campaign.",
                    task_class="continuous_monitor",
                    claimed_by=AGENT_ID,
                    material_change_generation=global_generation,
                    material_change_scope_generations=(
                        f"{scope_digest}:{scoped_generation}"
                    ),
                    watch_only=True,
                ),
                quota_todo_item(
                    todo_id=MATERIAL_WAIT_ID,
                    index=2,
                    text="[P0] Reconcile case-a run-1 after its terminal score.",
                    claimed_by=AGENT_ID,
                    required_capabilities=["shell"],
                    resume_when=f"monitor_changed:{MATERIAL_MONITOR_ID}@{scope}",
                    resume_monitor_generation=4,
                ),
                quota_todo_item(
                    todo_id=FALLBACK_TODO_ID,
                    index=3,
                    priority="P1",
                    text=FALLBACK_ACTION,
                    claimed_by=AGENT_ID,
                    required_capabilities=["shell"],
                ),
            ],
            role="agent",
        )

    unrelated = summary(4, 9)
    waiting = next(
        item for item in unrelated["backlog_items"]
        if item["todo_id"] == MATERIAL_WAIT_ID
    )
    assert waiting["resume_ready"] is False, waiting
    assert waiting["resume_condition"]["material_change_generation"] == 9, waiting
    assert waiting["resume_condition"]["scoped_material_change_generation"] == 4, waiting
    unrelated_quota = build_quota_should_run(
        status_payload(unrelated, next_action=waiting["text"]),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert selected_todo_id(unrelated_quota) == FALLBACK_TODO_ID, unrelated_quota

    matched = summary(10, 10)
    resumed = next(
        item for item in matched["backlog_items"]
        if item["todo_id"] == MATERIAL_WAIT_ID
    )
    assert resumed["resume_ready"] is True, resumed
    matched_quota = build_quota_should_run(
        status_payload(matched, next_action=resumed["text"]),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert selected_todo_id(matched_quota) == MATERIAL_WAIT_ID, matched_quota


def assert_ready_deferred_p0_preempts_open_p1_for_successor_replan() -> None:
    deferred_action = "[P0] Resume the validated release successor."
    agent_todos = quota_todo_summary(
        [
            quota_todo_item(
                todo_id=BLOCKING_TODO_ID,
                index=1,
                text="[P0] Complete the release prerequisite.",
                status="done",
                claimed_by=PRIMARY_AGENT,
            ),
            quota_todo_item(
                todo_id=READY_DEFERRED_ID,
                index=2,
                text=deferred_action,
                status="deferred",
                claimed_by=AGENT_ID,
                resume_when=f"todo_done:{BLOCKING_TODO_ID}",
            ),
            quota_todo_item(
                todo_id=FALLBACK_TODO_ID,
                index=3,
                priority="P1",
                text=FALLBACK_ACTION,
                claimed_by=AGENT_ID,
                required_capabilities=["shell"],
            ),
        ],
        role="agent",
    )
    quota_payload = build_quota_should_run(
        status_payload(agent_todos, next_action=FALLBACK_ACTION),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert quota_payload["decision"] == "successor_replan_required", quota_payload
    assert quota_payload["normal_delivery_allowed"] is False, quota_payload
    assert quota_payload.get("agent_lane_next_action") is None, quota_payload
    frontier = quota_payload["agent_scope_frontier"]
    assert frontier["priority_preemption"] is True, frontier
    assert frontier["deferred_resume_candidates"][0]["todo_id"] == READY_DEFERRED_ID, frontier
    assert quota_payload["selected_todo"]["todo_id"] == READY_DEFERRED_ID, quota_payload
    assert quota_payload["selected_todo"]["source"] == (
        "agent_scope_frontier.deferred_resume_candidates"
    ), quota_payload
    assert quota_payload["goal_frontier_projection"]["deferred_successors"][
        "top_ready_todo_id"
    ] == READY_DEFERRED_ID, quota_payload

    # The bounded display lane and the lossless candidate lane are two
    # projections of the same canonical Todo, and the explicit candidate lane
    # owns the row when both carry it. Move the priority in every projection so
    # the fixture still models one real Todo instead of two different ones.
    for lane in ("deferred_items", "deferred_resume_candidates"):
        for item in agent_todos.get(lane, []):
            item["priority"] = "P1"
    equal_priority = build_quota_should_run(
        status_payload(agent_todos, next_action=FALLBACK_ACTION),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert equal_priority["decision"] == "run", equal_priority
    assert equal_priority["selected_todo"]["todo_id"] == FALLBACK_TODO_ID, equal_priority


def assert_nonblocking_user_action_preserves_successor_replan() -> None:
    deferred_action = "[P0] Resume the validated release successor."
    agent_todos = quota_todo_summary(
        [
            quota_todo_item(
                todo_id=BLOCKING_TODO_ID,
                index=1,
                text="[P0] Complete the release prerequisite.",
                status="done",
                claimed_by=PRIMARY_AGENT,
            ),
            quota_todo_item(
                todo_id=READY_DEFERRED_ID,
                index=2,
                text=deferred_action,
                status="deferred",
                claimed_by=AGENT_ID,
                resume_when=f"todo_done:{BLOCKING_TODO_ID}",
            ),
        ],
        role="agent",
    )
    user_todos = quota_todo_summary(
        [
            quota_todo_item(
                todo_id="todo_optional_user_action",
                index=3,
                role="user",
                task_class="user_action",
                text="Review the optional setting.",
                bound_agent=AGENT_ID,
            )
        ],
        role="user",
    )
    quota_payload = build_quota_should_run(
        status_payload(
            agent_todos,
            next_action=deferred_action,
            user_todos=user_todos,
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )

    assert quota_payload["decision"] == "successor_replan_required", quota_payload
    assert quota_payload["selected_todo"]["todo_id"] == READY_DEFERRED_ID, quota_payload
    obligation = quota_payload["execution_obligation"]
    contract = quota_payload["interaction_contract"]
    assert obligation["must_attempt_work"] is True, quota_payload
    assert obligation["delivery_allowed"] is False, quota_payload
    assert contract["mode"] == "successor_replan_required", contract
    assert contract["user_channel"]["action_required"] is False, contract
    assert contract["user_channel"]["notify"] == "NOTIFY", contract
    assert contract["user_channel"]["non_blocking"] is True, contract
    assert contract["agent_channel"]["must_attempt"] is True, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is False, contract
    assert "response_plan" not in contract, contract
    assert "loopx todo update" in contract["cli_channel"]["next_cli_actions"][0], contract
    assert READY_DEFERRED_ID in contract["cli_channel"]["next_cli_actions"][0], contract
    scheduler = quota_payload["scheduler_hint"]
    assert scheduler["action"] == "run_now", scheduler
    assert scheduler["cadence_class"] == "active_work", scheduler


def assert_archived_done_resume_target_wakes_claimed_monitor() -> None:
    fields = parse_active_state_todos(
        "# Active Goal State\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {ARCHIVE_MONITOR_ACTION}\n"
        "  <!-- loopx:todo "
        f"todo_id={ARCHIVE_GATED_MONITOR_ID} "
        "status=open "
        "task_class=continuous_monitor "
        "claimed_by=codex-product-capability "
        f"resume_when=todo_done:{ARCHIVED_DONE_TODO_ID} "
        "cadence=6h "
        "next_due_at=2026-01-01T00:00:00+00:00 "
        "-->\n\n"
        "## Completed Work Archive\n\n"
        "- [x] [P0] Merge predecessor PR.\n"
        "  <!-- loopx:todo "
        f"todo_id={ARCHIVED_DONE_TODO_ID} "
        "status=done "
        "task_class=advancement_task "
        "claimed_by=codex-main-control "
        f"unblocks_todo_id={ARCHIVE_GATED_MONITOR_ID} "
        "-->\n"
    )
    agent_todos = fields["agent_todos"]
    active_ids = {item["todo_id"] for item in agent_todos["items"]}
    assert ARCHIVE_GATED_MONITOR_ID in active_ids, agent_todos
    assert ARCHIVED_DONE_TODO_ID not in active_ids, agent_todos

    monitor = next(
        item for item in agent_todos["items"] if item["todo_id"] == ARCHIVE_GATED_MONITOR_ID
    )
    assert monitor["resume_ready"] is True, monitor
    assert monitor["resume_condition"]["target_status"] == "done", monitor
    assert monitor["resume_condition"]["target_archive_state"] == "archive", monitor

    monitor_open_ids = {
        item["todo_id"] for item in agent_todos["monitor_open_items"]
    }
    assert ARCHIVE_GATED_MONITOR_ID in monitor_open_ids, agent_todos
    assert agent_todos["monitor_due_count"] == 1, agent_todos

    quota_payload = build_quota_should_run(
        status_payload(agent_todos, next_action=ARCHIVE_MONITOR_ACTION),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    summary = quota_payload["agent_todo_summary"]
    current_agent_monitor_ids = {
        item["todo_id"] for item in summary["current_agent_claimed_monitor_items"]
    }
    assert ARCHIVE_GATED_MONITOR_ID in current_agent_monitor_ids, quota_payload
    assert summary["current_agent_claimed_monitor_count"] == 1, quota_payload
    assert summary["monitor_due_count"] == 1, quota_payload


def main() -> int:
    assert_not_ready_open_resume_todo_is_not_executable()
    assert_ready_open_resume_todo_can_run()
    assert_monitor_generation_resumes_without_boolean_or_replay_drift()
    assert_scoped_monitor_generation_ignores_unrelated_case_changes()
    assert_ready_deferred_p0_preempts_open_p1_for_successor_replan()
    assert_nonblocking_user_action_preserves_successor_replan()
    assert_archived_done_resume_target_wakes_claimed_monitor()
    print("quota-resume-gated-open-todo-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
