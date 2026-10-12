"""Causal feedback must use the carrier's declared order, never its timestamps."""
from __future__ import annotations

import json

import pytest

from loopx.control_plane.work_items.progress_observation import build_replan_context
from loopx.control_plane.work_items.replan_context_codec import project_replan_context
from test_replan_context_evidence import _settled_pair


def _rows():
    work, _ = _settled_pair()

    def decision(turn, timestamp):
        return {**work, "generated_at": timestamp, "turn_instance_id": turn,
            "settlement_identity": {**work["settlement_identity"], "turn_instance_id": turn},
            "agent_vision": {"schema_version": "goal_vision_replan_contract_v0", "agent_id": "agent-a",
                "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
                    "prior_assumption": "The current route could explain the gap.",
                    "observed_reality": "The gap remains unexplained.", "changed": ["Compare another route."],
                    "evidence_refs": ["evidence:route-basis"]}},
            "autonomous_replan_ack": {"schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "semantic_delta": {"schema_version": "replan_semantic_delta_v0", "accepted": True,
                    "satisfying_outcomes": ["fresh_vision_path_outcome"]}}}

    older = decision("turn-old", "2026-08-18T03:00:00Z")
    latest = decision("turn-latest", "2026-08-18T01:00:00Z")
    later = {**work, "turn_instance_id": "turn-probe", "generated_at": "2026-08-18T00:00:00Z",
        "settlement_identity": {**work["settlement_identity"], "turn_instance_id": "turn-probe"},
        "progress_observation": {**work["progress_observation"], "surface_id": "surface-probe"}}
    return [older, latest, later]


def _assert_followthrough(context):
    follow = context["decision_followthrough"]
    assert follow["previous_decision"]["turn_instance_id"] == "turn-latest"
    assert follow["recorded_progress_count"] == 1
    assert follow["observations"][0]["generated_at"] == "2026-08-18T00:00:00Z"
    assert follow["execution_and_goal_gain"] == "unknown"
    return follow


@pytest.mark.parametrize("shape", ["runs", "goals"])
def test_compact_status_newest_first_returns_latest_decision_and_later_work(shape):
    rows = list(reversed(_rows()))
    history = {"runs": rows} if shape == "runs" else {"goals": [{"id": "evidence-goal", "latest_runs": rows}]}
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
        source_status={"run_history": history}, stride_strategy="evidence_conditioned_v1")
    assert _assert_followthrough(context)["source_history_complete"] is False


def test_named_newest_first_builder_preserves_order_without_a_status_source():
    context = build_replan_context({"obligation_id": "replan-fixture"}, goal_id="evidence-goal",
        agent_id="agent-a", newest_first_runs=reversed(_rows()), stride_strategy="evidence_conditioned_v1")
    _assert_followthrough(context)


def test_real_append_index_ignores_compact_display_order_and_clock_skew(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "evidence-goal", "repo": str(tmp_path)}]}))
    index = tmp_path / "goals/evidence-goal/runs/index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in _rows()))
    before = index.read_bytes()
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
        source_status={"registry": str(registry), "runtime_root": str(tmp_path),
            "run_history": {"runs": [_rows()[0]]}}, stride_strategy="evidence_conditioned_v1")
    assert _assert_followthrough(context)["source_history_complete"] is True
    assert index.read_bytes() == before


@pytest.mark.parametrize("rows", [_rows(), list(reversed(_rows()))])
def test_unknown_order_does_not_fabricate_a_previous_decision_or_later_work(rows):
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
        stride_strategy="evidence_conditioned_v1")
    assert "decision_followthrough" not in context
    assert context["evidence_count"] == 3


def test_carrier_metadata_preserves_feature_off_parity_and_rejects_non_boolean_order():
    rows = list(reversed(_rows()))
    ordinary = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows)
    declared = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
        history_newest_first=True, stride_strategy="off")
    assert declared == ordinary
    for invalid in [0, 1, "newest_first"]:
        with pytest.raises(ValueError, match="boolean or unknown"):
            project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
                history_newest_first=invalid)
