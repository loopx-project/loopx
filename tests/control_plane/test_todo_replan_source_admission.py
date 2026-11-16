from copy import deepcopy

import pytest

from loopx.control_plane.quota import heartbeat_receipt
from loopx.control_plane.quota.effect_program import SettlementIdentity


@pytest.mark.parametrize("selected_todo", ["todo_selected", "todo_other"])
def test_source_capture_uses_typed_todo_replan_binding(tmp_path, monkeypatch, selected_todo):
    identity = SettlementIdentity(goal_id="source-fixture", agent_id="fixture-agent",
        todo_id="todo_selected", turn_instance_id="source-turn")
    obligation_id = "replan-1111111111111111"
    payload = {"ok": True, "should_run": True, "goal_id": identity.goal_id,
        "agent_identity": {"agent_id": identity.agent_id},
        "selected_todo": {"todo_id": selected_todo, "selection_binding": "pending_action_selection"},
        "action_selection_qualification": {"state": "qualified", "requested_todo_id": selected_todo},
        "interaction_contract": {
            "agent_channel": {"must_attempt": True, "delivery_allowed": False},
            "cli_channel": {"settlement_plan": {"identity": identity.as_dict()},
                "replan_settlement_contract": {"schema_version": "replan_settlement_contract_v0",
                    "single_binding_required": True,
                    "settlement_binding": {"kind": "todo", "id": identity.todo_id, "cli_argument": "--todo-id"},
                    "semantic_obligation": {"kind": "autonomous_replan", "id": obligation_id,
                        "settlement_bound": False, "discharge": "todo_bound_writeback"}}}}}
    original = deepcopy(payload)
    captured = []

    def capture(*args, **kwargs):
        captured.append(kwargs["obligation_id"])
        return {"obligation": {"obligation_id": obligation_id}}

    def store(*args):
        captured.append("stored")
        return "1" * 64

    monkeypatch.setattr(heartbeat_receipt, "guard_source_from_decision", capture)
    monkeypatch.setattr(heartbeat_receipt, "store_guard_source_blob", store)
    details = {"semantic_replan_obligation_id": obligation_id}
    result = heartbeat_receipt.capture_heartbeat_admission_source(payload, details=details,
        identity=identity, registry_path=tmp_path / "registry.json", runtime_root=tmp_path)
    if selected_todo == identity.todo_id:
        assert captured == [obligation_id, "stored"]
        assert result["semantic_replan_obligation_id"] == obligation_id
        assert result["semantic_replan_source_ref"] == "1" * 64
    else:
        assert captured == [] and result == {}
    assert payload == original and details == {"semantic_replan_obligation_id": obligation_id}
    assert list(tmp_path.iterdir()) == []
