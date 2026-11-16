"""Direction consumes the original retained result and its work evidence."""
from __future__ import annotations

import hashlib
import json

import pytest

from tests.control_plane.test_retained_artifact_integrity import retained_result
from tests.control_plane.test_outcome_replan_composition_cli import _review_packet
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, _projected_cli_args, _spend_run_count,
)


def direction(provider, tmp_path, monkeypatch):
    project, runtime, call, guard_args, guard, prior, turn = retained_result(provider, tmp_path, monkeypatch)
    rc, context = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0, context
    rc, cold = call(*guard_args)
    assert rc == 0, cold
    _, supplement, _ = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    decision = _review_packet(guard["replan_action_packet"]["stride_control"])
    decision["vision_patch"]["acceptance_summary"] = "Judge the original retained progress; final validation stays open."
    path = project / "current-direction.json"
    path.write_text(json.dumps(decision), encoding="utf-8")
    supplement = supplement.replace("<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(path))
    supplement = supplement.replace("<read-context-id>", context["read_context_id"])
    return runtime, call, prior, turn, context, supplement


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_direction_read_freezes_owner_derived_original_result_refs(provider, tmp_path, monkeypatch):
    runtime, call, prior, turn, context, supplement = direction(provider, tmp_path, monkeypatch)
    proof = context["retained_result_binding"]
    assert proof["settlement_identity"] == prior["settlement_identity"]
    assert proof["refresh_recovery"] == prior["refresh_recovery"]
    assert proof["result_artifact_digests"] == prior["result_artifact_digests"]
    assert proof["result_work_context"] == prior["result_work_context"]
    key = hashlib.sha256(prior["settlement_identity"]["effect_id"].encode()).hexdigest()
    receipt = runtime / "goals" / GOAL_ID / "checkpoint-contexts" / f"{key}.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["retained_result_binding"] == proof
    work_path = receipt.with_name(hashlib.sha256((prior["settlement_identity"]["effect_id"] + ":delivery_result").encode()).hexdigest() + ".json")
    original_work = work_path.read_bytes()
    rc, qualified = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 0 and qualified["vision_checkpoint"]["satisfied"] is True, qualified
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text(encoding="utf-8").splitlines()]
    qualified_row = next(row for row in reversed(rows) if row.get("settlement_identity") == prior["settlement_identity"])
    assert qualified_row["vision_checkpoint"]["read_context"]["retained_result_binding"] == proof
    assert work_path.read_bytes() == original_work
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("damage", ["work_evidence", "direction_binding", "binding_removed"])
def test_changed_original_work_evidence_cannot_qualify_retained_progress(provider, damage, tmp_path, monkeypatch):
    runtime, call, prior, turn, _, supplement = direction(provider, tmp_path, monkeypatch)
    key = hashlib.sha256((prior["settlement_identity"]["effect_id"] + ":delivery_result").encode()).hexdigest()
    work_path = runtime / "goals" / GOAL_ID / "checkpoint-contexts" / f"{key}.json"
    if damage == "work_evidence":
        work = json.loads(work_path.read_text(encoding="utf-8"))
        # Even a "passed" replacement is not the original validation evidence.
        work["terminal_validation_effect"] = {"status": "passed", "attempt_id": "different-validation"}
        work_path.write_text(json.dumps(work), encoding="utf-8")
    else:
        direction_path = work_path.with_name(hashlib.sha256(prior["settlement_identity"]["effect_id"].encode()).hexdigest() + ".json")
        receipt = json.loads(direction_path.read_text(encoding="utf-8"))
        if damage == "binding_removed":
            receipt.pop("retained_result_binding", None)
        else:
            receipt["retained_result_binding"] = {"settlement_identity": {"effect_id": "foreign-result"}}
        direction_path.write_text(json.dumps(receipt), encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    rc, rejected = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 1, rejected
    assert "checkpoint_commit_unknown" in json.dumps(rejected), rejected
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_work_evidence_changed_before_first_direction_read_is_not_adopted(provider, tmp_path, monkeypatch):
    _, runtime, call, _, _, prior, turn = retained_result(provider, tmp_path, monkeypatch)
    key = hashlib.sha256((prior["settlement_identity"]["effect_id"] + ":delivery_result").encode()).hexdigest()
    path = runtime / "goals" / GOAL_ID / "checkpoint-contexts" / f"{key}.json"
    work = json.loads(path.read_text(encoding="utf-8"))
    work["terminal_validation_effect"] = {"status": "passed", "attempt_id": "substituted-before-read"}
    path.write_text(json.dumps(work), encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    rc, rejected = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 1, rejected
    assert "checkpoint_commit_unknown" in json.dumps(rejected), rejected
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0
