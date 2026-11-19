"""A settled outcome review stays closed until its source materially changes.

These journeys use the real CLI, File/SQLite authority, original Turn receipt
and quota settlement. Artifact versions come only from the configured oracle.
"""
from __future__ import annotations

import hashlib
import json
import sys

import pytest

from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_goal_outcome_refinement import record, refinement


GOAL = "goal-acceptance"
AGENT = "agent-a"


def _decision(run, *, turn=None):
    args = ["quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT]
    if turn:
        args.extend(["--codex-app", "--turn-instance-id", turn,
            "--codex-app-current-rrule", "FREQ=MINUTELY;INTERVAL=3"])
    code, result = run(*args)
    assert code == 0, result
    return result


def _gaps(decision):
    return [gap for gap in decision["goal_frontier_projection"]["acceptance_gaps"]
        if gap["kind"] == "goal_outcome_uncovered"]


def _complete(run, todo_id="todo_export"):
    code, result = run("todo", "complete", "--goal-id", GOAL, "--agent-id", AGENT,
        "--todo-id", todo_id, "--no-follow-up", "--evidence", "artifact:initial-delivery")
    assert code == 0, result


def _record_blocker(project, run, decision, *, turn):
    obligation = decision["autonomous_replan_obligation"]["obligation_id"]
    document = project / "blocked-outcome-vision.json"
    document.write_text(json.dumps({"goal_id": GOAL, "agent_id": AGENT, "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Retain the original tour requirements while its owner evidence is unavailable."},
        "path_delta": {"outcome": "no_change", "prior_assumption": "The introduction could be checked now",
            "observed_reality": "The required owner evidence is pending",
            "retained": ["Original objective and every retained requirement"],
            "evidence_refs": ["owner-review:pending"]}}))
    code, written = run("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", turn, "--replan-obligation-id", obligation,
        "--classification", "bounded_replan_progress", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
        "--progress-blocker-id", "owner-review:pending", "--progress-evidence-id", "owner-review:pending-evidence",
        "--agent-vision-json", str(document), "--no-global-sync", "--suppress-external-sinks")
    assert code == 0, written
    ack = written.get("autonomous_replan_ack") or written.get("run", {}).get("autonomous_replan_ack")
    assert ack, written
    assert ack["recorded"] and ack["semantic_delta"]["accepted"], written
    assert "new_concrete_blocker" in ack["semantic_delta"]["satisfying_outcomes"]
    code, spent = run("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
        "--slots", "1", "--source", "heartbeat", "--execute",
        "--turn-instance-id", turn, "--replan-obligation-id", obligation)
    assert code == 0, spent
    return obligation


@pytest.mark.parametrize("outcome_count", [1, 5])
def test_settled_outcome_blocker_preserves_requirements_and_rearms_material_work(acceptance_goal, outcome_count):
    project, _, cli, run = acceptance_goal
    for index in range(outcome_count):
        patch = refinement([{"id": "opening", "description": "Retain the introduction"}], f"message:{index}")
        patch["outcome_id"] = f"tour-{index}"
        assert record(project, cli, patch, f"outcome-{index}")[0][0] == 0
    _complete(run)
    first = _decision(run, turn="outcome-blocker-turn")
    assert len(_gaps(first)) == outcome_count
    assert first["goal_frontier_projection"]["replan_required"]
    obligation = _record_blocker(project, run, first, turn="outcome-blocker-turn")
    current = cli("inspect")[1]
    assert len(current["goal_acceptance_contract"]["outcome_requirements"]["acceptance_gaps"]) == outcome_count
    for _ in range(2):
        quiet = _decision(run)
        assert quiet["goal_frontier_projection"]["replan_required"] is False, quiet
        assert quiet.get("autonomous_replan_obligation") is None, quiet
        assert _gaps(quiet), "the bounded decision still shows the retained unmet requirements"
    assert cli("inspect")[1] == current, "read-only polls do not rewrite outcomes or receipts"
    note = {"outcome_id": "tour-0", "source": {"ref": "note:still-waiting", "summary": "The same owner evidence remains pending"}}
    assert record(project, cli, note, "same-evidence-note")[0][0] == 0
    assert _decision(run).get("autonomous_replan_obligation") is None, "recording the same source does not buy another review"
    code, changed = run("todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--text", "Produce the presentation and prove its introduction matches the cited original",
        "--claimed-by", AGENT, "--task-class", "advancement_task", "--action-kind", "implement",
        "--operation-id", "changed-delivery-work")
    assert code == 0, json.dumps(changed, indent=2)
    successor = changed["todo_id"]
    assert record(project, cli, {"outcome_id": "tour-0", "todo_ids": [successor],
        "source": {"ref": "work:changed-delivery", "summary": "The owner authorized the revised delivery work"}},
        "link-changed-work")[0][0] == 0
    _complete(run, successor)
    work = _decision(run)
    assert work["goal_frontier_projection"]["replan_required"] is True, work
    assert work["autonomous_replan_obligation"]["obligation_id"] != obligation
    assert _gaps(work)[0]["frontier_revision"] != _gaps(quiet)[0]["frontier_revision"]
    delta = {"outcome_id": f"tour-{outcome_count - 1}",
        "requirements": [{"id": "appendix", "description": "Retain the supporting appendix"}],
        "source": {"ref": "message:appendix", "summary": "The owner also requires an appendix"}}
    assert record(project, cli, delta, "new-requirement")[0][0] == 0
    changed = _decision(run)
    assert changed["autonomous_replan_obligation"]["obligation_id"] != work["autonomous_replan_obligation"]["obligation_id"]
    assert changed["goal_frontier_projection"]["replan_required"] is True


def test_next_stale_artifact_rearms_the_original_outcome_review(acceptance_goal):
    project, document, cli, run = acceptance_goal
    patch = refinement([{"id": "opening", "description": "Retain the introduction"}], "message:initial")
    assert record(project, cli, patch, "initial-outcome")[0][0] == 0
    checker, artifact = project / "check.py", project / "tour.txt"
    checker.write_text("from pathlib import Path\nassert Path('tour.txt').read_text() == 'Introduction'\n")
    artifact.write_text("Introduction")
    document.write_text(json.dumps({"outcome_id": "tour", "criteria": [{"id": "opening",
        "validation_argv": [sys.executable, "check.py"],
        "validation_files": [{"path": "check.py", "sha256": hashlib.sha256(checker.read_bytes()).hexdigest()}],
        "oracle": {"kind": "owner_pinned_oracle", "source_id": "tour-checker", "input_refs": [], "output_refs": ["tour.txt"]}}]}))
    code, configured = cli("configure", "--document", str(document), "--operation-id", "checks",
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"], "--execute")
    assert code == 0, configured
    _complete(run)
    code, verified = cli("verify", "--execute")
    assert code == 0, verified
    artifact.write_text("First unverified replacement")
    first = _decision(run, turn="artifact-blocker-turn")
    assert _gaps(first)
    _record_blocker(project, run, first, turn="artifact-blocker-turn")
    quiet = _decision(run)
    assert quiet["goal_frontier_projection"]["replan_required"] is False, quiet
    assert quiet.get("autonomous_replan_obligation") is None, quiet
    assert _gaps(quiet)[0]["frontier_revision"] == _gaps(first)[0]["frontier_revision"]
    receipt = cli("inspect")[1]
    artifact.write_text("Second unverified replacement")
    changed = _decision(run)
    assert changed["goal_frontier_projection"]["replan_required"] is True, changed
    assert _gaps(changed)[0]["frontier_revision"] != _gaps(quiet)[0]["frontier_revision"]
    after = cli("inspect")[1]
    assert after["provider_revision"] == receipt["provider_revision"]
    assert after["goal_acceptance_contract"]["verification"] == receipt["goal_acceptance_contract"]["verification"]
    requirement = after["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]
    assert requirement["status"] == "unverified" and not requirement["planning_satisfied"]


def test_shared_manager_observes_the_source_goal_workspace(acceptance_goal, monkeypatch):
    from loopx.status import active_state_todo_fields
    import loopx.state_refresh as refresh

    project, document, cli, run = acceptance_goal
    assert record(project, cli, refinement([{"id": "opening", "description": "Retain the introduction"}],
        "message:initial"), "initial-outcome")[0][0] == 0
    checker, artifact = project / "check.py", project / "tour.txt"
    checker.write_text("from pathlib import Path\nassert Path('tour.txt').read_text() == 'Introduction'\n")
    artifact.write_text("Introduction")
    document.write_text(json.dumps({"outcome_id": "tour", "criteria": [{"id": "opening",
        "validation_argv": [sys.executable, "check.py"],
        "validation_files": [{"path": "check.py", "sha256": hashlib.sha256(checker.read_bytes()).hexdigest()}],
        "oracle": {"kind": "owner_pinned_oracle", "source_id": "tour-checker", "input_refs": [], "output_refs": ["tour.txt"]}}]}))
    code, configured = cli("configure", "--document", str(document), "--operation-id", "checks",
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"], "--execute")
    assert code == 0, configured
    _complete(run)
    code, verified = cli("verify", "--execute")
    assert code == 0, verified

    registry, runtime = project.parent / "registry.json", project.parent / "runtime"
    source_goal = json.loads(registry.read_text())["goals"][0]
    stale_project = project.parent / "stale-manager-checkout"
    stale_project.mkdir()
    (stale_project / "check.py").write_bytes(checker.read_bytes())
    (stale_project / "tour.txt").write_text("An obsolete checkout must not determine current acceptance")
    stale_goal = {**source_goal, "source_registry": str(registry), "repo": str(stale_project)}
    mirror = project.parent / "shared-registry.json"
    mirror.write_text(json.dumps({"registry_role": "global-local", "common_runtime_root": str(runtime), "goals": [stale_goal]}))
    source = active_state_todo_fields(source_goal, runtime_root=runtime, registry_path=registry)
    observed = active_state_todo_fields(stale_goal, runtime_root=runtime, registry_path=mirror)
    assert observed["agent_todos"]["goal_acceptance_contract"] == source["agent_todos"]["goal_acceptance_contract"], (
        "a shared manager must observe the current source Goal, even when its mirrored repo is stale")
    assert source["agent_todos"]["goal_acceptance_contract"]["status"] == "accepted"
    planning_reads = []
    load_planning = refresh.load_refresh_planning_source

    def capture_planning(*args, **kwargs):
        planning = load_planning(*args, **kwargs)
        planning_reads.append(planning.todo_fields["agent_todos"]["goal_acceptance_contract"])
        return planning

    monkeypatch.setattr(refresh, "load_refresh_planning_source", capture_planning)
    refresh.refresh_state_run(registry_path=mirror, runtime_root_override=str(runtime), goal_id=GOAL,
        project=None, state_file=None, classification="state_refreshed", recommended_action="Retain the accepted tour",
        agent_id=AGENT, dry_run=False, sync_global=False)
    assert len(planning_reads) == 1 and planning_reads[0]["criteria"][0]["oracle"]["state"] == "current", (
        "plain refresh, including outcome ACK planning, must use the same current source route")
    code, added = run("todo", "add", "--goal-id", GOAL, "--role", "agent", "--text", "Inspect the next iteration",
        "--claimed-by", AGENT, "--task-class", "advancement_task", "--action-kind", "implement",
        "--operation-id", "next-iteration")
    assert code == 0, added
    written = refresh.refresh_state_run(registry_path=mirror, runtime_root_override=str(runtime), goal_id=GOAL,
        project=None, state_file=None, classification="state_refreshed", recommended_action=None,
        next_action="Inspect the next iteration without changing the accepted tour", agent_id=AGENT,
        dry_run=False, sync_global=False)
    assert written["recommended_action_resolution"]["todo_id"] == added["todo_id"]
    assert len(planning_reads) == 3, "both initial planning and its guarded recheck use current source bytes"
    assert all(contract["criteria"][0]["oracle"]["state"] == "current" for contract in planning_reads)
    assert all(not contract["outcome_requirements"]["acceptance_gaps"] for contract in planning_reads)
