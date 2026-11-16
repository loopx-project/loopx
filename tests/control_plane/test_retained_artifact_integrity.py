"""A retained result's bytes cannot change while direction is being judged."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from tests.control_plane.test_outcome_replan_composition_cli import _review_packet
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, _configure_completion_validation_todo,
    _configure_selected_todo_replan_fixture, _initialize_fixture_authority,
    _projected_cli_args, _run_cli, _spend_run_count, _write_fixture,
)


def retained_result(provider, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _configure_completion_validation_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "retained-integrity-original-turn"

    def call(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn)
    rc, guard = call(*guard_args)
    if rc:
        assert guard["action_selection_qualification"]["state"] == "deferred", guard
        [selection] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        rc, guard = call(*_projected_cli_args(selection, turn_instance_id=turn))
    assert rc == 0, guard
    [first, *_] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    rc, result = call(*_projected_cli_args(first, turn_instance_id=turn))
    assert rc == 0, result
    readback = read_heartbeat_settlement(runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=SELECTED_REPLAN_TODO_ID, turn_instance_id=turn, registry_path=registry)
    assert readback.writeback_run["vision_checkpoint"]["satisfied"] is False
    assert readback.spend_run is None
    return project, runtime, call, guard_args, guard, readback.writeback_run, turn


def corrupt_artifact(prior, artifact):
    target = Path(prior[f"{artifact}_path"])
    if artifact == "json":
        record = json.loads(target.read_text(encoding="utf-8"))
        # Not one of the four envelope fields checked by historical replay.
        record["classification"] = "replaced-result-content"
        target.write_text(json.dumps(record), encoding="utf-8")
    else:
        target.write_text("Replaced retained report.\n", encoding="utf-8")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("artifact", ["json", "markdown"])
def test_changed_retained_artifact_cannot_supply_a_new_direction_read(provider, artifact, tmp_path, monkeypatch):
    _, runtime, call, _, _, prior, turn = retained_result(provider, tmp_path, monkeypatch)
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    receipts = {p: p.read_bytes() for p in (index.parent.parent / "checkpoint-contexts").glob("*.json")}
    corrupt_artifact(prior, artifact)
    rc, rejected = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 1, rejected
    assert "checkpoint_commit_unknown" in json.dumps(rejected), rejected
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0
    assert {p: p.read_bytes() for p in receipts} == receipts


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("artifact", ["json", "markdown"])
def test_changed_retained_artifact_after_read_cannot_commit_progress(provider, artifact, tmp_path, monkeypatch):
    project, runtime, call, guard_args, guard, prior, turn = retained_result(provider, tmp_path, monkeypatch)
    rc, context = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0, context
    rc, cold = call(*guard_args)
    assert rc == 0, cold
    _, supplement, _ = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    decision = _review_packet(guard["replan_action_packet"]["stride_control"])
    decision["vision_patch"]["acceptance_summary"] = "Judge the retained progress; final validation remains open."
    decision_path = project / "current-direction.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    supplement = supplement.replace("<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(decision_path))
    supplement = supplement.replace("<read-context-id>", context["read_context_id"])
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    receipts = {p: p.read_bytes() for p in (index.parent.parent / "checkpoint-contexts").glob("*.json")}
    corrupt_artifact(prior, artifact)
    rc, rejected = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 1, rejected
    assert "checkpoint_commit_unknown" in json.dumps(rejected), rejected
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0
    assert {p: p.read_bytes() for p in receipts} == receipts


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_new_retained_result_has_original_artifact_digests(provider, tmp_path, monkeypatch):
    _, _, call, _, _, prior, turn = retained_result(provider, tmp_path, monkeypatch)
    expected = {key: hashlib.sha256(Path(prior[f"{artifact}_path"]).read_bytes()).hexdigest()
        for key, artifact in (("json_sha256", "json"), ("markdown_sha256", "markdown"))}
    assert prior["result_artifact_digests"] == expected
    rc, context = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0 and context["original_outcome"]["settlement_identity"] == prior["settlement_identity"], context
