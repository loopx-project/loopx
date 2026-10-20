"""Actual feedback sources, one analysis Host, and original acceptance receipts."""
from __future__ import annotations

from hashlib import sha256
import json
import shlex
import sys

from loopx.control_plane.effect_runtime import restart_effect_runtime
import test_dependency_vision_wait_cli as dependency_fixtures
from test_dependency_vision_wait_cli import (
    AGENT, GOAL, PEER, REPO, add, assert_quiet, canonical_todos,
    guard, worker_args,
)

public_goal = dependency_fixtures.public_goal
isolated_effect = dependency_fixtures.isolated_effect

OPERATOR = "external-evaluator"

_VERIFY = '''import json
from pathlib import Path
feedback=json.loads(Path("current-feedback.json").read_text())
result=json.loads(Path("analysis.json").read_text())
assert (feedback["candidate_id"],feedback["operation_id"],feedback["feedback_id"])==("C1","O1","F2")
assert [row["case"] for row in feedback["results"]]==["case-a","case-b","case-c"]
deltas=[row["candidate_score"]-row["baseline_score"] for row in feedback["results"]]
assert result=={"candidate_id":"C1","operation_id":"O1","feedback_id":"F2",
    "covered_cases":[row["case"] for row in feedback["results"]],"score_deltas":deltas,
    "max_delta":max(deltas),"conclusion":"no_improvement_on_declared_cases",
    "global_optimality_proved":False,"stop_basis":"owner_authorized_finite_analysis_complete"}
assert max(deltas)<=0
'''

_HOST = '''import json,subprocess,sys
from hashlib import sha256
from pathlib import Path
from loopx.goal_mode_mcp import GoalModeMCPConfig,GoalModeMCPControlPlane
request=json.loads(Path(sys.argv[1]).read_text())
def count(kind):
    with Path(request["counts"]).open("a") as stream:
        stream.write(json.dumps({"kind":kind})+"\\n")
def cli(*args):
    result=subprocess.run([sys.executable,"-m","loopx.cli","--registry",request["registry"],
        "--runtime-root",request["runtime"],"--format","json",*args],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)
count("dispatch")
control=GoalModeMCPControlPlane(
    GoalModeMCPConfig(server_name="feedback-fixture",runtime_profile="generic_cli",legacy_host_surface="claude_code"),
    lambda:{"goal_id":request["goal_id"],"agent_id":request["agent_id"],"registry":request["registry"]})
control.command_prefix=lambda:[sys.executable,"-m","loopx.cli","--runtime-root",request["runtime"]]
admission=json.loads(control.should_run())
assert admission["should_run"] is True,admission
assert admission["interaction_contract"]["agent_channel"]["must_attempt"] is True,admission
assert admission["selected_todo"]["todo_id"]==request["todo_id"],admission
source=cli("todo","list","--goal-id",request["goal_id"],"--todo-id",request["source_id"])["todo"]
data=Path(request["feedback"]).read_bytes()
assert source["result_hash"]==sha256(data).hexdigest()
assert source["target_key"]=="candidate:C1:operation:O1"
count("model_boundary")
# This deterministic model substitute performs the requested finite analysis.
feedback=json.loads(data)
assert (feedback["candidate_id"],feedback["operation_id"],feedback["feedback_id"])==("C1","O1","F2")
deltas=[row["candidate_score"]-row["baseline_score"] for row in feedback["results"]]
assert max(deltas)<=0
result={"candidate_id":"C1","operation_id":"O1","feedback_id":"F2",
    "covered_cases":[row["case"] for row in feedback["results"]],"score_deltas":deltas,
    "max_delta":max(deltas),"conclusion":"no_improvement_on_declared_cases",
    "global_optimality_proved":False,"stop_basis":"owner_authorized_finite_analysis_complete"}
artifact=(json.dumps(result,sort_keys=True)+"\\n").encode()
Path(request["artifact"]).write_bytes(artifact)
evidence="fixture:finite-feedback-analysis-sha256:"+sha256(artifact).hexdigest()
vision={"schema_version":"goal_vision_replan_contract_v0","state":"no_followup",
    "vision_patch":{"vision_summary":"Analyze the current C1/O1 feedback once.",
        "acceptance_summary":"Report the declared finite cases and preserve unproved global claims.",
        "last_patch_summary":"The owner-authorized finite analysis is complete."},
    "path_delta":{"schema_version":"goal_path_delta_v0","outcome":"no_change",
        "prior_assumption":"The current evaluation still required analysis.",
        "observed_reality":"The declared cases show no improvement; global optimality remains unproved.",
        "evidence_refs":[evidence],"stopped":["The requested finite analysis is complete; no new experiment was authorized."]}}
# The original external wait retains its already-completed O1 successor.
# Goal-level no-follow-up must not overwrite that existing Todo relationship.
completion=json.loads(control.complete_task(request["todo_id"],request["agent_id"],evidence,
    agent_vision=vision))
Path(request["receipt"]).write_text(json.dumps({"completion":completion,"evidence":evidence,"vision":vision}))
assert completion["ok"] is True,completion
print(json.dumps({"ok":True,"completed_todo_id":request["todo_id"]}))
'''


def _change(goal, todo_id, agent, *options):
    revision = goal.call("todo", "list", "--goal-id", GOAL)["authority_read"]["provider_revision"]
    return goal.call("todo", "update", "--goal-id", GOAL, "--todo-id", todo_id, "--agent-id", agent,
        "--update-operation-id", f"update-{revision}", "--update-expected-provider-revision", revision, *options)


def _monitor(goal, candidate, operation, due):
    return add(goal, f"Observe candidate {candidate} evaluation {operation} feedback.",
        "--task-class", "continuous_monitor", "--action-kind", "observe", "--watch-only",
        "--target-key", f"candidate:{candidate}:operation:{operation}", "--cadence", "1h",
        "--next-due-at", due, "--expires-at", "2100-01-01T00:00:00Z", owner=PEER)


def _observe(goal, monitor, candidate, operation, path, turn):
    binding = ("--goal-id", GOAL, "--agent-id", PEER, "--turn-instance-id", turn)
    admitted = goal.call("quota", "should-run", "--codex-app", *binding)
    if admitted["heartbeat_receipt"]["status"] != "replayed":
        assert admitted["selected_todo"]["todo_id"] == monitor, admitted
    return goal.call("quota", "monitor-poll", "--execute", "--codex-app", *binding,
        "--todo-id", monitor, "--target-key", f"candidate:{candidate}:operation:{operation}",
        "--result-hash", sha256(path.read_bytes()).hexdigest(), "--material-change",
        "--next-due-at", "2099-01-01T00:00:00Z")


def test_current_feedback_is_adopted_and_analyzed_once_with_old_feedback_retained(public_goal, monkeypatch):
    goal = public_goal
    project = goal.root / "project"
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    goal.call("configure-goal", "--goal-id", GOAL, "--registered-agent", AGENT,
        "--registered-agent", PEER, "--registered-agent", OPERATOR, "--execute")
    old_source = _monitor(goal, "C0", "O0", "2026-01-01T00:00:00Z")
    source = _monitor(goal, "C1", "O1", "2099-01-01T00:00:00Z")
    operation = add(goal, "Retain the result of evaluation O1 for candidate C1.",
        "--task-class", "advancement_task", "--action-kind", "validate", owner=OPERATOR)
    analysis = add(goal, "Analyze current C1/O1 feedback over exactly the three declared cases; retain unproved global claims.",
        "--task-class", "advancement_task", "--action-kind", "validate")
    _change(goal, analysis, AGENT, "--resume-when", f"monitor_changed:{source}", "--successor-todo-id", operation)
    vision = project / "initial-vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_drift_detected",
        "todo_delta": [f"activate:{analysis}"], "vision_patch": {
            "acceptance_summary": "Analyze current C1/O1 feedback over the three declared cases.",
            "advancement_policy": "as_needed", "replan_trigger_summary": "Current feedback still requires its declared analysis."}}))
    goal.call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--classification", "feedback_fixture",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    verifier = project / "verify_analysis.py"
    verifier.write_text(_VERIFY)
    document = project / "acceptance.json"
    document.write_text(json.dumps({"scope": {"kind": "selected_work", "todo_ids": [analysis]},
        "objective": "Analyze C1/O1 feedback once and stop after reporting the finite evidence and remaining unproved claims.",
        "non_goals": ["No new experiment or global optimality proof is requested."],
        "criteria": [{"id": "finite-feedback-analysis", "description": "Compute every declared case delta and preserve the bounded conclusion.",
            "validation_argv": [sys.executable, "verify_analysis.py"], "validation_timeout_seconds": 5,
            "validation_files": [{"path": "verify_analysis.py", "sha256": sha256(verifier.read_bytes()).hexdigest()}]}],
        "bindings": [{"todo_id": analysis, "criterion_ids": ["finite-feedback-analysis"]}]}))
    basis = goal.call("goal-acceptance", "inspect", "--goal-id", GOAL)["provider_revision"]
    configured = goal.call("goal-acceptance", "configure", "--goal-id", GOAL, "--document", str(document),
        "--expected-provider-revision", basis, "--operation-id", "confirm-current-feedback-analysis", "--execute")
    assert configured["goal_acceptance_contract"]["enabled"] is True
    assert_quiet(guard(goal), analysis)

    old_feedback = project / "old-feedback.json"
    old_feedback.write_text(json.dumps({"candidate_id": "C0", "operation_id": "O0", "feedback_id": "F0", "score": 100}))
    old = _observe(goal, old_source, "C0", "O0", old_feedback, "feedback-C0-O0-F0")
    assert old["replayed"] is False
    assert _observe(goal, old_source, "C0", "O0", old_feedback, "feedback-C0-O0-F0")["replayed"] is True
    current = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", source)["todo"]
    assert current.get("material_change_generation", 0) == 0
    assert current.get("result_hash") is None
    assert_quiet(guard(goal), analysis)
    retained_old = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", old_source)["todo"]
    assert retained_old["result_hash"] == sha256(old_feedback.read_bytes()).hexdigest()

    host, request = project / "fake-feedback-host.py", project / "host-request.json"
    host.write_text(_HOST)
    counts, artifact, receipt = (project / name for name in ("host-counts.jsonl", "analysis.json", "analysis-receipt.json"))
    feedback = project / "current-feedback.json"
    request.write_text(json.dumps({"goal_id": GOAL, "agent_id": AGENT, "registry": str(goal.registry),
        "runtime": str(goal.runtime), "source_id": source, "todo_id": analysis, "feedback": str(feedback),
        "artifact": str(artifact), "counts": str(counts), "receipt": str(receipt)}))
    launcher = project / "loopx-cli"
    launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.cli"]) + ' "$@"\n')
    launcher.chmod(0o700)
    args = worker_args(goal)
    args.cli_bin = str(launcher)
    host_error = project / "host-error.txt"
    args.wake_cmd = shlex.join([sys.executable, str(host), str(request)]) + " 2>" + shlex.quote(str(host_error))
    args.wake_timeout_seconds = 180
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import external_scheduler_worker as worker
    assert worker.run_worker(args) == 0, host_error.read_text() if host_error.exists() else "no Host error output"
    restart_effect_runtime()
    assert worker.run_worker(args) == 0, host_error.read_text() if host_error.exists() else "no Host error output"
    assert not counts.exists() and not artifact.exists()

    feedback.write_text(json.dumps({"candidate_id": "C1", "operation_id": "O1", "feedback_id": "F2", "results": [
        {"case": "case-a", "baseline_score": 10, "candidate_score": 10},
        {"case": "case-b", "baseline_score": 20, "candidate_score": 19},
        {"case": "case-c", "baseline_score": 30, "candidate_score": 28}]}))
    _change(goal, source, PEER, "--next-due-at", "2026-01-01T00:00:00Z")
    adopted = _observe(goal, source, "C1", "O1", feedback, "feedback-C1-O1-F2")
    assert adopted["replayed"] is False and adopted["material_change"] is True
    assert _observe(goal, source, "C1", "O1", feedback, "feedback-C1-O1-F2")["replayed"] is True
    goal.call("todo", "complete", "--goal-id", GOAL, "--todo-id", operation, "--agent-id", OPERATOR,
        "--no-follow-up", "--evidence", "fixture:feedback-F2-sha256:" + sha256(feedback.read_bytes()).hexdigest())
    ready = guard(goal)
    assert ready["should_run"] is True and ready["selected_todo"]["todo_id"] == analysis
    assert ready["effective_action"] != "autonomous_replan_required"
    assert worker.run_worker(args) == 0, host_error.read_text() if host_error.exists() else "no Host error output"
    result = json.loads(artifact.read_text())
    assert result["score_deltas"] == [0, -1, -2]
    assert result["global_optimality_proved"] is False
    completed = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", analysis)
    assert completed["todo"]["status"] == "done"
    assert completed["todo"]["successor_todo_ids"] == [operation]
    assert completed["todo"]["completion_continuation"] == "successor"
    assert json.loads(receipt.read_text())["completion"]["ok"] is True
    verified = goal.call("goal-acceptance", "verify", "--goal-id", GOAL, "--agent-id", AGENT, "--execute")
    assert verified["checks_passed"] is True
    assert verified["goal_acceptance_contract"]["status"] == "accepted"

    before_replay = goal.call("todo", "list", "--goal-id", GOAL)
    settled = guard(goal)
    (goal.root / "post-analysis-readback.json").write_text(json.dumps({"quota": settled,
        "todos": before_replay, "acceptance": verified}, ensure_ascii=False, indent=2))
    assert settled["should_run"] is False, json.dumps({key: settled.get(key) for key in (
        "effective_action", "agent_lane_next_action", "goal_frontier_projection", "interaction_contract")})
    for _ in range(2):
        assert _observe(goal, source, "C1", "O1", feedback, "feedback-C1-O1-F2")["replayed"] is True
        restart_effect_runtime()
        assert worker.run_worker(args) == 0
    after = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(after) == canonical_todos(before_replay)
    assert after["authority_read"]["provider_revision"] == before_replay["authority_read"]["provider_revision"]
    final = guard(goal)
    assert final["should_run"] is False
    assert final["interaction_contract"]["agent_channel"]["must_attempt"] is False
    # This accepted selected-work scope stops an empty analysis dispatch. The
    # retained successor and other agents' monitors do not certify Goal closure.
    assert final["goal_frontier_projection"].get("terminal_state") is None
    acceptance = goal.call("goal-acceptance", "inspect", "--goal-id", GOAL)["goal_acceptance_contract"]
    assert acceptance["status"] == "accepted"
    assert acceptance["scope"] == {"kind": "selected_work", "todo_ids": [analysis]}
    assert [json.loads(line)["kind"] for line in counts.read_text().splitlines()] == ["dispatch", "model_boundary"]
    runs = [json.loads(line) for line in (goal.runtime / f"goals/{GOAL}/runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" and row.get("todo_id") == analysis for row in runs) == 1
    assert sum(row.get("classification") == "quota_monitor_poll" and row.get("turn_instance_id") == "feedback-C1-O1-F2"
               for row in runs) == 1
