"""Actual work after local gates and dated waits, using public local providers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import shlex
import sys
from types import SimpleNamespace

import pytest

from loopx.control_plane.effect_runtime import restart_effect_runtime
import test_dependency_vision_wait_cli as dependency_fixtures
from test_dependency_vision_wait_cli import (
    AGENT, GOAL, REPO, add, canonical_todos, guard, update, wait_on, worker_args,
)

public_goal = dependency_fixtures.public_goal
isolated_effect = dependency_fixtures.isolated_effect

_RESULT = {"input": [1, 2, 3], "squares": [1, 4, 9], "sum": 14}

_HOST = '''import json,subprocess,sys
from hashlib import sha256
from pathlib import Path
from loopx.goal_mode_mcp import GoalModeMCPConfig,GoalModeMCPControlPlane
request=json.loads(Path(sys.argv[1]).read_text())
def count(kind):
    with Path(request["counts"]).open("a") as stream:
        stream.write(json.dumps({"kind":kind})+"\\n")
def cli(*args):
    process=subprocess.run([sys.executable,"-m","loopx.cli","--registry",request["registry"],
        "--runtime-root",request["runtime"],"--format","json",*args],capture_output=True,text=True,check=True)
    return json.loads(process.stdout)
count("dispatch")
control=GoalModeMCPControlPlane(
    GoalModeMCPConfig(server_name="dependent-dispatch",runtime_profile="generic_cli",legacy_host_surface="claude_code"),
    lambda:{"goal_id":request["goal_id"],"agent_id":request["agent_id"],"registry":request["registry"]})
control.command_prefix=lambda:[sys.executable,"-m","loopx.cli","--runtime-root",request["runtime"]]
admission=json.loads(control.should_run())
assert admission["should_run"] is True,admission
assert admission["interaction_contract"]["agent_channel"]["must_attempt"] is True,admission
assert admission["selected_todo"]["todo_id"]==request["todo_id"],admission
if admission["effective_action"]=="successor_replan_required":
    current=cli("todo","list","--goal-id",request["goal_id"],"--todo-id",request["todo_id"])["todo"]
    assert current["status"]=="deferred" and current["resume_condition"]["kind"]=="resume_at",current
    assert current["resume_condition"]["satisfied"] is True,current
    revision=cli("todo","list","--goal-id",request["goal_id"])["authority_read"]["provider_revision"]
    cli("todo","update","--goal-id",request["goal_id"],"--agent-id",request["agent_id"],
        "--todo-id",request["todo_id"],"--status","open","--clear-resume-when",
        "--update-operation-id","activate-original-dated-work","--update-expected-provider-revision",revision)
    admission=json.loads(control.should_run())
    assert admission["should_run"] is True and admission["selected_todo"]["todo_id"]==request["todo_id"],admission
count("model_boundary")
data=json.loads(Path(request["input"]).read_text())
squares=[item*item for item in data]
result={"input":data,"squares":squares,"sum":sum(squares)}
artifact=(json.dumps(result,sort_keys=True)+"\\n").encode()
Path(request["artifact"]).write_bytes(artifact)
evidence="fixture:sum-of-squares-sha256:"+sha256(artifact).hexdigest()
vision={"schema_version":"goal_vision_replan_contract_v0","state":"no_followup",
    "vision_patch":{"vision_summary":"Retain the requested work and each independent dependency.",
        "acceptance_summary":"Compute the sum of squares for this task's declared input.",
        "last_patch_summary":"This finite calculation passed its original validator."},
    "path_delta":{"schema_version":"goal_path_delta_v0","outcome":"no_change",
        "prior_assumption":"The selected finite calculation still required execution.",
        "observed_reality":"The declared inputs were processed and the actual result was validated.",
        "evidence_refs":[evidence],"stopped":["This task is complete; other retained work keeps its own requirements."]}}
completion=json.loads(control.complete_task(request["todo_id"],request["agent_id"],evidence,
    no_follow_up=True,agent_vision=vision))
Path(request["receipt"]).write_text(json.dumps(completion))
assert completion["ok"] is True,completion
print(json.dumps({"ok":True,"completed_todo_id":request["todo_id"]}))
'''


def _finite_work(goal, *, deferred=False):
    project = goal.root / "project"
    artifact = project / "calculation.json"
    validator = [sys.executable, "-c",
        "import json,pathlib,sys; assert json.loads(pathlib.Path(sys.argv[1]).read_text()) == " + repr(_RESULT),
        str(artifact)]
    options = ["--task-class", "advancement_task", "--action-kind", "validate",
               "--validation-command-json", json.dumps(validator)]
    if deferred:
        options += ["--status", "deferred", "--resume-when", "resume_at:2026-01-01T00:10:00Z"]
    return add(goal, "Compute the sum of squares for the declared input [1, 2, 3].", *options)


def _host(goal, todo_id, monkeypatch):
    project = goal.root / "project"
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    input_path, artifact, counts, receipt, error = (project / name for name in
        ("input.json", "calculation.json", "host-counts.jsonl", "host-receipt.json", "host-error.txt"))
    input_path.write_text(json.dumps([1, 2, 3]))
    host, request = project / "fake-calculation-host.py", project / "host-request.json"
    host.write_text(_HOST)
    request.write_text(json.dumps({"registry": str(goal.registry), "runtime": str(goal.runtime),
        "goal_id": GOAL, "agent_id": AGENT, "todo_id": todo_id, "input": str(input_path),
        "artifact": str(artifact), "counts": str(counts), "receipt": str(receipt)}))
    launcher = project / "loopx-cli"
    launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.cli"]) + ' "$@"\n')
    launcher.chmod(0o700)
    args = worker_args(goal)
    args.cli_bin = str(launcher)
    args.wake_cmd = shlex.join([sys.executable, str(host), str(request)]) + " 2>" + shlex.quote(str(error))
    args.wake_timeout_seconds = 180
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import external_scheduler_worker as worker

    def run():
        assert worker.run_worker(args) == 0, error.read_text() if error.exists() else "no Host error output"

    return SimpleNamespace(worker=worker, args=args, run=run, artifact=artifact, counts=counts, receipt=receipt)


def _gate(goal, target=None):
    return goal.call("todo", "add", "--goal-id", GOAL, "--role", "user", "--task-class", "user_gate",
        "--text", f"Approve access to {target or 'the unspecified dependent work'}.",
        "--blocks-agent", AGENT, "--agent-id", AGENT,
        *(("--unblocks-todo-id", target) if target else ()))["todo_id"]


def _single_execution(goal, host, todo_id):
    assert json.loads(host.artifact.read_text()) == _RESULT
    assert json.loads(host.receipt.read_text())["ok"] is True
    assert [json.loads(line)["kind"] for line in host.counts.read_text().splitlines()] == ["dispatch", "model_boundary"]
    rows = [json.loads(line) for line in (goal.runtime / f"goals/{GOAL}/runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" and row.get("todo_id") == todo_id for row in rows) == 1
    current = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", todo_id)["todo"]
    assert current["status"] == "done"


def test_local_wait_and_user_gates_preserve_only_verified_independent_work(public_goal, monkeypatch):
    goal = public_goal
    _, waiting = wait_on(goal, "todo_done")
    independent = _finite_work(goal)
    gated_work = add(goal, "Validate the separate artifact after owner approval.",
        "--task-class", "advancement_task", "--action-kind", "validate")
    gate = _gate(goal)
    _gate(goal, gated_work)
    monitor = add(goal, "Observe the result that needs separate owner approval.",
        "--task-class", "continuous_monitor", "--action-kind", "observe", "--watch-only",
        "--target-key", "owner-protected-result", "--cadence", "1h", "--next-due-at", "2026-01-01T00:00:00Z")
    _gate(goal, monitor)
    host = _host(goal, independent, monkeypatch)

    unknown = guard(goal)
    assert unknown["requires_user_action"] is True and unknown["should_run"] is False
    assert unknown.get("scoped_user_gate_fallback") is None
    host.run()
    assert not host.counts.exists()

    update(goal, gate, "--unblocks-todo-id", waiting)
    fresh = guard(goal)
    assert fresh["should_run"] is True and fresh["requires_user_action"] is True
    assert fresh["selected_todo"]["todo_id"] == independent
    assert fresh["safe_bypass_kind"] == "scoped_user_gate_fallback"
    assert fresh["normal_delivery_allowed"] is False

    # The real Goal's compute=0 contract outranks its still-present exact gate
    # and runnable independent work, including after a worker restart.
    goal.call("configure-goal", "--goal-id", GOAL, "--quota-compute", "0", "--execute")
    paused_source = goal.call("todo", "list", "--goal-id", GOAL)
    for _ in range(2):
        paused = guard(goal)
        assert paused["quota"]["compute"] == 0 and paused["state"] == "paused"
        assert paused["should_run"] is False and paused["safe_bypass_allowed"] is False
        assert paused["interaction_contract"]["agent_channel"]["must_attempt"] is False
        assert paused["scheduler_hint"]["action"] == "stop_until_explicit_resume"
        host.run()
        restart_effect_runtime()
    after_pause = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(after_pause) == canonical_todos(paused_source)
    assert after_pause["authority_read"]["provider_revision"] == paused_source["authority_read"]["provider_revision"]
    assert not host.counts.exists() and not host.artifact.exists()
    rows = [json.loads(line) for line in (goal.runtime / f"goals/{GOAL}/runs/index.jsonl").read_text().splitlines()]
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)
    goal.call("configure-goal", "--goal-id", GOAL, "--quota-compute", "1", "--execute")
    assert guard(goal)["selected_todo"]["todo_id"] == independent

    before = goal.call("todo", "list", "--goal-id", GOAL)
    refused = goal.call("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--runtime-profile", "generic_cli", "--todo-id", gated_work,
        "--turn-instance-id", "refused-gated-selection", expected_exit=1)
    assert refused["ok"] is False and refused["should_run"] is False
    assert refused["safe_bypass_allowed"] is False
    assert refused["action_selection_qualification"]["state"] == "deferred"
    assert refused["interaction_contract"]["agent_channel"]["must_attempt"] is False
    denied = goal.call("quota", "monitor-poll", "--execute", "--goal-id", GOAL, "--agent-id", AGENT,
        "--runtime-profile", "generic_cli", "--todo-id", monitor, "--target-key", "owner-protected-result",
        "--result-hash", "unauthorized-result", "--material-change", expected_exit=1)
    assert denied["error_code"] == "monitor_poll_admission_rejected", denied
    after = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(after) == canonical_todos(before)
    assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]

    host.run()
    _single_execution(goal, host, independent)
    retained = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", waiting)["todo"]
    assert retained["status"] == "deferred" and retained["resume_condition"]["satisfied"] is False
    blocked_monitor = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", monitor)["todo"]
    assert blocked_monitor.get("result_hash") is None
    restart_effect_runtime()
    host.run()
    _single_execution(goal, host, independent)

    next_work = add(goal, "Perform the next independent calculation only while the Goal is active.",
        "--task-class", "advancement_task", "--action-kind", "validate")
    assert guard(goal)["selected_todo"]["todo_id"] == next_work
    goal.call("goal-lifecycle", "--goal-id", GOAL, "--operation", "stop", "--actor-kind", "owner",
        "--reason", "Stop all work in this isolated fixture.", "--execute")
    for _ in range(2):
        restart_effect_runtime()
        stopped = guard(goal)
        assert stopped["should_run"] is False
        assert stopped["scheduler_hint"]["action"] == "stop_until_explicit_resume"
        host.run()
    _single_execution(goal, host, independent)


@pytest.mark.parametrize("owner_gate", [False, True])
def test_dated_wait_rechecks_current_readiness_and_dispatches_original_work_once(public_goal, monkeypatch, owner_gate):
    from loopx.control_plane.todos import resume_condition

    goal = public_goal
    waiting = _finite_work(goal, deferred=True)
    clock = {"seconds": 0}
    original = resume_condition.build_todo_resume_evaluation_request

    def evaluated(*args, **kwargs):
        instant = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=clock["seconds"])
        kwargs["evaluated_at"] = instant.isoformat().replace("+00:00", "Z")
        return original(*args, **kwargs)

    monkeypatch.setattr(resume_condition, "build_todo_resume_evaluation_request", evaluated)
    vision = goal.root / "dated-vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_drift_detected",
        "todo_delta": [f"activate:{waiting}"], "vision_patch": {"advancement_policy": "as_needed",
            "acceptance_summary": "Compute the finite declared calculation when its registered time arrives.",
            "replan_trigger_summary": "The dated calculation remains required."}}))
    goal.call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--classification", "dated_fixture",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    if owner_gate:
        _gate(goal, waiting)
    host = _host(goal, waiting, monkeypatch)

    def probe(command, **kwargs):
        # Only the non-model clock is injected; each poll reloads canonical
        # state through the actual public CLI and original resume evaluator.
        return goal.call(*command[command.index("quota"):])

    monkeypatch.setattr(host.worker, "run_quota_should_run", probe)
    before = goal.call("todo", "list", "--goal-id", GOAL)
    for seconds in (0, 300, 599):
        clock["seconds"] = seconds
        packet = guard(goal)
        assert packet["should_run"] is False
        host.run()
    assert not host.counts.exists()
    after_wait = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(after_wait) == canonical_todos(before)
    assert after_wait["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]

    clock["seconds"] = 600
    due = guard(goal)
    current = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", waiting)["todo"]
    assert current["resume_when"] == "resume_at:2026-01-01T00:10:00Z"
    assert current["resume_condition"]["satisfied"] is True
    (goal.root / "due-readback.json").write_text(json.dumps({"quota": due, "todo": current}, indent=2))
    if owner_gate:
        assert due["should_run"] is False and due["requires_user_action"] is True, json.dumps({key: due.get(key)
            for key in ("effective_action", "requires_user_action", "safe_bypass_kind", "selected_todo",
                        "scoped_user_gate_fallback", "goal_frontier_projection", "interaction_contract")})
    else:
        assert due["should_run"] is True and due["selected_todo"]["todo_id"] == waiting
    host.run()
    for seconds in (601, 900, 1200):
        clock["seconds"] = seconds
        restart_effect_runtime()
        host.run()
    if owner_gate:
        assert not host.counts.exists() and not host.artifact.exists()
        after = goal.call("todo", "list", "--goal-id", GOAL)
        assert canonical_todos(after) == canonical_todos(before)
        assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
    else:
        _single_execution(goal, host, waiting)
        assert guard(goal)["interaction_contract"]["mode"] == "terminal_no_followup"


def test_prose_time_cannot_create_a_dated_automatic_wait(public_goal):
    goal = public_goal
    before = goal.call("todo", "list", "--goal-id", GOAL)
    rejected = goal.call("todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--claimed-by", AGENT, "--task-class", "advancement_task", "--status", "deferred",
        "--text", "Resume this task ten minutes from now.", expected_exit=1)
    assert "requires --resume-when" in json.dumps(rejected), rejected
    after = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(after) == canonical_todos(before)
    assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
