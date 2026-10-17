#!/usr/bin/env python3
"""Smoke-test the extracted quota scheduler policy helper."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.scheduler.scheduler_hint import build_scheduler_hint  # noqa: E402
from loopx.control_plane.agents.agent_scope_frontier import AgentScopeFrontierAction  # noqa: E402
from loopx.control_plane.quota.should_run_packet import _scheduler_hint  # noqa: E402
from loopx.control_plane.work_items.interaction_contract import (  # noqa: E402
    _interaction_reads_and_arbitration,
)


AGENT_SCOPE_ACTIONS = [action.value for action in AgentScopeFrontierAction]
CODEX_APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
)


def payload(
    *,
    should_run: bool,
    effective_action: str,
    recommended_mode: str = "",
    automation_action: str = "",
    user_required: bool = False,
    stop_if_unchanged: bool = False,
) -> dict:
    mode = (
        effective_action
        if effective_action == "monitor_quiet_skip"
        else recommended_mode or effective_action
    )
    must_attempt = bool(should_run and mode != "mapped_noop_if_unchanged")
    return {
        "goal_id": "scheduler-policy-smoke",
        "should_run": should_run,
        "effective_action": effective_action,
        "recommended_action": "Run the scheduler policy smoke.",
        "heartbeat_recommendation": {
            "recommended_mode": recommended_mode,
            "notify": "DONT_NOTIFY",
            "spend_policy": "spend only after validated writeback",
            "stop_if_unchanged": stop_if_unchanged,
        },
        "execution_obligation": {
            "must_attempt_work": should_run,
            "spend_policy": "execution obligation spend policy",
        },
        "automation_liveness": {
            "automation_action": automation_action,
            "spend_policy": "automation liveness spend policy",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": mode,
            "user_channel": {
                "action_required": user_required,
            },
            "agent_channel": {
                "must_attempt": must_attempt,
                "delivery_allowed": must_attempt,
                "quiet_noop_allowed": not user_required and not must_attempt,
            },
            "cli_channel": {},
        },
    }


def assert_policy_case(
    name: str,
    base_payload: dict,
    *,
    expected_action: str,
    expected_rrule: str,
    expected_progression: list[int] | None = None,
    expected_same_identity_action: str = "advance_index_after_applied_interval_elapsed",
) -> None:
    base_payload = deepcopy(base_payload)
    _, arbitration = _interaction_reads_and_arbitration(
        base_payload,
        base_payload["interaction_contract"],
        runtime_root=None,
        registry_path=None,
        agent_scope_frontier_actions=AGENT_SCOPE_ACTIONS,
    )
    quota_wrapper = _scheduler_hint(
        deepcopy(base_payload),
        arbitration=arbitration,
        scheduler_execution_context=CODEX_APP_SCHEDULER_CONTEXT,
    )
    extracted = build_scheduler_hint(
        deepcopy(base_payload),
        user_action_required=bool(
            base_payload.get("interaction_contract", {})
            .get("user_channel", {})
            .get("action_required")
        ),
        arbitration=arbitration,
        scheduler_execution_context=CODEX_APP_SCHEDULER_CONTEXT,
    )
    detailed = build_scheduler_hint(
        deepcopy(base_payload),
        user_action_required=bool(
            base_payload.get("interaction_contract", {})
            .get("user_channel", {})
            .get("action_required")
        ),
        arbitration=arbitration,
        include_detail=True,
        scheduler_execution_context=CODEX_APP_SCHEDULER_CONTEXT,
    )
    assert extracted == quota_wrapper, (name, extracted, quota_wrapper)
    assert extracted["schema_version"] == "scheduler_hint_v0", (name, extracted)
    assert extracted["source"] == "quota.should-run", (name, extracted)
    assert extracted["action"] == expected_action, (name, extracted)
    assert extracted["app_automation"]["recommended_rrule"] == expected_rrule, (name, extracted)
    assert extracted["app_automation"]["host_tool"] == "automation_update", (name, extracted)
    assert extracted["app_automation"]["host_action"] == "update_current_heartbeat_rrule", (name, extracted)
    assert extracted["app_automation"]["rrule_source"] == "scheduler_hint.app_automation.recommended_rrule", (
        name,
        extracted,
    )
    stateful_backoff = extracted["app_automation"]["stateful_backoff"]
    if expected_progression is not None:
        assert extracted["app_automation"]["example_progression_minutes"] == expected_progression, (
            name,
            extracted,
        )
    assert stateful_backoff["schema_version"] == "app_automation_stateful_backoff_v0", (name, extracted)
    assert stateful_backoff["state_key"] == "scheduler_hint.app_automation.stateful_backoff", (name, extracted)
    assert stateful_backoff["apply_needed"] is True, (name, extracted)
    assert stateful_backoff["current_rrule"] == expected_rrule, (name, extracted)
    assert stateful_backoff["state_status"] == "missing", (name, extracted)
    for omitted in (
        "progression_minutes",
        "current_interval_minutes",
        "ack_required_after_apply",
        "same_identity_action",
        "reset_action",
        "automation_update_scope",
    ):
        assert omitted not in stateful_backoff, (name, omitted, extracted)
    assert "local_scheduler" not in extracted, (name, extracted)
    assert "codex_cli_tui" not in extracted, (name, extracted)
    assert "claude_code_loop" not in extracted, (name, extracted)
    assert "cold_path_detail" not in extracted, (name, extracted)
    assert extracted["detail_ref"]["request"] == "loopx quota should-run --include-detail scheduler", (name, extracted)
    assert detailed["cold_path_detail"]["local_scheduler"]["recommended_interval_minutes"], (name, detailed)
    assert detailed["cold_path_detail"]["codex_cli_tui"]["final_quota_replan_check"], (name, detailed)
    assert detailed["cold_path_detail"]["claude_code_loop"]["after_limit"], (name, detailed)
    stateful_detail = detailed["cold_path_detail"]["stateful_backoff_detail"]
    if expected_progression is not None:
        assert stateful_detail["progression_minutes"] == expected_progression, (name, detailed)
    assert stateful_detail["ack_required_after_apply"] is True, (name, detailed)
    assert stateful_detail["same_identity_action"] == expected_same_identity_action, (
        name,
        detailed,
    )
    assert stateful_detail["reset_action"] == "clear_progression_index_apply_initial_rrule", (
        name,
        detailed,
    )
    assert stateful_detail["automation_update_scope"] == "rrule_only_preserve_body_name_status", (
        name,
        detailed,
    )
    reset = extracted["reset_policy"]
    assert isinstance(reset["reset_token"], str) and len(reset["reset_token"]) == 16, (name, reset)
    assert len(reset["identity_signature"]) == 12, (name, reset)
    reset_detail = detailed["cold_path_detail"]["reset_policy_detail"]
    assert reset_detail["schema_version"] == "scheduler_reset_policy_v0", (name, reset_detail)
    assert reset_detail["app_automation_tool"] == "automation_update", (name, reset_detail)
    assert "automation_update" in reset_detail["app_automation_apply"], (name, reset_detail)
    assert len(reset_detail["profile_signature"]) == 12, (name, reset_detail)
    assert stateful_backoff["reset_token"] == reset["reset_token"], (name, reset)
    assert stateful_backoff["identity_signature"] == reset["identity_signature"], (name, reset)
    assert "identity_snapshot" not in reset, (name, reset)
    assert "profile_snapshot" not in reset, (name, reset)


def main() -> int:
    assert_policy_case(
        "active-work",
        payload(should_run=True, effective_action="normal_run"),
        expected_action="run_now",
        expected_rrule="FREQ=MINUTELY;INTERVAL=3",
        expected_same_identity_action="keep_initial_interval_while_active_work",
    )
    assert_policy_case(
        "mapped-noop",
        payload(
            should_run=True,
            effective_action="normal_run",
            recommended_mode="mapped_noop_if_unchanged",
            stop_if_unchanged=True,
        ),
        expected_action="backoff_until_fresh_evidence",
        expected_rrule="FREQ=MINUTELY;INTERVAL=60",
    )
    assert_policy_case(
        "human-gate",
        payload(
            should_run=False,
            effective_action="operator_gate_notify",
            recommended_mode="ask_operator_gate",
            user_required=True,
        ),
        expected_action="backoff_waiting_for_user",
        expected_rrule="FREQ=MINUTELY;INTERVAL=30",
    )
    assert_policy_case(
        "agent-scope-wait",
        payload(
            should_run=False,
            effective_action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT.value,
        ),
        expected_action="backoff_until_reassigned",
        expected_rrule="FREQ=MINUTELY;INTERVAL=10",
        expected_progression=[10, 20, 30, 60],
    )
    assert_policy_case(
        "monitor-wait",
        payload(
            should_run=False,
            effective_action="monitor_quiet_skip",
            recommended_mode="monitor_quiet_until_material_transition",
        ),
        expected_action="backoff_until_material_transition",
        expected_rrule="FREQ=MINUTELY;INTERVAL=15",
        expected_progression=[15, 30, 60],
    )
    assert_policy_case(
        "quiet-wait",
        payload(should_run=False, effective_action="quota_skip"),
        expected_action="backoff_until_state_change",
        expected_rrule="FREQ=MINUTELY;INTERVAL=30",
    )
    print("ok: quota scheduler policy helper matches quota wrapper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
