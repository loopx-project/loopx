"""Current progress proof keeps the final completion gate independent."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.control_plane.test_retained_result_binding import direction
from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity, read_heartbeat_settlement
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from tests.control_plane.test_outcome_replan_composition_cli import _review_packet
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, _configure_completion_validation_todo,
    _configure_selected_todo_replan_fixture, _initialize_fixture_authority, _projected_cli_args,
    _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_progress_proof_is_owned_and_survives_qualified_append(provider, tmp_path, monkeypatch):
    runtime, call, prior, turn, context, supplement = direction(provider, tmp_path, monkeypatch)
    proof = context["current_progress_qualification"]
    assert proof["settlement_identity"] == prior["settlement_identity"]
    assert proof["direction_read_context_id"] == context["read_context_id"]
    assert proof["changed_work_components"] == []
    assert proof["validation_contract"]["progress_validation"] == {
        "required": False, "owner": "settlement.open_todo_semantic_checkpoint",
        "completion_validation_contract": "loopx_todo_completion_validation_plan_result_v0"}
    assert proof["validation_contract"]["todo_completion"]["required"] is True
    assert proof["original_validation_reusable"] is False  # No original independent result was recorded.
    original = json.loads(Path(context["retained_result_artifacts"]["json_path"]).read_text(encoding="utf-8"))
    assert original["classification"] == prior["classification"]
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    rc, qualified = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 0 and qualified["vision_checkpoint"]["satisfied"] is True, qualified
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    accepted = next(row for row in reversed(rows) if row.get("settlement_identity") == prior["settlement_identity"])
    saved = accepted["vision_checkpoint"]["read_context"]["current_progress_qualification"]
    assert saved["judgment_sha256"] and {k:v for k,v in saved.items() if k != "judgment_sha256"} == proof
    assert _spend_run_count(runtime) == 0
    key = hashlib.sha256(prior["settlement_identity"]["effect_id"].encode()).hexdigest()
    receipt = json.loads((index.parent.parent / "checkpoint-contexts" / f"{key}.json").read_text(encoding="utf-8"))
    assert receipt["current_agent_decision"]["read_context_id"] == context["read_context_id"]
    assert receipt["current_progress_qualification"] == saved
    assert not receipt.get("direction_host_attempt_count")  # CLI did not invent a Host/model attempt.


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("damage", ["missing", "rehashed_validation_waiver", "artifact_refs"])
def test_missing_current_progress_proof_cannot_qualify_retained_result(provider, damage, tmp_path, monkeypatch):
    runtime, call, prior, turn, _, supplement = direction(provider, tmp_path, monkeypatch)
    key = hashlib.sha256(prior["settlement_identity"]["effect_id"].encode()).hexdigest()
    path = runtime / "goals" / GOAL_ID / "checkpoint-contexts" / f"{key}.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if damage == "missing":
        receipt.pop("current_progress_qualification", None)
    elif damage == "artifact_refs":
        receipt["retained_result_artifacts"]["json_path"] = str(runtime / "foreign-result.json")
    else:
        proof = receipt["current_progress_qualification"]
        proof["validation_contract"]["todo_completion"]["required"] = False
        proof["validation_contract_sha256"] = hashlib.sha256(json.dumps(proof["validation_contract"],
            sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    path.write_text(json.dumps(receipt), encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    rc, rejected = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 1, rejected
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["goal", "final_validation"])
def test_related_change_requires_current_read_but_can_keep_exact_retained_progress(provider, change, tmp_path, monkeypatch):
    runtime, call, prior, turn, old_context, supplement = direction(provider, tmp_path, monkeypatch)
    project = runtime.parent / "project"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    if change == "goal":
        state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
        state.write_text(state.read_text(encoding="utf-8") + "\nFinal delivery also needs an independent follow-up check.\n", encoding="utf-8")
    else:
        rc, current = call("todo", "list", "--goal-id", GOAL_ID, "--todo-id", SELECTED_REPLAN_TODO_ID)
        assert rc == 0, current
        rc, updated = call("todo", "update", "--goal-id", GOAL_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
            "--agent-id", AGENT_ID, "--update-operation-id", "change-final-validation",
            "--update-expected-provider-revision", current["authority_read"]["provider_revision"],
            "--validation-command-json", json.dumps([sys.executable, "-c", "raise SystemExit(0)"]))
        assert rc == 0, updated
    rc, stale = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 1 and index.read_bytes() == before, stale
    rc, context = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0, context
    proof = context["current_progress_qualification"]
    assert ("goal" if change == "goal" else "todo") in proof["changed_work_components"]
    assert proof["original_validation_reusable"] is False
    assert proof["validation_contract"]["progress_validation"]["required"] is False
    assert proof["validation_contract"]["todo_completion"]["required"] is True
    if change == "final_validation":
        assert proof["validation_contract_sha256"] != old_context["current_progress_qualification"]["validation_contract_sha256"]
    packet_path = project / "current-direction.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["vision_patch"]["acceptance_summary"] = "This exact retained progress remains useful; the changed final gate stays open."
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    command = supplement.replace(old_context["read_context_id"], context["read_context_id"])
    rc, accepted = call(*_projected_cli_args(command, turn_instance_id=turn))
    assert rc == 0 and accepted["vision_checkpoint"]["satisfied"] is True, accepted
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_agent_can_stop_exact_r_without_replacing_or_qualifying_it(provider, tmp_path, monkeypatch):
    runtime, _, prior, _, context, _ = direction(provider, tmp_path, monkeypatch)
    project = runtime.parent / "project"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
    calls = []
    def decide(current):
        artifact = Path(current["retained_result_artifacts"]["json_path"])
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == prior["result_artifact_digests"]["json_sha256"]
        assert current["current_progress_qualification"] == context["current_progress_qualification"]
        calls.append(current["read_context_id"])
        return {"result_kind": "iteration_failed", "stop_reason": "retained_result_requires_revision"}
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    with pytest.raises(ValueError, match="result retained"):
        CurrentAgentCheckpoint(project / ".loopx/registry.json", runtime, state, identity, decide).continue_direction({}, purpose="supplement_checkpoint")
    receipt = json.loads(context_io._receipt_path(runtime, identity).read_text(encoding="utf-8"))
    assert receipt["direction_stopped"] is True and receipt["retained_result_binding"] == context["retained_result_binding"]
    assert calls == [context["read_context_id"]]
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0


def _hard_progress(provider, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _configure_completion_validation_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    turn = "current-progress-original-turn"
    def call(*args):
        return _run_cli(registry, runtime, *args, cwd=project)
    task = ("--goal-id", GOAL_ID, "--todo-id", SELECTED_REPLAN_TODO_ID)
    rc, acquired = call("task-lease", "acquire", *task, "--owner", AGENT_ID,
        "--idempotency-key", "progress-execution", "--ttl-seconds", "1800")
    assert rc == 0, acquired
    guard_args = ("quota", "should-run", "--codex-app", *task, "--agent-id", AGENT_ID, "--turn-instance-id", turn)
    rc, guard = call(*guard_args)
    if rc:
        [selection] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        rc, guard = call(*_projected_cli_args(selection, turn_instance_id=turn))
    assert rc == 0, guard
    [first, *_] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert call(*_projected_cli_args(first, turn_instance_id=turn))[0] == 0
    prior = read_heartbeat_settlement(runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=SELECTED_REPLAN_TODO_ID, turn_instance_id=turn, registry_path=registry).writeback_run
    rc, cold = call(*guard_args)
    assert rc == 0, cold
    _, supplement, _ = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    rc, context = call("checkpoint-context", *task, "--agent-id", AGENT_ID,
        "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0, context
    packet = _review_packet(guard["replan_action_packet"]["stride_control"])
    path = project / "hard-lease-direction.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    command = supplement.replace("<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(path))
    command = command.replace("<read-context-id>", context["read_context_id"])
    return runtime, registry, call, acquired["lease"], prior, turn, context, command


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("maintenance", ["renew", "release"])
def test_current_progress_uses_live_lease_without_invalidating_normal_renewal(provider, maintenance, tmp_path, monkeypatch):
    runtime, registry, call, lease, prior, turn, context, command = _hard_progress(provider, tmp_path, monkeypatch)
    rc, changed = call("task-lease", maintenance, "--goal-id", GOAL_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
        "--owner", AGENT_ID, "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]),
        *(("--ttl-seconds", "2400") if maintenance == "renew" else ()))
    assert rc == 0, changed
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    rc, result = call(*_projected_cli_args(command, turn_instance_id=turn))
    if maintenance == "renew":
        assert rc == 0 and result["vision_checkpoint"]["read_context"]["read_context_id"] == context["read_context_id"], result
    else:
        assert rc == 1 and "checkpoint_direction_source_changed" in json.dumps(result), result
        assert index.read_bytes() == before
        identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
        def no_model(_):
            pytest.fail("Released original work authority cannot launch a model")
        state = runtime.parent / "project" / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
        with pytest.raises(context_io.CheckpointReadContextRejected) as error:
            CurrentAgentCheckpoint(registry, runtime, state, identity, no_model).continue_direction({}, purpose="supplement_checkpoint")
        assert error.value.code == "checkpoint_direction_source_changed"
    assert _spend_run_count(runtime) == 0
