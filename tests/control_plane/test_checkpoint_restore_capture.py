"""A restored fork needs a current, settled semantic decision before recapture."""
import copy

import pytest

from loopx.control_plane.effect_program import SettlementIdentity
from loopx.control_plane.effect_runtime import effect_runtime_result


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
