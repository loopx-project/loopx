#!/usr/bin/env python3
"""Smoke-test multi-project quota plan ordering."""

from __future__ import annotations

import json
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_RUNTIME_ROOT = str((REPO_ROOT / "fixtures/runtime").resolve())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import (  # noqa: E402
    build_quota_plan,
    build_quota_should_run,
    build_quota_slot_spend_event,
    build_quota_slot_preview,
    goal_quota_with_spend_ledger,
    goal_quota_config,
    quota_status,
    render_quota_markdown,
    render_quota_should_run_markdown,
)
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)

from quota_plan_fixtures import (  # noqa: E402
    _nested_value,
    _short_hash,
    assert_dry_run_left_cli_fixture_unchanged,
    assert_quota_void_event_net_ledger,
    assert_slot_preview,
    assert_slot_spend_execute,
    assert_slot_void_execute,
    assert_throttled_cli_should_run,
    attention,
    build_status_fixture,
    expected_scheduler_reset_token,
    goal,
    run_cli_quota_plan,
    run_cli_slot_preview,
    run_cli_slot_spend_execute,
    run_cli_slot_void_execute,
    run_cli_throttled_should_run,
    scheduler_reset_profile_snapshot,
)


APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


def assert_plan_shape(plan: dict, markdown: str | None = None) -> None:
    eligible_ids = [item["goal_id"] for item in plan["groups"]["eligible"]]
    operator_gate_ids = [item["goal_id"] for item in plan["groups"]["operator_gate"]]
    throttled_ids = [item["goal_id"] for item in plan["groups"]["throttled"]]

    assert plan["summary"]["next_automatic_turn"] == "full-speed", plan
    assert plan["next_automatic_turn"]["goal_id"] == "full-speed", plan
    assert eligible_ids == ["full-speed", "half-speed", "near-limit-half", "low-speed"], eligible_ids
    assert operator_gate_ids == ["needs-operator"], operator_gate_ids
    assert throttled_ids == ["throttled-half"], throttled_ids
    assert "needs-operator" not in eligible_ids, eligible_ids
    gated = plan["groups"]["operator_gate"][0]
    assert gated["quota"]["safe_bypass_allowed"] is True, gated
    assert gated["quota"]["blocked_action_scope"] == "gated_delivery", gated
    assert "operator_question" in gated, gated
    if "user_todos" in gated:
        assert gated["user_todos"]["open_count"] == 1, gated
    assert "throttled-half" not in eligible_ids, eligible_ids
    if markdown is not None:
        assert "next_automatic_turn=full-speed" in markdown, markdown
        assert "### operator_gate" in markdown, markdown
        assert "### throttled" in markdown, markdown
        assert "`throttled-half`" in markdown, markdown
        assert markdown.index("`full-speed`") < markdown.index("`half-speed`") < markdown.index("`near-limit-half`") < markdown.index("`low-speed`"), markdown


def assert_scheduler_advisory_does_not_override_goal_should_run() -> None:
    meta_id = "loopx-meta"
    creator_id = "showcase-creator-operator"
    side_bypass_id = "showcase-side-agent-self-iteration"
    gated_id = "owner-gated"
    meta_goal = goal(meta_id, compute=0.5)
    creator_goal = goal(creator_id, compute=1.0)
    side_bypass_goal = goal(side_bypass_id, compute=0.8)
    gated_goal = goal(gated_id, compute=1.0)

    meta_item = attention(meta_id, compute=0.5)
    meta_item["recommended_action"] = "advance one gate-independent LoopX backlog item"
    meta_item["agent_todos"] = {
        "source_section": "Agent Todo",
        "total_count": 2,
        "open_count": 2,
        "done_count": 0,
        "items": [
            {
                "index": 1,
                "done": False,
                "text": "Add scheduler/fairness regression coverage for automatic turn ordering.",
            },
            {
                "index": 2,
                "done": False,
                "text": "Project sub-agent run history into status.",
            },
        ],
    }
    creator_item = attention(creator_id, compute=1.0)
    creator_item["recommended_action"] = "continue creator-operator showcase monitoring"
    side_bypass_item = attention(side_bypass_id, compute=0.8, state="focus_wait")
    side_bypass_item["lifecycle_phase"] = "focus_wait"
    side_bypass_item["lifecycle_flags"] = ["continuation_boundary"]
    gated_item = attention(
        gated_id,
        compute=1.0,
        state="operator_gate",
        waiting_on="user_or_controller",
    )
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 4,
        "run_count": 4,
        "attention_queue": {"items": [meta_item, creator_item, side_bypass_item, gated_item]},
        "run_history": {"goals": [meta_goal, creator_goal, side_bypass_goal, gated_goal]},
    }

    plan = build_quota_plan(payload, mode="plan")
    meta_decision = build_quota_should_run(payload, goal_id=meta_id)
    meta_markdown = render_quota_should_run_markdown(meta_decision)
    side_bypass_decision = build_quota_should_run(payload, goal_id=side_bypass_id)
    gated_decision = build_quota_should_run(payload, goal_id=gated_id)

    assert [item["goal_id"] for item in plan["groups"]["operator_gate"]] == [gated_id], plan
    assert [item["goal_id"] for item in plan["groups"]["focus_wait"]] == [side_bypass_id], plan
    assert [item["goal_id"] for item in plan["groups"]["eligible"]] == [creator_id, meta_id], plan
    assert plan["summary"]["next_automatic_turn"] == creator_id, plan
    assert plan["next_automatic_turn"]["goal_id"] == creator_id, plan

    assert meta_decision["decision"] == "run", meta_decision
    assert meta_decision["should_run"] is True, meta_decision
    assert meta_decision["normal_delivery_allowed"] is True, meta_decision
    assert meta_decision["state"] == "eligible", meta_decision
    assert meta_decision["plan_summary"]["next_automatic_turn"] == creator_id, meta_decision
    assert meta_decision["execution_obligation"]["must_attempt_work"] is True, meta_decision
    assert meta_decision["execution_obligation"]["notify_is_execution_gate"] is False, meta_decision
    assert meta_decision["agent_todo_summary"]["open_count"] == 2, meta_decision
    assert "agent_todo_next[1]: Add scheduler/fairness regression coverage" in meta_markdown, meta_markdown
    assert "execution_obligation: must_attempt_work=True" in meta_markdown, meta_markdown

    assert side_bypass_decision["should_run"] is False, side_bypass_decision
    assert side_bypass_decision["state"] == "focus_wait", side_bypass_decision
    assert gated_decision["should_run"] is False, gated_decision
    assert gated_decision["state"] == "operator_gate", gated_decision


def assert_default_quota_is_duty_cycle() -> None:
    full = goal_quota_config({"quota": {"compute": 1.0, "window_hours": 24}})
    half = goal_quota_config({"quota": {"compute": 0.5, "window_hours": 24}})
    low = goal_quota_config({"quota": {"compute": 0.3, "window_hours": 24}})

    assert full["slot_minutes"] == 1, full
    assert full["allowed_slots"] == 1440, full
    assert half["allowed_slots"] == 720, half
    assert low["allowed_slots"] == 432, low


def assert_rolling_window_ledger_expires_old_spends() -> None:
    now = datetime.fromisoformat("2026-06-03T11:20:00+08:00")
    goal_payload = {
        "id": "rolling-window",
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
        },
    }
    runs = [
        {
            "goal_id": "rolling-window",
            "classification": "quota_slot_spent",
            "generated_at": "2026-06-02T11:19:22+08:00",
            "quota_event": {
                "event_type": "quota_slot_spent",
                "slots": 1,
            },
        },
        {
            "goal_id": "rolling-window",
            "classification": "quota_slot_spent",
            "generated_at": "2026-06-03T11:19:13+08:00",
            "quota_event": {
                "event_type": "quota_slot_spent",
                "slots": 1,
            },
        },
    ]

    quota = goal_quota_with_spend_ledger(goal_payload, runs, now=now)

    assert quota["spent_slots"] == 1, quota
    assert quota["spend_event_count"] == 1, quota
    assert quota["spend_source"] == "runtime_events", quota


def assert_throttled_should_run(status_payload: dict) -> None:
    payload = build_quota_should_run(status_payload, goal_id="throttled-half")
    quota = payload["quota"]

    assert payload["ok"] is True, payload
    assert payload["decision"] == "skip", payload
    assert payload["should_run"] is False, payload
    assert payload["state"] == "throttled", payload
    assert payload["plan_summary"]["next_automatic_turn"] == "full-speed", payload
    assert quota["spent_slots"] == 12, payload
    assert quota["allowed_slots"] == 12, payload
    assert "spent 12/12" in payload["reason"], payload


def assert_operator_gate_should_run(status_payload: dict) -> None:
    payload = build_quota_should_run(status_payload, goal_id="needs-operator")

    assert payload["ok"] is True, payload
    assert payload["decision"] == "skip", payload
    assert payload["should_run"] is False, payload
    assert payload["state"] == "operator_gate", payload
    assert payload["safe_bypass_allowed"] is True, payload
    assert payload["notify_user_on_gate"] is True, payload
    assert payload["operator_question"] == "是否同意 needs-operator 继续 gated delivery？", payload
    assert payload["user_todo_summary"]["open_count"] == 1, payload
    assert payload["agent_todo_summary"]["open_count"] == 1, payload
    assert payload["todo_write_hint"]["section"] == "User Todo / Owner Review Reading Queue", payload
    assert "loopx todo add --goal-id needs-operator --role user --task-class user_gate" in payload["todo_write_hint"][
        "user_gate_command_template"
    ], payload
    assert "loopx todo add --goal-id needs-operator --role user --task-class user_action" in payload["todo_write_hint"][
        "user_action_command_template"
    ], payload
    assert "loopx todo add --goal-id needs-operator --role agent" in payload["todo_write_hint"][
        "agent_todo_command_template"
    ], payload
    assert "Next Action" in payload["todo_write_hint"]["rule"], payload
    assert "请用户/控制器确认当前 gate" in payload["gate_prompt"], payload
    assert "Confirm the operator gate." in payload["gate_prompt"], payload
    markdown = render_quota_should_run_markdown(payload)
    assert "agent_todo_summary: open=1 total=1" in markdown, markdown
    assert "user_todo_next[1]: Confirm the operator gate." in markdown, markdown
    assert "agent_todo_next[1]: Run the safe follow-up after gate approval." in markdown, markdown
    assert "agent_todo_command_template" in markdown, markdown


def assert_focus_wait_should_run() -> None:
    direct_quota = quota_status(
        {"quota": {"compute": 1.0}},
        waiting_on="codex",
        severity="action",
        lifecycle_flags=["continuation_boundary"],
    )
    assert direct_quota["state"] == "focus_wait", direct_quota
    assert direct_quota["focus_wait"] is True, direct_quota
    assert direct_quota["blocked_action_scope"] == "delivery_focus", direct_quota

    focus_goal = goal("focus-wait", compute=1.0)
    focus_item = attention("focus-wait", compute=1.0)
    focus_item["lifecycle_phase"] = "focus_wait"
    focus_item["lifecycle_flags"] = ["continuation_boundary"]
    focus_item["user_todos"] = {
        "source_section": "User Todo",
        "total_count": 1,
        "open_count": 1,
        "done_count": 0,
        "items": [
            {
                "index": 1,
                "done": False,
                "text": "Provide new owner evidence, a clean baseline, or external eval before delivery resumes.",
                "task_class": "user_action",
            }
        ],
    }

    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [focus_item]},
        "run_history": {"goals": [focus_goal]},
    }
    decision = build_quota_should_run(payload, goal_id="focus-wait")
    markdown = render_quota_should_run_markdown(decision)

    assert decision["ok"] is True, decision
    assert decision["decision"] == "skip", decision
    assert decision["should_run"] is False, decision
    assert decision["state"] == "focus_wait", decision
    assert decision["waiting_on"] == "codex", decision
    assert decision["quota"]["focus_wait"] is True, decision
    assert decision["quota"]["blocked_action_scope"] == "delivery_focus", decision
    assert decision["safe_bypass_allowed"] is False, decision
    assert decision["user_todo_summary"]["open_count"] == 1, decision
    assert decision["notify_user_on_open_todo"] is True, decision
    assert decision["heartbeat_recommendation"]["repeat_notification_required"] is True, decision
    assert decision["open_todo_notification_policy"] == "repeat_until_resolved", decision
    assert "focus_wait" in decision["open_todo_notify_reason"], decision
    assert decision["plan_summary"]["next_automatic_turn"] is None, decision
    assert decision["lifecycle_phase"] == "focus_wait", decision
    assert decision["lifecycle_flags"] == ["continuation_boundary"], decision
    assert "agent_command" not in decision, decision
    assert "- state: `focus_wait`" in markdown, markdown
    assert "- notify_user_on_open_todo: `True`" in markdown, markdown
    assert "- open_todo_notification_policy: repeat_until_resolved" in markdown, markdown
    assert "- user_todo_next[1]: Provide new owner evidence" in markdown, markdown


def assert_outcome_floor_recovery_should_run() -> None:
    goal_id = "outcome-floor-recovery"
    recovery_goal = goal(goal_id, compute=1.0)
    recovery_item = attention(goal_id, compute=1.0)
    recovery_item["project_asset"] = {
        "owner": "codex",
        "next_action": "produce ranker evidence or write back the concrete blocker",
        "stop_condition": "stop if recovery needs owner approval",
        "quota": recovery_item["quota"],
        "execution_profile": {
            "cadence": "macro_evidence_segment",
            "minimum_scale": "implementation",
            "must_include": ["experiment_or_evidence_artifact", "targeted_validation", "state_writeback"],
            "outcome_floor": {
                "surface_streak_threshold": 1,
                "must_advance": ["ranker_or_cross_domain_evidence"],
                "avoid": ["surface_only_summary", "synthetic_only_test_chain"],
            },
        },
    }
    recovery_item["handoff_readiness"] = {
        "ready": False,
        "codex_ready": False,
        "source": "project_asset",
        "quota_state": "eligible",
        "post_handoff_run_seen": True,
        "post_handoff_outcome_gap_streak": 1,
        "post_handoff_latest_run": {
            "classification": "surface_only_summary",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_gap",
        },
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [recovery_item]},
        "run_history": {"goals": [recovery_goal]},
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)
    with tempfile.TemporaryDirectory(prefix="loopx-outcome-floor-spend-") as tmp:
        runtime_root = Path(tmp)
        index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-01-01T00:01:00+00:00",
                    "goal_id": goal_id,
                    "classification": "validated_recovery",
                    "delivery_outcome": "outcome_progress",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload["runtime_root"] = str(runtime_root)
        preview = build_quota_slot_preview(payload, goal_id=goal_id, slots=1)
        spend_event = build_quota_slot_spend_event(preview, source="heartbeat")

    assert decision["ok"] is True, decision
    assert decision["decision"] == "safe_bypass_recovery", decision
    assert decision["should_run"] is True, decision
    assert decision["normal_delivery_allowed"] is False, decision
    assert decision["recovery_delivery_allowed"] is True, decision
    assert decision["actionable_by_codex"] is True, decision
    assert decision["requires_user_action"] is False, decision
    assert decision["effective_action"] == "outcome_floor_recovery", decision
    assert decision["state"] == "focus_wait", decision
    assert decision["blocked_action_scope"] == "delivery_outcome_floor", decision
    assert decision["safe_bypass_allowed"] is True, decision
    assert decision["safe_bypass_kind"] == "outcome_floor_recovery", decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == "outcome_floor_recovery", decision
    assert decision["quota"]["must_advance"] == ["ranker_or_cross_domain_evidence"], decision
    assert "decision: `safe_bypass_recovery`" in markdown, markdown
    assert "recovery_delivery_allowed: `True`" in markdown, markdown
    assert "effective_action: `outcome_floor_recovery`" in markdown, markdown
    assert preview["ok"] is True, preview
    assert preview["safe_bypass_spend"] is True, preview
    assert preview["before"]["effective_action"] == "outcome_floor_recovery", preview
    assert preview["after"]["quota"]["spent_slots"] == preview["before"]["quota"]["spent_slots"] + 1, preview
    assert "outcome-floor recovery safe-bypass" in spend_event["health_check"], spend_event
    assert "outcome-floor recovery safe-bypass" in spend_event["quota_event"]["reason_summary"], spend_event


def assert_outcome_floor_projected_blocker_quiet_noop() -> None:
    goal_id = "outcome-floor-blocker-projected"
    recovery_goal = goal(goal_id, compute=1.0)
    recovery_item = attention(goal_id, compute=1.0)
    recovery_item["project_asset"] = {
        "owner": "codex",
        "next_action": (
            "Blocked until a fresh public-safe non-replay ranker/cross-domain route appears."
        ),
        "stop_condition": "stop if recovery needs owner approval",
        "quota": recovery_item["quota"],
        "execution_profile": {
            "cadence": "macro_evidence_segment",
            "minimum_scale": "implementation",
            "must_include": ["experiment_or_evidence_artifact", "targeted_validation", "state_writeback"],
            "outcome_floor": {
                "surface_streak_threshold": 1,
                "must_advance": ["ranker_or_cross_domain_evidence"],
                "avoid": ["surface_only_summary", "synthetic_only_test_chain"],
            },
        },
        "agent_todos": {
            "source_section": "Agent Todo",
            "total_count": 1,
            "open_count": 1,
            "done_count": 0,
            "items": [
                {
                    "index": 1,
                    "done": False,
                    "text": (
                        "Blocked until a fresh public-safe non-replay ranker/cross-domain "
                        "route appears."
                    ),
                    "task_class": "blocker",
                    "action_kind": "outcome_floor_no_nonduplicate_successor_blocker",
                }
            ],
        },
    }
    recovery_item["handoff_readiness"] = {
        "ready": False,
        "codex_ready": False,
        "source": "project_asset",
        "quota_state": "eligible",
        "post_handoff_run_seen": True,
        "post_handoff_outcome_gap_streak": 2,
        "post_handoff_latest_run": {
            "classification": "outcome_floor_blocker_recorded",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_gap",
        },
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [recovery_item]},
        "run_history": {"goals": [recovery_goal]},
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)
    contract = decision["interaction_contract"]

    assert decision["ok"] is True, decision
    assert decision["decision"] == "skip", decision
    assert decision["should_run"] is False, decision
    assert decision["normal_delivery_allowed"] is False, decision
    assert decision["recovery_delivery_allowed"] is False, decision
    assert decision["actionable_by_codex"] is False, decision
    assert decision["effective_action"] == "blocked_wait", decision
    assert decision["state"] == "focus_wait", decision
    assert decision["quota"]["outcome_floor_blocker_projected"] is True, decision
    assert decision["safe_bypass_allowed"] is False, decision
    assert decision["safe_bypass_kind"] is None, decision
    assert decision["agent_todo_summary"]["first_executable_items"] == [], decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == (
        "outcome_floor_blocker_projected_noop"
    ), decision
    assert decision["heartbeat_recommendation"]["notify"] == "DONT_NOTIFY", decision
    assert contract["mode"] == "blocked_wait", contract
    assert contract["agent_channel"]["must_attempt"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is True, contract
    assert contract["cli_channel"]["spend_after_validation"] is False, contract
    assert "outcome_floor_blocker_projected_noop" in markdown, markdown

    monitor_payload = deepcopy(payload)
    monitor_item = monitor_payload["attention_queue"]["items"][0]
    monitor_item["project_asset"]["agent_todos"]["items"][0]["task_class"] = "continuous_monitor"
    monitor_item["project_asset"]["agent_todos"]["items"][0]["action_kind"] = "monitor"
    monitor_decision = build_quota_should_run(monitor_payload, goal_id=goal_id)
    assert "outcome_floor_blocker_projected" not in monitor_decision["quota"], monitor_decision
    assert monitor_decision["effective_action"] == "outcome_floor_recovery", monitor_decision
    assert monitor_decision["should_run"] is True, monitor_decision
    assert monitor_decision["safe_bypass_kind"] == "outcome_floor_recovery", monitor_decision
    assert (
        monitor_decision["heartbeat_recommendation"]["recommended_mode"]
        == "outcome_floor_recovery"
    ), monitor_decision
    assert "work_lane_contract" not in monitor_decision, monitor_decision
    execution = monitor_decision["execution_obligation"]
    assert execution["kind"] == "outcome_floor_recovery", execution
    assert execution["contract"] == "delivery_outcome_floor", execution
    assert "outcome-floor evidence" in execution["contract_obligation"], execution
    monitor_contract = monitor_decision["interaction_contract"]
    assert monitor_contract["mode"] == "outcome_floor_recovery", monitor_contract
    assert monitor_contract["agent_channel"]["must_attempt"] is True, monitor_contract
    assert monitor_contract["agent_channel"]["quiet_noop_allowed"] is False, monitor_contract
    assert "outcome-floor evidence" in monitor_contract["agent_channel"]["primary_action"], monitor_contract
    assert "protocol_action_packet" not in monitor_decision, monitor_decision


def assert_control_plane_health_self_repair_should_run() -> None:
    goal_id = "loopx-meta"
    meta_goal = goal(goal_id, compute=1.0)
    meta_goal["control_plane"] = {"self_repair": {"enabled": True}}
    meta_item = attention(goal_id, compute=1.0)
    health_item = {
        "goal_id": "loopx-contract",
        "status": "contract_check_failed",
        "waiting_on": "codex",
        "severity": "high",
        "recommended_action": "fix contract errors before advancing goal adapters",
        "source": "contract",
    }
    payload = {
        "ok": False,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [health_item, meta_item]},
        "run_history": {"goals": [meta_goal]},
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)
    preview = build_quota_slot_preview(payload, goal_id=goal_id, slots=1)
    spend_event = build_quota_slot_spend_event(preview, source="heartbeat")

    assert decision["ok"] is True, decision
    assert decision["status_health_ok"] is False, decision
    assert decision["decision"] == "self_repair", decision
    assert decision["should_run"] is True, decision
    assert decision["normal_delivery_allowed"] is False, decision
    assert decision["self_repair_allowed"] is True, decision
    assert decision["effective_action"] == "control_plane_health_repair", decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == "repair_control_plane_health", decision
    assert decision["stall_self_repair"]["trigger"] == "health_blocker", decision
    assert decision["stall_self_repair"]["blocking_health_items"][0]["goal_id"] == "loopx-contract", decision
    assert decision["control_plane"]["self_repair"]["enabled"] is True, decision
    assert "decision: `self_repair`" in markdown, markdown
    assert "self_repair_allowed: `True`" in markdown, markdown
    assert "stall_self_repair: trigger=health_blocker" in markdown, markdown
    assert preview["ok"] is True, preview
    assert preview["self_repair_spend"] is True, preview
    assert preview["before"]["effective_action"] == "control_plane_health_repair", preview
    assert "control-plane self-repair" in spend_event["health_check"], spend_event
    assert "control-plane self-repair" in spend_event["quota_event"]["reason_summary"], spend_event


def assert_control_plane_self_repair_default_off() -> None:
    goal_id = "ordinary-goal"
    ordinary_goal = goal(goal_id, compute=1.0)
    ordinary_item = attention(goal_id, compute=1.0)
    health_item = {
        "goal_id": "loopx-contract",
        "status": "contract_check_failed",
        "waiting_on": "codex",
        "severity": "high",
        "recommended_action": "fix contract errors before advancing goal adapters",
        "source": "contract",
    }
    payload = {
        "ok": False,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [health_item, ordinary_item]},
        "run_history": {"goals": [ordinary_goal]},
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)

    assert decision["ok"] is False, decision
    assert decision["decision"] == "skip", decision
    assert decision["should_run"] is False, decision
    assert decision["self_repair_allowed"] is False, decision
    assert decision["effective_action"] == "quota_skip", decision
    assert "stall_self_repair" not in decision, decision


def assert_control_plane_waiting_projection_self_repair_should_run() -> None:
    goal_id = "loopx-meta"
    meta_goal = goal(goal_id, compute=1.0)
    meta_goal["control_plane"] = {"self_repair": {"enabled": True}}
    meta_item = attention(goal_id, compute=1.0, state="waiting", waiting_on="")
    meta_item["status"] = "state_refreshed"
    meta_item["recommended_action"] = "continue gate-independent product hardening"
    meta_item["project_asset"] = {
        "owner": "codex",
        "gate": "none",
        "next_action": "continue gate-independent product hardening",
        "stop_condition": "stop if the repair needs user approval",
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [meta_item]},
        "run_history": {"goals": [meta_goal]},
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)
    preview = build_quota_slot_preview(payload, goal_id=goal_id, slots=1)
    spend_event = build_quota_slot_spend_event(preview, source="heartbeat")

    assert decision["ok"] is True, decision
    assert decision["decision"] == "self_repair", decision
    assert decision["should_run"] is True, decision
    assert decision["state"] == "waiting", decision
    assert decision["waiting_on"] == "none", decision
    assert decision["self_repair_allowed"] is True, decision
    assert decision["effective_action"] == "control_plane_projection_repair", decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == "repair_waiting_projection", decision
    assert decision["stall_self_repair"]["trigger"] == "waiting_without_owner_projection", decision
    assert "stall_self_repair: trigger=waiting_without_owner_projection" in markdown, markdown
    assert preview["self_repair_spend"] is True, preview
    assert "control-plane self-repair" in spend_event["health_check"], spend_event


def post_handoff_meta_fixture(
    *,
    with_agent_todo: bool,
    latest_classification: str = "handoff_only_budget_fields_merged",
    agent_todo_text: str = "Keep heartbeat prompt and agent-to-CLI interaction lean as an ongoing interface-budget task.",
) -> dict:
    goal_id = "loopx-meta"
    meta_goal = goal(goal_id, compute=1.0)
    meta_goal["adapter_kind"] = "harness_self_improvement"
    meta_goal["adapter_status"] = "connected-read-only"
    meta_goal["control_plane"] = {
        "self_repair": {
            "enabled": True,
            "allow_health_blocker_repair": True,
            "allow_waiting_projection_repair": True,
        }
    }
    meta_item = attention(goal_id, compute=1.0)
    meta_item["status"] = "handoff_only_budget_fields_merged"
    meta_item["recommended_action"] = "continue the ongoing interface-budget observation loop"
    meta_item["handoff_readiness"] = {
        "ready": True,
        "codex_ready": True,
        "source": "project_asset",
        "quota_state": "eligible",
        "handoff_status": "post_handoff_run_seen",
        "post_handoff_run_seen": True,
        "post_handoff_small_scale_streak": 0,
        "post_handoff_latest_run": {
            "generated_at": "2026-06-06T21:35:46+08:00",
            "classification": latest_classification,
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "primary_goal_outcome",
            "health_check": "state_file 1/1; registry_goal 1/1",
            "json_exists": True,
            "markdown_exists": True,
        },
    }
    if with_agent_todo:
        meta_item["agent_todos"] = {
            "source_section": "Agent Todo",
            "total_count": 1,
            "open_count": 1,
            "done_count": 0,
            "items": [
                {
                    "index": 1,
                    "done": False,
                    "text": agent_todo_text,
                }
            ],
        }
    return {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [meta_item]},
        "run_history": {"goals": [meta_goal]},
    }


def assert_control_plane_post_handoff_observe_if_unchanged() -> None:
    goal_id = "loopx-meta"
    payload = post_handoff_meta_fixture(with_agent_todo=False)
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)

    assert decision["ok"] is True, decision
    assert decision["decision"] == "run", decision
    assert decision["should_run"] is True, decision
    assert decision["normal_delivery_allowed"] is True, decision
    assert decision["self_repair_allowed"] is False, decision
    assert decision["effective_action"] == "normal_run", decision
    assert (
        decision["heartbeat_recommendation"]["recommended_mode"]
        == "post_handoff_observe_if_unchanged"
    ), decision
    assert decision["heartbeat_recommendation"]["stop_if_unchanged"] is True, decision
    assert decision["heartbeat_recommendation"]["latest_run"]["delivery_outcome"] == "primary_goal_outcome", decision
    assert decision["execution_obligation"]["must_attempt_work"] is False, decision
    assert decision["execution_obligation"]["kind"] == "quiet_noop_if_unchanged", decision
    assert "post_handoff_observe_if_unchanged" in markdown, markdown
    assert "heartbeat_stop_if_unchanged: `True`" in markdown, markdown
    assert (
        "execution_obligation: must_attempt_work=False kind=quiet_noop_if_unchanged notify_is_execution_gate=False"
        in markdown
    ), markdown


def assert_control_plane_post_handoff_agent_todo_stays_active() -> None:
    goal_id = "loopx-meta"
    payload = post_handoff_meta_fixture(with_agent_todo=True)
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)

    assert decision["ok"] is True, decision
    assert decision["decision"] == "run", decision
    assert decision["should_run"] is True, decision
    assert decision["normal_delivery_allowed"] is True, decision
    assert decision["effective_action"] == "normal_run", decision
    assert decision["agent_todo_summary"]["open_count"] == 1, decision
    assert (
        decision["heartbeat_recommendation"]["recommended_mode"]
        == "post_handoff_observe_then_backlog_step"
    ), decision
    assert "stop_if_unchanged" not in decision["heartbeat_recommendation"], decision
    assert decision["execution_obligation"]["must_attempt_work"] is True, decision
    assert decision["execution_obligation"]["kind"] == "work_lane_contract", decision
    assert decision["execution_obligation"]["contract_obligation"] == "advance_one_bounded_segment", decision
    assert "post_handoff_observe_then_backlog_step" in markdown, markdown
    assert "heartbeat_stop_if_unchanged" not in markdown, markdown
    assert (
        "execution_obligation: must_attempt_work=True kind=work_lane_contract notify_is_execution_gate=False"
        in markdown
    ), markdown
    assert "execution_contract_obligation: advance_one_bounded_segment" in markdown, markdown


def assert_dependency_observation_label_keeps_primary_backlog() -> None:
    goal_id = "loopx-meta"
    payload = post_handoff_meta_fixture(
        with_agent_todo=True,
        latest_classification="side_bypass_seed308_dependency_observed",
        agent_todo_text="SOTA long-horizon agent paper and runner dossier.",
    )
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)

    assert decision["ok"] is True, decision
    assert decision["decision"] == "run", decision
    assert decision["should_run"] is True, decision
    first_todo = decision["agent_todo_summary"]["first_open_items"][0]
    first_todo_text = str(first_todo.get("title") or first_todo.get("text") or "")
    assert first_todo_text.startswith("SOTA long-horizon"), decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == "post_handoff_observe_then_backlog_step", decision
    assert (
        decision["heartbeat_recommendation"]["latest_run"]["progress_scope"]
        == "primary_goal"
    ), decision
    lane = decision["work_lane_contract"]
    assert lane["schema_version"] == "work_lane_contract_v1", lane
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane
    assert "dependency_observation_cap" not in markdown, markdown
    assert "post_handoff_observe_then_backlog_step" in markdown, markdown
    assert "obligation=advance_one_bounded_segment" in markdown, markdown
    assert decision["execution_obligation"]["must_attempt_work"] is True, decision
    assert decision["execution_obligation"]["kind"] == "work_lane_contract", decision
    assert decision["execution_obligation"]["contract_obligation"] == lane["obligation"], decision


def assert_attention_queue_overrides_stale_run_history() -> None:
    stale_goal = goal("queue-authority", compute=1.0)
    stale_goal["status"] = "operator_gate_deferred"
    stale_goal["lifecycle_phase"] = "controller_gated"
    stale_goal["quota"] = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 1440,
        "spent_slots": 0,
        "state": "operator_gate",
        "reason": "stale run-history gate should not block current queue authority",
        "blocked_action_scope": "gated_delivery",
        "safe_bypass_allowed": True,
    }
    stale_goal["latest_runs"][0]["classification"] = "operator_gate_deferred"
    stale_goal["latest_runs"][0]["recommended_action"] = "ask the old gate again"

    current_item = attention("queue-authority", compute=1.0)
    current_item.update(
        {
            "status": "operator_gate_approved",
            "waiting_on": "codex",
            "recommended_action": "run the approved dry-run",
            "agent_command": "loopx read-only-map --goal-id queue-authority --dry-run",
            "source": "latest_run",
        }
    )
    current_item["project_asset"] = {
        "owner": "codex",
        "gate": "none",
        "next_action": "run the approved dry-run",
        "stop_condition": "stop if the command needs write control",
    }

    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [current_item]},
        "run_history": {"goals": [stale_goal]},
    }
    decision = build_quota_should_run(payload, goal_id="queue-authority")

    assert decision["ok"] is True, decision
    assert decision["decision"] == "run", decision
    assert decision["should_run"] is True, decision
    assert decision["state"] == "eligible", decision
    assert decision["waiting_on"] == "codex", decision
    assert decision["status"] == "operator_gate_approved", decision
    assert decision["recommended_action"] == "run the approved dry-run", decision
    assert decision["agent_command"] == "loopx read-only-map --goal-id queue-authority --dry-run", decision
    assert "operator_question" not in decision, decision
    assert "gate_prompt" not in decision, decision


def assert_project_asset_backed_no_evidence_should_run() -> None:
    goal_id = "platform-migration-material-registry"
    expected_next = "Refresh the public-safe material registry summary."
    expected_stop = "stop if the next action needs reward, gate approval, write control, or production access"
    expected_user_todo = "Confirm whether owner review is fresh enough to resume delivery."
    expected_agent_todo = "Run read-only map and report material freshness without internal links."
    raw_next = "raw queue says ask for an old gate"
    raw_user_todo = "Raw fallback user todo should not become owner authority."
    raw_agent_todo = "Raw fallback agent todo should not become routing authority."

    project_goal = goal(goal_id, compute=0.25, spent_slots=120, allowed_slots=120)
    project_item = attention(goal_id, compute=0.25, state="throttled", spent_slots=120, allowed_slots=120)
    project_item.update(
        {
            "status": "state_refreshed",
            "waiting_on": "codex",
            "recommended_action": raw_next,
            "user_todos": {
                "source_section": "Raw User Todo",
                "total_count": 1,
                "open_count": 1,
                "done_count": 0,
                "items": [{"index": 1, "done": False, "text": raw_user_todo}],
            },
            "agent_todos": {
                "source_section": "Raw Agent Todo",
                "total_count": 1,
                "open_count": 1,
                "done_count": 0,
                "items": [{"index": 1, "done": False, "text": raw_agent_todo}],
            },
            "project_asset": {
                "owner": "codex",
                "gate": "none",
                "next_action": expected_next,
                "stop_condition": expected_stop,
                "user_todos": {"open": 1, "done": 0, "total": 1, "next": expected_user_todo},
                "agent_todos": {"open": 1, "done": 0, "total": 1, "next": expected_agent_todo},
                "quota": {
                    "compute": 1.0,
                    "state": "eligible",
                    "spent_slots": 0,
                    "allowed_slots": 1440,
                    "reason": "project_asset quota is current no-evidence routing authority",
                },
            },
        }
    )
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [project_item]},
        "run_history": {"goals": [project_goal]},
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)

    assert decision["ok"] is True, decision
    assert decision["decision"] == "run", decision
    assert decision["should_run"] is True, decision
    assert decision["state"] == "eligible", decision
    assert decision["project_asset_source"] == "project_asset", decision
    assert decision["recommended_action"] == expected_agent_todo, decision
    assert decision["quota"]["compute"] == 1.0, decision
    assert decision["quota"]["state"] == "eligible", decision
    assert decision["quota"]["spent_slots"] == 0, decision
    assert decision["quota"]["allowed_slots"] == 1440, decision
    assert decision["user_todo_summary"]["open_count"] == 1, decision
    assert decision["user_todo_summary"]["first_open_items"][0]["text"] == expected_user_todo, decision
    assert decision["agent_todo_summary"]["open_count"] == 1, decision
    assert decision["agent_todo_summary"]["first_open_items"][0]["text"] == expected_agent_todo, decision
    warning = decision["state_action_projection_warning"]
    assert warning["requires_state_writeback"] is True, decision
    assert warning["active_state_next_action"] == expected_next, decision
    assert warning["selected_recommended_action"] == expected_agent_todo, decision
    assert decision["goal_boundary"]["stop_condition"] == expected_stop, decision
    assert decision["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step", decision
    assert decision["heartbeat_recommendation"]["notify"] == "DONT_NOTIFY", decision
    assert decision["execution_obligation"]["must_attempt_work"] is True, decision
    assert decision["execution_obligation"]["kind"] == "work_lane_contract", decision
    assert decision["execution_obligation"]["contract_obligation"] == "advance_one_bounded_segment", decision
    assert decision["execution_obligation"]["notify_is_execution_gate"] is False, decision
    assert "project_asset_source: project_asset" in markdown, markdown
    assert "quota: compute=1.0 slot_minutes=1 slots=0/1440" in markdown, markdown
    assert (
        "execution_obligation: must_attempt_work=True kind=work_lane_contract notify_is_execution_gate=False"
        in markdown
    ), markdown
    assert "execution_contract_obligation: advance_one_bounded_segment" in markdown, markdown
    assert f"goal_boundary_stop_condition: {expected_stop}" in markdown, markdown
    assert f"user_todo_next[1]: {expected_user_todo}" in markdown, markdown
    assert f"agent_todo_next[1]: {expected_agent_todo}" in markdown, markdown
    assert raw_next not in markdown, markdown
    assert raw_user_todo not in markdown, markdown
    assert raw_agent_todo not in markdown, markdown


def assert_heartbeat_recommendation_lifecycle() -> None:
    first_map_goal = goal("first-map", compute=1.0)
    mapped_goal = goal("mapped-quiet", compute=1.0)
    first_map_item = attention("first-map", compute=1.0)
    first_map_item.update(
        {
            "status": "connected_without_run",
            "lifecycle_phase": "connected",
            "lifecycle_flags": ["connected"],
            "recommended_action": "run the first read-only adapter tick and save a compact run record",
        }
    )
    mapped_item = attention("mapped-quiet", compute=1.0)
    mapped_item.update(
        {
            "status": "read_only_project_map",
            "lifecycle_phase": "mapped",
            "lifecycle_flags": ["mapped"],
            "recommended_action": "inspect the map and wait for new evidence",
        }
    )
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 2,
        "run_count": 2,
        "attention_queue": {"items": [first_map_item, mapped_item]},
        "run_history": {"goals": [first_map_goal, mapped_goal]},
    }

    first_decision = build_quota_should_run(
        payload,
        goal_id="first-map",
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    first_rec = first_decision["heartbeat_recommendation"]

    assert first_decision["should_run"] is True, first_decision
    assert first_rec["recommended_mode"] == "run_first_read_only_map", first_rec
    assert first_rec["command"] == "loopx read-only-map --goal-id first-map", first_rec
    assert first_rec["notify"] == "NOTIFY", first_rec
    assert "append exactly one heartbeat spend" in first_rec["spend_policy"], first_rec

    mapped_decision = build_quota_should_run(
        payload,
        goal_id="mapped-quiet",
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    mapped_rec = mapped_decision["heartbeat_recommendation"]
    mapped_markdown = render_quota_should_run_markdown(mapped_decision)

    assert mapped_decision["should_run"] is True, mapped_decision
    assert mapped_rec["recommended_mode"] == "mapped_noop_if_unchanged", mapped_rec
    assert mapped_rec["stop_if_unchanged"] is True, mapped_rec
    assert mapped_rec["notify"] == "DONT_NOTIFY", mapped_rec
    assert mapped_decision["execution_obligation"]["must_attempt_work"] is False, mapped_decision
    assert mapped_decision["execution_obligation"]["kind"] == "quiet_noop_if_unchanged", mapped_decision
    scheduler = mapped_decision["scheduler_hint"]
    assert scheduler["action"] == "backoff_until_fresh_evidence", mapped_decision
    assert scheduler["codex_app"]["recommended_interval_minutes"] == 60, mapped_decision
    assert scheduler["codex_app"]["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=60", mapped_decision
    assert scheduler["codex_app"]["example_progression_minutes"] == [60], mapped_decision
    assert scheduler["unchanged_poll"]["limits"]["codex_cli_tui"] == 3, mapped_decision
    assert scheduler["unchanged_poll"]["final_quota_replan_check_enabled"] is True, mapped_decision
    assert "local_scheduler" not in scheduler, scheduler
    assert "codex_cli_tui" not in scheduler, scheduler
    assert "claude_code_loop" not in scheduler, scheduler
    assert "cold_path_detail" not in scheduler, scheduler
    reset = scheduler["reset_policy"]
    assert isinstance(reset["reset_token"], str) and len(reset["reset_token"]) == 16, reset
    assert reset["reset_token"] == expected_scheduler_reset_token(scheduler, mapped_decision), reset
    assert reset["host_state_key"] == "scheduler_hint.reset_policy.reset_token", reset
    assert reset["app_automation_initial_interval_minutes"] == 60, reset
    assert reset["app_automation_initial_rrule"] == "FREQ=MINUTELY;INTERVAL=60", reset
    assert len(reset["identity_signature"]) == 12, reset
    assert "identity_snapshot" not in reset, reset
    assert "profile_snapshot" not in reset, reset
    assert "identity_keys" not in reset, reset
    assert "profile" not in reset, reset
    profile_snapshot = scheduler_reset_profile_snapshot(scheduler)
    assert profile_snapshot["cadence_class"] == "unchanged_noop", profile_snapshot
    assert profile_snapshot["codex_app_initial_rrule"] == "FREQ=MINUTELY;INTERVAL=60", profile_snapshot
    assert profile_snapshot["codex_app_max_interval_minutes"] == 60, profile_snapshot
    assert profile_snapshot["unchanged_poll_backoff_multiplier"] == 2, profile_snapshot
    identity_snapshot = {
        key: _nested_value(mapped_decision, key)
        for key in scheduler["unchanged_identity_keys"]
    }
    assert identity_snapshot["recommended_action"] == mapped_decision["recommended_action"], identity_snapshot
    assert reset["identity_signature"] == _short_hash(identity_snapshot, 12), reset
    assert "profile_signature" not in reset, reset
    assert "reset_condition_summary" not in reset, reset
    assert "do not run another dry-run" in mapped_rec["spend_policy"], mapped_rec
    assert "heartbeat_recommendation: mode=mapped_noop_if_unchanged notify=DONT_NOTIFY" in mapped_markdown
    assert "heartbeat_stop_if_unchanged: `True`" in mapped_markdown, mapped_markdown
    assert "scheduler_hint: action=backoff_until_fresh_evidence" in mapped_markdown, mapped_markdown
    assert "app_automation_rrule=FREQ=MINUTELY;INTERVAL=60" in mapped_markdown, mapped_markdown
    assert "app_automation_progression=[60]" in mapped_markdown, mapped_markdown
    assert "scheduler_reset: initial_interval=60" in mapped_markdown, mapped_markdown
    assert "initial_rrule=FREQ=MINUTELY;INTERVAL=60" in mapped_markdown, mapped_markdown
    assert "reset_generation=" in mapped_markdown, mapped_markdown
    assert (
        "execution_obligation: must_attempt_work=False kind=quiet_noop_if_unchanged notify_is_execution_gate=False"
        in mapped_markdown
    ), mapped_markdown

    mapped_detailed = build_quota_should_run(
        payload,
        goal_id="mapped-quiet",
        include_scheduler_detail=True,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    detailed_scheduler = mapped_detailed["scheduler_hint"]
    local_scheduler = detailed_scheduler["cold_path_detail"]["local_scheduler"]
    stateful_detail = detailed_scheduler["cold_path_detail"]["stateful_backoff_detail"]
    assert local_scheduler["example_progression_minutes"] == [60, 120, 240], local_scheduler
    assert local_scheduler["max_interval_minutes"] == 240, local_scheduler
    assert stateful_detail["host_max_interval_minutes"] == 60, stateful_detail
    assert stateful_detail["coarser_wait_fallback"] == "local_scheduler_only", stateful_detail
    failure_policy = stateful_detail["host_update_failure"]
    assert "failed_target_and_observed_host_pairs" in failure_policy, stateful_detail
    assert "suppress_each_exact_repeat" in failure_policy, stateful_detail
    assert stateful_detail["ack_required_after_apply"] is True, stateful_detail
    assert stateful_detail["ack_required_from_host_match"] is False, stateful_detail
    persisted_fields = stateful_detail["persist"].split("|")
    assert "host_update_failures" in persisted_fields, stateful_detail
    assert "host_update_failure_compat" in persisted_fields, stateful_detail


def assert_goal_boundary_in_should_run() -> None:
    delivery_goal = goal("delivery-side-bypass", compute=0.33)
    delivery_goal.update(
        {
            "adapter_kind": "agent_harness_side_bypass_delivery_v0",
            "adapter_status": "connected-delivery",
            "coordination": {
                "write_scope": ["docs/design/**", "src/agent_harness/**", "tests/**"],
                "requires_parent_approval": [
                    "write-outside-side-bypass-scope",
                    "production-action",
                    "managed-mirror-sync",
                ],
                "registered_agents": ["codex-main-control", "codex-side-bypass", "codex-side-reviewer"],
                "agent_model": "peer_v1",
                "side_agent_handoff_agent": "codex-side-reviewer",
            },
            "spawn_policy": {
                "mode": "multi_subagent",
                "allowed": True,
                "max_children": 2,
                "allowed_domains": ["side-bypass"],
            },
            "guards": [
                "low-conflict delivery within side-bypass write_scope only",
                "do not touch protected main-control files",
            ],
            "next_probe": "loopx quota should-run --goal-id delivery-side-bypass",
        }
    )
    delivery_item = attention("delivery-side-bypass", compute=0.33)
    delivery_item.update(
        {
            "status": "state_refreshed",
            "recommended_action": "choose one public-safe delivery step",
        }
    )
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {"items": [delivery_item]},
        "run_history": {"goals": [delivery_goal]},
    }
    decision = build_quota_should_run(payload, goal_id="delivery-side-bypass")
    boundary = decision["goal_boundary"]
    markdown = render_quota_should_run_markdown(decision)
    peer_decision = build_quota_should_run(
        payload,
        goal_id="delivery-side-bypass",
        agent_id="codex-side-bypass",
    )
    peer_markdown = render_quota_should_run_markdown(peer_decision)

    assert decision["decision"] == "automation_prompt_upgrade", decision
    assert decision["should_run"] is False, decision
    assert decision["normal_delivery_allowed"] is False, decision
    assert decision["effective_action"] == "automation_prompt_upgrade_required", decision
    assert decision["interaction_contract"]["mode"] == "automation_prompt_upgrade", decision
    assert decision["interaction_contract"]["user_channel"]["action_required"] is False, decision
    assert decision["interaction_contract"]["agent_channel"]["delivery_allowed"] is False, decision
    assert decision["automation_prompt_upgrade"]["required"] is True, decision
    assert decision["automation_prompt_upgrade"]["blocks_should_run"] is True, decision
    commands = decision["automation_prompt_upgrade"]["agent_example_commands"]
    assert "--agent-id codex-main-control" in commands[0]["command"], decision
    assert decision["automation_prompt_upgrade"]["migration_id"] in decision[
        "automation_prompt_upgrade"
    ]["completion_command"], decision
    assert "- automation_prompt_upgrade: required=True blocks_should_run=True" in markdown, markdown
    assert peer_decision["agent_identity"]["agent_id"] == "codex-side-bypass", peer_decision
    assert peer_decision["agent_identity"]["agent_model"] == "peer_v1", peer_decision
    assert "role" not in peer_decision["agent_identity"], peer_decision
    assert peer_decision["decision"] == "automation_prompt_upgrade", peer_decision
    assert "agent_identity: agent_id=codex-side-bypass agent_model=peer_v1" in peer_markdown, peer_markdown
    assert boundary["adapter"]["status"] == "connected-delivery", boundary
    assert boundary["write_scope"] == ["docs/design/**", "src/agent_harness/**", "tests/**"], boundary
    assert "production-action" in boundary["requires_parent_approval"], boundary
    assert boundary["orchestration"]["mode"] == "multi_subagent", boundary
    assert boundary["orchestration"]["max_children"] == 2, boundary
    assert "low-conflict delivery" in boundary["guards"][0], boundary
    assert "goal_boundary_adapter: agent_harness_side_bypass_delivery_v0:connected-delivery" in markdown, markdown
    assert "goal_boundary_write_scope: docs/design/**, src/agent_harness/**, tests/**" in markdown, markdown
    assert "goal_boundary_requires_approval:" in markdown, markdown
    assert "goal_boundary_orchestration: mode=multi_subagent spawn_allowed=True max_children=2" in markdown, markdown


def assert_decision_freshness_warning_in_should_run() -> None:
    goal_id = "stale-gate-reuse"
    stale_goal = goal(goal_id, compute=1.0)
    stale_item = attention(goal_id, compute=1.0)
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": FIXTURE_RUNTIME_ROOT,
        "goal_count": 1,
        "run_count": 3,
        "attention_queue": {"items": [stale_item]},
        "run_history": {"goals": [stale_goal]},
        "decision_freshness_summary": {
            "available": True,
            "source": "run_history",
            "sample_run_count": 3,
            "window_days": 7,
            "summary": {
                "decision_count": 1,
                "stale_count": 1,
                "rebase_required_count": 1,
                "fresh_count": 0,
            },
            "items": [
                {
                    "goal_id": goal_id,
                    "decision_kind": "operator_gate",
                    "decision_at": "2026-01-01T00:00:00+00:00",
                    "classification": "operator_gate_approved",
                    "age_days": 8.0,
                    "stale_by_age": True,
                    "newer_event_count_7d": 2,
                    "freshness_state": "stale_rebase_required",
                    "requires_decision_point_rebase": True,
                    "reason": "decision older than freshness window and newer sampled events exist",
                },
                {
                    "goal_id": "other-goal",
                    "decision_kind": "human_reward",
                    "decision_at": "2026-01-02T00:00:00+00:00",
                    "age_days": 1.0,
                    "newer_event_count_7d": 1,
                    "freshness_state": "rebase_required",
                    "requires_decision_point_rebase": True,
                },
            ],
        },
    }
    decision = build_quota_should_run(payload, goal_id=goal_id)
    markdown = render_quota_should_run_markdown(decision)

    assert decision["should_run"] is True, decision
    assert decision["state"] == "eligible", decision
    warning = decision["decision_freshness_warning"]
    assert warning["rebase_required_count"] == 1, warning
    assert warning["global_rebase_required_count"] == 1, warning
    assert warning["global_stale_count"] == 1, warning
    assert warning["items"][0]["decision_kind"] == "operator_gate", warning
    assert warning["items"][0]["freshness_state"] == "stale_rebase_required", warning
    assert "decision_freshness_warning: rebase_required=1 window_days=7 source=run_history" in markdown, markdown
    assert "decision_freshness_action: decision-point rebase required" in markdown, markdown
    assert "decision_freshness_item: kind=operator_gate state=stale_rebase_required" in markdown, markdown
    assert "other-goal" not in markdown, markdown


def assert_safe_bypass_slot_preview(status_payload: dict) -> None:
    rejected = build_quota_slot_preview(
        status_payload,
        goal_id="needs-operator",
        slots=1,
    )
    assert rejected["ok"] is False, rejected
    assert "Turn settlement writeback" in rejected["reason"], rejected

    with tempfile.TemporaryDirectory(prefix="loopx-safe-bypass-spend-") as tmp:
        runtime_root = Path(tmp)
        index_path = (
            runtime_root
            / "goals"
            / "needs-operator"
            / "runs"
            / "index.jsonl"
        )
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "goal_id": "needs-operator",
                    "classification": "validated_safe_bypass",
                    "delivery_outcome": "outcome_progress",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        status_with_delivery = deepcopy(status_payload)
        status_with_delivery["runtime_root"] = str(runtime_root)
        payload = build_quota_slot_preview(
            status_with_delivery,
            goal_id="needs-operator",
            slots=1,
        )

    assert payload["ok"] is True, payload
    assert payload["safe_bypass_spend"] is True, payload
    assert payload["before"]["state"] == "operator_gate", payload
    assert payload["before"]["safe_bypass_allowed"] is True, payload
    assert payload["after"]["state"] == "operator_gate", payload
    assert payload["after"]["quota"]["spent_slots"] == payload["before"]["quota"]["spent_slots"] + 1, payload


def assert_delivery_completion_spend_uses_delivery_action() -> None:
    preview = {
        "ok": True,
        "goal_id": "delivery-completion",
        "slots": 1,
        "delivery_completion_spend": True,
        "delivery_run_generated_at": "2026-01-01T00:00:00+00:00",
        "delivery_run_classification": "state_refreshed",
        "delivery_run_recommended_action": "Validated delivery action to account.",
        "before": {
            "ok": True,
            "decision": "observe",
            "should_run": False,
            "state": "waiting",
            "effective_action": "external_evidence_observe",
            "quota": {
                "compute": 1.0,
                "window_hours": 24,
                "slot_minutes": 1,
                "allowed_slots": 1440,
                "spent_slots": 3,
                "state": "waiting",
            },
        },
        "after": {
            "ok": True,
            "decision": "run",
            "should_run": True,
            "state": "eligible",
            "effective_action": "normal_run",
            "recommended_action": "Next guard action should not label the spend event.",
            "quota": {
                "compute": 1.0,
                "window_hours": 24,
                "slot_minutes": 1,
                "allowed_slots": 1440,
                "spent_slots": 4,
                "state": "eligible",
            },
        },
    }

    spend_event = build_quota_slot_spend_event(preview, source="heartbeat")

    assert spend_event["recommended_action"] == "Validated delivery action to account.", spend_event
    assert spend_event["quota_event"]["delivery_run_recommended_action"] == (
        "Validated delivery action to account."
    ), spend_event
    assert spend_event["quota_event"]["delivery_run_classification"] == "state_refreshed", spend_event
    assert "validated delivery completion" in spend_event["health_check"], spend_event


def assert_eligible_spend_preserves_delivery_lineage() -> None:
    preview = {
        "ok": True,
        "goal_id": "eligible-delivery",
        "slots": 1,
        "delivery_completion_spend": False,
        "delivery_run_generated_at": "2026-01-01T00:00:00+00:00",
        "delivery_run_classification": "state_refreshed",
        "delivery_run_recommended_action": "Completed delivery action.",
        "before": {
            "ok": True,
            "decision": "run",
            "should_run": True,
            "state": "eligible",
            "effective_action": "normal_run",
            "quota": {
                "compute": 1.0,
                "window_hours": 24,
                "slot_minutes": 1,
                "allowed_slots": 1440,
                "spent_slots": 3,
                "state": "eligible",
            },
        },
        "after": {
            "ok": True,
            "decision": "run",
            "should_run": True,
            "state": "eligible",
            "effective_action": "normal_run",
            "recommended_action": "Inspect next quota should-run decision.",
            "quota": {
                "compute": 1.0,
                "window_hours": 24,
                "slot_minutes": 1,
                "allowed_slots": 1440,
                "spent_slots": 4,
                "state": "eligible",
            },
        },
    }

    spend_event = build_quota_slot_spend_event(preview, source="heartbeat")

    assert spend_event["recommended_action"] == "Inspect next quota should-run decision.", spend_event
    assert spend_event["quota_event"]["delivery_run_generated_at"] == "2026-01-01T00:00:00+00:00", spend_event
    assert spend_event["quota_event"]["delivery_run_classification"] == "state_refreshed", spend_event
    assert spend_event["quota_event"]["delivery_run_recommended_action"] == "Completed delivery action.", spend_event
    assert "quota should-run eligible" in spend_event["health_check"], spend_event


def main() -> int:
    assert_default_quota_is_duty_cycle()
    assert_rolling_window_ledger_expires_old_spends()
    status_payload = build_status_fixture()
    status_payload["runtime_root"] = FIXTURE_RUNTIME_ROOT
    plan = build_quota_plan(status_payload, mode="plan")
    markdown = render_quota_markdown(plan)
    assert_plan_shape(plan, markdown)
    assert_scheduler_advisory_does_not_override_goal_should_run()
    assert_throttled_should_run(status_payload)
    assert_operator_gate_should_run(status_payload)
    assert_focus_wait_should_run()
    assert_outcome_floor_recovery_should_run()
    assert_outcome_floor_projected_blocker_quiet_noop()
    assert_control_plane_health_self_repair_should_run()
    assert_control_plane_self_repair_default_off()
    assert_control_plane_waiting_projection_self_repair_should_run()
    assert_control_plane_post_handoff_observe_if_unchanged()
    assert_control_plane_post_handoff_agent_todo_stays_active()
    assert_dependency_observation_label_keeps_primary_backlog()
    assert_attention_queue_overrides_stale_run_history()
    assert_project_asset_backed_no_evidence_should_run()
    assert_heartbeat_recommendation_lifecycle()
    assert_goal_boundary_in_should_run()
    assert_decision_freshness_warning_in_should_run()
    assert_safe_bypass_slot_preview(status_payload)
    assert_delivery_completion_spend_uses_delivery_action()
    assert_eligible_spend_preserves_delivery_lineage()
    assert_quota_void_event_net_ledger()
    assert_slot_preview(build_quota_slot_preview(status_payload, goal_id="near-limit-half", slots=1))
    with tempfile.TemporaryDirectory(prefix="loopx-quota-plan-smoke-") as tmp:
        cli_plan, cli_markdown = run_cli_quota_plan(Path(tmp))
    assert_plan_shape(cli_plan, cli_markdown)
    with tempfile.TemporaryDirectory(prefix="loopx-quota-should-run-smoke-") as tmp:
        assert_throttled_cli_should_run(run_cli_throttled_should_run(Path(tmp)))
    with tempfile.TemporaryDirectory(prefix="loopx-quota-slot-smoke-") as tmp:
        slot_preview, should_run_after_preview = run_cli_slot_preview(Path(tmp))
    assert_slot_preview(slot_preview, goal_scoped=True)
    assert_dry_run_left_cli_fixture_unchanged(should_run_after_preview)
    with tempfile.TemporaryDirectory(prefix="loopx-quota-slot-execute-smoke-") as tmp:
        assert_slot_spend_execute(*run_cli_slot_spend_execute(Path(tmp)))
    with tempfile.TemporaryDirectory(prefix="loopx-quota-slot-void-smoke-") as tmp:
        assert_slot_void_execute(*run_cli_slot_void_execute(Path(tmp)))
    print("quota-plan-smoke ok")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
