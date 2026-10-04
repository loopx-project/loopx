from loopx.control_plane.work_items.interaction_contract import build_interaction_contract
from loopx.control_plane.quota.turn_envelope import build_turn_envelope


def _payload(*, should_run: bool) -> dict:
    return {
        "goal_id": "workspace-causality-fixture",
        "should_run": should_run,
        "normal_delivery_allowed": should_run,
        "execution_obligation": {
            "must_attempt_work": should_run,
            "delivery_allowed": should_run,
        },
        "heartbeat_recommendation": {},
        "agent_identity": {"agent_id": "codex-peer"},
        "selected_todo": {
            "todo_id": "todo-cross-repository",
            "task_repository": "git:github.com/example/delivery",
        },
    }


def test_bounded_delivery_projects_workspace_causality() -> None:
    contract = build_interaction_contract(_payload(should_run=True))

    causality = contract["cli_channel"]["delivery_workspace_causality"]
    assert causality == {
        "schema_version": "delivery_workspace_causality_v0",
        "refresh": "delivery_workspace; otherwise --delivery-workspace-path",
        "spend": "recorded_delivery_workspace",
        "mismatch": "fail_closed",
    }


def test_non_delivery_contract_omits_workspace_causality() -> None:
    contract = build_interaction_contract(_payload(should_run=False))

    assert "delivery_workspace_causality" not in contract["cli_channel"]


def test_local_delivery_packet_explains_workspace_without_non_delivery_relabel() -> None:
    payload = _payload(should_run=True)
    payload["selected_todo"].pop("task_repository")

    contract = build_interaction_contract(payload)

    causality = contract["cli_channel"]["delivery_workspace_causality"]
    assert causality["refresh"] == "registered local Goal workspace; Git peer delivery requires an independent worktree"
    assert causality["spend"] == "recorded_delivery_workspace"
    assert causality["mismatch"] == "fail_closed"


def test_turn_envelope_preserves_workspace_causality() -> None:
    payload = _payload(should_run=True)
    payload["interaction_contract"] = build_interaction_contract(payload)

    envelope = build_turn_envelope(payload)

    assert envelope["writeback"]["delivery_workspace_causality"] == {
        "schema_version": "delivery_workspace_causality_v0",
        "refresh": "delivery_workspace; otherwise --delivery-workspace-path",
        "spend": "recorded_delivery_workspace",
        "mismatch": "fail_closed",
    }
    assert envelope["action_signature"]["matches"] is True
