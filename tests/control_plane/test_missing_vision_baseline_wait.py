"""A retained missing baseline cannot repeatedly wake a blocked-only Agent."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item
from loopx.control_plane.scheduler.execution_context import GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
from loopx.quota import build_quota_should_run

GOAL = "baseline-wait-fixture"
AGENT = "agent-baseline"


def status(*, goal=GOAL, agent=AGENT, extra=()):
    checkpoint = {"schema_version": "vision_checkpoint_v0", "agent_id": agent,
                  "required": True, "satisfied": False, "decision": "missing_required",
                  "missing_baseline": True,
                  "triggers": [{"kind": "material_delivery_outcome", "delivery_outcome": "outcome_progress"}]}
    return quota_status_payload(
        goal_id=goal, status="active", recommended_action="Wait for external input.",
        agent_todo_items=[quota_todo_item(todo_id="todo_blocked", title="External input pending.",
                                        status="blocked", claimed_by=agent), *extra],
        latest_runs=[{"classification": "state_refreshed", "generated_at": "2026-01-01T00:00:00Z",
                      "agent_id": agent, "progress_scope": "agent_lane", "vision_checkpoint": checkpoint}],
        coordination={"agent_model": "peer_v1", "registered_agents": [agent]},
        quota_extra={"allowed_slots": 12, "spent_slots": 1})


def guard(payload, runtime, turn, *, goal=GOAL, agent=AGENT, **kwargs):
    return build_quota_should_run(
        payload, goal_id=goal, agent_id=agent, runtime_root=runtime, turn_instance_id=turn,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
        include_scheduler_detail=True, **kwargs)


def assert_wait(result):
    assert result["effective_action"] == "agent_scope_wait"
    assert result["should_run"] is False
    assert result["normal_delivery_allowed"] is False
    assert result["execution_obligation"]["must_attempt_work"] is False
    channel = result["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] is False
    assert channel["quiet_noop_allowed"] is True
    assert channel["must_attempt"] is False
    assert result["scheduler_hint"]["action"] == "backoff_until_reassigned"
    assert result["autonomous_replan_obligation"]["required"] is True


def test_first_initialization_then_fresh_turns_and_process_readback_wait(tmp_path):
    payload = status()
    first = guard(payload, tmp_path, "initialization")
    assert first["effective_action"] == "autonomous_replan_required"
    assert first["should_run"] is True
    assert guard(payload, tmp_path, "initialization")["should_run"] is True
    for turn in ("poll-1", "poll-2"):
        assert_wait(guard(payload, tmp_path, turn))
    script = (
        "import json,sys; from pathlib import Path; "
        "from tests.control_plane.test_missing_vision_baseline_wait import guard,assert_wait; "
        "result=guard(json.load(sys.stdin),Path(sys.argv[1]),'process-readback'); "
        "assert_wait(result); print(result['effective_action'])"
    )
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], input=json.dumps(payload),
                               text=True, capture_output=True, check=True,
                               env={**os.environ, "LOOPX_USAGE_PING": "0"})
    assert completed.stdout.strip() == "agent_scope_wait"
    assert len(list((tmp_path / "vision-baseline-admissions").glob("*.json"))) == 1


@pytest.mark.parametrize("task_class", ["advancement_task", "continuous_monitor"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_work_and_due_monitor_resume_after_wait(tmp_path, task_class, provider):
    from canonical_authority_fixture import initialize_canonical_authority

    guard(status(), tmp_path, "initialization")
    assert_wait(guard(status(), tmp_path, "blocked"))
    work = quota_todo_item(todo_id="todo_independent", title="Perform independent work.",
                               task_class=task_class, claimed_by=AGENT, archive_state="active",
                           **({"cadence": "30m", "next_due_at": "2000-01-01T00:00:00Z"}
                              if task_class == "continuous_monitor" else {}))
    state = tmp_path / "state.md"
    state.write_text("# Synthetic fixture\n\n## Agent Todo\n", encoding="utf-8")
    initialize_canonical_authority(
        tmp_path, GOAL, build_todo_runtime_shadow_projection(
            goal_id=GOAL, todos=[work], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    result = guard(status(extra=[work]), tmp_path, "ready")
    assert result["should_run"] is True
    assert result["effective_action"] != "agent_scope_wait"
    assert_wait(guard(status(), tmp_path, "blocked-again"))


def test_admission_is_scoped_to_goal_and_agent(tmp_path):
    guard(status(), tmp_path, "initialization")
    for goal, agent in (("another-goal", AGENT), (GOAL, "another-agent")):
        result = guard(status(goal=goal, agent=agent), tmp_path, "initialization", goal=goal, agent=agent)
        assert result["effective_action"] == "autonomous_replan_required"
    assert len(list((tmp_path / "vision-baseline-admissions").glob("*.json"))) == 3


def test_receipt_bound_replan_retains_original_settlement(tmp_path):
    first = guard(status(), tmp_path, "initialization")
    obligation = first["autonomous_replan_obligation"]["obligation_id"]
    result = guard(status(), tmp_path, "original-owed-turn",
                   receipt_bound_replan_obligation_id=obligation)
    assert result["should_run"] is True
    assert result["autonomous_replan_obligation"]["obligation_id"] == obligation


def test_changed_checkpoint_timestamp_does_not_rearm_baseline(tmp_path):
    guard(status(), tmp_path, "initialization")
    updated = status()
    updated["run_history"]["goals"][0]["latest_runs"][0]["generated_at"] = "2026-02-01T00:00:00Z"
    assert_wait(guard(updated, tmp_path, "changed-checkpoint"))
