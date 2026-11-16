"""A historical source's current-Agent judgment reaches its original writer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeConflict
from loopx.control_plane.projects.registry_codec import (
    load_project_registry, source_session_registry_transaction,
)
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from loopx.control_plane.quota.effect_program import (
    SettlementStepKind, build_turn_scoped_cli_settlement_plan, settlement_step_command,
)
from loopx.state_refresh import refresh_state_run
from tests.control_plane.test_checkpoint_provider_fence import (
    INSTANCE_B, _historical_source_context, _replace_source_goal,
)
from tests.control_plane.test_quota_settlement_cli import _run_generated_cli, _spend_run_count
from tests.control_plane.test_scoped_checkpoint_writer import _decision, _write_cli


def _state(registry):
    goal = load_project_registry(registry)["goals"][0]
    return Path(goal["repo"]) / goal["state_file"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scoped_current_agent_hands_one_decision_to_original_commit_and_settlement(
    tmp_path, monkeypatch, provider,
):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery")
    artifact = tmp_path / "original-result.txt"
    artifact.write_text("Independently verified original source result.")
    original = artifact.read_bytes()
    candidate = {"summary": "Retain the original result.",
        "artifact_sha256": hashlib.sha256(original).hexdigest()}
    calls = []
    def decide(context):
        calls.append(context["read_context_id"])
        assert context["settlement_identity"] == identity.as_dict()
        assert context["basis"]["source"]["goal_ref"] == ref
        assert context["basis"]["todo"]["status"] == "done"
        return {"read_context_id": context["read_context_id"], "agent_vision": _decision()}
    driver = CurrentAgentCheckpoint(registry, runtime, _state(registry), identity, decide, goal_ref=ref)
    judged = driver.continue_direction(candidate)
    token = judged["checkpoint_read_context_id"]
    assert driver.continue_direction(candidate) == judged
    assert calls == [token] and judged["agent_vision"] == _decision()
    committed = refresh_state_run(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id, project=None, state_file=None,
        classification="validated_change", recommended_action=None, delivery_batch_scale="implementation",
        delivery_outcome="outcome_progress", checkpoint_read_context_id=token,
        agent_vision_packet=judged["agent_vision"], dry_run=False, sync_global=False,
        external_delivery={"suppress": True, "resume_key": None},
        delivery_workspace_path=registry.parent.parent, goal_ref=ref, first_delivery=True)
    assert committed["ok"] and committed["vision_checkpoint"]["satisfied"], committed
    rc, settled = _write_cli(registry, runtime, ref, identity, token)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled

    # Execute the existing generated exact-source spend, independently of the
    # handwritten fixture command, and prove that it reuses the original debit.
    import shlex
    prefix = shlex.join(["loopx", "--registry", str(registry), "--runtime-root", str(runtime)])
    plan = build_turn_scoped_cli_settlement_plan(goal_id=identity.goal_id, agent_id=identity.agent_id,
        todo_id=identity.todo_id, replan_obligation_id=None, turn_instance_id=identity.turn_instance_id,
        command_prefix=prefix, scoped_cli_args=f" --agent-id {identity.agent_id}",
        lifecycle_actor_args="", goal_ref=ref, quota_spend_source="heartbeat").as_dict()
    spend = settlement_step_command(plan, SettlementStepKind.QUOTA_SPEND)
    rc, replay = _run_generated_cli(spend, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0 and replay["idempotent_replay"] and not replay["appended"], replay
    restored = driver.continue_direction(candidate)
    assert restored["checkpoint_read_context_id"] == token and calls == [token]
    assert artifact.read_bytes() == original and _spend_run_count(runtime) == 1
    rows = [json.loads(line) for line in Path(committed["index_path"]).read_text().splitlines()]
    assert sum(row.get("classification") == "validated_change" for row in rows) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drift", ["replacement", "stopped"])
def test_scoped_source_change_during_current_agent_judgment_cannot_commit(
    tmp_path, monkeypatch, provider, drift,
):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "first_delivery")
    calls = []
    def decide(context):
        calls.append(context["read_context_id"])
        if drift == "replacement":
            _replace_source_goal(registry, INSTANCE_B)
        else:
            with source_session_registry_transaction(registry, operation="fixture_source_stop") as transaction:
                data = transaction.payload_copy()
                data["goals"][0]["activation"] = {"schema_version": "loopx_goal_activation_v1",
                    "state": "stopped", "updated_at": "2026-10-10T12:00:00Z", "reason": "Owner stopped the Goal"}
                transaction.commit(data)
        return {"read_context_id": context["read_context_id"], "agent_vision": _decision()}
    driver = CurrentAgentCheckpoint(registry, runtime, _state(registry), identity, decide, goal_ref=ref)
    index = runtime / "goals" / identity.goal_id / "runs/index.jsonl"
    before = index.read_bytes() if index.exists() else None
    with pytest.raises(EffectRuntimeConflict):
        driver.continue_direction({"summary": "Original result remains retained."})
    assert len(calls) == 1 and _spend_run_count(runtime) == 0
    assert (index.read_bytes() if index.exists() else None) == before
