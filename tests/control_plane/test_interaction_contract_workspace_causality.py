import subprocess

import pytest

from loopx.control_plane.agents.workspace_guard import (
    observe_goal_delivery_workspace_isolation,
)
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
            "todo_id": "todo_cross_repository",
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


def test_local_delivery_packet_names_target_without_requiring_caller_relocation(tmp_path) -> None:
    payload = _payload(should_run=True)
    payload["selected_todo"].pop("task_repository")
    payload["delivery_workspace_isolation"] = observe_goal_delivery_workspace_isolation(
        {"id": payload["goal_id"], "repo": str(tmp_path)}, payload["selected_todo"],
    )

    contract = build_interaction_contract(payload)

    causality = contract["cli_channel"]["delivery_workspace_causality"]
    assert "--delivery-workspace-path" in causality["refresh"]
    assert "caller cwd differs" in causality["refresh"]
    assert "recorded delivery workspace" in causality["refresh"]
    assert causality["spend"] == "recorded_delivery_workspace"
    assert causality["mismatch"] == "fail_closed"

    payload["interaction_contract"] = contract
    envelope = build_turn_envelope(payload)
    assert envelope["writeback"]["delivery_workspace_causality"] == causality
    assert envelope["action_signature"]["matches"] is True


def test_missing_repository_without_workspace_observation_does_not_certify_local_goal() -> None:
    payload = _payload(should_run=True)
    payload["selected_todo"].pop("task_repository")

    contract = build_interaction_contract(payload)

    assert contract["cli_channel"]["delivery_workspace_causality"]["refresh"] == (
        "delivery_workspace; otherwise --delivery-workspace-path"
    )


@pytest.mark.parametrize("origin", [False, True])
@pytest.mark.parametrize("agent_count", [1, 2])
@pytest.mark.parametrize("policy", [None, False, True])
def test_guidance_reuses_current_goal_identity_and_typed_isolation(
    tmp_path, origin, agent_count, policy,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    if origin:
        subprocess.run([
            "git", "-C", str(tmp_path), "config", "remote.origin.url",
            "https://github.com/example/project.git",
        ], check=True)
    payload = _payload(should_run=True)
    payload["selected_todo"].pop("task_repository")
    goal = {
        "id": payload["goal_id"], "repo": str(tmp_path),
        "coordination": {"registered_agents": ["worker", "peer"][:agent_count]},
    }
    if policy is not None:
        goal["workspace_guard_policy"] = {"peer_independent_worktree_required": policy}
    observation = observe_goal_delivery_workspace_isolation(goal, payload["selected_todo"])
    payload["delivery_workspace_isolation"] = observation
    contract = build_interaction_contract(payload)
    hint = contract["cli_channel"]["delivery_workspace_causality"]

    requires_worktree = agent_count == 2 and (policy is True or (origin and policy is None))
    assert observation["peer_independent_worktree_required"] is requires_worktree
    assert ("independent git worktree" in hint["refresh"]) is requires_worktree
    assert ("registered local Goal target" in hint["refresh"]) is (not origin and not requires_worktree)
    assert hint["spend"] == "recorded_delivery_workspace"
    assert hint["mismatch"] == "fail_closed"
    payload["interaction_contract"] = contract
    assert build_turn_envelope(payload)["writeback"]["delivery_workspace_causality"] == hint
    # Rebuilding after required-read hooks must retain the same observed policy.
    payload["required_reads"] = []
    assert build_interaction_contract(payload)["cli_channel"]["delivery_workspace_causality"] == hint


def test_non_delivery_does_not_gain_a_worktree_obligation(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run([
        "git", "-C", str(tmp_path), "config", "remote.origin.url",
        "https://github.com/example/project.git",
    ], check=True)
    payload = _payload(should_run=True)
    todo = payload["selected_todo"]
    todo.pop("task_repository")
    todo["continuation_policy"] = "same_agent_non_delivery"
    goal = {
        "id": payload["goal_id"], "repo": str(tmp_path),
        "coordination": {"registered_agents": ["worker", "peer"]},
    }
    payload["delivery_workspace_isolation"] = observe_goal_delivery_workspace_isolation(goal, todo)
    hint = build_interaction_contract(payload)["cli_channel"]["delivery_workspace_causality"]

    assert "independent git worktree" not in hint["refresh"]


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
