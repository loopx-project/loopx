"""A repaired checkpoint cannot strand its already admitted Vision review."""
from copy import deepcopy

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.work_items.semantic_replan_writeback import (
    ReplanWritebackRejected,
    enforce_open_replan_writeback,
    qualify_replan_writeback,
)
from test_refresh_state_replan_gate import AGENT_ID, GOAL_ID, STATE_TEXT, _open_vision_after_prior_ack


def _history():
    vision = {**_open_vision_after_prior_ack(), "goal_id": GOAL_ID}
    vision["generated_at"] = "2026-08-13T11:00:00+08:00"
    missing = {
        "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "generated_at": "2026-08-13T11:27:00+08:00",
        "classification": "verified_delivery", "delivery_outcome": "primary_goal_outcome",
        "vision_checkpoint": {"schema_version": "vision_checkpoint_v0", "agent_id": AGENT_ID,
            "required": True, "satisfied": False, "decision": "missing_required",
            "triggers": [{"kind": "material_delivery_outcome", "delivery_outcome": "primary_goal_outcome"}]},
    }
    repaired = {**deepcopy(missing), "generated_at": "2026-08-13T11:29:00+08:00",
        "agent_vision": {**deepcopy(vision["agent_vision"]), "state": "vision_closed",
            "path_delta": {"outcome": "replan", "changed": ["The original milestone was checked."],
                "evidence_refs": ["evidence:original-milestone"]}},
        "refresh_recovery": {"decision": "supplement_checkpoint"}}
    repaired["vision_checkpoint"].update(satisfied=True, decision="patched")
    return [missing, vision], [repaired, missing, vision]


def _bound():
    original, current = _history()
    args = {"state_text": STATE_TEXT, "goal_id": GOAL_ID, "agent_id": AGENT_ID}
    admitted, _ = qualify_replan_writeback(newest_first_runs=original, **args)
    assert admitted is not None
    assert {t["kind"] for t in admitted["triggers"]} == {
        "vision_acceptance_gap", "vision_checkpoint_missing", "vision_outcome_checkpoint_required"}
    return {**args, "newest_first_runs": current, "guard_scoped": True,
        "guard_semantic_replan_obligation_id": admitted["obligation_id"],
        "receipt_bound_guard_recorded_at": "2026-08-13T11:28:00+08:00",
        "receipt_bound_guard_turn_instance_id": "original-vision-review"}, admitted


def test_repaired_checkpoint_retains_full_original_review_until_fresh_writeback():
    args, admitted = _bound()
    before = deepcopy(args)
    obligation, _ = qualify_replan_writeback(**args)
    assert obligation["obligation_id"] == admitted["obligation_id"]
    assert obligation["triggers"] == admitted["triggers"]
    assert obligation["stop_condition"] == admitted["stop_condition"]
    with pytest.raises(ReplanWritebackRejected):
        enforce_open_replan_writeback(**args)
    delta = enforce_open_replan_writeback(**args, agent_vision={
        "vision_patch": {"acceptance_summary": "The original evidence and independent review remain valid."},
        "path_delta": {"outcome": "continue", "evidence_refs": ["evidence:original-review-rechecked"]}})
    assert delta["obligation_id"] == admitted["obligation_id"]
    assert delta["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    assert args == before


@pytest.mark.parametrize("change", ["wrong-agent", "wrong-goal", "wrong-id", "too-early", "missing-history"])
def test_history_recovery_cannot_borrow_another_source(change):
    args, _ = _bound()
    if change == "wrong-agent":
        args["agent_id"] = "another-agent"
    elif change == "wrong-goal":
        args["goal_id"] = "another-goal"
    elif change == "wrong-id":
        args["guard_semantic_replan_obligation_id"] = "replan-1111111111111111"
    elif change == "too-early":
        args["receipt_bound_guard_recorded_at"] = "2026-08-13T10:00:00+08:00"
    else:
        args["newest_first_runs"] = args["newest_first_runs"][:1]
    with pytest.raises((ValueError, ReplanWritebackRejected, EffectRuntimeRejected)):
        enforce_open_replan_writeback(**args, agent_vision={
            "vision_patch": {"acceptance_summary": "An unrelated current result is insufficient."},
            "path_delta": {"outcome": "continue", "evidence_refs": ["evidence:unrelated"]}})
