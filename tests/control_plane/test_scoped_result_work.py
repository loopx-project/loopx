"""Original source work input remains protected through validation and commit."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.effect_runtime import EffectRuntimeConflict
from loopx.control_plane.goals.checkpoint_context_io import (
    CheckpointReadContextRejected, TurnResultWork, check_result_work_context,
)
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
from loopx.control_plane.turn_driver import build_loopx_turn_plan, run_loopx_turn_once
from loopx.state_refresh import refresh_state_run
from tests.control_plane.test_checkpoint_provider_fence import (
    INSTANCE_B, _historical_source_context, _replace_source_goal,
)
from tests.control_plane.test_quota_settlement_cli import _run_cli, _spend_run_count
from tests.test_loopx_turn_driver import _envelope


def _write(registry, runtime, ref, identity):
    return refresh_state_run(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id, project=None, state_file=None,
        classification="validated_change", recommended_action=None, delivery_batch_scale="implementation",
        delivery_outcome="outcome_progress", delivery_boundary="in_flight_continuation",
        dry_run=False, sync_global=False, external_delivery={"suppress": True, "resume_key": None},
        delivery_workspace_path=registry.parent.parent, goal_ref=ref)


def _cli(registry, runtime, ref, identity):
    return _run_cli(registry, runtime, "refresh-state", "--goal-id", identity.goal_id,
        "--goal-instance-id", ref["goal_instance_id"], "--agent-id", identity.agent_id,
        "--todo-id", identity.todo_id, "--turn-instance-id", identity.turn_instance_id,
        "--classification", "validated_change", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
        "--delivery-workspace-path", str(registry.parent.parent),
        "--no-global-sync", "--suppress-external-sinks", "--settle-turn")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scoped_work_checks_binds_and_settles_original_progress(tmp_path, monkeypatch, provider):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result")
    work = TurnResultWork(registry, runtime, identity, ref)
    context = work.read()
    assert context["basis"]["source"]["goal_ref"] == ref
    work.check(context)
    candidate, validation = {"summary": "Retain this verified result."}, {"ok": True, "exit_code": 0}
    binding = work.bind(context, candidate, validation)["binding"]
    assert work.bind(context, candidate, validation, binding)["ok"]
    saved = _write(registry, runtime, ref, identity)
    assert saved["ok"] and saved["appended"] and saved["goal_ref"] == ref, saved
    assert saved["vision_checkpoint"]["satisfied"]
    rc, settled = _cli(registry, runtime, ref, identity)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled
    rc, replay = _cli(registry, runtime, ref, identity)
    assert rc == 0 and replay["idempotent_replay"], replay
    rows = [json.loads(line) for line in Path(saved["index_path"]).read_text().splitlines()]
    assert sum(row.get("classification") == "validated_change" for row in rows) == 1
    assert _spend_run_count(runtime) == 1
    todo = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=identity.goal_id)["todos"][0]
    assert todo["status"] == "open" and not todo["done"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scoped_changed_basis_requires_reread_and_fresh_validation(tmp_path, monkeypatch, provider):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result")
    work = TurnResultWork(registry, runtime, identity, ref)
    context = work.read()
    artifact = tmp_path / "retained-candidate.txt"
    artifact.write_text("Verified original candidate.")
    candidate = {"summary": "Retained candidate.", "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    validation_runs = []
    def validate():
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == candidate["artifact_sha256"]
        validation_runs.append("validated")
        return {"ok": True, "exit_code": 0, "evidence_ref": f"validation:{len(validation_runs)}"}
    validation = validate()
    binding = work.bind(context, candidate, validation)["binding"]
    state = Path(context["basis"]["source"]["state_file"])
    state.write_text(state.read_text() + "\n## Acceptance change\nValidate the retained result again.\n")
    with pytest.raises(CheckpointReadContextRejected) as stale:
        work.check(context)
    assert stale.value.code == "checkpoint_read_context_stale"
    assert work.read()["read_context_id"] == context["read_context_id"]
    fresh = work.read(reread=True)
    assert fresh["read_context_id"] != context["read_context_id"]
    work.check(fresh)
    with pytest.raises(CheckpointReadContextRejected) as invalid:
        work.bind(fresh, candidate, validation, binding)
    assert invalid.value.code == "delivery_validation_binding_mismatch"
    assert work.bind(fresh, candidate, validate())["ok"]
    assert validation_runs == ["validated", "validated"]
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drift", ["replacement", "stopped"])
def test_scoped_check_does_not_borrow_a_changed_source(tmp_path, monkeypatch, provider, drift):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result")
    work = TurnResultWork(registry, runtime, identity, ref)
    context = work.read()
    if drift == "replacement":
        _replace_source_goal(registry, INSTANCE_B)
    else:
        with source_session_registry_transaction(registry, operation="fixture_source_stop") as transaction:
            data = transaction.payload_copy()
            data["goals"][0]["activation"] = {"schema_version": "loopx_goal_activation_v1",
                "state": "stopped", "updated_at": "2026-10-10T12:00:00Z", "reason": "Owner stopped this Goal"}
            transaction.commit(data)
    before = registry.read_bytes()
    with pytest.raises(EffectRuntimeConflict):
        check_result_work_context(registry_path=registry, runtime_root=runtime,
            identity=identity, context=context, goal_ref=ref)
    assert registry.read_bytes() == before and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scoped_managed_completion_checks_before_independent_validator(tmp_path, monkeypatch, provider):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result")
    envelope = _envelope()
    envelope.update(goal_id=identity.goal_id, agent_id=identity.agent_id)
    envelope["action"]["selected_todo"]["todo_id"] = identity.todo_id
    plan = build_loopx_turn_plan(envelope, host="generic-cli", execution_mode="isolated-headless",
        turn_instance_id=identity.turn_instance_id, goal_ref=ref)
    assert plan["transaction"]["settlement_plan"]["identity"] == identity.as_dict()
    calls = []
    def host(request):
        calls.append("host")
        assert request["delivery_result_context"]["basis"]["source"]["goal_ref"] == ref
        return {"schema_version": "loopx_turn_result_v0", "turn_key": request["turn_key"],
            "result_kind": "validated_completion", "completed_phases": ["host_execute", "typed_result"],
            "classification": "candidate_verified", "recommended_action": "Review the result.",
            "next_action": "Review remaining work.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "The original work remains open.",
            "summary": "Candidate requires independent validation."}
    def validator(_plan, _candidate):
        calls.append("validator")
        return {"status": "failed", "validator_kind": "fixture", "summary": "Independent rejection", "exit_code": 1}
    def forbidden(*_args):
        pytest.fail("A failed independent validation cannot write or spend")
    result = run_loopx_turn_once(plan, host_runner=host, project=registry.parent.parent,
        runtime_root=runtime, goal_id=identity.goal_id, timeout_seconds=10, execute=True,
        task_validator=validator, writeback=forbidden, spend=forbidden, scheduler=forbidden,
        completion_writeback=forbidden, completion_intent=forbidden, terminal_closeout=forbidden,
        result_work=TurnResultWork(registry, runtime, identity, ref),
        goal_admission=FirstPartyHostGoalAdmission.for_plan(
            registry_path=registry, goal_id=identity.goal_id, planned_goal_ref=ref))
    assert result["result_kind"] == "validation_failed", result
    assert calls == ["host", "validator"], {key: result.get(key) for key in
        ("reason", "validation_stage", "result_kind", "errors", "validation")}
    assert result["effects"]["state_written"] is False and result["effects"]["quota_spent"] is False
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unmarked_source_does_not_gain_a_result_check_context(tmp_path, monkeypatch, provider):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result", marked=False)
    assert TurnResultWork(registry, runtime, identity, ref).read() is None
    before = registry.read_bytes()
    with pytest.raises(CheckpointReadContextRejected) as refused:
        check_result_work_context(registry_path=registry, runtime_root=runtime, identity=identity,
            context={"read_context_id": "unadmitted"}, goal_ref=ref)
    assert refused.value.code == "checkpoint_first_delivery_not_admitted"
    assert registry.read_bytes() == before and _spend_run_count(runtime) == 0
