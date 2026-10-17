from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopx.control_plane.todos.user_gate import apply_scoped_user_gate_fallback_projection
from loopx.control_plane.quota.scheduler_ack import (
    record_quota_scheduler_ack_for_decision,
)
from loopx.control_plane.quota.stall_repair import (
    build_runtime_capability_user_gate_repair_hint,
)
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.scheduler import scheduler_hint as scheduler_hint_module
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from tests.control_plane.scheduler_arbitration_fixture import build_native_scheduler_hint as build_scheduler_hint
from loopx.control_plane.scheduler.state import (
    SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.quota import build_quota_should_run

GOAL_ID = "user-gate-lane-progress-fixture"
AGENT_ID = "codex-main-control"
APP_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


@pytest.mark.parametrize("action", ["quota_skip", "monitor_quiet_skip", None])
def test_runnable_user_gate_fallback_replaces_a_canonical_skip_action(action):
    original = {"decision": "skip", "should_run": False, "effective_action": action}
    result = apply_scoped_user_gate_fallback_projection(
        original, fallback={"recommended_action": "advance non-gated work"},
        replan_decision_allowed=False,
    )
    assert result["effective_action"] == "scoped_user_gate_fallback"
    assert result["should_run"] is True
    assert original["should_run"] is False


def test_user_gate_fallback_preserves_repair_and_replan_precedence():
    original = {"decision": "run", "should_run": True, "effective_action": "capability_bridge_repair"}
    fallback = {"recommended_action": "advance non-gated work"}
    result = apply_scoped_user_gate_fallback_projection(
        original, fallback=fallback, replan_decision_allowed=False,
    )
    assert result["effective_action"] == "capability_bridge_repair"
    assert apply_scoped_user_gate_fallback_projection(
        original, fallback=fallback, replan_decision_allowed=True,
    ) is original


def _status_payload(
    *,
    gate_action_kind: str,
    blocks_deferred: bool = False,
    include_gated_agent: bool = False,
) -> dict:
    completed = quota_todo_item(
        todo_id="todo_prerequisite",
        status="done",
        text="[P0] Complete the prerequisite.",
        claimed_by=AGENT_ID,
    )
    deferred = quota_todo_item(
        todo_id="todo_ready_deferred",
        status="deferred",
        text="[P1] Continue benchmark treatment refinement.",
        claimed_by=AGENT_ID,
        action_kind="refine_benchmark_treatment",
        resume_when="todo_done:todo_prerequisite",
        done=True,
    )
    gated_agent = quota_todo_item(
        todo_id="todo_first_screen",
        status="open",
        text="[P0] Publish the gated product first screen.",
        claimed_by=AGENT_ID,
        action_kind="publish_product_first_screen",
        required_write_scopes=["docs/**"],
    )
    agent_todos = quota_todo_summary(
        [completed, deferred, *([gated_agent] if include_gated_agent else [])],
        role="agent",
        claim_scope_agent_id=AGENT_ID,
    )
    gate = quota_todo_item(
        todo_id="todo_product_doc_gate",
        role="user",
        status="open",
        task_class="user_gate",
        text="[P2-user] Review the product first screen.",
        action_kind=gate_action_kind,
        blocks_agent=AGENT_ID,
        unblocks_todo_id="todo_ready_deferred" if blocks_deferred else "todo_first_screen",
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Continue benchmark treatment refinement.",
        agent_todos=agent_todos,
        user_todos=quota_todo_summary([gate], role="user"),
        quota_state="operator_gate",
        safe_bypass=True,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        item_extra={
            "long_task_cadence_hint": {
                "schema_version": "cadence_hint_v0",
                "signal": "blocked",
                "recommendation": "wait",
                "reason_codes": [
                    "quota_state_operator_gate",
                    "open_user_todos_visible",
                ],
            }
        },
    )


def _quality_vision_run() -> dict:
    return {
        "classification": "quality_vision_fixture",
        "generated_at": "2026-07-18T00:00:00+00:00",
        "agent_id": AGENT_ID,
        "progress_scope": "agent_lane",
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "agent_id": AGENT_ID,
            "state": "vision_active",
            "vision_patch": {
                "acceptance_summary": "Uncovered quality gaps remain.",
                "advancement_policy": "repeat_until_closed",
                "replan_trigger_summary": (
                    "Replan when no runnable quality advancement remains."
                ),
            },
        },
    }


def test_unrelated_user_gate_allows_ready_deferred_successor_replan() -> None:
    payload = build_quota_should_run(
        _status_payload(
            gate_action_kind="approve_product_first_screen",
            include_gated_agent=True,
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_CONTEXT,
    )

    fallback = payload["scoped_user_gate_fallback"]
    selected = fallback["selected_executable"]
    assert selected["todo_id"] == "todo_ready_deferred"
    assert selected["fallback_kind"] == "deferred_successor_replan"
    assert payload["decision"] == "successor_replan_required"
    assert payload["effective_action"] == "successor_replan_required"
    assert payload["selected_todo"]["todo_id"] == "todo_ready_deferred"
    assert payload["interaction_contract"]["mode"] == "scoped_user_gate_fallback"
    assert payload["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert payload["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    assert payload["requires_user_action"] is True
    assert "response_plan" not in payload["interaction_contract"]
    assert "replan ready deferred successor" in payload["interaction_contract"][
        "agent_channel"
    ]["primary_action"]
    assert payload["scheduler_hint"]["cadence_class"] == "active_work"
    assert payload["long_task_cadence_hint"] == {
        "schema_version": "cadence_hint_v0",
        "signal": "active_work",
        "recommendation": "keep",
        "reason_codes": ["final_agent_scoped_active_work"],
        "authority": "final_agent_scoped_interaction_and_scheduler",
        "superseded": {
            "signal": "blocked",
            "recommendation": "wait",
            "reason_codes": [
                "quota_state_operator_gate",
                "open_user_todos_visible",
            ],
        },
    }


def test_ready_deferred_successor_replan_preempts_workspace_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_todo_ids: list[str | None] = []

    def _record_workspace_guard_selection(
        *args: object,
        selected_todo: dict | None = None,
        **kwargs: object,
    ) -> dict:
        guarded_todo_ids.append((selected_todo or {}).get("todo_id"))
        return {
            "schema_version": "agent_workspace_guard_v1",
            "reason": "delivery would require another workspace",
            "required_action": "move to the delivery workspace",
        }

    monkeypatch.setattr(
        "loopx.control_plane.quota.should_run.build_agent_workspace_guard",
        _record_workspace_guard_selection,
    )

    payload = build_quota_should_run(
        _status_payload(
            gate_action_kind="approve_product_first_screen",
            include_gated_agent=True,
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_CONTEXT,
    )

    assert payload["decision"] == "successor_replan_required"
    assert payload["effective_action"] == "successor_replan_required"
    assert payload["selected_todo"]["todo_id"] == "todo_ready_deferred"
    assert "workspace_guard" not in payload
    assert guarded_todo_ids == []


def test_consumed_review_gate_exposes_quality_vision_replan() -> None:
    completed = quota_todo_item(
        todo_id="todo_completed_design",
        status="done",
        text="[P0] Complete the reviewed design.",
        claimed_by=AGENT_ID,
    )

    def decide(gate_status: str) -> dict:
        gate = quota_todo_item(
            todo_id="todo_owner_review",
            role="user",
            status=gate_status,
            task_class="user_gate",
            text="[P0] Review the design.",
            blocks_agent=AGENT_ID,
        )
        status = quota_status_payload(
            goal_id=GOAL_ID,
            status="active",
            recommended_action="Review the design.",
            agent_todos=quota_todo_summary(
                [completed],
                role="agent",
                claim_scope_agent_id=AGENT_ID,
            ),
            user_todos=quota_todo_summary([gate], role="user"),
            quota_state="operator_gate",
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": [AGENT_ID],
            },
            latest_runs=[_quality_vision_run()],
        )
        return build_quota_should_run(
            status,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            scheduler_execution_context=APP_CONTEXT,
        )

    waiting = decide("open")
    assert waiting["decision"] == "skip"
    assert waiting["interaction_contract"]["mode"] == "user_gate"

    recovered = decide("done")
    assert recovered["decision"] == "autonomous_replan_required"
    assert recovered["should_run"] is True
    assert recovered["goal_frontier_projection"]["acceptance_gaps"][0][
        "kind"
    ] == "vision_acceptance_gap"
    assert recovered["interaction_contract"]["agent_channel"]["must_attempt"] is True


def test_blocking_user_gate_backs_off_instead_of_polling_as_active_work() -> None:
    payload = build_quota_should_run(
        _status_payload(gate_action_kind="refine_benchmark_treatment", blocks_deferred=True),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_CONTEXT,
    )

    assert "scoped_user_gate_fallback" not in payload
    assert payload["interaction_contract"]["mode"] == "user_gate"
    assert payload["interaction_contract"]["agent_channel"]["must_attempt"] is False
    expected_response_plan = {
        "schema_version": "interaction_response_plan_v0",
        "kind": "surface_user_gate",
        "decision": "ask_user",
        "action_sequence": ["notify", "wait"],
        "silent_wait_allowed": False,
    }
    assert payload["interaction_contract"]["response_plan"] == expected_response_plan
    envelope = build_turn_envelope(payload)
    assert envelope["response_plan"] == expected_response_plan
    assert envelope["action_signature"]["matches"] is True
    assert envelope["action_signature"]["coverage"] == (
        "turn_envelope_action_dimensions_v1"
    )
    assert payload["scheduler_hint"]["cadence_class"] == "human_gate"
    assert payload["scheduler_hint"]["codex_app"]["recommended_interval_minutes"] == 30
    assert payload["long_task_cadence_hint"] == {
        "schema_version": "cadence_hint_v0",
        "signal": "blocked",
        "recommendation": "wait",
        "reason_codes": [
            "quota_state_operator_gate",
            "open_user_todos_visible",
        ],
    }

    initial_backoff = payload["scheduler_hint"]["codex_app"]["stateful_backoff"]
    next_hint = build_scheduler_hint(
        payload,
        user_action_required=True,
        codex_app_scheduler_state={
            "reset_token": initial_backoff["reset_token"],
            "identity_signature": initial_backoff["identity_signature"],
            "progression_index": initial_backoff["progression_index"],
            "last_applied_rrule": initial_backoff["current_rrule"],
        },
        codex_app_current_rrule=initial_backoff["current_rrule"],
        scheduler_execution_context=APP_CONTEXT,
    )

    assert next_hint["cadence_class"] == "human_gate"
    assert next_hint["codex_app"]["recommended_interval_minutes"] == 60
    assert next_hint["codex_app"]["stateful_backoff"]["progression_index"] == 1
    assert next_hint["codex_app"]["stateful_backoff"]["apply_needed"] is True
    assert next_hint["codex_app"]["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=60"


def _runtime_recovery_gate_status(
    *,
    decision_scope: str | None = None,
    required_capabilities: list[str] | None = None,
) -> dict:
    target = quota_todo_item(
        todo_id="todo_runtime_recovery",
        status="blocked",
        text="[P0] Restore the benchmark runtime and continue.",
        claimed_by=AGENT_ID,
        action_kind="repair_benchmark_runtime",
        required_capabilities=required_capabilities
        or ["shell", "filesystem_write", "network", "benchmark_runner"],
    )
    gate_metadata = {
        "unblocks_todo_id": target["todo_id"],
    }
    if decision_scope:
        gate_metadata["decision_scope"] = decision_scope
    gate = quota_todo_item(
        todo_id="todo_restore_runtime",
        role="user",
        status="open",
        task_class="user_gate",
        text="[P0-user] Restore the local runtime endpoint.",
        action_kind="restore_local_runtime_endpoint",
        blocks_agent=AGENT_ID,
        **gate_metadata,
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Restore the benchmark runtime and continue.",
        agent_todos=quota_todo_summary(
            [target],
            role="agent",
            claim_scope_agent_id=AGENT_ID,
        ),
        user_todos=quota_todo_summary([gate], role="user"),
        quota_state="operator_gate",
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
    )


def test_capability_runnable_runtime_recovery_gate_routes_to_agent_repair() -> None:
    payload = build_quota_should_run(
        _runtime_recovery_gate_status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=[
            "shell",
            "filesystem_write",
            "network",
            "benchmark_runner",
        ],
        scheduler_execution_context=APP_CONTEXT,
    )

    repair = payload["stall_self_repair"]
    assert repair["trigger"] == "runtime_capability_user_gate_overreach"
    assert repair["gate_todo_id"] == "todo_restore_runtime"
    assert repair["target_todo_id"] == "todo_runtime_recovery"
    assert payload["decision"] == "self_repair"
    assert payload["self_repair_allowed"] is True
    assert payload["requires_user_action"] is False
    assert payload["interaction_contract"]["mode"] == "control_plane_self_repair"
    assert payload["interaction_contract"]["user_channel"] == {
        "action_required": False,
        "notify": "DONT_NOTIFY",
        "reason": repair["reason"],
    }
    assert payload["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert "response_plan" not in payload["interaction_contract"]


def test_empty_authoritative_user_source_ignores_stale_summary_gate() -> None:
    status = _runtime_recovery_gate_status()
    item = status["attention_queue"]["items"][0]

    repair = build_runtime_capability_user_gate_repair_hint(
        user_todo_summary=item["user_todos"],
        agent_todo_summary=item["agent_todos"],
        user_todo_source_items=[],
        agent_todo_source_items=item["agent_todos"]["items"],
        agent_id=AGENT_ID,
        available_capabilities=[
            "shell",
            "filesystem_write",
            "network",
            "benchmark_runner",
        ],
    )

    assert repair is None


def test_runtime_recovery_gate_with_decision_scope_remains_owner_gate() -> None:
    payload = build_quota_should_run(
        _runtime_recovery_gate_status(
            decision_scope="direction:action:runtime_recovery",
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=[
            "shell",
            "filesystem_write",
            "network",
            "benchmark_runner",
        ],
        scheduler_execution_context=APP_CONTEXT,
    )

    assert "stall_self_repair" not in payload
    assert payload["interaction_contract"]["mode"] == "user_gate"
    assert payload["interaction_contract"]["user_channel"]["action_required"] is True
    assert (
        payload["interaction_contract"]["response_plan"]["kind"] == "surface_user_gate"
    )


def test_runtime_recovery_gate_with_owner_capability_remains_owner_gate() -> None:
    payload = build_quota_should_run(
        _runtime_recovery_gate_status(required_capabilities=["credentials"]),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=["credentials"],
        scheduler_execution_context=APP_CONTEXT,
    )

    assert "stall_self_repair" not in payload
    assert payload["interaction_contract"]["mode"] == "user_gate"
    assert payload["interaction_contract"]["user_channel"]["action_required"] is True


def test_acked_human_gate_advances_despite_unrelated_historical_host_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_hint_module, "now_utc", lambda: now)
    payload = build_quota_should_run(
        _status_payload(gate_action_kind="refine_benchmark_treatment", blocks_deferred=True),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_CONTEXT,
    )
    first_rrule = payload["scheduler_hint"]["codex_app"]["stateful_backoff"][
        "current_rrule"
    ]
    historical_failure = {
        "schema_version": SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
        "target_rrule": "FREQ=MINUTELY;INTERVAL=3",
        "observed_host_rrule": first_rrule,
        "failure_kind": "timeout",
        "failure_count": 1,
        "failed_at": now.isoformat(),
    }

    host_matched = build_scheduler_hint(
        payload,
        user_action_required=True,
        codex_app_scheduler_state={
            "reset_token": "previous-active-work",
            "identity_signature": "previous-active-work",
            "progression_index": 0,
            "progression_minutes": [3, 6, 10],
            "last_applied_rrule": first_rrule,
            "updated_at": now.isoformat(),
            "host_update_failures": [historical_failure],
        },
        codex_app_current_rrule=first_rrule,
        scheduler_execution_context=APP_CONTEXT,
    )
    matched_app = host_matched["codex_app"]
    assert matched_app["stateful_backoff"]["apply_needed"] is False
    assert matched_app["stateful_backoff"]["ack_needed"] is True
    assert matched_app["ack_hint"]["after"] == "matching_host_rrule_observed"

    backoff = matched_app["stateful_backoff"]
    fallback_ack = record_quota_scheduler_ack_for_decision(
        {"goal_id": GOAL_ID, "scheduler_hint": host_matched},
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        execute=True,
        applied_rrule=first_rrule,
        reset_token=backoff["reset_token"],
        identity_signature=backoff["identity_signature"],
        host_match_observed=True,
        generated_at=now.isoformat(),
    )
    assert fallback_ack["ok"] is True, fallback_ack
    settled_state = fallback_ack["scheduler_ack_event"]["scheduler_state"]
    assert settled_state["last_applied_rrule"] == first_rrule
    assert settled_state["host_update_failures"] == [historical_failure]

    elapsed = now + timedelta(minutes=30)
    monkeypatch.setattr(scheduler_hint_module, "now_utc", lambda: elapsed)
    next_hint = build_scheduler_hint(
        payload,
        user_action_required=True,
        codex_app_scheduler_state=settled_state,
        codex_app_current_rrule=first_rrule,
        scheduler_execution_context=APP_CONTEXT,
    )

    next_app = next_hint["codex_app"]
    assert next_app["stateful_backoff"]["progression_index"] == 1
    assert next_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=60"
    assert next_app["stateful_backoff"]["apply_needed"] is True
