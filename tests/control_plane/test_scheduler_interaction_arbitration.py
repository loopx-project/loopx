from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from loopx.control_plane.scheduler import scheduler_hint as scheduler_hint_module
from loopx.control_plane.scheduler.arbitration import (
    SchedulerDisposition,
)
from tests.control_plane.scheduler_arbitration_fixture import (
    build_native_scheduler_hint as build_scheduler_hint,
    project_native_arbitration,
)
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.work_items.interaction_contract import (
    build_interaction_contract,
)


AGENT_SCOPE_ACTIONS = {
    "agent_scope_exhausted",
    "agent_scope_wait",
    "reassignment_required",
    "successor_replan_required",
}
APP_CONTEXT = scheduler_execution_context_for_runtime_profile("codex_app_heartbeat")


def _app_scheduler_hint(payload: dict, **kwargs) -> dict:
    return build_scheduler_hint(
        payload,
        scheduler_execution_context=APP_CONTEXT,
        **kwargs,
    )


def _payload(
    *,
    mode: str,
    should_run: bool,
    user_required: bool,
    must_attempt: bool,
    delivery_allowed: bool,
    quiet_noop_allowed: bool,
) -> dict:
    return {
        "goal_id": "scheduler-authority-test",
        "agent_identity": {"agent_id": "codex-quality-qualification"},
        "should_run": should_run,
        "effective_action": mode,
        "recommended_action": "Exercise the final interaction decision.",
        "heartbeat_recommendation": {
            "recommended_mode": mode,
            "notify": "NOTIFY" if user_required else "DONT_NOTIFY",
            "spend_policy": "spend only after validated writeback",
        },
        "execution_obligation": {
            "must_attempt_work": should_run,
            "spend_policy": "spend only after validated writeback",
        },
        "automation_liveness": {
            "automation_action": "execute_bounded_work"
            if should_run
            else "keep_active_quiet",
            "spend_policy": "spend only after validated writeback",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": mode,
            "user_channel": {"action_required": user_required, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": must_attempt,
                "delivery_allowed": delivery_allowed,
                "quiet_noop_allowed": quiet_noop_allowed,
            },
            "cli_channel": {"next_cli_actions": [], "spend_allowed_now": False},
        },
    }


@pytest.mark.parametrize(
    ("name", "payload", "disposition", "cadence"),
    [
        (
            "blocking-gate",
            _payload(
                mode="user_gate",
                should_run=False,
                user_required=True,
                must_attempt=False,
                delivery_allowed=False,
                quiet_noop_allowed=False,
            ),
            SchedulerDisposition.HUMAN_GATE,
            "human_gate",
        ),
        (
            "nonblocking-notice-with-work",
            _payload(
                mode="bounded_delivery_with_user_notice",
                should_run=True,
                user_required=True,
                must_attempt=True,
                delivery_allowed=True,
                quiet_noop_allowed=False,
            ),
            SchedulerDisposition.ACTIVE_WORK,
            "active_work",
        ),
        (
            "repair-only",
            _payload(
                mode="automation_prompt_upgrade",
                should_run=False,
                user_required=False,
                must_attempt=True,
                delivery_allowed=False,
                quiet_noop_allowed=False,
            ),
            SchedulerDisposition.ACTIVE_WORK,
            "active_work",
        ),
        (
            "mapped-compatibility",
            _payload(
                mode="mapped_noop_if_unchanged",
                should_run=True,
                user_required=False,
                must_attempt=False,
                delivery_allowed=False,
                quiet_noop_allowed=True,
            ),
            SchedulerDisposition.UNCHANGED_WAIT,
            "unchanged_noop",
        ),
        (
            "successor-replan",
            _payload(
                mode="successor_replan_required",
                should_run=True,
                user_required=False,
                must_attempt=True,
                delivery_allowed=False,
                quiet_noop_allowed=False,
            ),
            SchedulerDisposition.ACTIVE_WORK,
            "active_work",
        ),
        (
            "terminal-no-followup",
            _payload(
                mode="terminal_no_followup",
                should_run=False,
                user_required=False,
                must_attempt=False,
                delivery_allowed=False,
                quiet_noop_allowed=True,
            ),
            SchedulerDisposition.TERMINAL_STOP,
            "terminal_no_followup",
        ),
        (
            "peer-coordination-blocked",
            _payload(
                mode="peer_coordination_blocked",
                should_run=False,
                user_required=False,
                must_attempt=False,
                delivery_allowed=False,
                quiet_noop_allowed=True,
            ),
            SchedulerDisposition.PEER_COORDINATION_WAIT,
            "peer_coordination_wait",
        ),
    ],
)
def test_interaction_contract_drives_scheduler(
    name: str,
    payload: dict,
    disposition: SchedulerDisposition,
    cadence: str,
) -> None:
    arbitration = project_native_arbitration(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )
    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert arbitration.ok, (name, arbitration)
    assert arbitration.disposition == disposition, (name, arbitration)
    assert hint["cadence_class"] == cadence, (name, hint)
    assert hint["reason_code"] == arbitration.reason_code, (name, hint)


@pytest.mark.parametrize(
    ("effective_action", "obligation_kind", "expected_mode"),
    [
        (
            "successor_replan_required",
            "successor_replan_required",
            "successor_replan_required",
        ),
        (
            "external_evidence_observation_required",
            "external_evidence_observation_required",
            "external_evidence_observation",
        ),
        (
            "boundary_projection_repair",
            "boundary_projection_repair",
            "boundary_projection_repair",
        ),
    ],
)
def test_nonblocking_user_action_preserves_required_non_delivery_work(
    effective_action: str,
    obligation_kind: str,
    expected_mode: str,
) -> None:
    user_action = {
        "todo_id": "todo_optional_user_action",
        "status": "open",
        "task_class": "user_action",
        "text": "Review the optional setting.",
    }
    payload = {
        "goal_id": "required-non-delivery-work",
        "state": "eligible",
        "should_run": True,
        "effective_action": effective_action,
        "normal_delivery_allowed": False,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "agent_identity": {"agent_id": "codex-fixture"},
        "heartbeat_recommendation": {
            "notify": "DONT_NOTIFY",
            "spend_policy": "spend only after validated writeback",
        },
        "execution_obligation": {
            "kind": obligation_kind,
            "must_attempt_work": True,
            "delivery_allowed": False,
        },
        "user_todo_summary": {
            "first_open_items": [user_action],
            "user_action_items": [user_action],
        },
        # A ready deferred successor is intentionally absent from the ordinary
        # executable lane.  Its selected lifecycle obligation is still work.
        "agent_todo_summary": {"first_executable_items": []},
        "selected_todo": {"todo_id": "todo_ready_deferred"},
        "agent_scope_frontier": {
            "deferred_resume_candidates": [{"todo_id": "todo_ready_deferred"}]
        },
    }

    payload["interaction_contract"] = build_interaction_contract(
        payload,
        scheduler_execution_context=APP_CONTEXT,
    )
    contract = payload["interaction_contract"]
    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert contract["mode"] == expected_mode, contract
    assert contract["user_channel"] == {
        "action_required": False,
        "notify": "NOTIFY",
        "max_items": 3,
        "actions": ["Review the optional setting."],
        "non_blocking": True,
        "reason": (
            "open non-blocking user action should be surfaced while "
            "independent agent work continues"
        ),
    }
    assert contract["agent_channel"]["must_attempt"] is True
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["agent_channel"]["quiet_noop_allowed"] is False
    assert "response_plan" not in contract
    assert hint["action"] == "run_now", hint
    assert hint["cadence_class"] == "active_work", hint


def test_true_user_gate_still_blocks_required_non_delivery_work() -> None:
    user_gate = {
        "todo_id": "todo_owner_decision",
        "status": "open",
        "task_class": "user_gate",
        "text": "Approve the protected release.",
    }
    payload = {
        "goal_id": "blocking-user-gate",
        "state": "operator_gate",
        "should_run": True,
        "effective_action": "successor_replan_required",
        "normal_delivery_allowed": False,
        "agent_identity": {"agent_id": "codex-fixture"},
        "heartbeat_recommendation": {"notify": "NOTIFY"},
        "execution_obligation": {
            "kind": "successor_replan_required",
            "must_attempt_work": True,
            "delivery_allowed": False,
        },
        "user_todo_summary": {"first_open_items": [user_gate]},
        "agent_todo_summary": {"first_executable_items": []},
    }

    payload["interaction_contract"] = build_interaction_contract(
        payload,
        scheduler_execution_context=APP_CONTEXT,
    )
    contract = payload["interaction_contract"]
    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert contract["mode"] == "user_gate"
    assert contract["user_channel"]["action_required"] is True
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["response_plan"]["action_sequence"] == ["notify", "wait"]
    assert hint["action"] == "backoff_waiting_for_user"
    assert hint["codex_app"]["recommended_interval_minutes"] == 30
    assert hint["codex_app"]["example_progression_minutes"] == [30, 60]


def test_human_gate_respects_a_tighter_continuous_monitor_deadline(
    monkeypatch,
) -> None:
    current_time = datetime(2026, 8, 25, 23, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(
        scheduler_hint_module,
        "now_utc",
        lambda: current_time,
    )
    payload = _payload(
        mode="user_gate",
        should_run=False,
        user_required=True,
        must_attempt=False,
        delivery_allowed=False,
        quiet_noop_allowed=False,
    )
    payload["agent_todo_summary"] = {
        "current_agent_claimed_monitor_items": [
            {
                "todo_id": "todo_deadline_sensitive_monitor",
                "task_class": "continuous_monitor",
                "cadence": "3m",
                "next_due_at": (current_time + timedelta(minutes=3)).isoformat(),
                "target_key": "deadline-sensitive-monitor",
            }
        ]
    }

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
        include_detail=True,
    )

    assert hint["action"] == "backoff_waiting_for_user"
    assert hint["cadence_class"] == "human_gate"
    assert hint["codex_app"]["recommended_interval_minutes"] == 3
    assert hint["codex_app"]["example_progression_minutes"] == [3]
    assert hint["cold_path_detail"]["cadence_context"]["cap_minutes"] == 3
    assert hint["reset_policy"]["app_automation_initial_interval_minutes"] == 3


def test_raw_should_run_cannot_override_blocking_gate() -> None:
    payload = _payload(
        mode="user_gate",
        should_run=True,
        user_required=True,
        must_attempt=False,
        delivery_allowed=False,
        quiet_noop_allowed=False,
    )

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["action"] == "backoff_waiting_for_user"
    assert hint["cadence_class"] == "human_gate"
    assert "consistency_error" not in hint


def test_raw_should_run_false_cannot_silently_cancel_final_contract_delivery() -> None:
    payload = _payload(
        mode="bounded_delivery",
        should_run=False,
        user_required=False,
        must_attempt=True,
        delivery_allowed=True,
        quiet_noop_allowed=False,
    )

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["cadence_class"] == "active_work"
    assert hint["action"] == "run_now"
    assert "consistency_error" not in hint


def test_branch_order_mutation_is_killed_by_final_contract() -> None:
    payload = _payload(
        mode="user_gate",
        should_run=False,
        user_required=True,
        must_attempt=False,
        delivery_allowed=False,
        quiet_noop_allowed=False,
    )
    mutated = deepcopy(payload)
    mutated["automation_liveness"]["automation_action"] = "execute_bounded_work"
    mutated["execution_obligation"]["must_attempt_work"] = True

    hint = _app_scheduler_hint(
        mutated,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["cadence_class"] == "human_gate"
    assert hint["action"] != "run_now"


def test_raw_terminal_liveness_cannot_override_active_final_contract() -> None:
    payload = _payload(
        mode="bounded_delivery",
        should_run=True,
        user_required=False,
        must_attempt=True,
        delivery_allowed=True,
        quiet_noop_allowed=False,
    )
    payload["automation_liveness"]["automation_action"] = "stop_terminal_no_followup"

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["action"] == "run_now"
    assert hint["cadence_class"] == "active_work"


def test_terminal_state_is_projected_into_final_interaction_contract() -> None:
    contract = build_interaction_contract(
        {
            "goal_id": "terminal-contract-test",
            "state": "terminal_no_followup",
            "effective_action": "terminal_no_followup",
            "should_run": False,
            "normal_delivery_allowed": False,
            "recovery_delivery_allowed": False,
            "self_repair_allowed": False,
            "heartbeat_recommendation": {"notify": "DONT_NOTIFY"},
            "execution_obligation": {"must_attempt_work": False},
        }
    )

    assert contract["mode"] == "terminal_no_followup"
    assert contract["user_channel"]["action_required"] is False
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["agent_channel"]["quiet_noop_allowed"] is True
    assert contract["cli_channel"]["spend_after_validation"] is False


def test_quiet_monitor_cannot_inherit_delivery_without_attempt() -> None:
    payload = {
        "goal_id": "monitor-delivery-invariant",
        "state": "eligible",
        "should_run": True,
        "normal_delivery_allowed": True,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "effective_action": "monitor_quiet_skip",
        "heartbeat_recommendation": {"notify": "DONT_NOTIFY"},
        "execution_obligation": {
            "kind": "work_lane_contract",
            "must_attempt_work": False,
        },
    }

    contract = build_interaction_contract(
        payload,
        scheduler_execution_context=APP_CONTEXT,
    )

    assert contract["mode"] == "monitor_quiet_skip"
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["agent_channel"]["quiet_noop_allowed"] is True


def test_terminal_contract_with_open_action_fails_closed() -> None:
    payload = _payload(
        mode="terminal_no_followup",
        should_run=False,
        user_required=True,
        must_attempt=False,
        delivery_allowed=False,
        quiet_noop_allowed=False,
    )

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["action"] == "repair_interaction_contract_projection"
    assert (
        "interaction_contract.terminal_conflicts_with_open_action"
        in hint["consistency_error"]["errors"]
    )


def test_blocked_peer_coordination_uses_stateful_backoff() -> None:
    payload = _payload(
        mode="peer_coordination_blocked",
        should_run=False,
        user_required=False,
        must_attempt=False,
        delivery_allowed=False,
        quiet_noop_allowed=True,
    )

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["action"] == "backoff_until_reassigned"
    assert hint["cadence_class"] == "peer_coordination_wait"
    assert hint["codex_app"]["host_action"] == "update_current_heartbeat_rrule"
    assert hint["codex_app"]["stateful_backoff"]["apply_needed"] is True
    assert hint["codex_app"]["recommended_interval_minutes"] == 10
    assert hint["codex_app"]["example_progression_minutes"] == [10, 20, 30, 60]
    assert hint["unchanged_poll"]["final_quota_replan_check_enabled"] is True


def test_structurally_invalid_contract_fails_closed() -> None:
    payload = _payload(
        mode="bounded_delivery",
        should_run=True,
        user_required=False,
        must_attempt=True,
        delivery_allowed=True,
        quiet_noop_allowed=False,
    )
    del payload["interaction_contract"]["agent_channel"]["quiet_noop_allowed"]

    hint = _app_scheduler_hint(
        payload,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )

    assert hint["cadence_class"] == "control_plane_repair"
    assert (
        "interaction_contract.agent_channel.quiet_noop_allowed_must_be_boolean"
        in hint["consistency_error"]["errors"]
    )


@pytest.mark.parametrize("flag", [None, 0, 1, "false"])
def test_python_source_codec_does_not_coerce_non_boolean_authority(flag) -> None:
    payload = _payload(mode="\u0085bounded_delivery\u001c", should_run=True,
        user_required=False, must_attempt=True, delivery_allowed=True, quiet_noop_allowed=False)
    payload["interaction_contract"]["user_channel"]["action_required"] = flag
    original = deepcopy(payload)

    arbitration = project_native_arbitration(payload)

    assert arbitration.mode == "bounded_delivery"
    assert arbitration.disposition is SchedulerDisposition.CONSISTENCY_REPAIR
    assert arbitration.errors == ("interaction_contract.user_channel.action_required_must_be_boolean",)
    assert payload == original


@pytest.mark.parametrize("minutes,progression", [
    (1, [1]), (11, [10, 11]), (25, [10, 20, 25]), (61, [10, 20, 30, 60]),
])
def test_automatic_dated_wait_bounds_rechecks_without_finite_stop(minutes, progression):
    payload = _payload(mode="agent_scope_wait", should_run=False, user_required=False,
        must_attempt=False, delivery_allowed=False, quiet_noop_allowed=True)
    evaluated = datetime(2099, 1, 1, tzinfo=timezone.utc)
    due = (evaluated + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    resume = f"resume_at:{due}"
    payload["vision_wait_state"] = {
        "schema_version": "goal_vision_wait_state_v0", "state": "waiting",
        "reason_code": "exact_blocked_successor", "automatic_resume": True,
        "agent_id": payload["agent_identity"]["agent_id"], "waiting_todo_count": 1,
        "selected_todo_id": "todo_scheduled", "selected_todo_status": "deferred",
        "resume_when": resume, "resume_condition": {
            "schema_version": "todo_resume_condition_v0", "kind": "resume_at",
            "resume_when": resume, "target": due, "satisfied": False,
            "scheduled_for": due, "evaluated_at": "2099-01-01T00:00:00Z",
            "clock_provider": "runtime_clock", "material_change": False,
            "material_change_generation": 0, "resume_receipt": None,
        },
    }
    original = deepcopy(payload)
    hint = build_scheduler_hint(payload, agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile("generic_cli"),
        include_detail=True)
    local = hint["cold_path_detail"]["local_scheduler"]
    assert hint["action"] == "backoff_until_state_change"
    assert local["example_progression_minutes"] == progression
    assert all(limit is None for limit in hint["unchanged_poll"]["limits"].values())
    assert set(hint["unchanged_poll"]["after_limits"].values()) == {"continue"}
    assert payload == original
