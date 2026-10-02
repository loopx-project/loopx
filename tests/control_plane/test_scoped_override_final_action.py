"""Scoped gate diagnostics must describe only the final admitted Todo."""
import pytest

from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.effect_program import ReceiptBoundReplayPhase
from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item


def decision(*, capabilities=("shell",), task_class="user_gate", claimed=True, **options):
    rows = [quota_todo_item(
        todo_id=todo_id, title=title, priority=priority,
        claimed_by="agent-a" if claimed else None,
        required_capabilities=required,
    ) for todo_id, title, priority, required in (
        ("todo_network", "Validate remote result", "P0", ["shell", "network"]),
        ("todo_local", "Validate local result", "P1", ["shell"]),
    )]
    peer_gate = quota_todo_item(
        todo_id="todo_peer", title="Choose peer destination", role="user",
        task_class=task_class, blocks_agent="agent-b" if task_class == "user_gate" else None,
        bound_agent="agent-b" if task_class == "user_action" else None,
    )
    status = quota_status_payload(
        goal_id="scoped-action", status="active", quota_state="operator_gate",
        recommended_action="Wait for the peer destination", waiting_on="controller",
        agent_todo_items=rows, user_todo_items=[peer_gate],
        coordination={"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"]},
    )
    return build_quota_should_run(status, goal_id="scoped-action", agent_id="agent-a",
                                 available_capabilities=list(capabilities),
                                 scheduler_execution_context=scheduler_execution_context_for_runtime_profile("codex_app_heartbeat"),
                                 **options)


@pytest.mark.parametrize("task_class", ["user_gate", "user_action"])
@pytest.mark.parametrize("capabilities,todo_id", [(("shell",), "todo_local"),
                                                 (("shell", "network"), "todo_network")])
def test_override_uses_final_capability_eligible_todo(task_class, capabilities, todo_id):
    payload = decision(task_class=task_class, capabilities=capabilities, requested_action_todo_id=todo_id)
    selected = payload["selected_todo"]
    assert selected["todo_id"] == todo_id
    assert payload["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    assert todo_id in payload["interaction_contract"]["agent_channel"]["primary_action"]
    override = payload[f"agent_scoped_{task_class}_override"]
    assert override["selected_action"] == selected["text"]
    assert override["from_state"] == "operator_gate"


@pytest.mark.parametrize("options", [
    {"capabilities": ("network",)},
    {"receipt_bound_replay_phase": ReceiptBoundReplayPhase.SETTLED},
    {"requested_action_todo_id": "todo_absent"},
    {"claimed": False, "capabilities": ("shell", "network")},
])
def test_non_delivery_packet_does_not_advertise_override_action(options):
    payload = decision(**options)
    assert payload["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert "selected_action" not in payload["agent_scoped_user_gate_override"]


def test_receipt_binding_preserves_identity_across_peer_gate_override():
    payload = decision(capabilities=("shell", "network"), receipt_bound_todo_id="todo_local")
    selected = payload["selected_todo"]
    assert selected["todo_id"] == "todo_local"
    assert selected["selection_binding"] == "heartbeat_receipt"
    assert payload["agent_scoped_user_gate_override"]["selected_action"] == selected["text"]
