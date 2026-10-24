"""A restored fork needs a current, settled semantic decision before recapture."""
import copy

import pytest

from loopx.control_plane.effect_program import SettlementIdentity
from loopx.control_plane.effect_runtime import effect_runtime_result


def test_nonrestored_conversation_has_no_completed_checkpoint_review(tmp_path):
    from benchmark.runtime.checkpoint_continuation import restored_capture_gate, restored_checkpoint_review
    env = {"LOOPX_RUNTIME_ROOT": str(tmp_path), "LOOPX_GOAL_ID": "destination", "LOOPX_AGENT_ID": "worker"}
    assert restored_capture_gate(env)["capture_allowed"]
    review = restored_checkpoint_review(env)
    assert review["semantic_review_accepted"] is False and "capture_allowed" not in review


@pytest.mark.parametrize("fault", [None, "pending", "neutral", "source_actor", "unlinked", "same_node", "wrong_binding"])
def test_restored_capture_requires_current_consumption_not_local_dedup(fault):
    sha = "a" * 64
    identity = SettlementIdentity("destination", "researcher", "todo_probe", "decision-1").as_dict()
    obligation = "replan-" + "1" * 16
    review = {"identity": identity, "progress": {"state": "settled"},
        "semantic_replan_guard": {"schema_version": "semantic_replan_guard_v0", "selected_obligation_id": obligation},
        "writeback_run": {"goal_id": "destination", "agent_id": "researcher", "todo_id": "todo_probe",
            "turn_instance_id": "decision-1", "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "semantic_delta": {"schema_version": "replan_semantic_delta_v0", "accepted": True,
                    "obligation_id": obligation}},
            "agent_vision": {"path_delta": {"evidence_refs": ["checkpoint:" + sha]}}}}
    current = "replan-" + "2" * 16
    if fault == "pending":
        review["progress"]["state"] = "settlement_pending"
    elif fault == "neutral":
        review["writeback_run"]["autonomous_replan_ack"]["semantic_delta"]["accepted"] = False
    elif fault == "source_actor":
        review["writeback_run"]["goal_id"] = "source"
    elif fault == "unlinked":
        review["writeback_run"]["agent_vision"]["path_delta"]["evidence_refs"] = ["checkpoint:" + "b" * 64]
    elif fault == "same_node":
        current = obligation
    elif fault == "wrong_binding":
        review["writeback_run"]["todo_id"] = "todo_other"
    request = {"action": "restore_capture", "checkpoint_sha256": sha,
        "goal_id": "destination", "agent_id": "researcher", "current_obligation_id": current,
        "review": review}
    result = effect_runtime_result("runtime.checkpoint_artifacts", request)
    assert result["capture_allowed"] is (fault is None)
    assert result["execution_authority_granted"] is False
    assert "review_basis" not in result
    detailed = effect_runtime_result("runtime.checkpoint_artifacts", {**request, "include_review_basis": True})
    assert ("review_basis" in detailed) is (fault is None)
    if fault is None:
        assert detailed["review_basis"]["identity"] == identity
        assert detailed["review_basis"]["completed_decision"]["agent_vision"] == review["writeback_run"]["agent_vision"]
    # A fresh Goal/Agent cannot inherit even a valid destination review. This
    # is the outer restore-every-point counterexample to local-ID dedup.
    another = copy.deepcopy(request)
    another.update(goal_id="another-destination", agent_id="another-researcher")
    assert not effect_runtime_result("runtime.checkpoint_artifacts", another)["capture_allowed"]


def test_readonly_participant_capture_and_limited_basis_read(tmp_path):
    source = tmp_path / "code"
    source.write_text("retained evidence")
    source.chmod(0o444)
    components = [{"id": "code", "source_path": str(source)}]
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    unit = tmp_path / "point"
    captured = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
        "destination": str(unit), "point_metadata": {}, "expected_source_sha256": probe["source_sha256"]}, retry_safe=False)
    copied = unit / "components" / "code"
    assert copied.read_text() == source.read_text() and copied.stat().st_mode & 0o777 == 0o444
    copied.chmod(0o644)
    copied.write_text("changed member")
    # Digest-bound declaration readback is explicitly weaker than full member
    # verification. Native admission must continue using the latter.
    basis = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "read_basis", "root": str(unit),
        "expected_sha256": captured["sha256"]})
    assert basis["integrity_verified"] is False and basis["execution_authority_granted"] is False
    from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError
    with pytest.raises(EffectRuntimeRemoteError):
        effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(unit),
            "expected_sha256": captured["sha256"]})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("cited", [False, True])
def test_successor_review_needs_checkpoint_linked_settlement(tmp_path, monkeypatch, provider, cited):
    """A legal successor can close out without losing the recovery-specific review.

    Exercise real CLI authoring, canonical storage and settlement. The native
    conversation completion check is a separate Host gate, not established here.
    """
    import json
    import shlex

    from test_replan_successor_guard_reentry import _fixture, _guard, _add, GOAL, AGENT, TURN
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    call, runtime, _ = _fixture(tmp_path, monkeypatch, provider)
    original = _guard(call)
    obligation = original["heartbeat_receipt"]["settlement_identity"]["replan_obligation_id"]
    successor = _add(call, obligation)
    assert successor["host_action"] == "end_current_heartbeat"
    # The original guard's closeout route remains authoritative. Neither the
    # successor's creation receipt nor its open status is a completed review.
    closeout = _guard(call)
    actions = closeout["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert closeout["replan_action_packet"]["settlement_only"] is True
    sha = "a" * 64

    def capture():
        observed = read_heartbeat_settlement(runtime, goal_id=GOAL, agent_id=AGENT,
            todo_id=None, turn_instance_id=TURN, resolve_original_binding=True,
            registry_path=tmp_path / "registry.json")
        return effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "restore_capture", "checkpoint_sha256": sha,
            "goal_id": GOAL, "agent_id": AGENT, "current_obligation_id": None,
            "review": {"identity": observed.identity.value.as_dict(),
                "progress": observed.progress, "writeback_run": observed.writeback_run,
                "semantic_replan_guard": observed.semantic_replan_guard}})

    assert capture()["capture_allowed"] is False
    packet = {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Retained checkpoint evidence supports the next source audit."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The retained source needs review.",
            "observed_reality": "Reviewed source evidence supports the independently validated successor.",
            "retained": ["Original acceptance and remaining evaluation opportunity"],
            "evidence_refs": ["checkpoint:" + sha if cited else "evidence:source-review"]}}
    vision = tmp_path / "checkpoint-review.json"
    vision.write_text(json.dumps(packet))
    call(*shlex.split(actions[0])[1:], "--agent-vision-json", str(vision),
         "--no-global-sync", "--suppress-external-sinks")
    assert capture()["capture_allowed"] is False  # Writeback alone is not settled.
    call(*shlex.split(actions[1])[1:])
    assert capture()["capture_allowed"] is cited
    assert call("todo", "list", "--goal-id", GOAL,
                "--todo-id", successor["todo_id"])["todo"]["status"] == "open"
