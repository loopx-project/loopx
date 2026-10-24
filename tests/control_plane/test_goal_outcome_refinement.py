"""Ordinary refinements survive native reads and reject latest-request-only work.

The request interpretation is scripted; real File/SQLite authority, subprocess
CLI, artifact validators and enabled conversation tools are not substitutes.
"""
import json
import sys

import pytest

from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_goal_acceptance_prepared_action import conversation_check as conversation_check
from loopx.control_plane.coordination.local_authority import read_canonical_todo_fields_if_promoted
from loopx.control_plane.goals.goal_frontier.acceptance import acceptance_gaps_from_outcomes


def refinement(requirements, source):
    return {"outcome_id": "tour", "owner_agent_id": "agent-a", "objective": "Deliver a clear product tour",
        "requirements": requirements, "todo_ids": ["todo_export"],
        "source": {"ref": source, "summary": "Owner refined the presentation"}}


def record(project, cli, document, operation, *, revision=None):
    path = project / "refinement.json"
    path.write_text(json.dumps(document))
    revision = revision or cli("inspect")[1]["provider_revision"]
    args = ("refine", "--agent-id", "agent-a", "--document", str(path), "--operation-id", operation,
        "--expected-provider-revision", revision, "--execute")
    return cli(*args), args


def test_cumulative_refinement_guides_artifact_and_independent_check(acceptance_goal):
    project, acceptance_file, cli, run = acceptance_goal
    state = (project / "state.md").read_bytes()
    opening = {"id": "opening", "description": "Retain the introduction"}
    order = {"id": "order", "description": "Delegation precedes the task board"}
    (code, first), first_args = record(project, cli, refinement([opening], "message:first"), "first")
    assert code == 0, first
    assert "enabled" not in first["goal_acceptance_contract"]
    assert cli(*first_args)[1]["status"] == "replayed"
    (code, second), _ = record(project, cli, refinement([order], "message:second"), "second")
    assert code == 0, second
    # Each CLI is a new caller process; this is recovered native state.
    recovered = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]
    outcome = recovered["outcomes"][0]
    assert [r["id"] for r in outcome["requirements"]] == ["opening", "order"]
    assert len(outcome["sources"]) == 2
    assert (project / "state.md").read_bytes() == state
    fields = read_canonical_todo_fields_if_promoted(runtime_root=project.parent / "runtime", goal_id="goal-acceptance")
    assert fields["agent_todos"]["goal_acceptance_contract"]["outcome_requirements"] == recovered
    status = run("status", "--goal-id", "goal-acceptance")[1]
    assert "Retain the introduction" in json.dumps(status)
    admission = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
    required = admission["interaction_contract"]["agent_channel"]["required_reads"]
    context = admission["interaction_contract"]["agent_channel"]["work_context"]
    source = next(row for row in context["sources"] if row["source"] == "goal_acceptance")
    assert source["content"]["goal_acceptance_contract"]["outcome_requirements"] == recovered
    assert not any(read["source"] == "goal_acceptance" for read in required), "complete inline read avoids another Agent CLI call"
    plan = {"outcome_id": "tour", "criteria": [
            {"id": opening["id"], "validation_argv": [sys.executable, "-c", "from pathlib import Path; assert Path('tour.txt').read_text().startswith('Intro\\n')"]},
            {"id": order["id"], "validation_argv": [sys.executable, "-c", "from pathlib import Path; s=Path('tour.txt').read_text(); assert s.index('Delegation') < s.index('Board')"]},
        ]}
    acceptance_file.write_text(json.dumps(plan))
    code, configured = cli("configure", "--document", str(acceptance_file), "--operation-id", "checks",
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"], "--execute")
    assert code == 0, configured
    # The latest-request-only implementation is deliberately wrong.
    (project / "tour.txt").write_text("Delegation\nBoard\n")
    code, failed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 1 and not failed["checks_passed"], failed
    # Readback drives the next edit; original intent and the new ordering coexist.
    current = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert any(r["id"] == "opening" and r["status"] != "accepted" for r in current["requirements"])
    assert next(r for r in current["requirements"] if r["id"] == "order")["status"] == "accepted", "one failed check must not erase other current evidence"
    (project / "tour.txt").write_text("Intro\nDelegation\nBoard\n")
    code, passed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and passed["checks_passed"], passed
    verified = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert all(r["status"] == "accepted" for r in verified["requirements"])
    assert (project / "state.md").read_bytes() == state


def test_native_refinement_refusals_preserve_committed_requirements(acceptance_goal):
    project, _, cli, _ = acceptance_goal
    patch = refinement([{"id": "opening", "description": "Keep the introduction"}], "message:first")
    original_revision = cli("inspect")[1]["provider_revision"]
    assert record(project, cli, patch, "first")[0][0] == 0
    before = cli("inspect")[1]
    code, bad = record(project, cli, patch, "stale", revision=original_revision)[0]
    assert code == 1 and "revision" in bad["error"], bad
    for update in [{"objective": "Replace the Goal"}, {"owner_agent_id": "unregistered"},
        {"todo_ids": ["todo_missing"]}, {"passed": True},
        {"requirements": [{"id": "opening", "description": "Drop the introduction"}]}]:
        code, bad = record(project, cli, {**patch, **update}, "invalid")[0]
        assert code == 1, bad
        assert cli("inspect")[1] == before


def test_review_only_cli_retains_origins_and_requires_provenance_for_new_requirements(acceptance_goal):
    project, _, cli, _ = acceptance_goal
    declared = refinement([{"id": "opening", "description": "Keep the introduction"}], "message:first")
    assert record(project, cli, declared, "declare")[0][0] == 0
    origins = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["sources"]
    for index in range(2):
        review = {"outcome_id": "tour", "review": [{"id": "opening", "conclusion": "uncertain",
            "evidence_refs": [f"artifact:tour-{index}"], "note": "The introduction needs further validation"}]}
        (code, result), args = record(project, cli, review, f"review-{index}")
        assert code == 0 and result["status"] == "applied", result
        assert cli(*args)[1]["status"] == "replayed"
        outcome = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
        assert outcome["sources"] == origins and outcome["revision"] == index + 2
        assert outcome["requirements"][0]["agent_review"]["evidence_refs"] == [f"artifact:tour-{index}"]
    before = cli("inspect")[1]
    (code, rejected), _ = record(project, cli, {"outcome_id": "tour",
        "requirements": [{"id": "order", "description": "Retain the order"}]}, "missing-source")
    assert code == 1 and "require source provenance" in rejected["error_detail"], rejected
    assert cli("inspect")[1] == before


def test_typed_outcome_gaps_are_transported_only_to_their_agent():
    gap = {"kind": "goal_outcome_uncovered", "agent_id": "author", "outcome_id": "tour", "requirement_ids": ["opening"]}
    summary = {"goal_acceptance_contract": {"enabled": False, "outcome_requirements": {"acceptance_gaps": [gap]}}}
    assert acceptance_gaps_from_outcomes(summary, agent_id="author") == [gap]
    assert acceptance_gaps_from_outcomes(summary, agent_id="other") == []
    assert acceptance_gaps_from_outcomes(summary, agent_id=None) == []


def test_native_conversation_records_host_bound_outcome_and_reads_it(conversation_check):
    project, cli, controller, sid, _, _, adapter = conversation_check
    from loopx.chat_loopx_mode import TOOL
    handler = adapter.session.read_tool_handler
    before = handler(TOOL["name"], {"action": "outcomes"})
    request = {"action": "refine_outcome", "operation_id": "capture-user-delivery", "expected_provider_revision": before["provider_revision"],
        "document": {"outcome_id": "tour", "objective": "Deliver the tour", "requirements": [{"id": "opening", "description": "Keep the introduction"}],
            "todo_ids": [], "source_summary": "Direct user requested the tour"}}
    result = handler(TOOL["name"], request)
    assert result["ok"], result
    outcome = result["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["owner_agent_id"] == "coordinator"
    assert outcome["sources"][0]["ref"].startswith("turn:admitted-tool-turn:")
    assert handler(TOOL["name"], request)["status"] == "replayed"
    assert not (project / "runs.txt").exists()
    rejected = handler(TOOL["name"], {**request, "document": {**request["document"], "owner_agent_id": "other"}})
    assert rejected["error"] == "collaboration_request_rejected"
    mode = controller.store.load_session(sid)["loopx_mode"]
    controller.store.update_session(sid, loopx_mode={**mode, "paused": True})
    before = cli("inspect")[1]
    assert handler(TOOL["name"], request)["error"] == "conversation_execution_inactive"
    assert cli("inspect")[1] == before


def test_native_done_work_reenters_existing_frontier_without_accepting_outcome(acceptance_goal):
    project, _, cli, run = acceptance_goal
    assert record(project, cli, refinement([{"id": "opening", "description": "Keep the introduction"}], "message:first"), "first")[0][0] == 0
    code, completed = run("todo", "complete", "--goal-id", "goal-acceptance", "--agent-id", "agent-a", "--todo-id", "todo_export")
    assert code == 0, completed
    code, decision = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
    assert code == 0, decision
    frontier = decision["goal_frontier_projection"]
    gap = next(g for g in frontier["acceptance_gaps"] if g["kind"] == "goal_outcome_uncovered")
    assert gap["outcome_id"] == "tour" and gap["requirement_ids"] == ["opening"]
    assert frontier["replan_required"] is True
    assert cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]["status"] == "unverified"


@pytest.mark.parametrize("include_source", [False, True])
def test_default_review_uses_one_existing_writeback_and_preserves_verification_boundary(acceptance_goal, include_source):
    project, _, cli, run = acceptance_goal
    requirement = {"id": "opening", "description": "Keep the introduction"}
    assert record(project, cli, refinement([requirement], "message:first"), "first")[0][0] == 0
    (project / "tour.txt").write_text("Intro\nDelegation\nBoard\n")
    patch = {"outcome_id": "tour"}
    if include_source:
        patch["source"] = {"ref": "artifact:tour-v1", "summary": "Reviewed the produced artifact against all retained requirements"}
    patch["review"] = [{"id": "opening", "conclusion": "met", "evidence_refs": ["tour.txt#introduction"], "note": "Read the artifact and confirmed its introduction remains"}]
    envelope = {"operation_id": "review-tour", "expected_provider_revision": cli("inspect")[1]["provider_revision"], "refinement": patch}
    document = project / "outcome-update.json"
    document.write_text(json.dumps(envelope))
    args = ("refresh-state", "--goal-id", "goal-acceptance", "--agent-id", "agent-a", "--no-global-sync", "--goal-outcome-update-json", str(document))
    code, dry = run(*args, "--dry-run")
    assert code == 0 and dry["goal_outcome_update"]["status"] == "planned", json.dumps(dry)
    before = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]
    assert before["revision"] == 1
    code, applied = run(*args)
    assert code == 0, json.dumps(applied)
    result = cli("inspect")[1]["goal_acceptance_contract"]
    assert len(result["outcome_requirements"]["outcomes"][0]["sources"]) == (2 if include_source else 1)
    assert "enabled" not in result and "criteria" not in result, "review is a default capability"
    req = result["outcome_requirements"]["outcomes"][0]["requirements"][0]
    assert req["agent_review"]["conclusion"] == "met" and req["planning_satisfied"]
    assert req["status"] == "unverified" and not req["agent_review"]["independently_verified"]
    code, replay = run(*args)
    assert code == 0 and replay["goal_outcome_update"]["status"] == "replayed", json.dumps(replay)
    assert cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["revision"] == 2


def test_batched_review_returns_receipt_without_echoing_cumulative_contract(acceptance_goal):
    project, _, cli, run = acceptance_goal
    assert record(project, cli, refinement([
        {"id": "opening", "description": "Retain the introduction"},
    ], "message:tour"), "declare-tour")[0][0] == 0
    other = {
        **refinement([
            {"id": f"section-{index}", "description": ("Retain the complete approved section. " * 8).strip()}
            for index in range(8)
        ], "message:other-deliverable"),
        "outcome_id": "other-deliverable", "objective": "Deliver another complete artifact",
    }
    (code, declared), _ = record(project, cli, other, "declare-other")
    assert code == 0, declared
    before = cli("inspect")[1]
    retained = {item["id"]: item for item in before["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]}
    document = project / "review.json"
    document.write_text(json.dumps({
        "operation_id": "review-tour",
        "expected_provider_revision": before["provider_revision"],
        "refinement": {"outcome_id": "tour", "review": [{
            "id": "opening", "conclusion": "met", "evidence_refs": ["artifact:tour"],
            "note": "Read the artifact and confirmed the original introduction",
        }]},
    }))
    args = ("refresh-state", "--goal-id", "goal-acceptance", "--agent-id", "agent-a",
        "--no-global-sync", "--goal-outcome-update-json", str(document))
    for suffix, status in [(('--dry-run',), "planned"), ((), "applied"), ((), "replayed")]:
        code, result = run(*args, *suffix)
        assert code == 0, result
        receipt = result["goal_outcome_update"]
        assert receipt["ok"] and receipt["status"] == status
        assert receipt["operation_id"] == "review-tour"
        assert receipt["goal_id"] == "goal-acceptance"
        assert receipt["provider_revision"] and receipt["source_authority"]
        assert "goal_acceptance_contract" not in receipt
        assert "other-deliverable" not in json.dumps(receipt)
        current = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]
        outcomes = {item["id"]: item for item in current["outcomes"]}
        assert set(outcomes) == {"tour", "other-deliverable"}
        assert outcomes["other-deliverable"] == retained["other-deliverable"]
        assert outcomes["tour"]["sources"] == retained["tour"]["sources"]
        if status == "planned":
            assert outcomes == retained
        else:
            requirement = outcomes["tour"]["requirements"][0]
            assert requirement["agent_review"]["conclusion"] == "met"
            assert requirement["status"] == "unverified"
            assert not requirement["agent_review"]["independently_verified"]
            assert current["revision"] == 3, "replay must not apply the review twice"
    code, ordinary = run("refresh-state", "--goal-id", "goal-acceptance", "--agent-id", "agent-a", "--no-global-sync")
    assert code == 0 and "goal_outcome_update" not in ordinary


def test_local_research_outcomes_preserve_root_continuation_and_runnable_work(acceptance_goal):
    project, _, cli, run = acceptance_goal
    root_before = (project / "state.md").read_bytes()
    def admission():
        code, result = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
        assert code == 0, result
        return result
    baseline = admission()
    assert baseline["should_run"] is True, baseline
    # A research subquestion is not the whole campaign. The independent next
    # experiment remains runnable whether this subquestion is unresolved or met.
    patch = refinement([{"id": "hypothesis", "description": "Assess one research hypothesis"}], "research:question")
    patch.update(objective="Assess one hypothesis in the continuing research program", todo_ids=[])
    assert record(project, cli, patch, "research-question")[0][0] == 0
    for conclusion in [None, "uncertain", "unmet", "met"]:
        if conclusion:
            patch["review"] = [{"id": "hypothesis", "conclusion": conclusion,
                "evidence_refs": ["research-results.md#experiment-1"], "note": "One experiment informs this subquestion only"}]
            assert record(project, cli, patch, f"research-review-{conclusion}")[0][0] == 0
        decision = admission()
        assert decision["should_run"] is True, decision
        assert decision["goal_frontier_projection"]["replan_required"] is False, "outcome review must not preempt runnable research"
        for field in ("decision", "reason", "reason_code", "recommended_action", "quota", "stop_reason", "terminal_no_followup"):
            assert decision.get(field) == baseline.get(field), field
        assert (project / "state.md").read_bytes() == root_before
    declaration = cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]
    assert declaration["goal_completion_assessed"] is False
    assert declaration["execution_authority_granted"] is False


def test_batched_refinement_receipt_survives_later_writeback_failure(acceptance_goal, monkeypatch, capsys):
    from loopx.cli import main
    import loopx.cli_commands.project_lifecycle_refresh_state as refresh
    project, _, cli, _ = acceptance_goal
    envelope = {"operation_id": "capture-before-writeback", "expected_provider_revision": cli("inspect")[1]["provider_revision"],
        "refinement": refinement([{"id": "opening", "description": "Keep the introduction"}], "message:first")}
    path = project / "outcome-update.json"
    path.write_text(json.dumps(envelope))
    original_refresh = refresh.refresh_state_run
    def unavailable(**kwargs):
        raise RuntimeError("Synthetic writeback unavailable after refinement")
    monkeypatch.setattr(refresh, "refresh_state_run", unavailable)
    args = ["loopx", "--format", "json", "--registry", str(project.parent / "registry.json"),
        "--runtime-root", str(project.parent / "runtime"), "refresh-state", "--goal-id", "goal-acceptance",
        "--agent-id", "agent-a", "--no-global-sync", "--goal-outcome-update-json", str(path)]
    monkeypatch.setattr(sys, "argv", args)
    assert main() == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["goal_outcome_update"]["status"] == "applied", failed
    assert "goal_acceptance_contract" not in failed["goal_outcome_update"]
    assert cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["revision"] == 1
    monkeypatch.setattr(refresh, "refresh_state_run", original_refresh)
    assert main() == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["goal_outcome_update"]["status"] == "replayed", recovered
    assert "goal_acceptance_contract" not in recovered["goal_outcome_update"]
    assert cli("inspect")[1]["goal_acceptance_contract"]["outcome_requirements"]["revision"] == 1


@pytest.mark.parametrize("change_requirements", [False, True])
def test_referenced_native_plan_reviews_retained_requirements_before_owner_execution(conversation_check, change_requirements):
    from loopx.chat_actions import ChatActionService
    from loopx.chat_action_store import ChatActionStore
    project, cli, controller, _, _, call, _ = conversation_check
    requirement = {"id": "opening", "description": "Keep the introduction"}
    assert record(project, cli, refinement([requirement], "message:first"), "first")[0][0] == 0
    compact = {"outcome_id": "tour", "criteria": [{"id": "opening", "validation_argv": ["python3", "check.py"]}]}
    prepared = call(document=compact)
    assert prepared["ok"], prepared
    proposal = prepared["proposal"]
    assert proposal["normalized_parameters"]["document"] == compact
    resolved = proposal["normalized_parameters"]["resolved_document"]
    assert resolved["objective"] == "Deliver a clear product tour"
    assert resolved["criteria"][0]["description"] == requirement["description"]
    assert resolved["scope"] == {"kind": "selected_work", "todo_ids": ["todo_export"]}
    assert not (project / "runs.txt").exists()
    # Same request recovers its immutable proposal even after a material update.
    if change_requirements:
        delta = {"outcome_id": "tour", "source": {"ref": "message:second", "summary": "Add ordering"},
                 "requirements": [{"id": "order", "description": "Keep the correct order"}]}
        assert record(project, cli, delta, "delta")[0][0] == 0
    assert call(document=compact)["proposal"] == proposal
    service = ChatActionService(store=ChatActionStore(project.parent / "runtime/chat/actions"), registry_path=controller.registry_path)
    result = service.apply(proposal["proposal_id"])
    if change_requirements:
        assert result["proposal"]["status"] == "stale", result
        assert not (project / "runs.txt").exists(), "stale requirements reject before commands run"
        assert "criteria" not in cli("inspect")[1]["goal_acceptance_contract"]
    else:
        assert result["proposal"]["receipt"]["checks_passed"] is True, result
        assert (project / "runs.txt").read_text() == "x"
        assert service.apply(proposal["proposal_id"]) == result
        assert (project / "runs.txt").read_text() == "x"
