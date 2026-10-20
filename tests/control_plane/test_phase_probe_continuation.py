"""A separately authorized phase is consumed by the resumed native Host.

Only inference and candidate grading are deterministic fixtures. Native
controller activation, CLI/Host session reuse, canonical Todo acceptance and
quota settlement use the existing owners, with no real model requests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import time
from types import SimpleNamespace

import pytest

from benchmark.edgebench import checkpoint_controller as transport
from benchmark.runtime import worker as benchmark_worker
from benchmark.tests import test_checkpoint_controller as checkpoint_fixture
from benchmark.tests.test_shared_codex_runtime import worker_env
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests import test_loopx_turn_driver as fixture
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from tests.control_plane.test_host_research_recovery_cli import AGENT, GOAL, ROOT, TODO, _cli, _in_process, _spends


controllers = checkpoint_fixture.controllers
register = checkpoint_fixture.register
submit = checkpoint_fixture.submit


_PHASE_HOST = r'''import json,os,re,sys,time
from pathlib import Path
args=sys.argv[1:]
prompt=sys.stdin.read()
request=json.loads(prompt.rsplit("Turn request:\n",1)[1])
selected=request["turn_envelope"]["action"]["selected_todo"]
text=request["turn_envelope"]["action"]["recommended_action"]
phase=re.search(r"Phase (S[12])",text).group(1)
inputs=json.loads(Path("retained-inputs.json").read_text())
history=Path("native-history.jsonl")
if phase=="S2":
    assert "resume" in args and "retained-phase-session" in args,args
    old=json.loads(history.read_text().splitlines()[0])
    assert old["phase"]=="S1" and old["old_deadline"]<float(os.environ["FIXTURE_HOST_NOW"]),old
    assert int(Path(os.environ["FIXTURE_PHASE_FILE"]).read_text())>float(os.environ["FIXTURE_HOST_NOW"])
    assert "sum of squares" in text,text
    value=sum(number*number for number in inputs)
else:
    assert "sum of retained inputs" in text,text
    value=sum(inputs)
artifact={"phase":phase,"inputs":inputs,"value":value,"consumed_todo_id":selected["todo_id"]}
Path("probe-"+phase+".json").write_text(json.dumps(artifact)+"\n")
with history.open("a") as stream:
    stream.write(json.dumps({"phase":phase,"old_deadline":int(os.environ["FIXTURE_S1_DEADLINE"]),"value":value})+"\n")
with Path("model-counts.jsonl").open("a") as stream:
    stream.write(json.dumps({"phase":phase,"D":1,"P":1})+"\n")
print(json.dumps({"type":"thread.started","thread_id":"retained-phase-session"}),flush=True)
Path(args[args.index("--output-last-message")+1]).write_text(json.dumps({
    "schema_version":"loopx_turn_result_v0","turn_key":request["turn_key"],
    "result_kind":"validated_completion","completed_phases":["host_execute","typed_result"],
    "classification":"phase_probe_completed","recommended_action":"Read the current Goal after accepting this probe.",
    "next_action":"Use current independently authorized work.","delivery_batch_scale":"implementation",
    "delivery_outcome":"outcome_progress","vision_unchanged_reason":"The finite authorized probe remains the accepted scope.",
    "summary":"The current phase probe executed on the retained inputs."}))
'''


_HEARTBEAT_PHASE_HOST = r'''import json,os,re,subprocess,sys
from hashlib import sha256
from pathlib import Path
args=sys.argv[1:]
prompt=sys.stdin.read()
assert os.environ["LOOPX_GOAL_ID"] in prompt,prompt
def cli(*arguments):
    completed=subprocess.run([os.environ["LOOPX_CLI"],"--registry",os.environ["LOOPX_REGISTRY"],
        "--runtime-root",os.environ["LOOPX_RUNTIME_ROOT"],"--format","json",*arguments],
        capture_output=True,text=True,timeout=90)
    result=json.loads(completed.stdout)
    if completed.returncode:
        Path("phase-host-error.json").write_text(json.dumps({"arguments":arguments,"result":result}))
    assert completed.returncode==0,result
    return result
common=["--goal-id",os.environ["LOOPX_GOAL_ID"],"--agent-id",os.environ["LOOPX_AGENT_ID"],
        "--turn-instance-id",os.environ["LOOPX_TURN"]]
guard=cli("quota","should-run","--runtime-profile","generic_cli",*common)
assert guard["should_run"] is True,guard
selected=guard["selected_todo"]
text=selected["text"]
phase=re.search(r"Phase (S[12])",text).group(1)
inputs=json.loads(Path("retained-inputs.json").read_text())
history=Path("native-history.jsonl")
if phase=="S2":
    assert "resume" in args and "retained-phase-session" in args,args
    old=json.loads(history.read_text().splitlines()[0])
    assert old["phase"]=="S1" and old["old_deadline"]<float(os.environ["FIXTURE_HOST_NOW"]),old
    assert int(Path(os.environ["FIXTURE_PHASE_FILE"]).read_text())>float(os.environ["FIXTURE_HOST_NOW"])
    assert "sum of squares" in text,text
    value=sum(number*number for number in inputs)
else:
    assert "sum of retained inputs" in text,text
    value=sum(inputs)
assert value==(5 if phase=="S1" else 13)
artifact=Path("probe-"+phase+".json")
artifact.write_text(json.dumps({"phase":phase,"inputs":inputs,"value":value,
    "consumed_todo_id":selected["todo_id"]})+"\n")
with history.open("a") as stream:
    stream.write(json.dumps({"phase":phase,"old_deadline":int(os.environ["FIXTURE_S1_DEADLINE"]),"value":value})+"\n")
with Path("model-counts.jsonl").open("a") as stream:
    stream.write(json.dumps({"phase":phase,"D":1,"P":1})+"\n")
print(json.dumps({"type":"thread.started","thread_id":"retained-phase-session"}),flush=True)
evidence="fixture:phase-probe:"+sha256(artifact.read_bytes()).hexdigest()
binding=[*common,"--todo-id",selected["todo_id"]]
completion=["todo","complete",*binding,"--claimed-by",os.environ["LOOPX_AGENT_ID"],"--evidence",evidence]
cli(*completion)
vision={"schema_version":"goal_vision_replan_contract_v0","state":"no_followup",
  "vision_patch":{"vision_summary":"Execute the explicitly authorized finite phase probe.",
                  "acceptance_summary":"The phase probe ran on retained inputs and matched its exact expected value."},
  "path_delta":{"schema_version":"goal_path_delta_v0","outcome":"no_change",
    "prior_assumption":"The currently authorized phase probe remained unexecuted.",
    "observed_reality":"The probe ran on retained inputs and its exact result passed validation.",
    "stopped":["No further work belongs to this finite phase probe."],"evidence_refs":[evidence]}}
vision_path=Path("phase-"+phase+".vision.json").resolve()
vision_path.write_text(json.dumps(vision))
refreshed=cli("refresh-state",*binding,"--classification","phase_probe_completed",
    "--delivery-batch-scale","implementation","--delivery-outcome","outcome_progress",
    "--agent-vision-json",str(vision_path),"--no-global-sync","--suppress-external-sinks")
spent=cli("quota","spend-slot",*binding,"--slots","1","--source","heartbeat","--execute")
terminal=cli(*completion,"--no-follow-up")
assert spent["appended"] is True and terminal["completion_continuation"]=="no_followup"
Path("phase-"+phase+"-settlement.json").write_text(json.dumps({"binding":binding,
    "completion":completion,"identity":spent["settlement_identity"],"evidence":evidence,
    "refreshed":refreshed,"spent":spent,"terminal":terminal}))
'''


def _spends_for_todo(index, todo_id):
    prefix = f"{GOAL}:{AGENT}:{todo_id}:"
    return [row for row in _spends(index)
            if row.get("quota_spend_commit", {}).get("effect_id", "").startswith(prefix)]


def _argv(project, runtime, registry, native, phase):
    validation = ("import json; from pathlib import Path; "
        f"r=json.loads(Path('probe-{phase}.json').read_text()); "
        f"assert r['phase']=={phase!r} and r['value']=={5 if phase == 'S1' else 13}")
    return ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--host", "codex-cli", "--codex-bin", str(native),
        "--goal-id", GOAL, "--agent-id", AGENT, "--project", str(project), "--scan-root", str(project),
        "--iteration-context", "resume", "--validation-command-json", json.dumps([sys.executable, "-c", validation]),
        "--timeout-seconds", "120", "--no-global-sync", "--execute"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["governed", "heartbeat"])
def test_current_phase_probe_executes_once_after_unique_extension(controllers, tmp_path, monkeypatch, provider, mode):
    isolate_sqlite_runtime(tmp_path / "canonical", monkeypatch)
    project, runtime, registry = fixture._write_live_fixture(tmp_path / "goal", provider=provider)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    workspace = project / "native-workspace"
    workspace.mkdir()
    (workspace / "retained-inputs.json").write_text(json.dumps([2, 3]))
    native = tmp_path / "controlled-codex"
    native.write_text(f"#!{sys.executable}\n" + (_PHASE_HOST if mode == "governed" else _HEARTBEAT_PHASE_HOST))
    native.chmod(0o700)
    environment = worker_env(workspace)
    skills = project / "shared-skills"
    skills.mkdir()
    launcher = project / "phase-loopx"
    launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.cli"]) + ' "$@"\n')
    launcher.chmod(0o700)
    environment.update(LOOPX_EXECUTION_MODE="heartbeat", LOOPX_ITERATION_CONTEXT="resume",
        LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_REGISTRY=str(registry), LOOPX_GOAL_ID=GOAL,
        LOOPX_AGENT_ID=AGENT, LOOPX_SHARED_SKILLS=str(skills), LOOPX_CLI=str(launcher),
        LOOPX_TURN_ENVELOPE="0", LOOPX_CODEX_SANDBOX="read-only", CODEX_BIN=str(native),
        LOOPX_CODEX_TURN_TIMEOUT_SEC="120")

    def run_phase(phase):
        if mode == "governed":
            result = _in_process(_argv(workspace, runtime, registry, native, phase))
            assert result["status"] == "committed"
            return result
        environment.update({key: os.environ[key] for key in (
            "FIXTURE_S1_DEADLINE", "FIXTURE_HOST_NOW", "FIXTURE_PHASE_FILE") if key in os.environ})
        environment["LOOPX_PHASE_DEADLINE_EPOCH"] = (
            os.environ["FIXTURE_S1_DEADLINE"] if phase == "S1"
            else Path(os.environ["FIXTURE_PHASE_FILE"]).read_text().strip())
        receipt = benchmark_worker.run_once(environment)
        error_path = workspace / "phase-host-error.json"
        assert receipt["ok"] is True, error_path.read_text() if error_path.exists() else receipt
        assert receipt["host_invoked"] is True and receipt["session"]["session_id"] == "retained-phase-session"
        return receipt
    _cli(registry, runtime, "todo", "update", "--goal-id", GOAL, "--agent-id", AGENT,
         "--todo-id", TODO, "--text", "Phase S1: compute the sum of retained inputs.")
    source_phase = tmp_path / "source-phase"
    source_phase.write_text(f"{int(time.time()) + 1800}\n")
    monkeypatch.setenv("FIXTURE_S1_DEADLINE", source_phase.read_text().strip())
    run_phase("S1")
    old_artifact = (workspace / "probe-S1.json").read_bytes()
    old_history = (workspace / "native-history.jsonl").read_bytes()
    index = runtime / "goals" / GOAL / "runs/index.jsonl"
    original_spends = _spends(index)
    assert len(original_spends) == 1

    source, destination, source_url, destination_url, files, grades = controllers
    candidate_policy = (tmp_path / "tasks/fixture.json").read_bytes()
    assert json.loads(candidate_policy)["judge"]["eval_timeout"] == 15
    token = register(source, source_url, max_agent_submissions=3, submission_cooldown=3600)
    assert submit(source_url, token, 7).status_code == 200
    original_phase = source_phase.read_bytes()
    unit = tmp_path / "retained-point"
    point = transport.capture(files[0], token=token, capture_id="e" * 32,
        destination=unit, phase_deadline_file=source_phase)
    # Advance only the receiving Host clock. HTTP, source bytes and candidate
    # grading remain real; the old deadline is past under this clock.
    host_clock = time.time() + 3600
    monkeypatch.setattr(transport, "time", SimpleNamespace(
        time=lambda: host_clock, monotonic=time.monotonic, sleep=time.sleep))
    monkeypatch.setattr(benchmark_worker, "time", SimpleNamespace(
        time=lambda: host_clock, monotonic=time.monotonic, sleep=time.sleep,
        time_ns=lambda: int(host_clock * 1_000_000_000)))
    monkeypatch.setenv("FIXTURE_HOST_NOW", str(host_clock))
    point_bytes = (unit / "checkpoint.json").read_bytes()
    # A retained point does not carry authority to restart a stopped Goal.
    stopped = set_goal_activation_state(registry_path=registry, goal_id=GOAL,
        state="stopped", reason="The first finite research phase stopped.",
        actor_kind="owner", execute=True)
    assert stopped["readback"]["verified"] is True
    stopped_guard = _cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
        "--goal-id", GOAL, "--agent-id", AGENT)
    assert stopped_guard["should_run"] is False and stopped_guard["pause_cause"] == "goal_stopped"
    if mode == "governed":
        stopped_turn = _in_process(_argv(workspace, runtime, registry, native, "S2"), code=1)
        assert stopped_turn["effects"]["host_invoked"] is False
    else:
        expired = benchmark_worker.run_once(environment)
        assert expired["budget_exhausted"] is True and expired["host_invoked"] is False
    assert len((workspace / "model-counts.jsonl").read_text().splitlines()) == 1
    assert _spends(index) == original_spends

    phase_request = {"event_id": "user-continue-phase-s2", "phase": "S2", "extension_seconds": 600,
                     "probe": "compute the sum of squares of retained inputs"}
    phase = tmp_path / "current-phase"
    restored = transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
        run_id=phase_request["event_id"], phase_deadline_file=phase,
        phase_extension_seconds=phase_request["extension_seconds"])
    deadline = int(phase.read_text())
    assert deadline > host_clock
    monkeypatch.setenv("FIXTURE_PHASE_FILE", str(phase))
    binding = phase.with_name(phase.name + ".restore.json").read_bytes()
    add = ("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
           "--task-class", "advancement_task", "--action-kind", "validate",
           "--text", "Phase S2: compute the sum of squares of retained inputs.",
           "--operation-id", phase_request["event_id"])
    resumed = set_goal_activation_state(registry_path=registry, goal_id=GOAL,
        state="active", reason=phase_request["event_id"] + ": " + phase_request["probe"],
        actor_kind="owner", execute=True)
    assert resumed["readback"]["verified"] is True
    current_todo = _cli(registry, runtime, *add)["todo_id"]
    assert _cli(registry, runtime, *add)["todo_id"] == current_todo
    assert transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
        run_id=phase_request["event_id"], phase_deadline_file=phase, recover=True,
        phase_extension_seconds=phase_request["extension_seconds"]) == restored
    assert phase.with_name(phase.name + ".restore.json").read_bytes() == binding
    info = destination.state.judge.tokens[restored["token"]]
    assert info["max_agent_submissions"] == 3 and info["submission_cooldown"] == 3600
    assert submit(destination_url, restored["token"], 8).status_code == 429
    assert len(grades) == 1

    second_argv = _argv(workspace, runtime, registry, native, "S2")
    second = run_phase("S2")
    artifact = json.loads((workspace / "probe-S2.json").read_text())
    assert artifact == {"phase": "S2", "inputs": [2, 3], "value": 13, "consumed_todo_id": current_todo}
    assert (workspace / "probe-S1.json").read_bytes() == old_artifact
    assert (workspace / "native-history.jsonl").read_bytes().startswith(old_history)
    current_spends = _spends_for_todo(index, current_todo)
    assert len(current_spends) == 1
    todo = _cli(registry, runtime, "todo", "list", "--goal-id", GOAL, "--todo-id", current_todo)
    assert todo["todo"]["status"] == "done"
    assert todo["authority_read"]["source_authority"] == f"{provider}_v0"
    for _ in range(2):
        assert _cli(registry, runtime, *add)["todo_id"] == current_todo
        if mode == "governed":
            replay = _in_process([*second_argv[:-1], "--resume-turn-key", second["resume_turn_key"], "--execute"])
            assert replay["effects"]["host_invoked"] is False
        else:
            original = json.loads((workspace / "phase-S2-settlement.json").read_text())
            replay = _cli(registry, runtime, *original["completion"], "--no-follow-up")
            assert replay["idempotent_replay"] is True
            replay = _cli(registry, runtime, "quota", "spend-slot", *original["binding"],
                "--slots", "1", "--source", "heartbeat", "--execute")
            assert replay["appended"] is False and replay["idempotent_replay"] is True
        transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
            run_id=phase_request["event_id"], phase_deadline_file=phase, recover=True,
            phase_extension_seconds=phase_request["extension_seconds"])
    assert int(phase.read_text()) == deadline
    assert source_phase.read_bytes() == original_phase and (unit / "checkpoint.json").read_bytes() == point_bytes
    assert [json.loads(line)["phase"] for line in (workspace / "model-counts.jsonl").read_text().splitlines()] == ["S1", "S2"]
    assert _spends_for_todo(index, TODO) == original_spends
    assert _spends_for_todo(index, current_todo) == current_spends
    assert (tmp_path / "tasks/fixture.json").read_bytes() == candidate_policy
    assert len(destination.state.judge.tokens) == 1
    if mode == "heartbeat":
        after = _cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
            "--goal-id", GOAL, "--agent-id", AGENT)
        assert after["should_run"] is False and after.get("selected_todo") is None, after
    with pytest.raises(ValueError, match="original activation"):
        transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
            run_id=phase_request["event_id"], phase_deadline_file=phase, recover=True,
            phase_extension_seconds=601)
