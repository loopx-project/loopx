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


def test_turn_envelope_preserves_executable_vision_authoring_contract() -> None:
    from loopx.control_plane.testing.control_plane_composition_scenarios import _required_vision_replan_source
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    source = _required_vision_replan_source(goal_id="example", agent_id="agent-example")
    envelope = build_turn_envelope(source)
    writeback = source["replan_action_packet"]["writeback_contract"]
    assert "path_delta.evidence_refs" in writeback["required_fields"]
    assert envelope["replan_action_packet"]["writeback_contract"] == writeback
