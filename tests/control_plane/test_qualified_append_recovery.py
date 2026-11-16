"""Partial qualified appends retain the original request before any new read."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity, read_heartbeat_settlement
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from tests.control_plane.test_retained_result_binding import direction
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, _projected_cli_args, _spend_run_count,
)


def _faulted_refresh(runtime, supplement, turn, monkeypatch, capsys, *, ack_lost=False):
    project = runtime.parent / "project"
    native = context_io._checkpoint_effect
    attempts = []

    def inject(method, request):
        if method == "goal.checkpoint_read_context.commit" and request["purpose"] == "supplement_checkpoint":
            attempts.append(request)
            if not ack_lost:
                # Real native JSON-write -> Markdown-write window.
                return native(method, {**request, "markdown": ""})
            native(method, request)
            raise context_io.CheckpointReadContextRejected({"ok": False,
                "error_code": "checkpoint_commit_unknown", "error": "Injected lost native append response"})
        return native(method, request)

    monkeypatch.chdir(project)
    with monkeypatch.context() as injection:
        injection.setattr(context_io, "_checkpoint_effect", inject)
        rc = main(["--registry", str(project / ".loopx/registry.json"),
            "--runtime-root", str(runtime), "--format", "json",
            *_projected_cli_args(supplement, turn_instance_id=turn)])
    rejected = json.loads(capsys.readouterr().out)
    assert rc == 1 and "checkpoint_commit_unknown" in json.dumps(rejected), rejected
    assert len(attempts) == 1
    return attempts[0]


def _paths(runtime, prior):
    contexts = runtime / "goals" / GOAL_ID / "checkpoint-contexts"
    effect = prior["settlement_identity"]["effect_id"]
    return (contexts / (hashlib.sha256(effect.encode()).hexdigest() + ".json"),
        contexts / (hashlib.sha256((effect + ":delivery_result").encode()).hexdigest() + ".json"),
        runtime / "goals" / GOAL_ID / "runs/index.jsonl")


def _progress(runtime, turn):
    registry = runtime.parent / "project/.loopx/registry.json"
    readback = read_heartbeat_settlement(runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=SELECTED_REPLAN_TODO_ID, turn_instance_id=turn, registry_path=registry)
    return context_io.first_delivery_progress(runtime, readback, registry_path=registry)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_partial_qualified_append_cannot_be_replaced_by_a_fresh_direction_read(
    provider, tmp_path, monkeypatch, capsys,
):
    runtime, call, prior, turn, _, supplement = direction(provider, tmp_path, monkeypatch)
    direction_path, work_path, index = _paths(runtime, prior)
    original_work = work_path.read_bytes()
    original_index = index.read_bytes()
    _faulted_refresh(runtime, supplement, turn, monkeypatch, capsys)
    assert index.read_bytes() == original_index
    assert work_path.read_bytes() == original_work and _spend_run_count(runtime) == 0
    held = direction_path.read_bytes()
    observed = _progress(runtime, turn)
    assert observed["stage"] == "operation_unknown" and observed["quota_spent"] is False
    pending = json.loads(held)["commit_attempt"]
    assert pending["index_record"]["vision_checkpoint"]["read_context"]["retained_result_binding"]
    rc, retry = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 1 and "checkpoint_commit_unknown" in json.dumps(retry), retry
    state = runtime.parent / "project" / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text(encoding="utf-8") + "\nCurrent requirements changed after the partial append.\n", encoding="utf-8")
    rc, reread = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 1 and "checkpoint_commit_unknown" in json.dumps(reread), reread
    assert direction_path.read_bytes() == held and work_path.read_bytes() == original_work
    assert index.read_bytes() == original_index and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("receipt_damage", ["none", "missing", "malformed"])
def test_lost_qualified_response_recovers_indexed_effect_before_later_freshness(
    provider, receipt_damage, tmp_path, monkeypatch, capsys,
):
    runtime, call, prior, turn, context, supplement = direction(provider, tmp_path, monkeypatch)
    direction_path, work_path, index = _paths(runtime, prior)
    original_work = work_path.read_bytes()
    _faulted_refresh(runtime, supplement, turn, monkeypatch, capsys, ack_lost=True)
    committed_index = index.read_bytes()
    rows = [json.loads(line) for line in committed_index.splitlines()]
    qualified = [row for row in rows if row.get("settlement_identity") == prior["settlement_identity"]
        and row.get("vision_checkpoint", {}).get("read_context")]
    assert len(qualified) == 1
    assert json.loads(direction_path.read_text(encoding="utf-8"))["commit_attempt"]["index_record"] == qualified[0]
    if receipt_damage == "missing":
        direction_path.unlink()
    elif receipt_damage == "malformed":
        direction_path.write_text("{", encoding="utf-8")
    project = runtime.parent / "project"
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text(encoding="utf-8") + "\nA later Goal requirement is now visible.\n", encoding="utf-8")
    current_state = state.read_bytes()
    rc, recovered = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0 and recovered["status"] == "committed", recovered
    assert recovered["context"]["read_context_id"] == context["read_context_id"]
    assert _progress(runtime, turn)["stage"] == "settlement_pending"
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])

    def no_model(_):
        pytest.fail("An indexed Q must not dispatch another direction model")

    replay = CurrentAgentCheckpoint(project / ".loopx/registry.json", runtime, state, identity, no_model)
    assert replay.continue_direction({}, purpose="supplement_checkpoint")["checkpoint_read_context_id"] == context["read_context_id"]
    rc, repeated = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 0 and repeated["appended"] is False, repeated
    spend = ("quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn,
        "--slots", "1", "--source", "heartbeat", "--execute")
    rc, spent = call(*spend)
    assert rc == 0 and spent["appended"] is True, spent
    assert call(*spend)[1]["appended"] is False and _spend_run_count(runtime) == 1
    assert _progress(runtime, turn)["stage"] == "settled"
    assert work_path.read_bytes() == original_work and state.read_bytes() == current_state
    assert index.read_bytes().startswith(committed_index)
    final_rows = [json.loads(line) for line in index.read_bytes().splitlines()]
    assert sum(row.get("settlement_identity") == prior["settlement_identity"] and
        bool(row.get("vision_checkpoint", {}).get("read_context")) for row in final_rows) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_proved_empty_attempt_allows_reread_then_one_original_turn_qualification(
    provider, tmp_path, monkeypatch, capsys,
):
    runtime, call, prior, turn, old_context, supplement = direction(provider, tmp_path, monkeypatch)
    direction_path, work_path, index = _paths(runtime, prior)
    original_work, original_index = work_path.read_bytes(), index.read_bytes()
    _faulted_refresh(runtime, supplement, turn, monkeypatch, capsys)
    marker = json.loads(direction_path.read_text(encoding="utf-8"))["commit_attempt"]
    # Controlled isolated fixture for the earlier, empty-reservation window.
    # Do not claim this truncation simulates an OS crash or touches a live store.
    for key in ("json_path", "markdown_path"):
        artifact = Path(marker[key])
        assert artifact.parent.resolve() == index.parent.resolve()
        artifact.write_bytes(b"")
    rc, context = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 0 and context["read_context_id"] != old_context["read_context_id"], context
    assert "commit_attempt" not in json.loads(direction_path.read_text(encoding="utf-8"))
    assert index.read_bytes() == original_index and work_path.read_bytes() == original_work
    rc, stale = call(*_projected_cli_args(supplement, turn_instance_id=turn))
    assert rc == 1 and "unknown_or_replaced" in json.dumps(stale), stale
    command = supplement.replace(old_context["read_context_id"], context["read_context_id"])
    rc, qualified = call(*_projected_cli_args(command, turn_instance_id=turn))
    assert rc == 0 and qualified["vision_checkpoint"]["satisfied"] is True, qualified
    assert work_path.read_bytes() == original_work and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("artifact", ["json", "markdown"])
def test_indexed_qualified_effect_does_not_recover_changed_report_bytes(
    provider, artifact, tmp_path, monkeypatch, capsys,
):
    runtime, call, prior, turn, _, supplement = direction(provider, tmp_path, monkeypatch)
    direction_path, work_path, index = _paths(runtime, prior)
    original_work = work_path.read_bytes()
    _faulted_refresh(runtime, supplement, turn, monkeypatch, capsys, ack_lost=True)
    marker = json.loads(direction_path.read_text(encoding="utf-8"))["commit_attempt"]
    path = Path(marker[f"{artifact}_path"])
    if artifact == "json":
        record = json.loads(path.read_text(encoding="utf-8"))
        record["classification"] = "substituted-qualified-content"
        path.write_text(json.dumps(record), encoding="utf-8")
    else:
        path.write_text("Substituted qualified report.\n", encoding="utf-8")
    before, held = index.read_bytes(), direction_path.read_bytes()
    rc, rejected = call("checkpoint-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn, "--purpose", "supplement_checkpoint")
    assert rc == 1 and "checkpoint_commit_unknown" in json.dumps(rejected), rejected
    assert index.read_bytes() == before and direction_path.read_bytes() == held
    assert work_path.read_bytes() == original_work and _spend_run_count(runtime) == 0
