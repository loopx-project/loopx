"""The projected input must be capable of satisfying its own obligation."""
from __future__ import annotations

import pytest

from loopx.control_plane.work_items.autonomous_replan_obligation import (
    build_autonomous_replan_cli_actions,
)
from loopx.control_plane.work_items.progress_observation import (
    semantic_delta_from_writeback,
)


@pytest.mark.parametrize("trigger", [
    "required_agent_vision_missing", "vision_acceptance_gap",
    "vision_checkpoint_missing", "vision_outcome_checkpoint_required",
    "vision_successor_required",
])
def test_vision_obligations_project_the_input_the_validator_accepts(trigger: str) -> None:
    obligation = {"obligation_id": "replan-example", "triggers": [{"kind": trigger}]}
    novel_probe = {
        "result_class": "advanced", "surface_id": "surface-new",
        "evidence_ids": ["evidence-new"],
    }
    # Novel telemetry alone cannot establish an acceptance/path decision.
    assert semantic_delta_from_writeback(
        obligation=obligation, progress_observation=novel_probe,
    )["accepted"] is False
    assert semantic_delta_from_writeback(
        obligation=obligation, progress_observation=None,
        agent_vision={"vision_patch": {"acceptance_summary": "Verified boundary"},
                      "path_delta": {"outcome": "continue", "evidence_refs": ["evidence-new"]}},
    )["accepted"] is True
    for payload in (obligation, {"autonomous_replan_obligation": obligation}):
        actions = build_autonomous_replan_cli_actions(
            payload, goal_id="example", settlement_args=" --replan-obligation-id replan-example",
            scoped_cli_args=" --agent-id agent-example", quota_spend_action="spend-bound-turn",
            settlement_chain_ready=True,
        )
        assert "--agent-vision-json" in actions[0]
        assert "--progress-surface-id" not in actions[0]
        assert actions[1] == "spend-bound-turn"


def test_declared_outcomes_not_trigger_name_determine_writeback_input() -> None:
    obligation = {"obligation_id": "replan-example", "triggers": [{"kind": "typed_progress_repeat"}],
                  "satisfying_semantic_outcomes": ["fresh_vision_path_outcome"]}
    actions = build_autonomous_replan_cli_actions(
        {"autonomous_replan_obligation": obligation}, goal_id="example",
        settlement_args="", scoped_cli_args="", quota_spend_action="",
        settlement_chain_ready=False,
    )
    assert "--agent-vision-json" in actions[0]


@pytest.mark.parametrize("profile", ["--codex-app", "--trae_app", "--runtime-profile outer_controller"])
def test_guarded_successor_guidance_closes_original_turn_before_stopping(profile: str) -> None:
    payload = {"replan_action_packet": {"writeback_contract": {"successor_command": "loopx todo add"}}}
    binding = "--replan-obligation-id replan-1111111111111111 --turn-instance-id turn-original"
    guard = f"loopx --format json quota should-run {profile} --goal-id example --agent-id agent-example {binding}"
    actions = build_autonomous_replan_cli_actions(
        payload, goal_id="example", settlement_args=f" {binding}",
        scoped_cli_args=" --agent-id agent-example", quota_spend_action="spend-bound-turn",
        settlement_chain_ready=True, successor_closeout_guard=guard,
    )
    assert actions[0] == "execute replan_action_packet.writeback_contract.successor_command"
    assert actions[1] == guard
    assert "do not execute or select its successor" in actions[2]
    assert not any("spend-bound-turn" in action for action in actions)


@pytest.mark.parametrize("settlement_chain_ready", [False, True])
def test_unguarded_successor_still_ends_without_inventing_turn_settlement(settlement_chain_ready: bool) -> None:
    actions = build_autonomous_replan_cli_actions(
        {"replan_action_packet": {"writeback_contract": {"successor_command": "loopx todo add"}}},
        goal_id="example", settlement_args="", scoped_cli_args=" --agent-id agent-example",
        quota_spend_action="spend-bound-turn", settlement_chain_ready=settlement_chain_ready,
    )
    assert actions == ["execute replan_action_packet.writeback_contract.successor_command",
                       "on host_action=end_current_heartbeat: stop"]


@pytest.mark.parametrize("profile", ["codex_app_heartbeat", "trae_app", "outer_controller"])
@pytest.mark.parametrize("binding_kind", ["autonomous_replan", "todo"])
def test_successor_guard_entrypoint_retains_host_and_only_pure_replan_binding(
    profile: str, binding_kind: str,
) -> None:
    import shlex
    from loopx.control_plane.quota.settlement import build_turn_scoped_cli_settlement_plan
    from loopx.control_plane.scheduler.execution_context import (
        render_scheduler_execution_args, scheduler_execution_context_for_runtime_profile,
    )
    from loopx.control_plane.work_items.interaction_contract import interaction_next_cli_actions

    context = scheduler_execution_context_for_runtime_profile(profile)
    identity = ({"replan_obligation_id": "replan-1111111111111111"}
                if binding_kind == "autonomous_replan" else {"todo_id": "todo_original"})
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id="example", agent_id="agent-example", turn_instance_id="turn-original",
        scoped_cli_args=" --agent-id agent-example", lifecycle_actor_args=" --agent-id agent-example",
        **identity,
    ).as_dict()
    actions = interaction_next_cli_actions(
        {"goal_id": "example", "agent_identity": {"agent_id": "agent-example"},
         "replan_action_packet": {"writeback_contract": {"successor_command": "loopx todo add"}}},
        mode="autonomous_replan", scheduler_execution_context=context,
        settlement_plan=plan, capability_reentry_resolved=True,
        runtime_root="/tmp/public-safe-replan-runtime",
    )
    assert actions[0] == "execute replan_action_packet.writeback_contract.successor_command"
    if binding_kind == "todo":
        assert actions[1:] == ["on host_action=end_current_heartbeat: stop"]
        return
    tokens = shlex.split(actions[1])
    assert tokens[tokens.index("--runtime-root") + 1] == "/tmp/public-safe-replan-runtime"
    assert tokens[tokens.index("--turn-instance-id") + 1] == "turn-original"
    assert tokens[tokens.index("--replan-obligation-id") + 1] == identity["replan_obligation_id"]
    assert tokens[tokens.index("--agent-id") + 1] == "agent-example"
    assert "--todo-id" not in tokens
    assert render_scheduler_execution_args(scheduler_execution_context=context).strip() in actions[1]
    assert "do not execute or select its successor" in actions[2]


def test_turn_envelope_preserves_executable_vision_authoring_contract() -> None:
    from loopx.control_plane.testing.control_plane_composition_scenarios import _required_vision_replan_source
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    source = _required_vision_replan_source(goal_id="example", agent_id="agent-example")
    envelope = build_turn_envelope(source)
    writeback = source["replan_action_packet"]["writeback_contract"]
    assert "path_delta.evidence_refs" in writeback["required_fields"]
    assert envelope["replan_action_packet"]["writeback_contract"] == writeback
