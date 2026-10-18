from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopx.control_plane.agents.agent_scope_frontier import AgentScopeFrontierAction
from loopx.control_plane.quota.scheduler_ack import (
    record_quota_scheduler_ack_for_decision,
)
from loopx.control_plane.scheduler import scheduler_hint as scheduler_hint_module
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from tests.control_plane.scheduler_arbitration_fixture import build_native_scheduler_hint as build_scheduler_hint

GOAL_ID = "scheduler-backoff-convergence"
AGENT_ID = "codex-fixture"
HOST_15 = "FREQ=MINUTELY;INTERVAL=15"
HOST_30 = "FREQ=MINUTELY;INTERVAL=30"
HOST_3 = "FREQ=MINUTELY;INTERVAL=3"
HOST_6 = "FREQ=MINUTELY;INTERVAL=6"
HOST_10 = "FREQ=MINUTELY;INTERVAL=10"
HOST_7 = "FREQ=MINUTELY;INTERVAL=7"
APP_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)
AGENT_SCOPE_ACTIONS = [action.value for action in AgentScopeFrontierAction]


def _monitor_decision(
    *,
    now: datetime,
    minutes_until_due: int,
    cadence: str = "3m",
    recommended_action: str = "Wait for material monitor evidence.",
) -> dict:
    return {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "should_run": False,
        "effective_action": "monitor_quiet_skip",
        "recommended_action": recommended_action,
        "heartbeat_recommendation": {
            "recommended_mode": "monitor_quiet_until_material_transition",
            "notify": "DONT_NOTIFY",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "monitor_quiet_skip",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": False,
                "delivery_allowed": False,
                "quiet_noop_allowed": True,
            },
        },
        "agent_todo_summary": {
            "current_agent_claimed_monitor_items": [
                {
                    "todo_id": "todo_scheduler_convergence",
                    "task_class": "continuous_monitor",
                    "target_key": "scheduler-convergence",
                    "cadence": cadence,
                    "next_due_at": (
                        now + timedelta(minutes=minutes_until_due)
                    ).isoformat(),
                    "expires_at": (
                        now + timedelta(minutes=minutes_until_due + 60)
                    ).isoformat(),
                }
            ],
            "monitor_open_items": [],
        },
    }


def _agent_wait_decision() -> dict:
    return {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "should_run": False,
        "effective_action": AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
        "recommended_action": "Wait for reassignment.",
        "heartbeat_recommendation": {
            "recommended_mode": AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
            "notify": "DONT_NOTIFY",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": False,
                "delivery_allowed": False,
                "quiet_noop_allowed": True,
            },
        },
    }


def _active_decision() -> dict:
    return {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "should_run": True,
        "effective_action": "normal_run",
        "recommended_action": "Run bounded delivery work.",
        "heartbeat_recommendation": {
            "recommended_mode": "run_first_read_only_map",
            "notify": "DONT_NOTIFY",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
            },
        },
    }


def _capability_bridge_decision() -> dict:
    return {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "should_run": True,
        "effective_action": "capability_bridge_repair",
        "recommended_action": "restore the missing network bridge capability",
        "heartbeat_recommendation": {
            "recommended_mode": "repair_capability_bridge",
            "notify": "NOTIFY",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "capability_bridge_repair",
            "user_channel": {
                "action_required": False,
                "notify": "NOTIFY",
                "non_blocking": True,
                "actions": [
                    "Refresh protected network authentication, then confirm access."
                ],
            },
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": False,
                "quiet_noop_allowed": False,
            },
        },
    }


def _hint(
    monkeypatch,
    decision: dict,
    *,
    now: datetime,
    scheduler_state: dict | None = None,
    host_rrule: str | None = None,
) -> dict:
    monkeypatch.setattr(scheduler_hint_module, "now_utc", lambda: now)
    return build_scheduler_hint(
        decision,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
        codex_app_scheduler_state=scheduler_state,
        codex_app_current_rrule=host_rrule,
        scheduler_execution_context=APP_CONTEXT,
    )


def _ack_state(
    hint: dict,
    *,
    runtime_root: Path,
    applied_rrule: str,
    generated_at: datetime,
) -> dict:
    event = record_quota_scheduler_ack_for_decision(
        {"goal_id": GOAL_ID, "scheduler_hint": hint},
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
        applied_rrule=applied_rrule,
        generated_at=generated_at.isoformat(),
    )
    assert event["ok"] is True, event
    return event["scheduler_ack_event"]["scheduler_state"]


CADENCE_POLICY_CASES = [
    {
        "id": "agent_wait_advances_after_acknowledged_interval",
        "decision": "agent_wait",
        "progression": [10, 20, 30, 60],
        "initial_rrule": "FREQ=MINUTELY;INTERVAL=10",
        "elapsed_minutes": 10,
        "expected_rrule": "FREQ=MINUTELY;INTERVAL=20",
        "expected_index": 1,
    },
    {
        "id": "monitor_wait_advances_after_acknowledged_interval",
        "decision": "monitor_wait",
        "progression": [15, 30, 60],
        "initial_rrule": HOST_15,
        "elapsed_minutes": 15,
        "expected_rrule": HOST_30,
        "expected_index": 1,
    },
    {
        "id": "active_work_holds_initial_interval",
        "decision": "active_work",
        "progression": [3, 6, 10],
        "initial_rrule": HOST_3,
        "elapsed_minutes": 3,
        "expected_rrule": None,
        "expected_index": 0,
    },
]


@pytest.mark.parametrize(
    "case",
    CADENCE_POLICY_CASES,
    ids=[case["id"] for case in CADENCE_POLICY_CASES],
)
def test_scheduler_hint_cadence_policy_decision_table(
    monkeypatch, tmp_path: Path, case: dict
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    if case["decision"] == "agent_wait":
        decision = _agent_wait_decision()
    elif case["decision"] == "monitor_wait":
        decision = _monitor_decision(now=now, minutes_until_due=119)
    else:
        decision = _active_decision()

    initial = _hint(monkeypatch, decision, now=now)
    initial_app = initial["codex_app"]
    assert initial_app["example_progression_minutes"] == case["progression"]
    assert initial_app["recommended_rrule"] == case["initial_rrule"]

    scheduler_state = _ack_state(
        initial,
        runtime_root=tmp_path,
        applied_rrule=case["initial_rrule"],
        generated_at=now,
    )
    after_interval = _hint(
        monkeypatch,
        decision,
        now=now + timedelta(minutes=case["elapsed_minutes"]),
        scheduler_state=scheduler_state,
        host_rrule=case["initial_rrule"],
    )
    after_app = after_interval["codex_app"]
    assert after_app["stateful_backoff"]["progression_index"] == case[
        "expected_index"
    ]
    if case["expected_rrule"] is None:
        assert after_app["stateful_backoff"]["apply_needed"] is False
        assert "recommended_rrule" not in after_app
    else:
        assert after_app["recommended_rrule"] == case["expected_rrule"]


def test_monitor_identity_ignores_recommended_action_text_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    original = _monitor_decision(
        now=now,
        minutes_until_due=119,
        recommended_action="Monitor the post-merge run.",
    )
    first = _hint(monkeypatch, original, now=now)
    scheduler_state = _ack_state(
        first,
        runtime_root=tmp_path,
        applied_rrule=HOST_15,
        generated_at=now,
    )

    mutated = _monitor_decision(
        now=now,
        minutes_until_due=119,
        recommended_action="Controller wording changed; the target did not.",
    )
    second = _hint(
        monkeypatch,
        mutated,
        now=now + timedelta(minutes=15),
        scheduler_state=scheduler_state,
        host_rrule=HOST_15,
    )

    assert second["codex_app"]["stateful_backoff"]["state_status"] == "same_identity"
    assert second["codex_app"]["recommended_rrule"] == HOST_30
    assert "recommended_action" not in second["unchanged_identity_keys"]


def test_monitor_ack_settles_before_progression_and_avoids_3_6_3_flip(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    decision = _monitor_decision(now=now, minutes_until_due=31)
    first = _hint(monkeypatch, decision, now=now)
    first_app = first["codex_app"]
    assert first_app["recommended_rrule"] == HOST_15

    settled_state = _ack_state(
        first,
        runtime_root=tmp_path,
        applied_rrule=HOST_15,
        generated_at=now,
    )
    immediate = _hint(
        monkeypatch,
        decision,
        now=now,
        scheduler_state=settled_state,
        host_rrule=HOST_15,
    )
    immediate_app = immediate["codex_app"]
    assert immediate_app["stateful_backoff"]["state_status"] == "same_identity"
    assert immediate_app["stateful_backoff"]["current_rrule"] == HOST_15
    assert immediate_app["stateful_backoff"]["apply_needed"] is False
    assert immediate_app["stateful_backoff"]["host_observation"]["status"] == (
        "matches_recommended"
    )
    assert "recommended_rrule" not in immediate_app

    near_due = _hint(
        monkeypatch,
        decision,
        now=now + timedelta(minutes=2),
        scheduler_state=settled_state,
        host_rrule=HOST_15,
    )
    near_due_app = near_due["codex_app"]
    assert near_due_app["example_progression_minutes"] == [15]
    assert near_due_app["stateful_backoff"]["current_rrule"] == HOST_15
    assert near_due_app["stateful_backoff"]["apply_needed"] is False
    assert "recommended_rrule" not in near_due_app


def test_monitor_near_due_under_floor_uses_tight_cadence(monkeypatch) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    decision = _monitor_decision(now=now, minutes_until_due=7, cadence="30m")
    first = _hint(monkeypatch, decision, now=now)

    first_app = first["codex_app"]
    assert first_app["recommended_rrule"] == HOST_7
    assert first_app["example_progression_minutes"] == [7]
    assert first["cadence_class"] == "monitor_wait"


def test_monitor_progression_advances_after_elapsed_interval_and_then_converges(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    decision = _monitor_decision(now=now, minutes_until_due=119, cadence="30m")
    first = _hint(monkeypatch, decision, now=now)
    settled_15 = _ack_state(
        first,
        runtime_root=tmp_path,
        applied_rrule=HOST_15,
        generated_at=now,
    )

    early = _hint(
        monkeypatch,
        decision,
        now=now + timedelta(minutes=14),
        scheduler_state=settled_15,
        host_rrule=HOST_15,
    )
    assert early["codex_app"]["stateful_backoff"]["current_rrule"] == HOST_15
    assert early["codex_app"]["stateful_backoff"]["apply_needed"] is False

    elapsed = now + timedelta(minutes=15)
    advance = _hint(
        monkeypatch,
        decision,
        now=elapsed,
        scheduler_state=settled_15,
        host_rrule=HOST_15,
    )
    advance_app = advance["codex_app"]
    assert advance_app["recommended_rrule"] == HOST_30
    assert advance_app["stateful_backoff"]["host_observation"]["status"] == (
        "drift_detected"
    )

    settled_30 = _ack_state(
        advance,
        runtime_root=tmp_path,
        applied_rrule=HOST_30,
        generated_at=elapsed,
    )
    converged = _hint(
        monkeypatch,
        decision,
        now=elapsed,
        scheduler_state=settled_30,
        host_rrule=HOST_30,
    )
    converged_app = converged["codex_app"]
    assert converged_app["stateful_backoff"]["current_rrule"] == HOST_30
    assert converged_app["stateful_backoff"]["apply_needed"] is False
    assert converged_app["stateful_backoff"]["host_observation"]["status"] == (
        "matches_recommended"
    )
    assert "recommended_rrule" not in converged_app


def test_capability_bridge_wait_backs_off_and_material_work_resets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    bridge = _capability_bridge_decision()

    first = _hint(monkeypatch, bridge, now=now)
    first_app = first["codex_app"]
    assert first["action"] == "run_now"
    assert first["cadence_class"] == "active_work"
    assert first_app["recommended_rrule"] == HOST_3

    settled_3 = _ack_state(
        first,
        runtime_root=tmp_path,
        applied_rrule=HOST_3,
        generated_at=now,
    )
    early = _hint(
        monkeypatch,
        bridge,
        now=now + timedelta(minutes=2),
        scheduler_state=settled_3,
        host_rrule=HOST_3,
    )
    assert early["codex_app"]["stateful_backoff"]["progression_index"] == 0
    assert "recommended_rrule" not in early["codex_app"]

    elapsed_3 = now + timedelta(minutes=3)
    advance_6 = _hint(
        monkeypatch,
        bridge,
        now=elapsed_3,
        scheduler_state=settled_3,
        host_rrule=HOST_3,
    )
    advance_6_app = advance_6["codex_app"]
    assert advance_6_app["stateful_backoff"]["progression_index"] == 1
    assert advance_6_app["recommended_rrule"] == HOST_6

    settled_6 = _ack_state(
        advance_6,
        runtime_root=tmp_path,
        applied_rrule=HOST_6,
        generated_at=elapsed_3,
    )
    elapsed_6 = elapsed_3 + timedelta(minutes=6)
    advance_10 = _hint(
        monkeypatch,
        bridge,
        now=elapsed_6,
        scheduler_state=settled_6,
        host_rrule=HOST_6,
    )
    assert advance_10["codex_app"]["recommended_rrule"] == HOST_10

    material_work = dict(bridge)
    material_work["effective_action"] = "normal_run"
    material_work["recommended_action"] = "run the matched benchmark arms"
    material_work["interaction_contract"] = {
        **bridge["interaction_contract"],
        "mode": "bounded_delivery",
        "agent_channel": {
            "must_attempt": True,
            "delivery_allowed": True,
            "quiet_noop_allowed": False,
        },
    }
    reset = _hint(
        monkeypatch,
        material_work,
        now=elapsed_6,
        scheduler_state=settled_6,
        host_rrule=HOST_6,
    )
    reset_app = reset["codex_app"]
    assert reset_app["stateful_backoff"]["state_status"] == "reset_required"
    assert reset_app["stateful_backoff"]["progression_index"] == 0
    assert reset_app["recommended_rrule"] == HOST_3

    settled_material = _ack_state(
        reset,
        runtime_root=tmp_path,
        applied_rrule=HOST_3,
        generated_at=elapsed_6,
    )
    still_active = _hint(
        monkeypatch,
        material_work,
        now=elapsed_6 + timedelta(minutes=3),
        scheduler_state=settled_material,
        host_rrule=HOST_3,
    )
    assert still_active["codex_app"]["stateful_backoff"]["progression_index"] == 0
    assert "recommended_rrule" not in still_active["codex_app"]
