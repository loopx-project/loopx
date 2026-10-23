"""Actual checkpoint handoff retains the original Turn at every native resume."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from benchmark.runtime import worker
from benchmark.runtime.checkpoint_wake import CheckpointWake
from benchmark.runtime.codex import Execution, prepare_codex_home, process_environment
from benchmark.runtime.sessions import BenchmarkSessionWake
from loopx.configure_goal import configure_goal
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals import activation_service
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from tests.control_plane import test_quota_settlement_cli as fixture
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane.test_benchmark_current_admission import _environment, _private_workspace, _quota
from tests.control_plane.test_codex_operation_launch import _bindings


# CheckpointWake's production transport interrupts an owned POSIX process group.
pytestmark = pytest.mark.skipif(os.name != "posix", reason="CheckpointWake requires Unix handoff and process groups")
HELD_TURN = "checkpoint-held-original-turn"
OUTER_TURN = "checkpoint-enclosing-wake"

_HOST = r'''
import hashlib, json, os, shlex, subprocess, sys
from pathlib import Path
env = os.environ
count = Path("checkpoint-native.jsonl")
started = len(count.read_text().splitlines()) if count.exists() else 0
with count.open("a") as stream:
    stream.write(json.dumps({"argv":sys.argv[1:], "turn":env["LOOPX_TURN"]})+"\n")
body = sys.stdin.read()
with Path("checkpoint-model.jsonl").open("a") as stream:
    stream.write(json.dumps({"turn":env["LOOPX_TURN"]})+"\n")
print(json.dumps({"type":"thread.started", "thread_id":"benchmark-original-session"}), flush=True)
base = [env["LOOPX_CLI"], "--format", "json", "--registry", env["LOOPX_REGISTRY"],
        "--runtime-root", env["LOOPX_RUNTIME_ROOT"]]
def cli(*args):
    result = subprocess.run([*base,*args], capture_output=True, text=True)
    assert result.stdout, (result.returncode, result.stderr)
    value = json.loads(result.stdout)
    if result.returncode:
        raise RuntimeError(json.dumps(value))
    return value
def rendered(command):
    tokens = shlex.split(command)
    index = next(i for i, token in enumerate(tokens) if token in {"quota","refresh-state"})
    turn = guard["heartbeat_receipt"]["turn_instance_id"]
    return [turn if token == "${LOOPX_TURN:?}" else token for token in tokens[index:]]
if started == 0:
    Path("retained-input.json").write_text(json.dumps([2,3]))
    args = ["quota","should-run","--goal-id",env["LOOPX_GOAL_ID"],"--agent-id",env["LOOPX_AGENT_ID"],
            "--runtime-profile","generic_cli","--turn-instance-id",env["FIXTURE_HELD_TURN"],
            "--available-capability","shell",
            "--available-capability","filesystem_write"]
    if env.get("FIXTURE_BOUND_TODO"):
        planning = cli(*args)
        assert planning["heartbeat_receipt"]["settlement_binding_owed"] is True
        args += ["--todo-id",env["FIXTURE_BOUND_TODO"]]
    elif env.get("FIXTURE_BOUND_WORK"):
        args += ["--todo-id",env["FIXTURE_BOUND_WORK"]]
    guard = cli(*args,"--decision-output-root",env["FIXTURE_CAPTURE_ROOT"],
        "--decision-handoff-socket",env["FIXTURE_HANDOFF_SOCKET"])
    # An admitted work guard owes closeout and stays in this process. A fresh
    # replan, including its Todo selection hop, instead reaches native resume.
    assert env.get("FIXTURE_BOUND_WORK") and guard["heartbeat_receipt"]["closeout_required"] is True
    Path("checkpoint-closeout-guard.json").write_text(json.dumps(guard))
else:
    assert started == 1 and "resume" in sys.argv and sys.argv[-2] == "benchmark-original-session"
    guard = json.loads(Path(env["FIXTURE_HELD_QUOTA"]).read_text())
    assert env["LOOPX_TURN"] == guard["heartbeat_receipt"]["turn_instance_id"]
    assert env["LOOPX_TURN"] != env["FIXTURE_OUTER_TURN"]
    assert guard["heartbeat_receipt"]["event_id"] in body
assert guard["heartbeat_receipt"]["turn_instance_id"] == env["FIXTURE_HELD_TURN"]
Path("checkpoint-original-consumed.json").write_text(json.dumps(guard["heartbeat_receipt"]))
if guard["heartbeat_receipt"].get("settlement_identity") is None:
    todo_id = env["FIXTURE_SELECTION_TODO"]
    args = ["quota","should-run","--goal-id",env["LOOPX_GOAL_ID"],"--agent-id",env["LOOPX_AGENT_ID"],
        "--runtime-profile","generic_cli","--turn-instance-id",env["LOOPX_TURN"],"--todo-id",todo_id,
]
    selected = subprocess.run([*base,*args], capture_output=True, text=True)
    guard = json.loads(selected.stdout)
    if selected.returncode:
        [recovery] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        guard = cli(*rendered(recovery))
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == todo_id
Path("checkpoint-business.json").write_text(json.dumps({"sum":sum(json.loads(Path("retained-input.json").read_text()))}))
evidence = "checkpoint-business:" + hashlib.sha256(Path("checkpoint-business.json").read_bytes()).hexdigest()
vision = Path("checkpoint-vision.json").resolve()
vision.write_text(json.dumps({"schema_version":"goal_vision_replan_contract_v0","agent_id":env["LOOPX_AGENT_ID"],
    "state":"active", "vision_patch":{"acceptance_summary":"Validate the retained inputs and keep remaining validation work.",
        "advancement_policy":"as_needed"}, "path_delta":{"schema_version":"goal_path_delta_v0","outcome":"continue",
        "prior_assumption":"The retained inputs still require a concrete validation probe.",
        "observed_reality":"The retained inputs sum to five; this does not validate the other open tasks.",
        "retained":["The remaining independent validation work"],"evidence_refs":[evidence]}}))
actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
refresh = next(command for command in actions if "refresh-state" in command)
for old,new in [("<validated_progress>","retained_inputs_validated"),
        ("<scale>","single_surface"),("<outcome>","outcome_progress"),
        ("<advanced|blocked|exploration_exhausted|no_followup>","advanced"),
        ("<surface-id>","checkpoint-input"),("<hypothesis-id>","retained-input-sum"),
        ("<probe-kind>","deterministic-sum"),("<evidence-id>",evidence)]:
    refresh = refresh.replace(old,new)
refresh_args = rendered(refresh)
if "--agent-vision-json" in refresh_args:
    refresh_args[refresh_args.index("--agent-vision-json")+1] = str(vision)
else:
    refresh_args += ["--agent-vision-json",str(vision)]
updated = cli(*refresh_args)
spend = rendered(next(command for command in actions if "spend-slot" in command))
spent, replay = cli(*spend), cli(*spend)
Path("checkpoint-settlement.json").write_text(json.dumps({"refresh":updated,"spend":spent,"replay":replay}))
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("refusal", ["source_compute_zero", "missing_state", "stop",
    "original_event_changed", "original_settlement_changed", "original_action_changed", None])
def test_checkpoint_resume_rechecks_current_source_with_original_guard(tmp_path, monkeypatch, provider, refusal):
    _run_checkpoint(tmp_path, monkeypatch, provider, refusal=refusal, todo_bound=False)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_checkpoint_fresh_todo_binding_keeps_original_turn(tmp_path, monkeypatch, provider):
    _run_checkpoint(tmp_path, monkeypatch, provider, refusal=None, todo_bound=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_checkpoint_work_closeout_keeps_original_host(tmp_path, monkeypatch, provider):
    _run_checkpoint(tmp_path, monkeypatch, provider, refusal=None, todo_bound=False, work_bound=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_checkpoint_resume_keeps_private_workspace_and_original_receipt(tmp_path, monkeypatch, provider):
    _run_checkpoint(tmp_path, monkeypatch, provider,
        refusal=None, todo_bound=False, workspace_change=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_checkpoint_unbound_guard_selects_existing_work_in_original_turn(tmp_path, monkeypatch, provider):
    _run_checkpoint(tmp_path, monkeypatch, provider, refusal=None, todo_bound=False, unbound=True)


def _run_checkpoint(tmp_path, monkeypatch, provider, *, refusal, todo_bound, unbound=False, workspace_change=False, work_bound=False):
    unbound = unbound or refusal == "original_event_changed"
    env, project, runtime, source, state = _environment(
        tmp_path, monkeypatch, provider, replan="todo" if todo_bound or unbound else not work_bound)
    env["CODEX_BIN"] = str(write_python_host_fixture(project / "checkpoint-host", f"#!{sys.executable}\n" + _HOST))
    env = process_environment(Path(env["LOOPX_CODEX_HOME"]), base=env)
    env.update(LOOPX_TURN=OUTER_TURN, FIXTURE_OUTER_TURN=OUTER_TURN, FIXTURE_HELD_TURN=HELD_TURN)
    if unbound:
        env["FIXTURE_SELECTION_TODO"] = fixture.SELECTED_REPLAN_TODO_ID
    if todo_bound:
        # Selecting a Todo on a fresh replan retains the semantic checkpoint
        # opportunity. Selection alone is not ordinary-work admission.
        env["FIXTURE_BOUND_TODO"] = fixture.SELECTED_REPLAN_TODO_ID
    if work_bound:
        # Admit real ordinary work through the guard inside the live Host.
        # Prebinding before launch would exercise prior-Turn recovery instead.
        env["FIXTURE_BOUND_WORK"] = fixture.TODO_ID
    execution = Execution(mode="heartbeat", context="resume", task_entry="seeded-todo", timeout_seconds=120)
    prepare_codex_home(Path(env["LOOPX_CODEX_HOME"]), execution=execution, workspace=project,
        model=env["MODEL_NAME"], effort=env["REASONING_EFFORT"],
        base_url=env["OPENAI_BASE_URL"], api_key=env["OPENAI_API_KEY"], wire_api="responses",
        skills=Path(env["LOOPX_SHARED_SKILLS"]))
    log_root = Path(env["LOOPX_WAKE_LOG_DIR"])
    wake = log_root / OUTER_TURN
    wake.mkdir(parents=True)
    component = project / "checkpoint-source.txt"
    component.write_text("Retained source before the intercepted original guard.\n")
    request = project / "checkpoint-request.json"
    request.write_text(json.dumps({"destination":str(log_root.parent / "checkpoints"),
        "components":[{"id":"source","source_path":str(component)}], "point_metadata":{}, "trigger":"fresh_replan"}))
    env["LOOPX_CHECKPOINT_REQUEST"] = str(request)
    receipt = {"turn_id":OUTER_TURN,"mode":"heartbeat","stage":"execute"}
    observations = {}
    mirror_before = Path(env["LOOPX_REGISTRY"]).read_bytes()
    with CheckpointWake(env, receipt) as hook:
        env.update(FIXTURE_CAPTURE_ROOT=str(hook.capture_root), FIXTURE_HANDOFF_SOCKET=hook.socket_path)
        body = worker.heartbeat_body(env, OUTER_TURN, native_goal=False, decision_handoff_socket=hook.socket_path)
        session = BenchmarkSessionWake(env, execution, receipt)
        original_capture = hook._capture

        def captured_then_changed(held, deadline):
            captured = original_capture(held, deadline)
            observations.update(held=held, bindings=_bindings(runtime),
                events=fixture._heartbeat_receipt_events(runtime, HELD_TURN))
            if unbound:
                assert held["quota"]["heartbeat_receipt"].get("settlement_identity") is None
                assert held["quota"]["heartbeat_receipt"]["settlement_binding_owed"] is True
            if todo_bound:
                assert held["quota"]["heartbeat_receipt"]["settlement_identity"]["todo_id"] == fixture.SELECTED_REPLAN_TODO_ID
            env["FIXTURE_HELD_QUOTA"] = str(held["path"])
            if workspace_change:
                _private_workspace(project)
                assert _quota(source, runtime, project)["status_health_ok"] is True
            if refusal == "source_compute_zero":
                changed = configure_goal(registry_path=source, goal_id=fixture.GOAL_ID, quota_compute=0, execute=True)
                assert changed["written"], changed
                assert _quota(source, runtime, project)["should_run"] is False
                assert _quota(Path(env["LOOPX_REGISTRY"]), runtime, project)["should_run"] is True
            elif refusal == "missing_state":
                state.rename(state.with_suffix(".saved"))
                assert _quota(source, runtime, project)["status_health_ok"] is False
            elif refusal == "stop":
                with monkeypatch.context() as fault:
                    fault.setattr(activation_service, "sync_project_registry_to_global", lambda **kwargs: {
                        "ok":False,"error":"fixture mirror sync failed after committed Stop"})
                    changed = activation_service.set_goal_activation_state(registry_path=source,
                        goal_id=fixture.GOAL_ID, state="stopped", reason="Stop before checkpoint continuation.",
                        actor_kind="owner", execute=True)
                assert changed["written"] and json.loads(source.read_text())["goals"][0]["activation"]["state"] == "stopped"
            elif refusal == "original_event_changed":
                original_receipt = held["quota"]["heartbeat_receipt"]
                assert original_receipt.get("settlement_identity") is None and original_receipt["event_id"]
                # A mismatched original packet cannot borrow the same Turn's
                # unbound receipt merely because both have no selected Todo.
                held["quota"] = {**held["quota"], "heartbeat_receipt": {
                    **original_receipt, "event_id": "different-committed-guard-event"}}
            elif refusal == "original_settlement_changed":
                original_receipt = held["quota"]["heartbeat_receipt"]
                assert original_receipt["settlement_identity"]
                held["quota"] = {**held["quota"], "heartbeat_receipt": {
                    **original_receipt, "settlement_identity": {
                        **original_receipt["settlement_identity"], "effect_id": "different-original-effect"}}}
            elif refusal == "original_action_changed":
                held["quota"] = {**held["quota"], "effective_action": "skip"}
            return captured

        monkeypatch.setattr(hook, "_capture", captured_then_changed)
        error = None
        with (wake / "stdout.jsonl").open("w") as stdout, (wake / "stderr.log").open("w") as stderr:
            try:
                returned = hook.run(execution, body, wake, session, stdout, stderr, 120)
            except FirstPartyHostRuntimeRejected as rejected:
                error = rejected
    assert Path(env["LOOPX_REGISTRY"]).read_bytes() == mirror_before
    assert not fixture._heartbeat_receipt_events(runtime, OUTER_TURN)
    native = [json.loads(line) for line in (project / "checkpoint-native.jsonl").read_text().splitlines()]
    models = [json.loads(line) for line in (project / "checkpoint-model.jsonl").read_text().splitlines()]
    if work_bound:
        assert not observations and not receipt.get("checkpoint_handoffs"), receipt
        assert not hook.root.exists(), "A closeout response created a fresh checkpoint attempt."
        held = {"quota":json.loads((project / "checkpoint-closeout-guard.json").read_text())}
        original = held["quota"]["heartbeat_receipt"]
        assert original["closeout_required"] is True
        assert original["settlement_identity"]["todo_id"] == fixture.TODO_ID
        assert effect_runtime_result("runtime.checkpoint_artifacts", {
            "action":"select_replan", "quota":held["quota"], "goal_id":fixture.GOAL_ID,
            "agent_id":fixture.AGENT_ID, "turn_instance_id":HELD_TURN,
        }, large_local_snapshot=True) == {
            "capture":False, "reason":"no_fresh_replan", "execution_authority_granted":False,
        }
    else:
        assert observations and receipt["checkpoint_handoffs"][0]["captured"] is True, receipt
        held = observations["held"]
        assert _bindings(runtime) == observations["bindings"]
    assert held["quota"]["heartbeat_receipt"]["turn_instance_id"] == HELD_TURN != OUTER_TURN
    if refusal:
        assert len(native) == len(models) == 1, "A refused original continuation launched another native Host."
        assert error is not None and {
            "source_compute_zero":"goal_launch_not_allowed", "missing_state":"goal_launch_not_allowed",
            "stop":"goal_stopped", "original_event_changed":"heartbeat_launch_original_binding_changed",
            "original_settlement_changed":"heartbeat_launch_original_binding_changed",
            "original_action_changed":"goal_launch_not_allowed",
        }[refusal] in str(error)
        assert not (project / "checkpoint-business.json").exists()
        assert fixture._heartbeat_receipt_events(runtime, HELD_TURN) == observations["events"]
        assert fixture._spend_run_count(runtime) == 0
    else:
        assert error is None and returned == 0, (error, (wake / "stderr.log").read_text())
        expected_turns = [OUTER_TURN] if work_bound else [OUTER_TURN, HELD_TURN]
        assert len(models) == len(expected_turns) and [row["turn"] for row in native] == expected_turns
        assert json.loads((project / "checkpoint-business.json").read_text()) == {"sum":5}
        result = json.loads((project / "checkpoint-settlement.json").read_text())
        assert result["refresh"]["settlement_result"]["ok"] is True
        assert result["spend"]["settlement_result"]["ok"] is True
        assert result["replay"]["idempotent_replay"] is True and result["replay"]["appended"] is False
        assert fixture._spend_run_count(runtime) == 1
        assert json.loads((project / "checkpoint-original-consumed.json").read_text()) == held["quota"]["heartbeat_receipt"]
        events = fixture._heartbeat_receipt_events(runtime, HELD_TURN)
        if work_bound:
            assert events[-1]["event_id"] == held["quota"]["heartbeat_receipt"]["event_id"]
        else:
            assert events[:len(observations["events"])] == observations["events"]
            if not unbound:
                assert events == observations["events"]
