from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import sys

import pytest

from loopx.control_plane.effect_program import (
    interpret_quota_should_run_packet,
    interpret_turn_result_packet,
)
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.turn_driver import (
    build_loopx_turn_host_request,
    build_loopx_turn_plan,
    load_loopx_turn_plan_from_journal,
    run_loopx_turn_once,
    validate_loopx_turn_host_result,
)
from loopx.control_plane.turn_driver.transaction import LoopXTurnResultKind
from loopx.quota import build_quota_should_run

GOAL_ID = "effect-interpreter-fixture"


def _advancement_payload() -> dict:
    todo_text = "[P1] Advance the bounded slice."
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )


def test_quota_should_run_exposes_canonical_effect_slots_without_legacy_packet() -> None:
    packet = build_quota_should_run(_advancement_payload(), goal_id=GOAL_ID)
    assert "protocol_action_packet" not in packet
    turn = interpret_quota_should_run_packet(
        packet,
        goal_id=GOAL_ID,
        agent_id="codex-fixture",
        capabilities=["shell", "filesystem_write"],
    )

    # interpretation
    assert turn.request.kind == "quota_should_run"
    assert turn.request.goal_id == GOAL_ID
    assert turn.request.agent_id == "codex-fixture"
    assert turn.request.capabilities == ("shell", "filesystem_write")
    assert turn.interpretation.route == "advancement_task"
    assert turn.interpretation.obligation == "advance_one_bounded_segment"
    assert turn.interpretation.interaction_mode == "bounded_delivery"

    # observation
    assert turn.observation.decision == "run"
    assert turn.observation.should_run is True
    assert turn.observation.effective_action == "normal_run"
    assert turn.observation.recommended_action == "[P1] Advance the bounded slice."
    assert turn.observation.protocol_summary is None

    # next effect
    assert turn.next_effect.cli_actions
    assert turn.next_effect.cli_actions[0].startswith("loopx refresh-state")
    assert turn.next_effect.scheduler_action
    assert turn.next_effect.cadence_class


def test_quota_should_run_capability_gate_is_structured_around_decision() -> None:
    todo_text = "[P1] Network-only slice."
    payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[
            {
                "index": 1,
                "text": todo_text,
                "role": "agent",
                "status": "open",
                "priority": "P1",
                "task_class": "advancement_task",
                "required_capabilities": ["network"],
                "action_kind": "inspect_target",
                "target_key": "fixture/target.json",
            }
        ],
        recommended_action=todo_text,
        next_action=todo_text,
    )
    packet = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        available_capabilities=["shell"],
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "ark_managed_agent_goal"
        ),
    )
    turn = interpret_quota_should_run_packet(
        packet,
        goal_id=GOAL_ID,
        agent_id="codex-fixture",
        capabilities=["shell"],
    )

    # The gate is an around decision: it short-circuits the original effect
    # and rewrites the next effect, but permission semantics stay visible.
    assert turn.interpretation.capability_action == "repair_bridge"
    assert turn.observation.decision == "repair_bridge"
    assert turn.observation.effective_action == "capability_bridge_repair"
    assert packet.get("capability_gate", {}).get("action") == "repair_bridge"
    assert packet.get("capability_gate", {}).get("owner_missing") == []
    contract = packet["interaction_contract"]
    task_action = contract["agent_channel"]["next_task_action"]
    assert task_action["operation"] == "inspect_target"
    assert task_action["target_ref"] == "fixture/target.json"
    assert task_action["preflight_allowed"] is False
    assert task_action["advancement_checkpoint"] is False
    assert task_action["settles_turn"] is False

    reentry = contract["cli_channel"]["runtime_capability_reentry"]
    reentry_command = reentry["candidates"][0]["command"]
    assert contract["cli_channel"]["next_cli_actions"] == [reentry_command]
    assert turn.next_effect.cli_actions == (reentry_command,)
    assert "--available-capability network" in reentry_command
    assert contract["cli_channel"]["spend_after_validation"] is False


def test_effect_turn_keeps_monitor_quiet_around_decision_data_visible() -> None:
    packet = {
        "decision": "skip",
        "should_run": False,
        "effective_action": "monitor_quiet_skip",
        "recommended_action": "quiet until the next material transition",
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "monitor_quiet_skip",
            "user_channel": {"notify": "DONT_NOTIFY", "action_required": False},
            "agent_channel": {
                "must_attempt": False,
                "delivery_allowed": False,
                "quiet_noop_allowed": True,
            },
            "cli_channel": {"next_cli_actions": []},
        },
        "work_lane_contract": {
            "lane": "continuous_monitor",
            "obligation": "attempt_due_monitor",
            "must_attempt_work": False,
        },
        "scheduler_hint": {
            "action": "no_spend",
            "cadence_class": "monitor_wait",
        },
    }
    turn = interpret_quota_should_run_packet(
        packet,
        goal_id=GOAL_ID,
        agent_id="codex-fixture",
        capabilities=["network", "external_evidence_poll"],
    )

    # A quiet turn must not be rewritten into a runnable next effect or a
    # spend. The around decision stays visible in typed packet slots.
    assert turn.interpretation.interaction_mode == "monitor_quiet_skip"
    assert turn.interpretation.route == "continuous_monitor"
    assert turn.observation.decision == "skip"
    assert turn.observation.should_run is False
    assert turn.observation.effective_action == "monitor_quiet_skip"
    assert turn.next_effect.cli_actions == ()
    assert turn.next_effect.scheduler_action == "no_spend"
    assert turn.next_effect.cadence_class == "monitor_wait"
    assert turn.next_effect.ack_cli_args == ()
    assert turn.next_effect.failure_cli_args == ()


def test_effect_turn_carries_scheduler_ack_and_failure_hints() -> None:
    packet = {
        "decision": "run",
        "should_run": True,
        "execution_mode": "interleaved",
        "effective_action": "normal_run",
        "recommended_action": "advance the bounded segment",
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {
                "action_required": False,
                "notify": "DONT_NOTIFY",
            },
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
            },
            "cli_channel": {
                "next_cli_actions": [
                    "loopx refresh-state --goal-id effect-interpreter-fixture",
                    "loopx quota spend-slot --goal-id effect-interpreter-fixture",
                ]
            },
        },
        "work_lane_contract": {
            "lane": "advancement_task",
            "obligation": "advance_one_bounded_segment",
            "must_attempt_work": True,
        },
        "scheduler_hint": {
            "action": "apply_rrule",
            "cadence_class": "active_work",
            "codex_app": {
                "ack_hint": {
                    "cli_args": [
                        "quota",
                        "scheduler-ack-current",
                        "--goal-id",
                        "effect-interpreter-fixture",
                        "--execute",
                    ]
                },
                "failure_hint": {
                    "cli_args": [
                        "quota",
                        "scheduler-ack-current",
                        "--goal-id",
                        "effect-interpreter-fixture",
                        "--failure",
                        "--execute",
                    ]
                },
            },
        },
    }
    turn = interpret_quota_should_run_packet(
        packet,
        goal_id=GOAL_ID,
        agent_id="codex-fixture",
    )

    assert turn.next_effect.cli_actions == (
        "loopx refresh-state --goal-id effect-interpreter-fixture",
        "loopx quota spend-slot --goal-id effect-interpreter-fixture",
    )
    assert turn.next_effect.execution_mode == "interleaved"
    assert turn.next_effect.scheduler_action == "apply_rrule"
    assert turn.next_effect.cadence_class == "active_work"
    assert turn.next_effect.ack_cli_args == (
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        "effect-interpreter-fixture",
        "--execute",
    )
    assert turn.next_effect.failure_cli_args == (
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        "effect-interpreter-fixture",
        "--failure",
        "--execute",
    )


@pytest.mark.parametrize("result_kind", [kind.value for kind in LoopXTurnResultKind])
def test_result_runtime_and_python_adapter_expose_verdict_without_action(result_kind):
    packet = {
        "result_kind": result_kind,
        "completed_phases": ["host_execute", "typed_result"],
        "failed_phase": "validation",
        "next_cli_actions": ["loopx status"],
    }
    before = deepcopy(packet)
    raw = effect_runtime_result("effect.interpret_turn_result", {"packet": packet})
    turn = interpret_turn_result_packet(packet)
    assert raw["observation"]["decision"] == turn.observation.decision == result_kind
    assert raw["observation"]["effective_action"] is None
    assert turn.observation.effective_action is None
    assert (
        json.loads(json.dumps(asdict(turn)))["observation"]["effective_action"] is None
    )
    assert turn.observation.should_run is False
    assert turn.request.context["failed_phase"] == "validation"
    assert turn.next_effect.cli_actions == ("loopx status",)
    assert packet == before


@pytest.mark.parametrize(
    "host_action",
    [
        "normal_run",
        "agent_scope_wait",
        "wait",
        "foreign_action",
        None,
        42,
        {"action": "normal_run"},
        ["normal_run"],
    ],
)
def test_host_action_cannot_enter_the_quota_slot(host_action):
    turn = interpret_turn_result_packet(
        {"result_kind": "wait", "effective_action": host_action}
    )
    assert turn.observation.decision == "wait"
    assert turn.observation.effective_action is None


def _executor_fixture_todo(*, claimed: bool) -> dict:
    todo = {
        "index": 1,
        "todo_id": "todo_effect_fixture",
        "text": "[P1] Advance the bounded slice.",
        "role": "agent",
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
    }
    if claimed:
        # A sole unclaimed candidate requires explicit selection before the
        # Turn may bind it; only a Todo claimed by this agent reaches the host
        # directly. These executor tests exercise that direct-delivery path.
        todo["claimed_by"] = "codex-fixture"
    return todo


def _executor_fixture_plan(*, claimed: bool) -> tuple[dict, dict]:
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Advance the bounded slice.",
        coordination={"agent_model": "peer_v1", "registered_agents": ["codex-fixture"]},
        agent_todo_items=[_executor_fixture_todo(claimed=claimed)],
    )
    packet = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id="codex-fixture",
    )
    plan = build_loopx_turn_plan(
        build_turn_envelope(packet),
        host="generic-cli",
        execution_mode="isolated-headless",
    )
    return packet, plan


def test_unclaimed_sole_candidate_plans_blocked_and_never_builds_a_host_request():
    packet, plan = _executor_fixture_plan(claimed=False)
    assert packet["should_run"] is True
    assert packet["effective_action"] == "normal_run"
    selected = plan["route"]["selected_todo"]
    assert selected["selected_by"] == "unclaimed_todo"
    assert plan["route"]["kind"] == "blocked"
    assert plan["route"]["would_invoke_host"] is False
    with pytest.raises(ValueError, match="not host executable"):
        build_loopx_turn_host_request(plan)


def test_real_executor_uses_verdict_and_replays_persisted_plan(tmp_path):
    packet, plan = _executor_fixture_plan(claimed=True)
    assert plan["route"]["kind"] == "ready_for_host"
    assert plan["route"]["selected_todo"]["selected_by"] == "current_agent_claimed_todo"
    result = {
        "schema_version": "loopx_turn_result_v0",
        "turn_key": plan["transaction"]["turn_key"],
        "result_kind": "wait",
        "completed_phases": ["host_execute", "typed_result"],
    }
    # The executor's existing host schema rejects this field before interpretation.
    invalid = validate_loopx_turn_host_result(
        plan, {**result, "effective_action": "normal_run"}
    )
    assert invalid["ok"] is False
    assert "unsupported host result fields: effective_action" in invalid["errors"]
    wrong_verdict = validate_loopx_turn_host_result(
        plan, {**result, "result_kind": "normal_run"}
    )
    assert wrong_verdict["ok"] is False
    assert "unsupported host result kind" in wrong_verdict["errors"]
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    def forbidden_effect(*_args, **_kwargs):
        pytest.fail(
            "a wait result must not write back, spend, or apply scheduler effects"
        )

    kwargs = {
        "host_argv": [
            sys.executable,
            "-c",
            "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())",
            str(result_path),
        ],
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": GOAL_ID,
        "execute": True,
        "timeout_seconds": 5,
        "writeback": forbidden_effect,
        "spend": forbidden_effect,
        "scheduler": forbidden_effect,
    }
    first = run_loopx_turn_once(plan, **kwargs)
    assert first["status"] == "stopped"
    assert first["result_kind"] == "wait"
    assert first["effects"]["host_invoked"] is True
    assert first["effects"]["quota_spent"] is False
    resumed = load_loopx_turn_plan_from_journal(
        tmp_path / "runtime",
        goal_id=GOAL_ID,
        turn_key=result["turn_key"],
    )
    assert resumed == plan
    assert resumed["turn_envelope"]["effective_action"] == packet["effective_action"]
    replay = run_loopx_turn_once(resumed, **kwargs)
    assert replay["replayed"] is True
    assert replay["result_kind"] == "wait"
    assert not any(replay["effects"].values())
