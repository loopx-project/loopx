"""Complete replan facts use the private transport, never a display window."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.work_items.replan_semantics_codec import project_replan_semantics
from loopx.control_plane.work_items.lifecycle_recovery import receipt_bound_lifecycle_candidate
from loopx.control_plane.goals.goal_frontier import derive_goal_frontier_replan_obligation_from_summaries
from loopx.control_plane.todos.resume_condition import evaluate_todo_resume_conditions


@pytest.fixture
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    yield tmp_path
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize("recovery", ["same_turn_terminal_closeout", "lifecycle_reentry_terminal_closeout"])
def test_lifecycle_source_preserves_complete_input_and_output(isolated_runtime: Path, large: bool, recovery: str):
    item = {
        "todo_id": "todo_accepted_source", "index": 1,
        "status": "done", "done": True, "task_class": "advancement_task",
        "claimed_by": "fixture-agent", "last_actor_agent_id": "fixture-agent",
        "completed_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
        "completion_turn_key": "original-completion",
        "no_followup": True, "completion_continuation": "no_followup",
        "completion_recovery": recovery,
        "note": "x" * (effect_runtime.MAX_REQUEST_BYTES + 128) if large else "Accepted source.",
    }
    params = {"operation": "lifecycle_source", "agent_id": "fixture-agent",
        "guard_recorded_at": "2026-08-01T00:00:00.500Z", "items": [item]}
    if large:
        # Registration is not a global bypass: ordinary requests still fail.
        with pytest.raises(effect_runtime.EffectRuntimeRejected, match="request is oversized"):
            effect_runtime.effect_runtime_result("work_item.replan_semantics.project", params)
    else:
        assert effect_runtime.effect_runtime_result("work_item.replan_semantics.project", params) == {
            "items": [item], "settled": True,
        }
    assert project_replan_semantics(params) == {"items": [item], "settled": True}
    assert list(isolated_runtime.glob("loopx-effect-*/params.json")) == []
    assert list(isolated_runtime.glob("loopx-effect-*/response.json")) == []


def test_rejected_large_projection_removes_private_files(isolated_runtime: Path):
    with pytest.raises(effect_runtime.EffectRuntimeRejected):
        project_replan_semantics({"operation": "unsupported",
            "items": [{"note": "x" * (effect_runtime.MAX_REQUEST_BYTES + 128)}]})
    assert list(isolated_runtime.glob("loopx-effect-*/params.json")) == []
    assert list(isolated_runtime.glob("loopx-effect-*/response.json")) == []
    assert effect_runtime.effect_runtime_result("runtime.ping", {})["ready"] is True


def test_lifecycle_dependency_reuses_the_canonical_successor_receipt(isolated_runtime: Path):
    original = {"todo_id": "todo_completed", "status": "done", "done": True,
        "task_class": "advancement_task", "claimed_by": "fixture-agent",
        "last_actor_agent_id": "fixture-agent", "completed_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z", "completion_turn_key": "original-completion"}

    def derive(summary):
        return derive_goal_frontier_replan_obligation_from_summaries(
            user_todo_summary=None, agent_todo_summary=summary, work_lane_contract=None,
            agent_id="fixture-agent", existing_replan_obligation=None)

    admitted = derive({"completed_without_successor_items": [original],
                      "completed_without_successor_count": 1})
    successor = {"todo_id": "todo_successor", "status": "open", "done": False,
        "task_class": "advancement_task", "claimed_by": "fixture-agent",
        "last_actor_agent_id": "fixture-agent", "updated_at": "2026-08-01T00:00:02Z",
        "replan_obligation_id": admitted["obligation_id"], "action_kind": "validate",
        "target_key": "snapshot-continuity", "resume_when": "todo_done:todo_completed"}
    condition = evaluate_todo_resume_conditions([successor], source_items=[original, successor])[
        successor["todo_id"]]
    successor = {**successor, "resume_condition": condition, "resume_ready": condition["satisfied"]}
    assert successor["resume_ready"] is True
    candidate = receipt_bound_lifecycle_candidate(selected_obligation_id=admitted["obligation_id"],
        guard_recorded_at="2026-08-01T00:00:01.500Z", agent_id="fixture-agent",
        items=[original, successor], derive=derive)
    assert candidate["ack"]["semantic_delta"]["successor_todo_id"] == successor["todo_id"]
    recovered = project_replan_semantics({"operation": "receipt_bound_obligation",
        "guard_scoped": True, "replay_phase": "open", "selected_obligation_id": admitted["obligation_id"],
        "agent_id": "fixture-agent", "current_obligation": None, "transition_candidates": [candidate]})
    packet = recovered["obligation"]["settlement_action_packet"]
    assert packet["settlement_only"] is True
    assert packet["successor_todo_id"] == successor["todo_id"]
    assert original["completion_turn_key"] == "original-completion"
    assert "no_followup" not in original
