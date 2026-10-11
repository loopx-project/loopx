"""Committed retry clocks survive the real quota/interaction/scheduler composition."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from loopx.control_plane.quota.blocked_retry import overlay_active_turn_retries
from loopx.control_plane.scheduler.execution_context import (
    SchedulerRuntimeProfile, scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.quota import build_quota_should_run

START = datetime(2026, 10, 1, tzinfo=UTC)
DUE = "2026-10-01T00:05:00Z"


def source(*, user_class="user_action", newer=False, cached_monitor=False):
    retry = {"schema_version": "quota_blocked_retry_v0", "source": "turn_settlement",
             "todo_id": "todo_work", "observed_at": "2026-10-01T00:00:00Z",
             "due_at": DUE, "resume_when": f"resume_at:{DUE}"}
    run = {"agent_id": "agent-a", "todo_id": "todo_work", "turn_instance_id": "turn-old",
           "delivery_outcome": "outcome_gap", "progress_observation": {"result_class": "blocked"},
           "blocked_retry": retry}
    result = quota_status_payload(goal_id="goal-a", status="active",
        recommended_action=("Wait for the recorded source." if cached_monitor else
                            "Validate remaining authorized work."), claim_scope_agent_id="agent-a",
        coordination={"registered_agents": ["agent-a", "agent-b"], "handoff_mode": "soft_claim"},
        agent_todo_items=[{"todo_id": "todo_work", "status": "open", "role": "agent",
            "archive_state": "active", "task_class": "advancement_task", "claimed_by": "agent-a",
            "text": "Validate remaining authorized work.", "action_kind": "validate"}] + ([{
            "todo_id": "todo_watch", "status": "open", "role": "agent",
            "task_class": "continuous_monitor", "claimed_by": "agent-a",
            "text": "Observe the independent source.", "watch_only": True,
            "cadence": "30m", "next_due_at": "2026-10-01T00:25:00Z",
            "target_key": "independent-source",
        }] if cached_monitor else []),
        user_todo_items=[{"todo_id": "todo_notice", "status": "open", "role": "user",
            "task_class": user_class, "bound_agent": "agent-a",
            **({"blocks_agent": "agent-a"} if user_class == "user_gate" else {}),
            "text": "Review the independent draft."}],
        latest_runs=([{"agent_id": "agent-a", "todo_id": "todo_work",
                      "delivery_outcome": "outcome_progress"}] if newer else []) + [run])
    if cached_monitor:
        for item in result["attention_queue"]["items"]:
            for summary in (item["agent_todos"], item["project_asset"]["agent_todos"]):
                summary["frontier_deadline"] = {
                    "schema_version": "todo_frontier_deadline_v0",
                    "identity": "todo_watch", "source": "continuous_monitor",
                    "next_due_at": "2026-10-01T00:25:00Z", "candidate_count": 1,
                }
    return result


def packet(monkeypatch, *, seconds=120, user_class="user_action", newer=False, runtime=None, floor=0,
           cached_monitor=False):
    now = START + timedelta(seconds=seconds)
    for path in ("loopx.control_plane.todos.quota_selection.now_utc",
                 "loopx.control_plane.todos.quota_summary.now_utc",
                 "loopx.control_plane.scheduler.monitor_todo.now_utc",
                 "loopx.control_plane.scheduler.scheduler_hint.now_utc"):
        monkeypatch.setattr(path, lambda: now)
    raw = source(user_class=user_class, newer=newer, cached_monitor=cached_monitor)
    before = deepcopy(raw)
    projected = overlay_active_turn_retries(raw, goal_id="goal-a", agent_id="agent-a", observed_at=now.isoformat())
    result = build_quota_should_run(projected, goal_id="goal-a", agent_id="agent-a",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            runtime or SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT))
    if floor:
        from loopx.control_plane.scheduler.scheduler_hint import build_scheduler_hint
        result["automation_cadence"] = {"schema_version": "automation_cadence_result_v1",
            "ok": True, "enabled": True, "min_interval_minutes": floor}
        result["scheduler_hint"] = build_scheduler_hint(result,
            scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
                runtime or SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT))
    assert raw == before, "wait projection must not write canonical Todo or Run facts"
    return result


def test_pending_retry_is_not_a_nonblocking_notice_gate(monkeypatch):
    result = packet(monkeypatch)
    assert result["execution_obligation"]["must_attempt_work"] is False
    assert result["execution_obligation"]["kind"] == "blocked_wait"
    assert result["interaction_contract"]["user_channel"]["action_required"] is True
    assert result["interaction_contract"]["user_channel"]["non_gating_notice"] is True
    assert result["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    hint = result["scheduler_hint"]
    assert hint["cadence_class"] == "quiet_wait"
    assert hint["app_automation"]["recommended_interval_minutes"] == 3
    assert hint["pending_retry"]["due_at"] == DUE
    assert hint["pending_retry"]["turn_instance_id"] == "turn-old"
    assert hint["pending_retry"]["source"] == "turn_settlement"
    assert hint["pending_retry"]["execution_authority_granted"] is False


@pytest.mark.parametrize("seconds,floor,interval", [(120, 0, 3), (295, 0, 1), (295, 6, 6)])
def test_retry_overlay_precedes_a_later_cached_monitor_deadline(monkeypatch, seconds, floor, interval):
    result = packet(monkeypatch, seconds=seconds, floor=floor, cached_monitor=True)
    assert result["execution_obligation"]["kind"] == "blocked_wait"
    assert result["interaction_contract"]["user_channel"]["non_gating_notice"] is True
    assert result["scheduler_hint"]["cadence_class"] == "quiet_wait"
    assert result["scheduler_hint"]["app_automation"]["recommended_interval_minutes"] == interval
    assert result["scheduler_hint"]["pending_retry"]["due_at"] == DUE
    assert result["scheduler_hint"]["pending_retry"]["execution_authority_granted"] is False
    assert result["agent_todo_summary"]["frontier_deadline"]["identity"] == "todo_work"
    assert result["agent_todo_summary"]["frontier_deadline"]["candidate_count"] == 2


@pytest.mark.parametrize("seconds", [300, 301])
def test_at_or_after_due_retry_does_not_hold_work(monkeypatch, seconds):
    result = packet(monkeypatch, seconds=seconds)
    assert result["execution_obligation"]["must_attempt_work"] is True
    assert "pending_retry" not in result["scheduler_hint"]


def test_newer_run_supersedes_retry(monkeypatch):
    result = packet(monkeypatch, newer=True)
    assert result["execution_obligation"]["must_attempt_work"] is True
    assert "pending_retry" not in result["scheduler_hint"]


def test_real_user_gate_survives_retry_clock(monkeypatch):
    # A real final gate contract wins over an independently pending retry.
    # Source Todo copy alone is not a final gate/admission contract.
    from loopx.control_plane.scheduler.scheduler_hint import build_scheduler_hint
    result = packet(monkeypatch)
    contract = result["interaction_contract"]
    contract["mode"] = "user_gate"
    contract["user_channel"].pop("non_gating_notice")
    contract["agent_channel"].pop("wait")
    hint = build_scheduler_hint(result, scheduler_execution_context=
        scheduler_execution_context_for_runtime_profile(SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT))
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert hint["cadence_class"] == "human_gate"
    assert hint["pending_retry"]["due_at"] == DUE


def test_owner_floor_clamps_retry_recheck(monkeypatch):
    result = packet(monkeypatch, floor=6)
    assert result["scheduler_hint"]["app_automation"]["recommended_interval_minutes"] == 6
    assert result["scheduler_hint"]["pending_retry"]["due_at"] == DUE


def test_goal_runtime_uses_same_retry_clock(monkeypatch):
    result = packet(monkeypatch, runtime=SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL)
    hint = result["scheduler_hint"]
    assert hint["goal_runtime_continuation"]["recheck_after_seconds"] == 180
    assert hint["pending_retry"]["due_at"] == DUE


@pytest.mark.parametrize("mismatch", ["agent", "goal", "todo", "claim", "canonical_wait"])
def test_retry_projection_does_not_cross_scope_or_replace_a_canonical_wait(mismatch):
    raw = source(cached_monitor=True)
    if mismatch == "todo":
        raw["run_history"]["goals"][0]["latest_runs"][0]["todo_id"] = "todo_other"
    for item in raw["attention_queue"]["items"]:
        for summary in (item["agent_todos"], item["project_asset"]["agent_todos"]):
            for key, values in summary.items():
                if not isinstance(values, list):
                    continue
                for row in values:
                    if row.get("todo_id") != "todo_work":
                        continue
                    if mismatch == "claim":
                        row["claimed_by"] = "agent-b"
                    if mismatch == "canonical_wait":
                        row["resume_when"] = "todo_done:todo_dependency"
    before = deepcopy(raw)
    result = overlay_active_turn_retries(raw, goal_id="goal-other" if mismatch == "goal" else "goal-a",
        agent_id="agent-b" if mismatch == "agent" else "agent-a", observed_at="2026-10-01T00:02:00Z")
    assert result is raw
    assert result == before
