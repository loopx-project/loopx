#!/usr/bin/env python3
"""Smoke-test monitor-vs-advancement lane projection in quota should-run."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SMOKE_DIR))
sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.scheduler.execution_context import (  # noqa: E402 - standalone smoke bootstraps repo imports
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.todos.contract import (  # noqa: E402 - standalone smoke bootstraps repo imports
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
)
from loopx.quota import (  # noqa: E402 - standalone smoke bootstraps repo imports
    build_quota_should_run as _build_quota_should_run,
    render_quota_should_run_markdown,
)
from loopx.status import (  # noqa: E402 - standalone smoke bootstraps repo imports
    compact_todo_group,
    compact_post_handoff_run,
    normalize_todo_task_class,
)
from work_lane_contract_fixtures import (  # noqa: E402 - standalone smoke bootstraps repo imports
    FUTURE_DUE_AT,
    GOAL_ID,
    PAST_DUE_AT,
    status_payload,
)


CODEX_APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
)


def build_quota_should_run(*args, **kwargs):
    kwargs.setdefault("scheduler_execution_context", CODEX_APP_SCHEDULER_CONTEXT)
    return _build_quota_should_run(*args, **kwargs)


def assert_dependency_monitor_requires_advancement() -> None:
    guard = build_quota_should_run(
        status_payload(status="side_bypass_dependency_observation"),
        goal_id=GOAL_ID,
    )
    assert guard["should_run"] is True, guard
    lane = guard["work_lane_contract"]
    assert lane["schema_version"] == "work_lane_contract_v1", lane
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["monitor_kind"] == "dependency_observation", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_unless_material_monitor_transition", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["dependency_observation", "open_agent_todo"], lane
    recommendation = guard["heartbeat_recommendation"]
    assert recommendation["recommended_mode"] == "follow_work_lane_contract", recommendation
    assert "dependency_observation_cap" not in recommendation, recommendation
    assert "work_lane_contract" not in recommendation, recommendation
    assert guard["execution_obligation"]["kind"] == "work_lane_contract", guard
    assert guard["execution_obligation"]["contract"] == "work_lane_contract", guard
    assert guard["execution_obligation"]["contract_obligation"] == lane["obligation"], guard
    assert "minimum" not in guard["execution_obligation"], guard
    markdown = render_quota_should_run_markdown(guard)
    assert "work_lane_contract: lane=continuous_monitor next=advancement_task" in markdown, markdown
    assert "obligation=advance_unless_material_monitor_transition" in markdown, markdown
    assert "work_lane_reason_codes: dependency_observation,open_agent_todo" in markdown, markdown


def assert_primary_status_stays_advancement_lane() -> None:
    guard = build_quota_should_run(
        status_payload(status="self_repair_planning_slice"),
        goal_id=GOAL_ID,
    )
    assert guard["should_run"] is True, guard
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", guard
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane


def assert_structured_surface_only_run_requires_outcome_followthrough() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="runner_contract_delivered",
            next_action="Execute the compact runner batch or write the exact blocker.",
            post_handoff_latest_run={
                "classification": "runner_contract_v0_delivered",
                "delivery_batch_scale": "implementation",
                "delivery_outcome": "surface_only",
            },
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["should_run"] is True, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_primary_outcome_or_write_blocker", lane
    assert lane["reason_codes"] == [
        "open_agent_todo",
        "outcome_followthrough_required",
    ], lane
    assert lane["outcome_followthrough"]["latest_delivery_outcome"] == "surface_only", lane
    assert (
        lane["outcome_followthrough"]["latest_delivery_turn_kind"]
        == "contract_only_preparation"
    ), lane
    assert lane["outcome_followthrough"]["accepted_resolution_kinds"] == [
        "product_path_execution",
        "compact_evidence",
        "blocker_writeback",
    ], lane
    assert "contract-only" in lane["action"], lane
    markdown = render_quota_should_run_markdown(guard)
    assert "work_lane_outcome_followthrough:" in markdown, markdown
    assert "latest_kind=contract_only_preparation" in markdown, markdown


def assert_blocker_writeback_satisfies_contract_followthrough() -> None:
    compact = compact_post_handoff_run(
        {
            "classification": "runner_precise_blocker_writeback",
            "delivery_turn_kind": "blocker_writeback",
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_gap",
            "health_check": "precise blocker writeback: remote runner auth unavailable",
        }
    )
    assert compact["delivery_turn_kind"] == "blocker_writeback", compact

    guard = build_quota_should_run(
        status_payload(
            status="runner_blocker_written",
            next_action="Choose the next independent runnable todo after the blocker.",
            post_handoff_latest_run=compact,
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["should_run"] is True, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert "outcome_followthrough" not in lane, lane


def assert_contract_word_alone_does_not_trigger_outcome_followthrough() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="runner_contract_delivered",
            post_handoff_latest_run={
                "classification": "runner_contract_v0_delivered",
                "delivery_batch_scale": "implementation",
                "delivery_outcome": "outcome_progress",
            },
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert "outcome_followthrough" not in lane, lane


def assert_monitor_only_todo_requires_replan_before_quiet() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="typed_task_lane_planning_writeback",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "[P2] Side-bypass dependency monitor: observe public-safe replay state transitions only.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
                {
                    "index": 2,
                    "text": "[P2] Meta canary/readiness observation lane: keep release readiness observable.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["normal_delivery_allowed"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["schema_version"] == "work_lane_contract_v1", lane
    assert lane["lane"] == "advancement_task", lane
    assert lane["monitor_kind"] == "todo_monitor_schedule_gap", lane
    assert lane["next_lane"] == "continuous_monitor", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["monitor_todo_only", "monitor_schedule_metadata_gap"], lane
    assert lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", lane
    assert lane["monitor_schedule_gap_count"] == 2, lane
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", guard
    assert guard["execution_obligation"]["kind"] == "work_lane_contract", guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    assert guard["interaction_contract"]["mode"] == "bounded_delivery", guard
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, guard
    assert "repair the selected continuous_monitor todo" in lane["action"], lane
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert [item["task_class"] for item in first_items] == ["continuous_monitor", "continuous_monitor"], guard
    markdown = render_quota_should_run_markdown(guard)
    assert "work_lane_contract: lane=advancement_task next=continuous_monitor" in markdown, markdown
    assert "obligation=repair_monitor_schedule_metadata" in markdown, markdown
    assert "work_lane_monitor_policy: repair_schedule_metadata_before_quiet_wait" in markdown, markdown


def assert_monitor_only_with_user_todo_surfaces_user_action_without_transition() -> None:
    user_todo = (
        "[P1] Decide whether to approve a no-submit Terminal-Bench setup check "
        "or provide public official-runner output."
    )
    guard = build_quota_should_run(
        status_payload(
            status="typed_task_lane_planning_writeback",
            user_todo_items=[
                {
                    "index": 1,
                    "text": user_todo,
                    "role": "user",
                    "status": "open",
                    "priority": "P1",
                }
            ],
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "[P2] Side-bypass dependency monitor: observe public-safe replay state transitions only.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
                {
                    "index": 2,
                    "text": "[P2] Meta canary/readiness observation lane: keep release readiness observable.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["normal_delivery_allowed"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", lane
    assert lane["must_attempt_work"] is True, lane
    recommendation = guard["heartbeat_recommendation"]
    assert recommendation["recommended_mode"] == "steering_audit_then_one_step", recommendation
    assert recommendation["notify"] == "DONT_NOTIFY", recommendation
    assert "repeat_notification_required" not in recommendation, recommendation
    assert "notify_user_on_open_todo" not in guard, guard
    assert "open_todo_notification_policy" not in guard, guard
    assert guard["requires_user_action"] is True, guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    interaction = guard["interaction_contract"]
    assert interaction["mode"] == "bounded_delivery_with_user_notice", interaction
    assert interaction["user_channel"]["action_required"] is True, interaction
    assert interaction["user_channel"]["notify"] == "NOTIFY", interaction
    assert interaction["agent_channel"]["must_attempt"] is True, interaction
    assert interaction["agent_channel"]["quiet_noop_allowed"] is False, interaction
    assert len(interaction["user_channel"]["actions"]) == 1, interaction
    assert interaction["user_channel"]["actions"][0].startswith(
        "[P1] Decide whether to approve a no-submit Terminal-Bench"
    ), interaction
    assert "protocol_action_packet" not in guard, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "obligation=repair_monitor_schedule_metadata" in markdown, markdown
    assert "work_lane_monitor_policy: repair_schedule_metadata_before_quiet_wait" in markdown, markdown
    assert "heartbeat_repeat_notification_required" not in markdown, markdown
    assert "notify_user_on_open_todo" not in markdown, markdown
    assert "open_todo_notification_policy" not in markdown, markdown
    assert user_todo in markdown, markdown


def assert_blocked_agent_todo_with_user_gate_notifies_without_execution() -> None:
    user_todo = (
        "[P1] Provide GCP_PROJECT plus GCP_SA_KEY before the formal ALE run."
    )
    blocked_agent_todo = (
        "[P1] Prove ALE task-data substrate readiness before any formal local "
        "no-upload run. The remaining formal ALE path is official gs://ale-data-public "
        "staging with credential-file presence only. Do not launch any selected "
        "formal task until official GCS substrate proof is present."
    )
    truncated_blocked_agent_todo = (
        "[P1] Prove ALE task-data substrate readiness before any formal local "
        "no-upload run. The baked sandbox input route is absent, and the current "
        "selected pool has 0 baked-input candidates. The remaining formal ALE path is t..."
    )
    guard = build_quota_should_run(
        status_payload(
            status="ale_substrate_gates_documented_gcs_gate_needed",
            next_action=(
                "Formal ALE remains gated on official gs://ale-data-public substrate proof."
            ),
            user_todo_items=[
                {
                    "index": 1,
                    "text": user_todo,
                    "role": "user",
                    "status": "open",
                    "priority": "P1",
                }
            ],
            agent_todo_items=[
                {
                    "index": 1,
                    "text": blocked_agent_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["normal_delivery_allowed"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "continuous_monitor", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["monitor_todo_only", "monitor_schedule_metadata_gap"], lane
    assert lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", lane
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "continuous_monitor", guard
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    assert guard["interaction_contract"]["mode"] == "bounded_delivery_with_user_notice", guard
    assert guard["interaction_contract"]["user_channel"]["action_required"] is True, guard
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True, guard
    assert guard["requires_user_action"] is True, guard

    truncated_guard = build_quota_should_run(
        status_payload(
            status="ale_substrate_gates_documented_gcs_gate_needed",
            next_action="Formal ALE remains gated on official substrate proof.",
            user_todo_items=[
                {
                    "index": 1,
                    "text": user_todo,
                    "role": "user",
                    "status": "open",
                    "priority": "P1",
                }
            ],
            agent_todo_items=[
                {
                    "index": 1,
                    "text": truncated_blocked_agent_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    truncated_lane = truncated_guard["work_lane_contract"]
    assert truncated_lane["lane"] == "advancement_task", truncated_lane
    assert truncated_lane["obligation"] == "repair_monitor_schedule_metadata", truncated_lane
    assert truncated_lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", truncated_lane
    assert truncated_guard["agent_todo_summary"]["first_open_items"][0]["task_class"] == "continuous_monitor", truncated_guard
    assert truncated_guard["decision"] == "run", truncated_guard
    assert truncated_guard["should_run"] is True, truncated_guard
    assert truncated_guard["effective_action"] == "normal_run", truncated_guard
    assert truncated_guard["execution_obligation"]["must_attempt_work"] is True, truncated_guard


def assert_monitor_only_with_planning_next_action_materializes_advancement() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="benchmark_experiment_report_template",
            next_action=(
                "Post-reporting lane should route to the planning/self-repair capability lane "
                "as the next eligible advancement turn."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "[P2] Side-bypass dependency monitor: observe public-safe replay state transitions only.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
                {
                    "index": 2,
                    "text": "[P2] Meta canary/readiness observation lane: keep release readiness observable.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["schema_version"] == "work_lane_contract_v1", lane
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "materialize_advancement_todo_or_blocker", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["monitor_todo_only", "next_action_requires_advancement"], lane
    assert lane["monitor_policy"] == "material_transition_only", lane
    assert "materialize the planning/self-repair advancement todo" in lane["action"], lane
    assert guard["execution_obligation"]["contract_obligation"] == lane["obligation"], guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "work_lane_contract: lane=advancement_task next=advancement_task" in markdown, markdown
    assert "obligation=materialize_advancement_todo_or_blocker" in markdown, markdown
    assert "work_lane_reason_codes: monitor_todo_only,next_action_requires_advancement" in markdown, markdown


def assert_monitor_only_with_adapter_next_action_materializes_advancement() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="ale_host_codex_e2e_validated",
            next_action=(
                "Continue after ALE host Codex e2e: package the ignored host-Codex "
                "adapter contract into a public-safe generic artifact, or select one "
                "local-material-ready ALE task for a concrete LoopX validation hypothesis."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "[P2] Side-bypass dependency monitor: observe public-safe replay state transitions only.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
                {
                    "index": 2,
                    "text": "[P2] Meta canary/readiness observation lane: keep release readiness observable.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "materialize_advancement_todo_or_blocker", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["monitor_todo_only", "next_action_requires_advancement"], lane
    assert lane["monitor_policy"] == "material_transition_only", lane
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    assert guard["execution_obligation"]["contract_obligation"] == lane["obligation"], guard


def assert_executable_repeat_with_blocker_context_routes_to_advancement() -> None:
    repeat_todo = (
        "Run a fresh full treatment repeat under the validated retrieval-smoke "
        "path; the previous seed is support-blocked and does not unblock the "
        "scorer by itself, but if the repeat completes with real retrieval and "
        "evaluation rows plus a non-empty retrieval trace, rebuild scoring "
        "inputs and scorer validation; if it fails, write the concrete blocker."
    )
    assert normalize_todo_task_class(None, text=repeat_todo) == TODO_TASK_CLASS_ADVANCEMENT
    guard = build_quota_should_run(
        status_payload(
            status="side_bypass_retrieval_smoke_passed",
            next_action=(
                "Run a fresh full treatment repeat, then rebuild scoring inputs "
                "only if the repeat completes with real retrieval and evaluation rows."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": repeat_todo,
                    "role": "agent",
                    "status": "open",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "advancement_task", guard


def assert_monitor_todo_with_executable_next_action_materializes_advancement() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="side_bypass_retrieval_smoke_passed",
            next_action=(
                "Run a fresh full treatment repeat, then rebuild scoring inputs "
                "only if the repeat completes with real retrieval and evaluation rows."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": (
                        "Blocked on owner evidence for the scorer-valid next "
                        "treatment source; the previous seed is support-blocked "
                        "and does not unblock this todo."
                    ),
                    "role": "agent",
                    "status": "open",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "materialize_advancement_todo_or_blocker", lane
    assert lane["reason_codes"] == ["monitor_todo_only", "next_action_requires_advancement"], lane
    assert lane["monitor_policy"] == "material_transition_only", lane
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "continuous_monitor", guard


def assert_structured_todo_lane_registration_beats_text_fallback() -> None:
    blocked_like_todo = (
        "[P0] Blocked on owner evidence for the selected runner source; when "
        "the source is selected, run the benchmark case and write back either "
        "validated results or the concrete blocker."
    )
    assert normalize_todo_task_class(None, text=blocked_like_todo) == TODO_TASK_CLASS_MONITOR
    assert (
        normalize_todo_task_class(
            TODO_TASK_CLASS_ADVANCEMENT,
            text=blocked_like_todo,
            action_kind="run_eval",
        )
        == TODO_TASK_CLASS_ADVANCEMENT
    )
    guard = build_quota_should_run(
        status_payload(
            status="structured_lane_registration_ready",
            next_action="Run the registered benchmark case and write back a result or blocker.",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": blocked_like_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "action_kind": "run_eval",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "advancement_task", guard
    assert first_items[0]["action_kind"] == "run_eval", guard


def assert_structured_monitor_registration_beats_action_text() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="structured_monitor_registration_waiting",
            next_action="Observe the external evidence channel until a material transition is recorded.",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "Run the remote evaluator only after owner evidence arrives.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "continuous_monitor", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", lane
    assert lane["monitor_schedule_gap_count"] == 1, lane
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", guard
    assert guard["interaction_contract"]["mode"] == "bounded_delivery", guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == TODO_TASK_CLASS_MONITOR, guard
    assert first_items[0]["action_kind"] == "monitor", guard


def assert_mixed_monitor_and_advancement_routes_to_advancement() -> None:
    executable_todo = "[P1] Add the typed task class routing smoke fixture."
    guard = build_quota_should_run(
        status_payload(
            status="typed_task_lane_planning_writeback",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "[P2] Side-bypass dependency monitor: observe public-safe replay state transitions only.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                },
                {
                    "index": 2,
                    "text": executable_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["must_attempt_work"] is True, lane
    assert guard["recommended_action"] == executable_todo, guard
    assert guard["interaction_contract"]["agent_channel"]["primary_action"] == executable_todo, guard
    assert "protocol_action_packet" not in guard, guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert [item["task_class"] for item in first_items] == ["advancement_task", "continuous_monitor"], guard


def assert_not_due_monitor_only_waits_quietly_without_replan_delta() -> None:
    guard = build_quota_should_run(
        status_payload(
            status="monitor_schedule_waiting",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": "[P1] Monitor update-note draft PR until the next scheduled check.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                    "next_due_at": FUTURE_DUE_AT,
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "skip", guard
    assert guard["effective_action"] == "monitor_quiet_skip", guard
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["obligation"] == "quiet_until_material_monitor_transition", lane
    assert guard["interaction_contract"]["mode"] == "monitor_quiet_skip", guard
    assert guard["goal_frontier_projection"]["replan_required"] is False, guard
    assert "autonomous_replan_obligation" not in guard, guard
    assert guard["scheduler_hint"]["cadence_class"] == "monitor_wait", guard
    assert guard["agent_todo_summary"]["monitor_due_count"] == 0, guard


def assert_due_monitor_only_requires_attempt() -> None:
    due_todo = "[P1] Monitor update-note draft PR and reschedule after checking."
    guard = build_quota_should_run(
        status_payload(
            status="monitor_schedule_due",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": due_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                    "next_due_at": PAST_DUE_AT,
                    "target_key": "update-note-draft-pr",
                    "cadence": "14d",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["monitor_kind"] == "todo_monitor_due", lane
    assert lane["obligation"] == "attempt_due_monitor", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["monitor_todo_only", "monitor_due"], lane
    assert lane["monitor_due_count"] == 1, lane
    assert lane["selected_next_due_at"] == PAST_DUE_AT, lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 1, guard
    assert guard["goal_frontier_projection"]["replan_required"] is False, guard
    assert guard["interaction_contract"]["mode"] == "bounded_delivery", guard
    assert guard["interaction_contract"]["agent_channel"]["primary_action"] == (
        due_todo
    ), guard
    assert guard["execution_obligation"]["kind"] == "work_lane_contract", guard
    assert guard["execution_obligation"]["contract_obligation"] == "attempt_due_monitor", guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    assert guard["scheduler_hint"]["cadence_class"] == "active_work", guard
    assert "agent_lane_next_action" not in guard, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "work_lane_monitor_due: count=1" in markdown, markdown
    assert "agent_todo_summary: open=1 total=1 monitor_due=1" in markdown, markdown


def assert_due_monitor_lower_priority_does_not_preempt_advancement() -> None:
    due_todo = "[P2] Monitor lower-priority release note draft on its schedule."
    executable_todo = "[P1] Implement the bounded runtime repair slice."
    guard = build_quota_should_run(
        status_payload(
            status="monitor_due_with_higher_priority_advancement",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": due_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                    "next_due_at": PAST_DUE_AT,
                },
                {
                    "index": 2,
                    "text": executable_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["reason_codes"] == ["open_agent_todo", "due_monitor_context"], lane
    assert guard["agent_todo_summary"]["monitor_due_count"] == 1, guard
    assert guard["recommended_action"] == executable_todo, guard
    assert guard["interaction_contract"]["agent_channel"]["primary_action"] == executable_todo, guard


def assert_due_monitor_higher_priority_preempts_advancement() -> None:
    due_todo = "[P0] Monitor the overdue update-note draft PR before feature work."
    executable_todo = "[P1] Implement the bounded runtime repair slice."
    guard = build_quota_should_run(
        status_payload(
            status="monitor_due_preempts_lower_priority_advancement",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": due_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                    "next_due_at": PAST_DUE_AT,
                },
                {
                    "index": 2,
                    "text": executable_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["monitor_kind"] == "todo_monitor_due", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["reason_codes"] == ["monitor_due", "due_monitor_priority_preempts_advancement"], lane
    assert guard["recommended_action"] == due_todo, guard
    assert guard["interaction_contract"]["agent_channel"]["primary_action"] == due_todo, guard
    assert "agent_lane_next_action" not in guard, guard


def assert_lower_priority_executable_before_higher_priority_is_reordered() -> None:
    p2_todo = "[P2] Fold server scheduling into the longer-term roadmap."
    p1_todo = "[P1] Fix first-screen dashboard acceptance before roadmap cleanup."
    guard = build_quota_should_run(
        status_payload(
            status="todo_priority_projection_fixture",
            agent_todo_items=[
                {
                    "index": 1,
                    "text": p2_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                    "task_class": "advancement_task",
                },
                {
                    "index": 2,
                    "text": p1_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    assert guard["decision"] == "run", guard
    assert guard["recommended_action"] == p1_todo, guard
    assert guard["interaction_contract"]["agent_channel"]["primary_action"] == p1_todo, guard
    executable_items = guard["agent_todo_summary"]["first_executable_items"]
    assert [item["priority"] for item in executable_items[:2]] == ["P1", "P2"], guard


def assert_external_monitor_context_recommends_executable_backlog() -> None:
    poll_action = (
        "Agent: continue compact-polling Terminal-Bench train-fasttext until a "
        "terminal compact result/trial reward appears."
    )
    executable_todo = (
        "[P1] Behavior regression suite lane: maintain `regression/` as the "
        "home for LoopX CLI plus real Codex CLI interaction regressions."
    )
    guard = build_quota_should_run(
        status_payload(
            status="benchmark_ledger_running_placeholder_guard",
            next_action=poll_action,
            agent_todo_items=[
                {
                    "index": 67,
                    "text": (
                        "[P0] [P0 monitor] Observe no-upload Terminal-Bench "
                        "train-fasttext using compact process/result markers only."
                    ),
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                },
                {
                    "index": 2,
                    "text": executable_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["reason_codes"] == ["open_agent_todo", "external_monitor_context"], lane
    assert guard["recommended_action"] == executable_todo, guard
    assert poll_action != guard["recommended_action"], guard
    assert guard["interaction_contract"]["agent_channel"]["primary_action"] == (
        "[P1] Behavior regression suite lane"
    ), guard
    assert guard["work_lane_contract"]["lane"] == "advancement_task", guard
    assert "protocol_action_packet" not in guard, guard


def assert_benchmark_readiness_scan_routes_to_advancement() -> None:
    readiness_scan_todo = (
        "[P1] Follow-up benchmark readiness scans while SWE-Marathon is blocked "
        "on local capacity and if the remote GPU route is blocked: AgentIssue-Bench "
        "first, then PerfBench, then SWE-Bench Pro public. Optimize for low "
        "frontier-agent success plus local/Docker reproducibility, objective scoring, "
        "Codex CLI wrapper feasibility, compact public-safe receipts, and an "
        "independent verifier. Do not spend a fresh benchmark execution "
        "quota slot until a setup-readiness scan proves a plausible learning run."
    )
    guard = build_quota_should_run(
        status_payload(
            status="remote_gpu_route_b_runner_plumbing_ready",
            next_action=(
                "Resume benchmark-candidate readiness scanning, starting with "
                "AgentIssue-Bench unless the owner pivots back to an existing route."
            ),
            user_todo_items=[
                {
                    "index": 1,
                    "text": "[P1] Optional local runtime cleanup only if returning to SWE-Marathon.",
                    "role": "user",
                    "status": "open",
                    "priority": "P1",
                }
            ],
            agent_todo_items=[
                {
                    "index": 1,
                    "text": readiness_scan_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "advancement_task", guard


def assert_benchmark_source_preflight_routes_to_advancement() -> None:
    source_preflight_todo = (
        "[P2] Run a sparse no-task TheAgentCompany source preflight unless the "
        "owner first approves a gated route. Exclude `workspaces/tasks`, task "
        "instructions, evaluators, checkpoints, scenarios, setup-script execution, "
        "Docker image pulls/runs, credential-bearing LLM config, trajectories, "
        "screenshots, uploads, leaderboard, submit, hidden refs, and shared-host "
        "workload inspection. The preflight should answer only whether a "
        "Codex-compatible runner wrapper can be designed from public harness code "
        "and docs without consuming task material."
    )
    assert normalize_todo_task_class(None, text=source_preflight_todo) == TODO_TASK_CLASS_ADVANCEMENT
    guard = build_quota_should_run(
        status_payload(
            status="theagentcompany_setup_readiness_ready_env_gate",
            next_action=(
                "TheAgentCompany is setup-readiness ready but environment and "
                "credential gated; next bounded step is a sparse no-task source "
                "preflight unless the owner approves Docker service setup."
            ),
            user_todo_items=[
                {
                    "index": 1,
                    "text": "[P1] Approve TheAgentCompany Docker service setup before a real run.",
                    "role": "user",
                    "status": "open",
                    "priority": "P1",
                }
            ],
            agent_todo_items=[
                {
                    "index": 1,
                    "text": source_preflight_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P2",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "advancement_task", guard


def assert_behavior_regression_suite_routes_to_advancement() -> None:
    regression_todo = (
        "[P1] Behavior regression suite lane: maintain `regression/` as the "
        "home for LoopX CLI plus real Codex CLI interaction regressions. "
        "Add focused cases when a control-plane behavior previously failed or "
        "could strand automation, especially external-evidence waits, "
        "P0-blocked/P1-P2 fallback, no-progress self-repair, compact blocker "
        "writeback, and Codex credential boundary expectations on shared or "
        "remote machines."
    )
    assert normalize_todo_task_class(None, text=regression_todo) == TODO_TASK_CLASS_ADVANCEMENT
    guard = build_quota_should_run(
        status_payload(
            status="regression_suite_lane_only",
            next_action=(
                "Continue the behavior regression suite lane by adding one focused "
                "LoopX CLI plus Codex CLI interaction regression."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": regression_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "advancement_task", guard


def assert_launched_external_observation_does_not_preempt_advancement_backlog() -> None:
    observe_todo = (
        "[P0] Observe launched private no-upload paired pilot for terminal-bench@2.0 / "
        "git-multibranch (run_basename=terminal-bench-git-multibranch-paired-fixture; "
        "baseline pid=111; treatment pid=222): poll compact Harbor materialization "
        "only, inspect case execution status via compact result/blocker markers, and "
        "when both arms close run verifier-attribution review before any repeat or claim."
    )
    backlog_todo = (
        "[P1] Re-rank follow-on benchmark lanes after the first Terminal-Bench paired "
        "pilot or a documented Terminal-Bench blocker."
    )
    guard = build_quota_should_run(
        status_payload(
            status="terminal_bench_git_multibranch_paired_launch_observation_v0",
            next_action=(
                "Observe launched private no-upload terminal-bench@2.0/git-multibranch "
                "paired pilot run_basename=terminal-bench-git-multibranch-paired-fixture "
                "via compact Harbor materialization/process state only; when both arms "
                "close, ingest compact result/blocker and run verifier-attribution review "
                "before any repeat or claim."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": observe_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "continuous_monitor",
                    "action_kind": "terminal_bench_case_observation",
                },
                {
                    "index": 2,
                    "text": backlog_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                    "action_kind": "planning_refresh",
                },
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert guard["actionable_by_codex"] is True, guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["reason_codes"] == [
        "open_agent_todo",
        "external_monitor_context",
    ], lane
    assert "external_evidence_observation" not in guard, guard
    interaction = guard["interaction_contract"]
    assert interaction["mode"] == "bounded_delivery", interaction
    assert interaction["agent_channel"]["must_attempt"] is True, interaction
    assert "Re-rank follow-on benchmark lanes" in interaction["agent_channel"]["primary_action"], interaction


def assert_launch_then_poll_todo_without_handle_routes_to_advancement() -> None:
    launch_repeat_todo = (
        "[P0] Launch a private no-upload paired repeat for terminal-bench@2.0 / "
        "large-scale-text-editing with agent_setup_timeout_multiplier=4 on both "
        "codex-goal-mode baseline and loopx treatment; poll only compact "
        "materialization/result summaries, then ingest and compare before any claim."
    )
    guard = build_quota_should_run(
        status_payload(
            status="terminal_bench_large_scale_repeat_handle_absent_v0",
            next_action=(
                "No observable public launch summary or compact writeback channel "
                "exists yet for the planned paired repeat; launch the private "
                "no-upload repeat before further polling."
            ),
            agent_todo_items=[
                {
                    "index": 1,
                    "text": launch_repeat_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "action_kind": "run_eval",
                }
            ],
        ),
        goal_id=GOAL_ID,
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert "external_evidence_observation" not in guard, guard
    first_items = guard["agent_todo_summary"]["first_open_items"]
    assert first_items[0]["task_class"] == "advancement_task", guard
    assert first_items[0]["action_kind"] == "run_eval", guard


def assert_side_agent_monitor_watch_without_handle_stays_quiet() -> None:
    monitor_todo = (
        "[P0] Observe launched external demo worker via compact public-safe "
        "markers only."
    )
    guard = build_quota_should_run(
        status_payload(
            status="external_demo_worker_launched_v0",
            next_action=(
                "Observe launched external demo worker until a compact public-safe "
                "result marker arrives."
            ),
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": monitor_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                    "claimed_by": "codex-side-bypass",
                    "todo_id": "todo_side_external_observe",
                }
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "run", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "normal_run", guard
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "continuous_monitor", lane
    assert lane["monitor_kind"] == "todo_monitor_schedule_gap", lane
    assert lane["obligation"] == "repair_monitor_schedule_metadata", lane
    assert lane["monitor_policy"] == "repair_schedule_metadata_before_quiet_wait", lane
    assert lane["monitor_schedule_gap_count"] == 1, lane
    assert lane["selected_todo_id"] == "todo_side_external_observe", lane
    assert lane["must_attempt_work"] is True, lane
    assert "external_evidence_observation" not in guard, guard
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    interaction = guard["interaction_contract"]
    assert interaction["mode"] == "bounded_delivery", interaction
    assert interaction["agent_channel"]["must_attempt"] is True, interaction
    assert interaction["agent_channel"]["delivery_allowed"] is True, interaction
    assert interaction["agent_channel"]["quiet_noop_allowed"] is False, interaction


def assert_side_agent_monitor_watch_with_handle_requires_observation() -> None:
    target_key = "external-demo-worker:run-42"
    monitor_todo = (
        "[P0] Observe launched external demo worker via compact public-safe "
        "markers only."
    )
    guard = build_quota_should_run(
        status_payload(
            status="external_demo_worker_launched_v0",
            next_action=(
                "Observe launched external demo worker until a compact public-safe "
                "result marker arrives."
            ),
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": monitor_todo,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "continuous_monitor",
                    "action_kind": "monitor",
                    "claimed_by": "codex-side-bypass",
                    "todo_id": "todo_side_external_observe",
                    "target_key": target_key,
                }
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    lane = guard["work_lane_contract"]
    assert guard["decision"] == "observe", guard
    assert guard["should_run"] is True, guard
    assert guard["effective_action"] == "external_evidence_observe", guard
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["monitor_kind"] == "external_evidence", lane
    assert lane["must_attempt_work"] is True, lane
    observation = guard["external_evidence_observation"]
    assert observation["kind"] == "launched_external_work_monitor", observation
    assert observation["must_attempt_observation"] is True, observation
    assert observation["monitor_handle"]["schema_version"] == "projected_monitor_handle_v0", observation
    assert observation["monitor_handle"]["target_key"] == target_key, observation
    assert observation["monitor_handle"]["todo_id"] == "todo_side_external_observe", observation
    assert observation["monitor_handle"]["claimed_by"] == "codex-side-bypass", observation
    assert observation["monitor_handle"]["target_text"] == monitor_todo, observation
    assert observation["observation_target"] == monitor_todo, observation
    assert guard["execution_obligation"]["must_attempt_work"] is True, guard
    interaction = guard["interaction_contract"]
    assert interaction["mode"] == "external_evidence_observation", interaction
    assert interaction["agent_channel"]["must_attempt"] is True, interaction
    assert interaction["agent_channel"]["quiet_noop_allowed"] is False, interaction


def assert_side_agent_next_action_projects_without_stealing_goal_next_action() -> None:
    primary_action = "[P0] Run the primary benchmark monitor owned by main control."
    side_action = (
        "[P0] Codex CLI TUI continuation: prove the same-open-TUI steering "
        "slice without adding temporary runtime packet fields."
    )
    guard = build_quota_should_run(
        status_payload(
            status="primary_goal_route_active",
            next_action=primary_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": primary_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_primary",
                },
                {
                    "index": 2,
                    "text": side_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "action_kind": "codex_cli_tui_continuation",
                    "claimed_by": "codex-side-bypass",
                    "todo_id": "todo_side_tui",
                    "required_capabilities": ["shell", "filesystem_write"],
                },
                {
                    "index": 3,
                    "text": "[P1] Render the public showcase animation prototype.",
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-side-bypass",
                    "todo_id": "todo_side_showcase",
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    assert guard["recommended_action"] == side_action, guard
    assert guard["agent_identity"]["agent_id"] == "codex-side-bypass", guard
    next_action = guard["agent_lane_next_action"]
    assert next_action["schema_version"] == "agent_lane_next_action_v0", next_action
    assert next_action["agent_id"] == "codex-side-bypass", next_action
    assert "agent_model" not in next_action and "primary_agent" not in next_action, next_action
    assert next_action["todo_id"] == "todo_side_tui", next_action
    assert next_action["selected_by"] == "current_agent_claimed_todo", next_action
    assert next_action["confidence"] == "selected", next_action
    assert next_action["source"] == "capability_gate.runnable_candidates", next_action
    assert next_action["preserves_goal_next_action"] is True, next_action
    route_hint = guard["goal_route_hint"]
    assert route_hint["schema_version"] == "goal_route_hint_v0", route_hint
    assert route_hint["route_decision"] == "run_current_agent_lane", route_hint
    assert route_hint["agent_id"] == "codex-side-bypass", route_hint
    assert route_hint["agent_model"] == "peer_v1" and "primary_agent" not in route_hint, route_hint
    assert route_hint["preserves_goal_next_action"] is True, route_hint
    assert route_hint["goal_next_action_mutation"] == "none", route_hint
    assert route_hint["has_durable_next_action"] is True, route_hint
    assert route_hint["has_latest_run_recommended_action"] is False, route_hint
    assert route_hint["selected_action_differs_from_durable"] is True, route_hint
    assert route_hint["current_agent_next_action"]["todo_id"] == "todo_side_tui", route_hint
    assert route_hint["other_agent_next_actions"][0]["todo_id"] == "todo_primary", route_hint
    assert route_hint["counts"]["current_agent_claimed_advancement_count"] == 2, route_hint
    assert route_hint["counts"]["other_agent_claimed_advancement_count"] == 1, route_hint
    assert guard["capability_gate"]["runnable_candidates"][0]["todo_id"] == "todo_side_tui", guard
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_lane_next_action: todo_id=todo_side_tui" in markdown, markdown
    assert "goal_route_hint: decision=run_current_agent_lane" in markdown, markdown
    assert "goal_route_hint_current_action: todo_id=todo_side_tui" in markdown, markdown
    assert side_action in markdown, markdown
    assert primary_action not in guard["agent_lane_next_action"]["text"], guard


def assert_peer_requires_reassignment_when_only_other_peer_has_claimed_work() -> None:
    primary_action = "[P0] Run SWE-Marathon full-suite polling and record compact results."
    guard = build_quota_should_run(
        status_payload(
            status="side_agent_other_claimed_frontier",
            next_action=primary_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": primary_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_primary_suite",
                    "required_capabilities": ["shell"],
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    assert guard["decision"] == "reassignment_required", guard
    assert guard["should_run"] is False, guard
    assert guard["normal_delivery_allowed"] is False, guard
    assert guard["actionable_by_codex"] is False, guard
    assert guard["effective_action"] == "reassignment_required", guard
    assert "agent_lane_next_action" not in guard, guard
    frontier = guard["agent_scope_frontier"]
    assert frontier["schema_version"] == "agent_scope_frontier_v1", frontier
    assert frontier["action"] == "reassignment_required", frontier
    assert "effective_action" not in frontier, frontier
    assert frontier["agent_id"] == "codex-side-bypass", frontier
    assert "primary_agent" not in frontier, frontier
    assert frontier["candidate_counts"]["current_agent_claimed_advancement_count"] == 0, frontier
    assert frontier["candidate_counts"]["unclaimed_advancement_count"] == 0, frontier
    assert frontier["candidate_counts"]["other_agent_claimed_advancement_count"] == 1, frontier
    assert frontier["other_claimants"] == ["codex-main-control"], frontier
    hint = guard["agent_lane_frontier_hint"]
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0", hint
    assert hint["decision"] == "quiet_noop_blocker", hint
    assert hint["source"] == "agent_scope_frontier", hint
    assert hint["reason_code"] == "blocked_by_other_agent_frontier", hint
    assert hint["target_todo_id"] == "todo_primary_suite", hint
    assert hint["quiet_noop_allowed"] is True, hint
    route_hint = guard["goal_route_hint"]
    assert route_hint["schema_version"] == "goal_route_hint_v0", route_hint
    assert route_hint["route_decision"] == "reassignment_required", route_hint
    assert route_hint["preserves_goal_next_action"] is True, route_hint
    assert route_hint["has_durable_next_action"] is True, route_hint
    assert route_hint["frontier_hint"]["decision"] == "quiet_noop_blocker", route_hint
    assert route_hint["other_agent_next_actions"][0]["todo_id"] == "todo_primary_suite", route_hint
    assert "current_agent_next_action" not in route_hint, route_hint
    assert frontier["frontier_hint"] == hint, frontier
    assert "codex-side-bypass" in guard["recommended_action"], guard
    assert "codex-main-control" in guard["recommended_action"], guard
    assert "SWE-Marathon" not in guard["recommended_action"], guard
    obligation = guard["execution_obligation"]
    assert obligation["must_attempt_work"] is False, obligation
    assert obligation["kind"] == "reassignment_required", obligation
    contract = guard["interaction_contract"]
    assert contract["mode"] == "reassignment_required", contract
    assert contract["agent_channel"]["must_attempt"] is False, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is True, contract
    assert contract["cli_channel"]["spend_after_validation"] is False, contract
    assert "no spend" in contract["cli_channel"]["spend_policy"], contract
    assert "no quota spend" in contract["cli_channel"]["next_cli_actions"][0], contract
    assert "codex-side-bypass has no current/unclaimed" in contract["user_channel"]["reason"], contract
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_scope_frontier: action=reassignment_required" in markdown, markdown
    assert "agent_lane_frontier_hint: decision=quiet_noop_blocker" in markdown, markdown
    assert "quiet_noop_allowed=True" in markdown, markdown


def assert_side_agent_scope_wait_mentions_blocking_owner() -> None:
    primary_action = "[P0] Run SkillsBench matrix and record compact results."
    review_action = (
        "[P1-review] codex-side-bypass review the agent permission replay packet."
    )
    guard = build_quota_should_run(
        status_payload(
            status="value_explorer_side_bypass_review_wait",
            next_action=primary_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": [
                    "codex-main-control",
                    "codex-side-bypass",
                    "codex-value-explorer",
                ],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": primary_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_primary_suite",
                    "required_capabilities": ["shell"],
                },
                {
                    "index": 2,
                    "text": review_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1-review",
                    "task_class": "advancement_task",
                    "action_kind": "side_bypass_review_agent_permission_replay_packet",
                    "claimed_by": "codex-side-bypass",
                    "excluded_agents": ["codex-value-explorer"],
                    "unblocks_todo_id": "todo_value_explorer_delivery",
                    "todo_id": "todo_side_bypass_review",
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-value-explorer",
    )
    assert guard["decision"] == "agent_scope_wait", guard
    assert guard["should_run"] is False, guard
    frontier = guard["agent_scope_frontier"]
    assert frontier["action"] == "agent_scope_wait", frontier
    assert frontier["blocking_handoff_claimants"] == ["codex-side-bypass"], frontier
    assert frontier["candidate_counts"]["other_agent_claimed_advancement_count"] == 2
    assert "codex-side-bypass" in guard["recommended_action"], guard
    assert "codex-main-control" not in guard["recommended_action"], guard
    assert "blocking handoff" in guard["interaction_contract"]["agent_channel"]["primary_action"], guard


def assert_excluded_agent_waits_for_dependency_handoff() -> None:
    route_review_gate = {
        "index": 1,
        "text": "[P0-review] Review the delivered visible launch slice before the route advances.",
        "role": "agent",
        "status": "open",
        "priority": "P0-review",
        "task_class": "advancement_task",
        "action_kind": "route_review_gate",
        "claimed_by": "codex-main-control",
        "excluded_agents": ["codex-side-bypass"],
        "todo_id": "todo_route_review_gate",
        "unblocks_todo_id": "todo_delivered_visible_launch_slice",
        "route_id": "auto_research_e2e",
        "route_continuation_replan_required": True,
        "route_continuation_reason": (
            "the delivered slice is review-gated, but the same route has an "
            "independent next e2e slice that must be projected as a todo"
        ),
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "total_count": 1,
        "open_count": 1,
        "done_count": 0,
        "first_open_items": [route_review_gate],
        "items": [route_review_gate],
    }
    payload = status_payload(
        status="route_continuation_review_gated",
        has_agent_todo=False,
        next_action="Continue the visible launch e2e route after the review gate.",
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["codex-main-control", "codex-side-bypass"],
        },
    )
    item = payload["attention_queue"]["items"][0]
    item["project_asset"]["agent_todos"] = agent_todos
    item["agent_todos"] = agent_todos

    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    assert guard["decision"] == "agent_scope_wait", guard
    assert guard["should_run"] is False, guard
    assert guard["actionable_by_codex"] is False, guard
    assert "agent_lane_next_action" not in guard, guard
    frontier = guard["agent_scope_frontier"]
    assert frontier["action"] == "agent_scope_wait", frontier
    assert frontier["quiet_noop_allowed"] is True, frontier
    assert frontier["blocking_handoff_claimants"] == ["codex-main-control"], frontier
    assert guard["agent_todo_summary"]["current_agent_route_continuation_replan_count"] == 0, guard
    assert guard["agent_todo_summary"]["current_agent_handoff_gate_count"] == 1, guard
    hint = guard["agent_lane_frontier_hint"]
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0", hint
    assert hint["decision"] == "quiet_noop_blocker", hint
    assert hint["quiet_noop_allowed"] is True, hint
    contract = guard["interaction_contract"]
    assert contract["mode"] == "agent_scope_wait", contract
    assert contract["agent_channel"]["must_attempt"] is False, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is True, contract


def assert_scoped_user_gate_does_not_steal_other_agent_fallback() -> None:
    gated_action = (
        "[P0] Sync the local long-horizon protocol packet into the approved "
        "Lark Kanban target."
    )
    primary_fallback = "[P0] Review and merge PR #623 for follow-up capture."
    guard = build_quota_should_run(
        status_payload(
            status="side_agent_scoped_gate_other_agent_fallback",
            next_action=gated_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-product-capability"],
            },
            user_todo_items=[
                {
                    "index": 1,
                    "text": (
                        "[P0-user] Choose the Lark Kanban target for local "
                        "long-horizon protocol management."
                    ),
                    "role": "user",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "user_gate",
                    "action_kind": "lark_kanban_target_decision",
                    "todo_id": "todo_lark_gate",
                    "blocks_agent": "codex-main-control",
                },
            ],
            agent_todo_items=[
                {
                    "index": 1,
                    "text": gated_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "action_kind": "lark_kanban_target_decision",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_product_kanban_sync",
                    "required_capabilities": ["shell"],
                },
                {
                    "index": 2,
                    "text": primary_fallback,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "action_kind": "review",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_primary_review_623",
                    "required_capabilities": ["shell"],
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-product-capability",
    )
    assert "scoped_user_gate_fallback" not in guard, guard
    assert guard["safe_bypass_kind"] != "scoped_user_gate_fallback", guard
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is False, guard
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is False, guard
    assert "todo_primary_review_623" not in guard.get("recommended_action", ""), guard
    markdown = render_quota_should_run_markdown(guard)
    assert "scoped_user_gate_fallback" not in markdown, markdown


def assert_side_agent_replans_when_deferred_successor_is_ready() -> None:
    deferred_action = "[P1] Continue the issue meta surface fixture implementation."
    payload = status_payload(
        status="side_agent_ready_deferred_successor",
        has_agent_todo=False,
        next_action="Continue the ready deferred issue-surface successor.",
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["codex-main-control", "codex-side-bypass"],
        },
    )
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "total_count": 2,
        "open_count": 0,
        "done_count": 2,
        "deferred_count": 1,
        "first_open_items": [],
        "backlog_items": [],
        "items": [
            {
                "index": 1,
                "text": "[P1] Finish CLI extraction prerequisite.",
                "role": "agent",
                "status": "done",
                "done": True,
                "priority": "P1",
                "task_class": "advancement_task",
                "todo_id": "todo_cli_done",
            },
            {
                "index": 2,
                "text": deferred_action,
                "role": "agent",
                "status": "deferred",
                "done": True,
                "priority": "P1",
                "task_class": "advancement_task",
                "claimed_by": "codex-side-bypass",
                "todo_id": "todo_issue_surface_deferred",
                "resume_when": "todo_done:todo_cli_done",
                "resume_ready": True,
                "resume_condition": {
                    "schema_version": "todo_resume_condition_v0",
                    "resume_when": "todo_done:todo_cli_done",
                    "kind": "todo_done",
                    "target_todo_id": "todo_cli_done",
                    "target_status": "done",
                    "satisfied": True,
                },
            },
        ],
        "deferred_items": [
            {
                "index": 2,
                "text": deferred_action,
                "role": "agent",
                "status": "deferred",
                "done": True,
                "priority": "P1",
                "task_class": "advancement_task",
                "claimed_by": "codex-side-bypass",
                "todo_id": "todo_issue_surface_deferred",
                "resume_when": "todo_done:todo_cli_done",
                "resume_ready": True,
                "resume_condition": {
                    "schema_version": "todo_resume_condition_v0",
                    "resume_when": "todo_done:todo_cli_done",
                    "kind": "todo_done",
                    "target_todo_id": "todo_cli_done",
                    "target_status": "done",
                    "satisfied": True,
                },
            }
        ],
        "deferred_resume_candidates": [
            {
                "index": 2,
                "text": deferred_action,
                "role": "agent",
                "status": "deferred",
                "done": True,
                "priority": "P1",
                "task_class": "advancement_task",
                "claimed_by": "codex-side-bypass",
                "todo_id": "todo_issue_surface_deferred",
                "resume_when": "todo_done:todo_cli_done",
                "resume_ready": True,
            }
        ],
    }
    item = payload["attention_queue"]["items"][0]
    item["project_asset"]["agent_todos"] = agent_todos
    item["agent_todos"] = agent_todos

    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    assert guard["decision"] == "successor_replan_required", guard
    assert guard["should_run"] is True, guard
    assert guard["normal_delivery_allowed"] is False, guard
    assert guard["actionable_by_codex"] is True, guard
    assert guard["effective_action"] == "successor_replan_required", guard
    frontier = guard["agent_scope_frontier"]
    assert frontier["action"] == "successor_replan_required", frontier
    assert frontier["quiet_noop_allowed"] is False, frontier
    assert frontier["requires_replan"] is True, frontier
    assert frontier["deferred_resume_candidates"][0]["todo_id"] == "todo_issue_surface_deferred", frontier
    goal_frontier = guard["goal_frontier_projection"]
    deferred_successors = goal_frontier["deferred_successors"]
    assert deferred_successors["ready_count"] == 1, goal_frontier
    assert deferred_successors["blocked_count"] == 0, goal_frontier
    assert deferred_successors["current_agent_ready_count"] == 1, goal_frontier
    assert deferred_successors["top_ready_todo_id"] == "todo_issue_surface_deferred", goal_frontier
    assert deferred_successors["ready_todo_ids"] == ["todo_issue_surface_deferred"], goal_frontier
    assert goal_frontier["acceptance_gaps"] == [], goal_frontier
    hint = guard["agent_lane_frontier_hint"]
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0", hint
    assert hint["decision"] == "add_next_advancement", hint
    assert hint["source"] == "agent_scope_frontier", hint
    assert hint["reason_code"] == "successor_replan_required", hint
    assert hint["target_todo_id"] == "todo_issue_surface_deferred", hint
    assert hint["quiet_noop_allowed"] is False, hint
    assert frontier["frontier_hint"] == hint, frontier
    assert guard["agent_todo_summary"]["current_agent_deferred_resume_count"] == 1, guard
    obligation = guard["execution_obligation"]
    assert obligation["kind"] == "successor_replan_required", obligation
    assert obligation["must_attempt_work"] is True, obligation
    assert obligation["delivery_allowed"] is False, obligation
    contract = guard["interaction_contract"]
    assert contract["mode"] == "successor_replan_required", contract
    assert contract["agent_channel"]["must_attempt"] is True, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is False, contract
    assert contract["cli_channel"]["spend_after_validation"] is True, contract
    actions = contract["cli_channel"]["next_cli_actions"]
    assert len(actions) == 3, actions
    assert "todo_issue_surface_deferred" in actions[0], actions
    assert "successor_replan_recorded" in actions[1], actions
    assert "loopx refresh-state" in actions[1], actions
    assert "--agent-id codex-side-bypass" in actions[1], actions
    assert "loopx --format json quota spend-slot" in actions[2], actions
    assert "--agent-id codex-side-bypass" in actions[2], actions
    assert guard["automation_liveness"]["automation_action"] == "execute_bounded_work", guard
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_scope_frontier: action=successor_replan_required" in markdown, markdown
    assert "agent_lane_frontier_hint: decision=add_next_advancement" in markdown, markdown
    assert "agent_scope_deferred_resume_candidates" in markdown, markdown


def assert_side_agent_can_take_unclaimed_work() -> None:
    primary_action = "[P0] Run SWE-Marathon full-suite polling and record compact results."
    unclaimed_action = "[P0] Validate hosted Frontstage screenshot and public CTA copy."
    guard = build_quota_should_run(
        status_payload(
            status="side_agent_unclaimed_frontier",
            next_action=primary_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": primary_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_primary_suite",
                },
                {
                    "index": 2,
                    "text": unclaimed_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "todo_id": "todo_unclaimed_frontstage",
                    "required_capabilities": ["shell"],
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-side-bypass",
    )
    assert guard["should_run"] is True, guard
    assert guard["normal_delivery_allowed"] is True, guard
    assert guard["recommended_action"] == unclaimed_action, guard
    assert "agent_scope_frontier" not in guard, guard
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_unclaimed_frontstage", guard
    assert next_action["selected_by"] == "unclaimed_todo", guard
    assert next_action["claim_required_before_work"] is True, guard
    hint = guard["agent_lane_frontier_hint"]
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0", hint
    assert hint["decision"] == "claim_unowned_in_scope", hint
    assert hint["source"] == "agent_lane_next_action", hint
    assert hint["reason_code"] == "unclaimed_advancement_selected", hint
    assert hint["target_todo_id"] == "todo_unclaimed_frontstage", hint
    assert hint["quiet_noop_allowed"] is False, hint
    assert "loopx todo claim" in hint["next_cli_action"], hint
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_lane_frontier_hint: decision=claim_unowned_in_scope" in markdown, markdown


def assert_agent_lane_next_action_prefers_capability_repair_candidate() -> None:
    ordinary_action = (
        "[P0] Run the already-launched full-suite polling lane and record compact results."
    )
    repair_action = (
        "[P0] Repair benchmark treatment product-path parity by building the "
        "benchmark_runner bridge itself before claiming uplift."
    )
    guard = build_quota_should_run(
        status_payload(
            status="target_capabilities_gate_merged",
            next_action=ordinary_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": ordinary_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_full_suite",
                },
                {
                    "index": 2,
                    "text": repair_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_bridge_repair",
                    "required_capabilities": ["shell"],
                    "target_capabilities": ["benchmark_runner"],
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-main-control",
    )
    capability_gate = guard["capability_gate"]
    repair_candidates = [
        item
        for item in capability_gate["runnable_candidates"]
        if item.get("capability_repair_mode") is True
    ]
    assert [item["todo_id"] for item in repair_candidates] == ["todo_bridge_repair"], guard
    assert guard["recommended_action"] == repair_action, guard
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_bridge_repair", guard
    assert next_action["source"] == "capability_gate.runnable_candidates", next_action
    assert next_action["selected_by"] == "current_agent_claimed_todo", next_action
    assert next_action["capability_repair_mode"] is True, next_action
    assert next_action["missing_target_capabilities"] == ["benchmark_runner"], next_action
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_lane_next_action: todo_id=todo_bridge_repair" in markdown, markdown


def assert_peer_handoff_uses_ordinary_priority_order() -> None:
    current_action = "[P0] Run SWE-Marathon full-suite polling and record compact results."
    review_action = "[P0] Review PR #464 to unblock the peer Frontstage delivery."
    guard = build_quota_should_run(
        status_payload(
            status="ordinary_handoff_frontier",
            next_action=current_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": current_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_current_suite",
                    "required_capabilities": ["shell"],
                },
                {
                    "index": 2,
                    "text": review_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "continuation_policy": "independent_handoff",
                    "action_kind": "review",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_review",
                    "required_capabilities": ["shell"],
                    "excluded_agents": ["codex-side-bypass"],
                    "unblocks_todo_id": "todo_frontstage_showcase",
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-main-control",
    )
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_current_suite", guard
    assert guard["capability_gate"]["candidate_order_policy"] == (
        "claim_then_priority_then_active_next_then_repair"
    ), guard
    assert guard["capability_gate"]["runnable_candidates"][0]["todo_id"] == "todo_current_suite", guard
    assert next_action["selected_by"] == "current_agent_claimed_todo", next_action
    assert guard["recommended_action"] == current_action, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_lane_next_action: todo_id=todo_current_suite" in markdown, markdown


def assert_handoff_without_dependency_link_has_no_special_rank() -> None:
    current_action = "[P0] Run the current benchmark frontier."
    review_action = "[P0] Review PR #675 to unblock the value-explorer peer."
    guard = build_quota_should_run(
        status_payload(
            status="handoff_without_link_frontier",
            next_action=current_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-value-explorer"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": current_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_current_frontier",
                    "required_capabilities": ["shell"],
                },
                {
                    "index": 2,
                    "text": review_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0-review",
                    "task_class": "advancement_task",
                    "continuation_policy": "independent_handoff",
                    "action_kind": "review",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_value_connector_review",
                    "required_capabilities": ["shell"],
                    "excluded_agents": ["codex-value-explorer"],
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-main-control",
    )
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_current_frontier", guard
    assert guard["capability_gate"]["runnable_candidates"][0]["todo_id"] == (
        "todo_current_frontier"
    ), guard
    assert "dependency_handoff" not in next_action, next_action
    assert guard["recommended_action"] == current_action, guard


def assert_handoff_does_not_cross_ordinary_priority_boundary() -> None:
    current_action = "[P0] Run the current benchmark frontier."
    review_action = "[P1] Review PR #464 to unblock the peer Frontstage delivery."
    guard = build_quota_should_run(
        status_payload(
            status="handoff_lower_priority_frontier",
            next_action=current_action,
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
            },
            agent_todo_items=[
                {
                    "index": 1,
                    "text": current_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P0",
                    "task_class": "advancement_task",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_current_frontier",
                    "required_capabilities": ["shell"],
                },
                {
                    "index": 2,
                    "text": review_action,
                    "role": "agent",
                    "status": "open",
                    "priority": "P1",
                    "task_class": "advancement_task",
                    "continuation_policy": "independent_handoff",
                    "action_kind": "review",
                    "claimed_by": "codex-main-control",
                    "todo_id": "todo_lower_priority_review",
                    "required_capabilities": ["shell"],
                    "excluded_agents": ["codex-side-bypass"],
                    "unblocks_todo_id": "todo_frontstage_showcase",
                },
            ],
        ),
        goal_id=GOAL_ID,
        agent_id="codex-main-control",
    )
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_current_frontier", guard
    assert guard["capability_gate"]["runnable_candidates"][0]["todo_id"] == "todo_current_frontier", guard
    assert guard["capability_gate"]["runnable_candidates"][1]["todo_id"] == "todo_lower_priority_review", guard
    assert guard["recommended_action"] == current_action, guard


def assert_explicit_action_selection_replaces_unbound_prose() -> None:
    ordinary_action = (
        "[P0] Run SWE-Marathon full-suite polling and record compact results."
    )
    parity_action = (
        "[P0] Repair benchmark treatment product-path parity before claiming uplift."
    )
    payload = status_payload(
        status="next_action_todo_id_projection",
        next_action=(
            "[P0] Continue todo_parity_slice: repair treatment parity before "
            "another full-suite polling slice."
        ),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["codex-main-control"],
        },
        agent_todo_items=[
            {
                "index": 1,
                "text": ordinary_action,
                "role": "agent",
                "status": "open",
                "priority": "P0",
                "task_class": "advancement_task",
                "claimed_by": "codex-main-control",
                "todo_id": "todo_full_suite",
                "required_capabilities": ["shell"],
            },
            {
                "index": 2,
                "text": parity_action,
                "role": "agent",
                "status": "open",
                "priority": "P0",
                "task_class": "advancement_task",
                "claimed_by": "codex-main-control",
                "todo_id": "todo_parity_slice",
                "required_capabilities": ["shell"],
            },
        ],
    )
    # Unbound prose cannot replace the typed selected Todo. Explicit selection
    # uses the authoritative inventory and does not rewrite the recommendation.
    unbound = build_quota_should_run(payload, goal_id=GOAL_ID, agent_id="codex-main-control")
    assert unbound["agent_lane_next_action"]["todo_id"] == "todo_full_suite", unbound
    summary = payload["attention_queue"]["items"][0]["project_asset"]["agent_todos"]
    summary["items"] = list(summary["first_open_items"])
    guard = build_quota_should_run(
        payload, goal_id=GOAL_ID, agent_id="codex-main-control",
        requested_action_todo_id="todo_parity_slice",
    )
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_parity_slice", guard
    assert next_action["selected_by"] == "agent_action_selection", guard
    assert guard["action_selection_qualification"]["state"] == "qualified", guard
    assert next_action["text"] == parity_action, guard
    assert guard["recommended_action"] == ordinary_action, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_lane_next_action: todo_id=todo_parity_slice" in markdown, markdown


def assert_explicit_action_selection_survives_compact_candidate_limits() -> None:
    filler_items = [
        {
            "index": index,
            "text": f"[P1] Retained filler todo {index}.",
            "role": "agent",
            "status": "open",
            "priority": "P1",
            "task_class": "advancement_task",
            "claimed_by": "codex-main-control",
            "todo_id": f"todo_filler_{index}",
            "required_capabilities": ["shell"],
        }
        for index in range(1, 13)
    ]
    target_action = (
        "[P1] Debug SkillsBench product-mode lifecycle counter semantics before "
        "expanding more cases."
    )
    target_item = {
        "index": 20,
        "text": target_action,
        "role": "agent",
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
        "action_kind": "skillsbench_lifecycle_observer_counter_semantics",
        "claimed_by": "codex-main-control",
        "todo_id": "todo_skillsbench_lifecycle",
        "required_capabilities": ["shell"],
    }
    agent_todos = compact_todo_group(
        [*filler_items, target_item],
        source_section="Agent Todo",
        role="agent",
        preferred_todo_ids={"todo_skillsbench_lifecycle"},
    )
    assert agent_todos is not None
    agent_todos["items"] = [*filler_items, target_item]
    assert all(
        item.get("todo_id") != "todo_skillsbench_lifecycle"
        for item in agent_todos["executable_backlog_items"]
    ), agent_todos
    assert agent_todos["active_next_action_executable_items"][0]["todo_id"] == (
        "todo_skillsbench_lifecycle"
    ), agent_todos

    payload = status_payload(
        status="active_next_action_candidate_limit_projection",
        next_action=(
            "codex-main-control: advance todo_skillsbench_lifecycle by debugging "
            "SkillsBench product-mode lifecycle observation semantics."
        ),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["codex-main-control"],
        },
        agent_todo_items=[],
    )
    payload["run_history"]["goals"][0]["latest_runs"] = [{"agent_id": "codex-main-control", "autonomous_replan_ack": {"recorded": True, "delta_contract": {"delta_present": True, "delta_kinds": ["runnable_todo_set"]}}}]
    item = payload["attention_queue"]["items"][0]
    item["agent_todos"] = agent_todos
    item["project_asset"]["agent_todos"] = agent_todos

    unbound = build_quota_should_run(
        payload, goal_id=GOAL_ID, agent_id="codex-main-control",
    )
    portfolio = unbound["action_portfolio"]
    assert portfolio["primary"]["todo_id"] == filler_items[0]["todo_id"], unbound
    assert portfolio["suggested_actions"][0]["todo_id"] == filler_items[0]["todo_id"], portfolio
    agent_channel = unbound["interaction_contract"]["agent_channel"]
    assert agent_channel["selection_required"] is True, agent_channel
    assert agent_channel["delivery_allowed"] is False, agent_channel
    assert agent_channel["action_portfolio_ref"] == "$.action_portfolio", agent_channel

    guard = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id="codex-main-control",
        requested_action_todo_id="todo_skillsbench_lifecycle",
    )
    next_action = guard["agent_lane_next_action"]
    assert next_action["todo_id"] == "todo_skillsbench_lifecycle", guard
    assert next_action["selected_by"] == "agent_action_selection", guard
    assert guard["action_selection_qualification"]["state"] == "qualified", guard
    assert next_action["selection_binding"] == "pending_action_selection", next_action
    assert next_action["text"] == target_action, guard
    assert guard["recommended_action"] == filler_items[0]["text"], guard
    selected_todo = guard["selected_todo"]
    assert selected_todo["todo_id"] == "todo_skillsbench_lifecycle", guard
    assert "action_portfolio" not in guard, guard
    markdown = render_quota_should_run_markdown(guard)
    assert "agent_lane_next_action: todo_id=todo_skillsbench_lifecycle" in markdown, markdown


def main() -> int:
    assert_dependency_monitor_requires_advancement()
    assert_primary_status_stays_advancement_lane()
    assert_structured_surface_only_run_requires_outcome_followthrough()
    assert_blocker_writeback_satisfies_contract_followthrough()
    assert_contract_word_alone_does_not_trigger_outcome_followthrough()
    assert_monitor_only_todo_requires_replan_before_quiet()
    assert_monitor_only_with_user_todo_surfaces_user_action_without_transition()
    assert_blocked_agent_todo_with_user_gate_notifies_without_execution()
    assert_monitor_only_with_planning_next_action_materializes_advancement()
    assert_monitor_only_with_adapter_next_action_materializes_advancement()
    assert_executable_repeat_with_blocker_context_routes_to_advancement()
    assert_monitor_todo_with_executable_next_action_materializes_advancement()
    assert_structured_todo_lane_registration_beats_text_fallback()
    assert_structured_monitor_registration_beats_action_text()
    assert_mixed_monitor_and_advancement_routes_to_advancement()
    assert_not_due_monitor_only_waits_quietly_without_replan_delta()
    assert_due_monitor_only_requires_attempt()
    assert_due_monitor_lower_priority_does_not_preempt_advancement()
    assert_due_monitor_higher_priority_preempts_advancement()
    assert_lower_priority_executable_before_higher_priority_is_reordered()
    assert_external_monitor_context_recommends_executable_backlog()
    assert_benchmark_readiness_scan_routes_to_advancement()
    assert_benchmark_source_preflight_routes_to_advancement()
    assert_behavior_regression_suite_routes_to_advancement()
    assert_launched_external_observation_does_not_preempt_advancement_backlog()
    assert_launch_then_poll_todo_without_handle_routes_to_advancement()
    assert_side_agent_monitor_watch_without_handle_stays_quiet()
    assert_side_agent_monitor_watch_with_handle_requires_observation()
    assert_side_agent_next_action_projects_without_stealing_goal_next_action()
    assert_peer_requires_reassignment_when_only_other_peer_has_claimed_work()
    assert_side_agent_scope_wait_mentions_blocking_owner()
    assert_excluded_agent_waits_for_dependency_handoff()
    assert_scoped_user_gate_does_not_steal_other_agent_fallback()
    assert_side_agent_replans_when_deferred_successor_is_ready()
    assert_side_agent_can_take_unclaimed_work()
    assert_agent_lane_next_action_prefers_capability_repair_candidate()
    assert_peer_handoff_uses_ordinary_priority_order()
    assert_handoff_without_dependency_link_has_no_special_rank()
    assert_handoff_does_not_cross_ordinary_priority_boundary()
    assert_explicit_action_selection_replaces_unbound_prose()
    assert_explicit_action_selection_survives_compact_candidate_limits()
    print("work-lane-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
