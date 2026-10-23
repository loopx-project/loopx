"""Real CLI and conversation effects; scripted callers are not model evidence."""
import json

import pytest

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


def apply_objective(project, cli, objective):
    document = {"objective": objective, "reason": "Keep the current direction explicit", "source_ref": "message:owner"}
    path = project / "objective.json"
    path.write_text(json.dumps(document))
    code, prepared = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(path),
        "--operation-id", "prepare-guidance", "--execute")
    assert code == 0 and prepared["proposal"]["status"] == "preview_ready", prepared
    service = ChatActionService(
        store=ChatActionStore(project.parent / "runtime" / "chat" / "actions"),
        registry_path=project.parent / "registry.json",
    )
    assert service.apply(prepared["proposal"]["proposal_id"])["proposal"]["status"] == "applied"


@pytest.mark.parametrize("acceptance_kind", ["configured-checks", "cumulative-outcome"])
def test_objective_and_acceptance_guidance_share_real_inline_goal_read(acceptance_goal, acceptance_kind):
    project, acceptance_file, cli, run = acceptance_goal
    prepare_source(project)
    apply_objective(project, cli, "Maintain the export with complete acceptance")
    current = cli("inspect")[1]
    if acceptance_kind == "configured-checks":
        code, configured = cli("configure", "--document", str(acceptance_file), "--operation-id", "guidance-checks",
            "--expected-provider-revision", current["provider_revision"], "--execute")
        assert code == 0 and configured["status"] == "applied", configured
    else:
        outcome = {"outcome_id": "tour", "owner_agent_id": "agent-a",
            "objective": "Maintain the export with complete acceptance",
            "requirements": [{"id": "export", "description": "Retain the exported artifact"}],
            "todo_ids": ["todo_export"],
            "source": {"ref": "message:outcome", "summary": "The Goal requires the export artifact"}}
        outcome_path = project / "outcome.json"
        outcome_path.write_text(json.dumps(outcome))
        current = cli("inspect")[1]
        code, refined = cli("refine", "--agent-id", "agent-a", "--document", str(outcome_path),
            "--operation-id", "guidance-outcome", "--expected-provider-revision",
            current["provider_revision"], "--execute")
        assert code == 0 and refined["status"] == "applied", refined

    before = cli("inspect", "--agent-id", "agent-a")[1]
    objective = before["goal_acceptance_contract"]["goal_objective"]
    adoption_path = project / "adoption.json"
    adoption_path.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": before["objective_work_digest"],
        "review_summary": "Reviewed the export and current acceptance before adopting the revision."}))
    code, adopted = cli("adopt-objective", "--agent-id", "agent-a", "--document", str(adoption_path),
        "--operation-id", "guidance-adoption", "--expected-provider-revision",
        before["provider_revision"], "--execute")
    assert code == 0, adopted

    admission = run("quota", "should-run", "--codex-app", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
    context = admission["interaction_contract"]["agent_channel"]["work_context"]
    source = next(row for row in context["sources"] if row["source"] == "goal_acceptance")
    reason = source["reason"]
    assert "revision/digest" in reason and "own objective_work_digest" in reason
    required = [
        clause for clause in (
            ("cumulative requirements", "switching work", "Agent review", "Scoped acceptance never replaces Goal intent")
            if acceptance_kind == "cumulative-outcome"
            else ("non-goals", "criteria", "scope/revision", "Scoped acceptance does not replace Goal intent")
        )
    ]
    assert all(clause in reason for clause in required), reason
    reads = admission["interaction_contract"]["agent_channel"]["required_reads"]
    assert not any(read["source"] == "goal_acceptance" for read in reads), "one inline read should fulfill the command"
    assert "prepare-objective" not in json.dumps([context, reads]), (
        "ordinary reads must not proactively direct objective revision")


def test_native_proposal_owner_apply_and_separate_peer_adoption(acceptance_goal, monkeypatch):
    project, _, cli, run = acceptance_goal
    prepare_source(project)
    original = (project / "state.md").read_bytes()
    initial_admission = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
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
    pending_admission = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
    for field in ("normal_delivery_allowed", "requires_user_action", "should_run"):
        assert pending_admission[field] == initial_admission[field], "surfacing a proposal cannot add an early gate"
    assert not any(gap.get("kind") == "goal_objective_review_required"
        for gap in pending_admission["goal_frontier_projection"]["acceptance_gaps"])
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


def test_cli_proposal_preview_authority_and_lost_response_recovery(acceptance_goal, monkeypatch):
    project, checks, cli, _ = acceptance_goal
    prepare_source(project)
    original = (project / "state.md").read_bytes()
    document = project / "objective.json"
    document.write_text(json.dumps({"objective": "Maintain reliable exports", "reason": "The owner added recovery",
        "source_ref": "message:owner-direction"}))
    code, prepared = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "cli-direction", "--execute")
    assert code == 0, prepared
    proposal_id = prepared["proposal"]["proposal_id"]
    code, inspected = cli("inspect-proposal", "--agent-id", "agent-a", "--proposal-id", proposal_id)
    assert code == 0 and inspected["proposal"] == prepared["proposal"], inspected
    before = cli("inspect")[1]
    apply = ("apply-proposal", "--proposal-id", proposal_id)
    code, preview = cli(*apply)
    assert code == 0 and not preview["executed"], preview
    assert preview["proposal"]["status"] == "preview_ready"
    assert cli("inspect")[1] == before
    code, denied = cli(*apply, "--agent-id", "agent-a", "--execute")
    assert code == 1 and not denied["ok"], denied
    assert cli("inspect-proposal", "--proposal-id", proposal_id)[1]["proposal"]["status"] == "preview_ready"
    assert cli("inspect")[1] == before
    code, missing = cli("inspect-proposal", "--proposal-id", "proposal-missing")
    assert code == 1 and not missing["ok"], missing
    check_document = json.loads(checks.read_text())
    check_document["criteria"][0]["validation_argv"][0] = "python3"
    checks.write_text(json.dumps(check_document))
    code, check = cli("prepare", "--agent-id", "agent-a", "--document", str(checks),
        "--operation-id", "check-is-not-objective", "--execute")
    assert code == 0, check
    code, wrong_kind = cli("apply-proposal", "--proposal-id", check["proposal"]["proposal_id"], "--execute")
    assert code == 1 and not wrong_kind["ok"], wrong_kind
    assert not (project / "artifact.txt").exists()
    from loopx.control_plane.goals import goal_objective_actions
    native_update = goal_objective_actions.update_goal_objective

    def lose_reply(**kwargs):
        value = native_update(**kwargs)
        if kwargs.get("execute"):
            monkeypatch.setattr(goal_objective_actions, "update_goal_objective", native_update)
            raise OSError("synthetic lost reply after canonical commit")
        return value

    monkeypatch.setattr(goal_objective_actions, "update_goal_objective", lose_reply)
    service = ChatActionService(store=ChatActionStore(project.parent / "runtime" / "chat" / "actions"),
        registry_path=project.parent / "registry.json")
    with pytest.raises(OSError, match="lost reply"):
        service.apply(proposal_id)
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 1
    code, recovered = cli(*apply, "--execute")
    assert code == 0 and recovered["executed"] and recovered["proposal"]["status"] == "applied", recovered
    own = cli("inspect", "--agent-id", "agent-a")[1]
    objective = own["goal_acceptance_contract"]["goal_objective"]
    document.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": own["objective_work_digest"], "review_summary": "Existing export work includes recovery."}))
    code, adopted = cli("adopt-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "cli-adopt-direction", "--expected-provider-revision", own["provider_revision"], "--execute")
    assert code == 0, adopted
    peers = adopted["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]
    assert next(peer for peer in peers if peer["agent_id"] == "agent-a")["current"]
    assert not next(peer for peer in peers if peer["agent_id"] == "agent-b")["current"]
    apply_objective(project, cli, "Maintain exports and validate restored artifacts")
    code, replay = cli(*apply, "--execute")
    assert code == 0 and replay["proposal"]["status"] == "applied", replay
    assert replay["current"]["goal_acceptance_contract"]["goal_objective"]["revision"] == 2
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 2
    assert (project / "state.md").read_bytes() == original


def test_pending_proposal_allows_independent_work_without_an_owner_gate(acceptance_goal):
    project, _, cli, run = acceptance_goal
    prepare_source(project)
    document = project / "objective.json"
    document.write_text(json.dumps({"objective": "Maintain export recovery", "reason": "Recorded owner direction includes recovery",
        "source_ref": "message:owner-direction"}))
    code, proposal = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "pending-work", "--execute")
    assert code == 0 and proposal["proposal"]["status"] == "preview_ready", proposal
    code, work = run("todo", "add", "--goal-id", "goal-acceptance", "--role", "agent",
        "--claimed-by", "agent-a", "--text", "Validate the existing export recovery",
        "--task-class", "advancement_task", "--action-kind", "implement")
    assert code == 0 and work["ok"] and work["added"], work
    listed = run("todo", "list", "--goal-id", "goal-acceptance", "--role", "agent")[1]
    assert any(todo["text"].endswith("Validate the existing export recovery") for todo in listed["todos"])
    assert "goal_objective" not in cli("inspect")[1]["goal_acceptance_contract"]
    assert cli("inspect-proposal", "--proposal-id", proposal["proposal"]["proposal_id"])[1]["proposal"]["status"] == "preview_ready"


@pytest.mark.parametrize("arguments", [
    ("inspect-proposal",),
    ("inspect-proposal", "--proposal-id", "proposal-example", "--execute"),
    ("apply-proposal", "--proposal-id", "proposal-example", "--operation-id", "new-request"),
    ("inspect", "--proposal-id", "proposal-example"),
])
def test_cli_proposal_rejects_ambiguous_or_mutating_inspection(acceptance_goal, arguments):
    _, _, cli, _ = acceptance_goal
    before = cli("inspect")[1]
    code, rejected = cli(*arguments)
    assert code == 1 and not rejected["ok"], rejected
    assert cli("inspect")[1] == before


def test_native_conversation_exposes_and_executes_revision_loop(conversation_check):
    project, cli, controller, sid, _, _, adapter = conversation_check
    from loopx.chat_loopx_mode import TOOL, GUIDANCE
    # Ordinary conversation can call the API without a proactive revision nudge.
    assert "prepare_goal_objective" not in GUIDANCE
    assert "adopt_goal_objective" in GUIDANCE
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
