"""Real CLI and conversation effects; scripted callers are not model evidence."""
import json

from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_goal_acceptance_prepared_action import conversation_check as conversation_check
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService


def prepare_source(project):
    state = project / "state.md"
    state.write_text(state.read_text() + "\n## Objective\nBootstrap the project\n")
    path = project.parent / "registry.json"
    registry = json.loads(path.read_text())
    registry["goals"][0]["coordination"]["registered_agents"] = ["agent-a", "agent-b"]
    path.write_text(json.dumps(registry))


def test_native_proposal_owner_apply_and_separate_peer_adoption(acceptance_goal, monkeypatch):
    project, _, cli, run = acceptance_goal
    prepare_source(project)
    original = (project / "state.md").read_bytes()
    document = {"objective": "Maintain a reliable service", "reason": "Bootstrap is complete", "source_ref": "message:owner"}
    path = project / "revision.json"
    path.write_text(json.dumps(document))
    args = ("prepare-objective", "--agent-id", "agent-a", "--document", str(path), "--operation-id", "prepare-current", "--execute")
    code, prepared = cli(*args)
    assert code == 0 and prepared["prepared"] and not prepared["executed"], prepared
    assert "goal_objective" not in cli("inspect")[1]["goal_acceptance_contract"]
    assert cli(*args)[1]["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"]
    other_args = ("prepare-objective", "--agent-id", "agent-b", "--document", str(path), "--operation-id", "peer-same-proposal", "--execute")
    assert cli(*other_args)[1]["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"], "peers share one pending owner review"
    from loopx.control_plane.work_items.team_plan_adapter import team_plan_state_fingerprint
    old_team_basis = team_plan_state_fingerprint(registry_path=project.parent / "registry.json", goal_id="goal-acceptance", basis_agent_id="agent-a")
    service = ChatActionService(store=ChatActionStore(project.parent / "runtime" / "chat" / "actions"), registry_path=project.parent / "registry.json")
    applied = service.apply(prepared["proposal"]["proposal_id"])
    assert applied["proposal"]["status"] == "applied", applied
    assert team_plan_state_fingerprint(registry_path=project.parent / "registry.json", goal_id="goal-acceptance", basis_agent_id="agent-a") != old_team_basis
    status = run("status", "--goal-id", "goal-acceptance")[1]
    assert status["run_history"]["goals"][0]["goal_objective"] == document["objective"]
    for agent in ("agent-a", "agent-b"):
        _, before = cli("inspect", "--agent-id", agent)
        current = before["goal_acceptance_contract"]["goal_objective"]
        own = next(row for row in current["peer_adoption"] if row["agent_id"] == agent)
        assert not own["current"]
        decision = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", agent)[1]
        assert "Maintain a reliable service" in json.dumps(decision), decision
        assert any(gap.get("kind") == "goal_objective_review_required" for gap in decision["goal_frontier_projection"]["acceptance_gaps"]), decision
        adoption = {"revision": current["revision"], "digest": current["digest"], "work_digest": before["objective_work_digest"],
            "review_summary": "Reviewed current work; the existing deliverable still supports the maintained service."}
        path.write_text(json.dumps(adoption))
        code, result = cli("adopt-objective", "--agent-id", agent, "--document", str(path), "--operation-id", f"adopt-{agent}",
            "--expected-provider-revision", before["provider_revision"], "--execute")
        assert code == 0, result
        peers = cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]
        assert next(row for row in peers if row["agent_id"] == agent)["current"]
        if agent == "agent-a":
            assert not next(row for row in peers if row["agent_id"] == "agent-b")["current"]
        next_decision = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", agent)[1]
        assert not any(gap.get("kind") == "goal_objective_review_required" for gap in next_decision["goal_frontier_projection"]["acceptance_gaps"]), next_decision
    assert (project / "state.md").read_bytes() == original
    assert service.apply(prepared["proposal"]["proposal_id"])["proposal"]["status"] == "applied"
    # A native committed revision whose response is lost recovers once, even
    # while the Chat proposal has not yet stored its completed checkpoint.
    from loopx.control_plane.goals import goal_objective_actions
    path.write_text(json.dumps({**document, "objective": "Maintain the service and prove recovery"}))
    code, next_proposal = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(path), "--operation-id", "prepare-next", "--execute")
    assert code == 0, next_proposal
    original_update = goal_objective_actions.update_goal_objective
    def lose_reply(**kwargs):
        value = original_update(**kwargs)
        if kwargs.get("execute"):
            monkeypatch.setattr(goal_objective_actions, "update_goal_objective", original_update)
            raise OSError("synthetic lost response after native commit")
        return value
    monkeypatch.setattr(goal_objective_actions, "update_goal_objective", lose_reply)
    import pytest
    with pytest.raises(OSError, match="lost response"):
        service.apply(next_proposal["proposal"]["proposal_id"])
    assert service.apply(next_proposal["proposal"]["proposal_id"])["proposal"]["status"] == "applied"
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 2


def test_native_conversation_exposes_and_executes_revision_loop(conversation_check):
    project, cli, controller, sid, _, _, adapter = conversation_check
    from loopx.chat_loopx_mode import TOOL, GUIDANCE
    assert "prepare_goal_objective" in GUIDANCE and "adopt_goal_objective" in GUIDANCE
    state = project / "state.md"
    state.write_text(state.read_text() + "\n## Objective\nBootstrap the project\n")
    handler = adapter.session.read_tool_handler
    request = {"action": "prepare_goal_objective", "operation_id": "revise-current-direction", "document": {
        "objective": "Maintain the service and recovery path", "reason": "The owner changed the ongoing operating scope"}}
    prepared = handler(TOOL["name"], request)
    assert prepared["ok"] and prepared["prepared"], prepared
    assert prepared["proposal"]["normalized_parameters"]["source_ref"].startswith("turn:")
    assert handler(TOOL["name"], request)["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"]
    assert "goal_objective" not in handler(TOOL["name"], {"action": "outcomes"})["goal_acceptance_contract"]
    service = ChatActionService(store=ChatActionStore(controller.store.root.parent / "chat" / "actions"), registry_path=controller.registry_path)
    assert service.apply(prepared["proposal"]["proposal_id"])["proposal"]["status"] == "applied"
    refused = handler(TOOL["name"], {"action": "start", "binding_id": "delivery", "operation_id": "work-before-adoption"})
    assert refused["error"] == "goal_objective_review_required", refused
    before = handler(TOOL["name"], {"action": "outcomes"})
    current = before["goal_acceptance_contract"]["goal_objective"]
    adopted = handler(TOOL["name"], {"action": "adopt_goal_objective", "operation_id": "adopt-revision",
        "expected_provider_revision": before["provider_revision"], "document": {"revision": current["revision"], "digest": current["digest"],
            "work_digest": before["objective_work_digest"], "review_summary": "Reviewed the current work against the revised scope."}})
    assert adopted["ok"], adopted
    assert any(row["agent_id"] == "coordinator" and row["current"] for row in adopted["goal_acceptance_contract"]["goal_objective"]["peer_adoption"])
