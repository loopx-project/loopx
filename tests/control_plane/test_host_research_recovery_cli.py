"""Original research results recover before a new review reaches a real Host.

The model is a deterministic local child. CLI planning, canonical providers,
the original journal, writeback and quota remain production implementations.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from loopx.cli import main as cli_main
from loopx.cli_commands import turn_run_once as turn_command
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from tests import test_loopx_turn_driver as turn_fixture
from tests.test_external_scheduler_worker import worker

GOAL = "loopx-turn-fixture"
AGENT = "codex-fixture"
TODO = "todo_fixture0001"
ROOT = Path(__file__).resolve().parents[2]


def _cli(registry, runtime, *args, code=0):
    completed = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=90)
    result = json.loads(completed.stdout)
    assert completed.returncode == code, json.dumps(result, ensure_ascii=False)
    return result


def _control(registry, runtime):
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="research-recovery", runtime_profile="generic_cli",
                         legacy_host_surface="claude_code"),
        lambda: {"goal_id": GOAL, "agent_id": AGENT, "registry": str(registry)},
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli", "--runtime-root", str(runtime)]
    return control


def _in_process(argv, code=0):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        observed = cli_main(argv)
    result = json.loads(output.getvalue())
    assert observed == code, json.dumps(result, ensure_ascii=False)
    return result


def _spends(index):
    return [row for line in index.read_text().splitlines()
            if (row := json.loads(line)).get("classification") == "quota_slot_spent"]


def _worker_args(project, runtime, registry, script, request):
    launcher = project / "fixture-loopx"
    launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.cli"]) + ' "$@"\n')
    launcher.chmod(0o700)
    return argparse.Namespace(cli_bin=str(launcher), registry=str(registry), runtime_root=str(runtime),
        runtime_profile="generic_cli", goal_id=GOAL, agent_id=AGENT,
        state_file=str(project / "worker-state.json"), once=True,
        wake_cmd=shlex.join([sys.executable, str(script), str(request)]),
        error_backoff_seconds=5, quota_timeout_seconds=90, wake_timeout_seconds=120)


_REVIEW_HOST = r'''import json,sys
from hashlib import sha256
from pathlib import Path
import subprocess
request=json.loads(Path(sys.argv[1]).read_text())
def cli(*args):
    result=subprocess.run([sys.executable,"-m","loopx.cli","--registry",request["registry"],
        "--runtime-root",request["runtime"],"--format","json",*args],capture_output=True,text=True,timeout=60)
    payload=json.loads(result.stdout)
    if result.returncode:
        Path(request["result"]).write_text(json.dumps({"failed_command":args,"result":payload}))
    assert result.returncode==0,payload
    return payload
guard=cli("quota","should-run","--runtime-profile","generic_cli","--goal-id",request["goal"],
          "--agent-id",request["agent"],"--turn-instance-id",request["review_turn"])
assert guard["effective_action"]=="autonomous_replan_required",guard
identity=guard["heartbeat_receipt"]["settlement_identity"]
assert identity["replan_obligation_id"]==request["obligation"],guard
with Path(request["counts"]).open("a") as stream:
    stream.write(json.dumps({"work":"R2","D":1,"P":1})+"\n")
original=Path(request["original"])
assert original.read_text()=="completed"
artifact=Path(request["artifact"])
artifact.write_text(json.dumps({"review":"R2","original_sha256":sha256(original.read_bytes()).hexdigest(),
                                "adopted_original":True})+"\n")
evidence="fixture:review:"+sha256(artifact.read_bytes()).hexdigest()
vision={"schema_version":"goal_vision_replan_contract_v0","state":"no_followup",
  "vision_patch":{"vision_summary":"Complete and independently read back the finite original probe.",
                  "acceptance_summary":"The original artifact was verified and its result was recovered."},
  "path_delta":{"schema_version":"goal_path_delta_v0","outcome":"stop",
                "prior_assumption":"The completed original probe still required a current review.",
                "observed_reality":"The existing original artifact passed the independently repeated readback.",
                "retained":["The original artifact and its operation identity."],"evidence_refs":[evidence]}}
vision_path=artifact.with_suffix(".vision.json")
vision_path.write_text(json.dumps(vision))
binding=["--goal-id",request["goal"],"--agent-id",request["agent"],
         "--turn-instance-id",identity["turn_instance_id"],"--replan-obligation-id",identity["replan_obligation_id"]]
refreshed=cli("refresh-state",*binding,"--classification","bounded_research_review",
    "--delivery-batch-scale","implementation","--delivery-outcome","outcome_progress",
    "--agent-vision-json",str(vision_path),"--progress-result-class","no_followup",
    "--progress-coverage-scope-id","original-probe","--progress-coverage-complete",
    "--progress-evidence-id",evidence,"--no-global-sync","--suppress-external-sinks")
assert refreshed["settlement_progress"]["state"]=="spend_required",refreshed
spent=cli("quota","spend-slot",*binding,"--slots","1","--source","heartbeat","--execute")
assert spent["settlement_progress"]["state"]=="settled",spent
Path(request["result"]).write_text(json.dumps({"identity":identity,"refreshed":refreshed,"spent":spent}))
'''


_HEARTBEAT_HOST = r'''import json,sys
from hashlib import sha256
from pathlib import Path
from loopx.goal_mode_mcp import GoalModeMCPConfig,GoalModeMCPControlPlane
request=json.loads(Path(sys.argv[1]).read_text())
control=GoalModeMCPControlPlane(GoalModeMCPConfig(server_name="original-heartbeat",
    runtime_profile="generic_cli",legacy_host_surface="claude_code"),
    lambda:{"goal_id":request["goal"],"agent_id":request["agent"],"registry":request["registry"]})
control.command_prefix=lambda:[sys.executable,"-m","loopx.cli","--runtime-root",request["runtime"]]
original_run_cli=control.run_cli
def observed_cli(*args,**kwargs):
    raw=original_run_cli(*args,**kwargs)
    with Path(request["result"]+".trace.jsonl").open("a") as stream:
        stream.write(json.dumps({"args":args,"result":json.loads(raw)})+"\n")
    return raw
control.run_cli=observed_cli
guard=json.loads(control.should_run())
assert guard["should_run"] and guard["selected_todo"]["todo_id"]==request["todo"],guard
with Path("host-counts.jsonl").open("a") as stream:
    stream.write(json.dumps({"work":"O3","D":1,"P":1})+"\n")
artifact=Path("completion-artifact.txt")
artifact.write_text("completed")
evidence="fixture:original:"+sha256(artifact.read_bytes()).hexdigest()
completed=json.loads(control.complete_task(request["todo"],request["agent"],evidence,no_follow_up=True))
Path(request["result"]).write_text(json.dumps({"completion":completed,"evidence":evidence}))
assert completed["ok"] is True,completed
# The provider accepted the result. Only the outer Host response is lost.
raise SystemExit(1)
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["governed", "heartbeat"])
def test_original_journal_recovers_before_review_host_once(tmp_path, monkeypatch, provider, mode):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = turn_fixture._write_live_fixture(
        tmp_path, provider=provider, todo_metadata_extra="no_followup=true" if mode == "governed" else "")
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    state = project / f".codex/goals/{GOAL}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text() + "\n## Objective\nComplete and independently read back the finite original probe.\n\n## User Todo / Owner Review Reading Queue\n")
    _cli(registry, runtime, "configure-goal", "--goal-id", GOAL,
         "--execution-replan-after-todos", "5", "--execute")
    index = runtime / "goals" / GOAL / "runs/index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    # The nineteenth prior work result is fixture setup, before O3 admission.
    evidence = project / "prior-results.json"
    evidence.write_text(json.dumps({"accepted_prior_results": 19}))
    report = project / "prior-results.md"
    report.write_text("Nineteen prior bounded work results were accepted.\n")
    index.write_text("".join(json.dumps({"goal_id": GOAL, "agent_id": AGENT,
        "classification": "evidence_validated", "delivery_outcome": "outcome_progress",
        "turn_instance_id": f"prior-work-{i}", "generated_at": f"2026-08-01T00:{i:02d}:00Z",
        "json_path": str(evidence), "markdown_path": str(report)}) + "\n" for i in range(19)))
    before = _cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
                  "--goal-id", GOAL, "--agent-id", AGENT)
    assert before["selected_todo"]["todo_id"] == TODO
    assert before.get("autonomous_replan_obligation") is None
    workspace = project / "host-workspace"
    workspace.mkdir()
    host, validate = turn_fixture._completion_host_and_validation_scripts()
    host = host.replace('request = json.load(sys.stdin)',
        'request = json.load(sys.stdin)\nwith pathlib.Path("host-counts.jsonl").open("a") as stream:\n    stream.write(json.dumps({"work":"O3","D":1,"P":1})+"\\n")')
    argv = turn_fixture._turn_run_once_completion_argv(workspace, runtime, registry, host, validate)
    real_append = turn_command.append_cli_rollout_event

    def lost_ack(payload, **kwargs):
        if kwargs.get("event_kind") == "quota_spend":
            raise OSError("fixture lost original completion ACK")
        return real_append(payload, **kwargs)

    if mode == "governed":
        with monkeypatch.context() as fault:
            fault.setattr(turn_command, "append_cli_rollout_event", lost_ack)
            first = _in_process(argv, code=1)
        assert first["error"] == "fixture lost original completion ACK"
        journal = turn_fixture._turn_journal(runtime)
        turn_key = journal["turn_key"]

        def recover_original():
            replay = _in_process([*argv[:-1], "--resume-turn-key", turn_key, "--execute"])
            assert replay["status"] == "committed" and replay["effects"]["host_invoked"] is False
    else:
        original_host, original_request, original_result = (
            project / "original-host.py", project / "original-request.json", project / "original-result.json")
        original_host.write_text(_HEARTBEAT_HOST)
        original_request.write_text(json.dumps({"registry": str(registry), "runtime": str(runtime),
            "goal": GOAL, "agent": AGENT, "todo": TODO, "result": str(original_result)}))
        process = subprocess.run([sys.executable, str(original_host), str(original_request)],
            cwd=workspace, capture_output=True, text=True, timeout=120)
        assert process.returncode == 1
        original = json.loads(original_result.read_text())
        assert original["completion"]["ok"] is True, json.dumps(original, ensure_ascii=False)
        control = _control(registry, runtime)

        def recover_original():
            replay = json.loads(control.complete_task(TODO, AGENT, original["evidence"], no_follow_up=True))
            assert replay["ok"] is True, replay
    original_bytes = (workspace / "completion-artifact.txt").read_bytes()
    counts = workspace / "host-counts.jsonl"
    assert len(counts.read_text().splitlines()) == 1
    assert len(_spends(index)) == 1
    current = _cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
                   "--goal-id", GOAL, "--agent-id", AGENT)
    assert current["effective_action"] == "autonomous_replan_required", current
    obligation = current["autonomous_replan_obligation"]["obligation_id"]
    # An old unbound write is not a substitute for resuming the original journal.
    stale = _cli(registry, runtime, "refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--todo-id", TODO, "--classification", "stale_original_submission",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks", code=1)
    assert not stale.get("appended")
    recover_original()
    assert len(_spends(index)) == 1
    assert (workspace / "completion-artifact.txt").read_bytes() == original_bytes
    assert len(counts.read_text().splitlines()) == 1

    review = project / "review-host.py"
    review.write_text(_REVIEW_HOST)
    request = project / "review-request.json"
    artifact, result = project / "review.json", project / "review-result.json"
    request.write_text(json.dumps({"registry": str(registry), "runtime": str(runtime),
        "goal": GOAL, "agent": AGENT, "obligation": obligation, "counts": str(counts),
        "review_turn": "fixture-current-review-after-original",
        "original": str(workspace / "completion-artifact.txt"), "artifact": str(artifact), "result": str(result)}))
    args = _worker_args(project, runtime, registry, review, request)
    assert worker.run_worker(args) == 0, result.read_text() if result.exists() else "Host failed before writeback"
    assert json.loads(artifact.read_text())["adopted_original"] is True
    adopted = json.loads(result.read_text())
    assert adopted["identity"]["replan_obligation_id"] == obligation
    assert len(_spends(index)) == 2
    for _ in range(2):
        recover_original()
        assert worker.run_worker(args) == 0
    assert [json.loads(row)["work"] for row in counts.read_text().splitlines()] == ["O3", "R2"]
    assert len(_spends(index)) == 2
    assert (workspace / "completion-artifact.txt").read_bytes() == original_bytes
