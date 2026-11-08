"""The projected input must be capable of satisfying its own obligation."""
from __future__ import annotations

import pytest

from loopx.control_plane.work_items.autonomous_replan_obligation import (
    build_autonomous_replan_cli_actions,
)
from loopx.control_plane.work_items.progress_observation import (
    semantic_delta_from_writeback,
)


@pytest.mark.parametrize("upgrade,expected", [
    (None, None),
    ([], None),
    ({"agent_example_commands": [{"command": " first "}, None, {"command": ""}],
      "completion_command": " finish "}, ["first", "finish"]),
    ({"completion_command": " finish "}, ["finish"]),
    ({"agent_example_commands": [{"command": "  "}]}, None),
])
def test_prompt_upgrade_keeps_supplied_commands_in_order_or_actionable_fallback(upgrade, expected):
    from loopx.control_plane.work_items.interaction_contract import interaction_next_cli_actions

    actions = interaction_next_cli_actions(
        {"goal_id": "example", "automation_prompt_upgrade": upgrade},
        mode="automation_prompt_upgrade", capability_reentry_resolved=True,
        runtime_root="/tmp/public-safe runtime",
    )
    if expected is not None:
        assert actions == expected
    else:
        assert len(actions) == 1
        assert "--runtime-root '/tmp/public-safe runtime' heartbeat-prompt" in actions[0]
        assert "--goal-id example" in actions[0]
        assert "--agent-id <registered-agent>" in actions[0]


@pytest.mark.parametrize("profile", ["generic_cli", "codex_app_heartbeat", "outer_controller"])
@pytest.mark.parametrize("bound_todo", ["todo_deferred", "todo_other"])
def test_deferred_reopen_requires_same_todo_readmission_before_execution(profile, bound_todo):
    import shlex
    from copy import deepcopy
    from loopx.control_plane.quota.settlement import build_turn_scoped_cli_settlement_plan
    from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
    from loopx.control_plane.work_items.interaction_contract import interaction_next_cli_actions

    payload = {"goal_id": "example", "agent_identity": {"agent_id": "agent-example"},
        "agent_scope_frontier": {"deferred_resume_candidates": [{"todo_id": "todo_deferred"}]}}
    original = deepcopy(payload)
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id="example", agent_id="agent-example", todo_id=bound_todo,
        turn_instance_id="turn-original", scoped_cli_args=" --agent-id agent-example",
        lifecycle_actor_args=" --agent-id agent-example",
    ).as_dict()
    actions = interaction_next_cli_actions(
        payload, mode="successor_replan_required", settlement_plan=plan,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(profile),
        capability_reentry_resolved=True, runtime_root="/tmp/public-safe runtime",
        registry_path="/tmp/public-safe registry.json",
    )
    reopen = shlex.split(actions[0])
    assert reopen[reopen.index("--todo-id") + 1] == "todo_deferred"
    assert reopen[reopen.index("--status") + 1] == "open"
    assert "--clear-resume-when" in reopen
    assert payload == original
    if bound_todo == "todo_deferred":
        guard = shlex.split(actions[1])
        assert guard[guard.index("--todo-id") + 1] == bound_todo
        assert guard[guard.index("--turn-instance-id") + 1] == "turn-original"
        assert guard[guard.index("--runtime-root") + 1] == "/tmp/public-safe runtime"
        assert "Only if it admits this same Todo" in actions[2]
        assert "fresh execution claim/lease" in actions[2]
        assert not any("spend-slot" in action or "refresh-state" in action for action in actions)
    else:
        assert "successor_replan_recorded" in actions[1]
        assert "quota spend-slot" in actions[2]
        assert all("--todo-id todo_deferred" not in action for action in actions[1:])


@pytest.mark.parametrize("repair", ["cycle", "monitor", "route"])
def test_successor_repair_preserves_projected_candidate_precedence(repair):
    from loopx.control_plane.work_items.interaction_contract import interaction_next_cli_actions

    frontier = {"deferred_resume_candidates": [{"todo_id": "todo_deferred"}],
        "route_continuation_replan_candidates": [{"todo_id": "todo_route"}]}
    if repair in {"cycle", "monitor"}:
        frontier["monitor_blocked_resume_candidates"] = [
            {"todo_id": "todo_gated", "blocking_monitor_todo_id": "todo_monitor"}]
    if repair == "cycle":
        frontier["dependency_cycle_candidates"] = [{"todo_id": "todo_cycle"}]
        frontier["recommended_action"] = "inspect the existing dependency cycle"
    actions = interaction_next_cli_actions(
        {"goal_id": "example", "agent_identity": {"agent_id": "agent-example"},
         "agent_scope_frontier": frontier}, mode="successor_replan_required",
        capability_reentry_resolved=True,
    )
    if repair == "cycle":
        assert "todo list" in actions[0] and "--todo-id todo_cycle" in actions[0]
        assert actions[1] == frontier["recommended_action"]
        assert not any("spend-slot" in action for action in actions)
    elif repair == "monitor":
        assert "todo complete" in actions[0] and "--todo-id todo_monitor" in actions[0]
        assert "--todo-id todo_gated" in actions[1]
        assert "--evidence '<validated gate evidence>'" in actions[0]
    else:
        assert "todo add" in actions[0]
        assert "route_continuation_replan_recorded" in actions[1]
    assert not any("--todo-id todo_deferred" in action for action in actions)


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


@pytest.mark.parametrize("profile", ["generic_cli", "codex_app_heartbeat", "outer_controller"])
@pytest.mark.parametrize("turn", [None, "turn-active-lifecycle"])
def test_lifecycle_reentry_retains_current_route_without_reusing_completion_identity(profile, turn):
    import shlex
    from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
    from loopx.control_plane.work_items.interaction_contract import interaction_next_cli_actions

    actions = interaction_next_cli_actions(
        {"goal_id": "example", "agent_identity": {"agent_id": "agent-example"},
         "autonomous_replan_obligation": {"resolution_mode": "todo_lifecycle_settlement",
            "triggers": [{"kind": "completed_advancement_without_successor",
                "todo_id": "todo_completed", "completion_turn_key": "old-completion"}]}},
        mode="autonomous_replan", available_capabilities=["shell"],
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(profile),
        turn_instance_id=turn, runtime_root="/tmp/lifecycle-runtime",
        registry_path="/tmp/lifecycle-registry.json",
    )
    assert "--completion-identity-key old-completion" in actions[0]
    assert "--turn-instance-id" not in actions[0]
    guard = shlex.split(actions[-1])
    if turn is None:
        assert "--turn-instance-id" not in guard
    else:
        assert guard[guard.index("--turn-instance-id") + 1] == turn
        assert guard[guard.index("--registry") + 1] == "/tmp/lifecycle-registry.json"
        assert guard[guard.index("--runtime-root") + 1] == "/tmp/lifecycle-runtime"
        assert guard[guard.index("--available-capability") + 1] == "shell"
        assert ("--codex-app" in guard) is (profile == "codex_app_heartbeat")
    assert "--completion-identity-key" not in guard


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


@pytest.mark.parametrize("current", [None, [], 42])
def test_missing_current_does_not_normalize_invalid_baseline_or_window(current):
    result = semantic_delta_from_writeback(
        obligation={"progress_baseline": {"result_class": "invalid"},
                    "progress_window": [{"result_class": "invalid"}]},
        progress_observation=current,
    )
    assert result["accepted"] is False
    assert result["observation_fingerprint"] is None
    assert result["outcomes"] == []


def test_current_normalization_error_precedes_baseline_normalization():
    with pytest.raises(ValueError, match="result_class must be one of"):
        semantic_delta_from_writeback(
            obligation={"progress_baseline": {"result_class": "advanced", "surface_id": "not an identifier"}},
            progress_observation={"result_class": "invalid"},
        )


def test_malformed_window_entries_do_not_replace_valid_novel_progress():
    result = semantic_delta_from_writeback(
        obligation={"progress_window": [{"result_class": "invalid"}, "untyped"]},
        progress_observation={"result_class": "advanced", "surface_id": "surface-new",
                              "evidence_ids": ["evidence-new"]},
    )
    assert result["accepted"] is True
    assert result["satisfying_outcomes"] == ["new_surface"]
