"""Historical protected source writes keep their original authority and effects."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeConflict
from loopx.control_plane.goals.checkpoint_context_io import CheckpointReadContextRejected, _receipt_path
from loopx.control_plane.projects.registry_codec import (
    ProjectRegistryProtocolError, load_project_registry, load_registry, source_session_registry_transaction,
)
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.history import collect_history
from loopx.state_refresh import refresh_state_run
from loopx.status import collect_status
from tests.control_plane.test_checkpoint_provider_fence import (
    INSTANCE_B, _historical_source_context, _replace_source_goal,
)
from tests.control_plane.test_quota_settlement_cli import _run_cli, _spend_run_count


def _decision():
    return {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
            "vision_patch": {"vision_summary": "Verify the original source.",
                "acceptance_summary": "The retained source result is verified; remaining work stays open."},
            "path_delta": {"outcome": "continue", "prior_assumption": "Retain the original source.",
                "observed_reality": "The original result is committed and its source remains active.",
                "retained": ["Original source, result and Turn."], "evidence_refs": ["evidence:source-result"]}}


def _write(registry, runtime, goal_ref, identity, token):
    # This historical fixture has no earlier Vision to declare unchanged.
    return refresh_state_run(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id, project=None, state_file=None,
        classification="validated_change", recommended_action=None, delivery_batch_scale="implementation",
        delivery_outcome="outcome_progress", checkpoint_read_context_id=token, agent_vision_packet=_decision(),
        dry_run=False, sync_global=False, external_delivery={"suppress": True, "resume_key": None},
        delivery_workspace_path=registry.parent.parent, goal_ref=goal_ref, first_delivery=True)


def _write_cli(registry, runtime, goal_ref, identity, token):
    vision = registry.parent / "direction.json"
    vision.write_text(json.dumps(_decision()), encoding="utf-8")
    return _run_cli(registry, runtime, "refresh-state", "--goal-id", identity.goal_id,
        "--goal-instance-id", goal_ref["goal_instance_id"], "--agent-id", identity.agent_id,
        "--todo-id", identity.todo_id, "--turn-instance-id", identity.turn_instance_id,
        "--classification", "validated_change", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--checkpoint-read-context", token,
        "--agent-vision-json", str(vision), "--first-delivery", "--delivery-workspace-path", str(registry.parent.parent),
        "--no-global-sync", "--suppress-external-sinks", "--settle-turn")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("receipt_loss", [None, "missing", "damaged"])
def test_historical_source_direction_commits_and_replays_original_effect(tmp_path, monkeypatch, provider, receipt_loss):
    runtime, registry, goal_ref, identity, read = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery")
    context = read()
    saved = _write(registry, runtime, goal_ref, identity, context["read_context_id"])
    assert saved["ok"] and saved["vision_checkpoint"]["satisfied"], saved
    assert saved["projection_delivery"] in {"delivered", "current"}, saved["projection_outbox"]
    assert saved["goal_ref"] == goal_ref
    assert json.loads(Path(saved["json_path"]).read_text())["goal_ref"] == goal_ref
    if receipt_loss == "missing":
        _receipt_path(runtime, identity, "first_delivery").unlink()
    elif receipt_loss == "damaged":
        _receipt_path(runtime, identity, "first_delivery").write_text("{", encoding="utf-8")
    index = runtime / "goals" / identity.goal_id / "runs" / "index.jsonl"
    original = index.read_bytes(), registry.read_bytes()
    replay = _write(registry, runtime, goal_ref, identity, context["read_context_id"])
    # The API commit has not yet crossed the CLI's rollout receipt owner.
    assert replay["receipt_repair_required"] and not replay["appended"], replay
    assert replay["settlement_identity"] == identity.as_dict()
    assert (index.read_bytes(), registry.read_bytes()) == original
    rc, settled = _write_cli(registry, runtime, goal_ref, identity, context["read_context_id"])
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", json.dumps({
        key: settled.get(key) for key in ("ok", "error", "error_code", "quota_spend", "rollout_event_log_error", "settlement_progress")})
    rc, repeated = _write_cli(registry, runtime, goal_ref, identity, context["read_context_id"])
    assert rc == 0 and repeated["idempotent_replay"], repeated
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert _spend_run_count(runtime) == 1
    assert sum(row.get("classification") == "validated_change" for row in rows) == 1
    proof = read_heartbeat_settlement(runtime, registry_path=registry, goal_ref=goal_ref,
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id)
    assert proof.writeback_run["goal_ref"] == goal_ref
    with pytest.raises(ProjectRegistryProtocolError, match="lifecycle-only profile"):
        load_registry(registry)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unmarked_source_writer_cannot_enable_checkpoint_policy(tmp_path, monkeypatch, provider):
    runtime, registry, goal_ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery", marked=False)
    files = [registry, runtime / "goals" / identity.goal_id / "runs" / "index.jsonl"]
    original = [path.read_bytes() if path.exists() else None for path in files]
    with pytest.raises(CheckpointReadContextRejected) as refused:
        _write(registry, runtime, goal_ref, identity, "0" * 32)
    assert refused.value.code == "checkpoint_first_delivery_not_admitted"
    assert [path.read_bytes() if path.exists() else None for path in files] == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drift", ["replacement", "stopped", "wrong_ref"])
@pytest.mark.parametrize("committed", [False, True])
def test_source_writer_rejects_changed_original_scope_without_effects(tmp_path, monkeypatch, provider, drift, committed):
    runtime, registry, goal_ref, identity, read = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery")
    context = read()
    if committed:
        _write(registry, runtime, goal_ref, identity, context["read_context_id"])
    if drift == "replacement":
        _replace_source_goal(registry, INSTANCE_B)
    elif drift == "wrong_ref":
        goal_ref = {**goal_ref, "goal_instance_id": INSTANCE_B}
    else:
        with source_session_registry_transaction(registry, operation="fixture_source_stop") as transaction:
            payload = transaction.payload_copy()
            payload["goals"][0]["activation"] = {"schema_version": "loopx_goal_activation_v1",
                "state": "stopped", "updated_at": "2026-10-10T12:00:00Z", "reason": "Owner stopped this Goal"}
            transaction.commit(payload)
    paths = [registry, runtime / "goals" / identity.goal_id / "runs/index.jsonl",
             runtime / "goals" / identity.goal_id / "rollout-event-log.jsonl"]
    before = [path.read_bytes() if path.exists() else None for path in paths]
    with pytest.raises(EffectRuntimeConflict):
        _write(registry, runtime, goal_ref, identity, context["read_context_id"])
    assert [path.read_bytes() if path.exists() else None for path in paths] == before
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_source_status_retains_exact_scope_without_history_mutation(tmp_path, monkeypatch, provider):
    runtime, registry, goal_ref, identity, read = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery")
    saved = _write(registry, runtime, goal_ref, identity, read()["read_context_id"])
    index = Path(saved["index_path"])
    foreign = json.loads(index.read_text().splitlines()[-1])
    foreign.update(goal_ref={**goal_ref, "goal_instance_id": INSTANCE_B}, generated_at="2030-01-01T00:00:00Z",
                   json_path="foreign.json", markdown_path="foreign.md")
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(foreign) + "\n")
    event_log = runtime / "goals" / identity.goal_id / "rollout-event-log.jsonl"
    before = registry.read_bytes(), index.read_bytes(), event_log.read_bytes()
    status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=identity.goal_id, limit=6, agent_lane_id=identity.agent_id,
        include_presentation_projections=False, registry_data=load_project_registry(registry))
    assert status["goal_ref"] == goal_ref
    history = collect_history(registry_path=registry, runtime_root=runtime,
        goal_id=identity.goal_id, limit=6, registry=load_project_registry(registry))
    goal = history["goals"][0]
    assert goal["goal_instance_id"] == goal_ref["goal_instance_id"]
    assert goal["unique_runs"] == 1
    assert len(goal["latest_runs"]) == 1
    assert all(row["goal_ref"] == goal_ref for row in goal["latest_runs"])
    assert (registry.read_bytes(), index.read_bytes(), event_log.read_bytes()) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("damage", ["tail", "artifact", "conflict"])
def test_uncertain_source_commit_is_preserved_without_replay(tmp_path, monkeypatch, provider, damage):
    runtime, registry, goal_ref, identity, read = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery")
    token = read()["read_context_id"]
    saved = _write(registry, runtime, goal_ref, identity, token)
    index = Path(saved["index_path"])
    if damage == "tail":
        index.write_bytes(index.read_bytes().rstrip(b"\r\n"))
    elif damage == "artifact":
        path = Path(saved["json_path"])
        record = json.loads(path.read_text())
        record["vision_checkpoint"]["satisfied"] = False
        path.write_text(json.dumps(record))
    else:
        row = json.loads(index.read_text().splitlines()[-1])
        row["vision_checkpoint"]["read_context"]["read_context_id"] = "conflicting-token"
        with index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    before = index.read_bytes(), registry.read_bytes()
    with pytest.raises(CheckpointReadContextRejected) as refused:
        _write(registry, runtime, goal_ref, identity, token)
    assert refused.value.code == "checkpoint_commit_unknown"
    assert (index.read_bytes(), registry.read_bytes()) == before
    assert _spend_run_count(runtime) == 0
