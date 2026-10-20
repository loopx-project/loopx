"""Real heartbeat Hosts consume current source eligibility before native launch."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from benchmark.runtime import worker
from benchmark.tests.test_shared_codex_runtime import worker_env
from loopx.configure_goal import configure_goal
from loopx.control_plane.goals import activation_service
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from loopx.global_registry import sync_project_registry_to_global
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane import test_quota_settlement_cli as fixture
from tests.control_plane.test_codex_operation_launch import _bindings


ROOT = Path(__file__).resolve().parents[2]


def _environment(tmp_path, monkeypatch, provider, *, replan=False):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    project, runtime, source = fixture._write_fixture(tmp_path)
    if replan == "empty":
        state = project / json.loads(source.read_text())["goals"][0]["state_file"]
        state.write_text("---\nstatus: active\nowner_mode: goal\n"
            'objective: "Plan one concrete task from the current objective."\n---\n\n'
            "# Active Goal State\n\n## Objective\n\nPlan one concrete task.\n\n"
            "## Agent Todo\n\n## Completed Work Archive\n")
    elif replan == "todo":
        fixture._configure_selected_todo_replan_fixture(project, source)
    elif replan:
        fixture._configure_autonomous_replan_fixture(project, runtime, source)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    synced = sync_project_registry_to_global(
        registry_path=source, runtime_root_override=str(runtime), goal_id=fixture.GOAL_ID,
    )
    assert synced["ok"], synced
    env = worker_env(project)
    skills = tmp_path / "shared-skills"
    skills.mkdir()
    env.update(LOOPX_EXECUTION_MODE="heartbeat", LOOPX_ITERATION_CONTEXT="resume",
        LOOPX_TASK_ENTRY="seeded-todo", LOOPX_TASK_STAGE="execute",
        LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_REGISTRY=str(runtime / "registry.global.json"),
        LOOPX_GOAL_ID=fixture.GOAL_ID, LOOPX_AGENT_ID=fixture.AGENT_ID,
        LOOPX_SHARED_SKILLS=str(skills),
        LOOPX_CODEX_TURN_TIMEOUT_SEC="90")
    env["LOOPX_CLI"] = str(write_python_host_fixture(tmp_path / "loopx-cli",
        f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n"))
    env["CODEX_BIN"] = str(write_python_host_fixture(Path(env["CODEX_BIN"]), f"#!{sys.executable}\n" + r'''
import json,sys
from pathlib import Path
with Path("native-starts").open("a") as marker: marker.write("native\n")
sys.stdin.read()
with Path("model-starts").open("a") as marker: marker.write("model\n")
Path("business.json").write_text(json.dumps({"value": sum([2,3])}))
print(json.dumps({"type":"thread.started","thread_id":"benchmark-original-session"}),flush=True)
'''))
    state = project / json.loads(source.read_text())["goals"][0]["state_file"]
    return env, project, runtime, source, state


def _quota(registry, runtime, project):
    code, result = fixture._run_cli(registry, runtime,
        "quota", "should-run", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
        "--runtime-profile", "generic_cli", cwd=project)
    assert code == 0 or code == 1 and result.get("status_health_ok") is False, result
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("refusal", ["source_compute_zero", "missing_state", "stop", None])
def test_heartbeat_worker_qualifies_current_source_at_actual_spawn(tmp_path, monkeypatch, provider, refusal):
    env, project, runtime, source, state = _environment(tmp_path, monkeypatch, provider)
    mirror = Path(env["LOOPX_REGISTRY"])
    mirror_before, state_before = mirror.read_bytes(), state.read_bytes()
    observations = []
    if refusal == "source_compute_zero":
        changed = configure_goal(registry_path=source, goal_id=fixture.GOAL_ID, quota_compute=0, execute=True)
        assert changed["written"], changed
        denied = _quota(source, runtime, project)
        assert denied["should_run"] is False and "compute_quota_zero" in json.dumps(denied), denied
        assert _quota(mirror, runtime, project)["should_run"] is True
        observations.append(denied)
    original_command = worker.native_command

    def after_session_selection(*arguments):
        if refusal == "missing_state":
            state.rename(state.with_suffix(".saved"))
            for registry in (source, mirror):
                denied = _quota(registry, runtime, project)
                assert denied.get("should_run") is not True and denied["status_health_ok"] is False, denied
                observations.append(denied)
        elif refusal == "stop":
            with monkeypatch.context() as fault:
                fault.setattr(activation_service, "sync_project_registry_to_global", lambda **kwargs: {
                    "ok": False, "error": "fixture mirror sync failed after source Stop committed"})
                stopped = activation_service.set_goal_activation_state(
                    registry_path=source, goal_id=fixture.GOAL_ID, state="stopped",
                    reason="Stop before the actual heartbeat model launch.", actor_kind="owner", execute=True)
            assert stopped["written"] and json.loads(source.read_text())["goals"][0]["activation"]["state"] == "stopped"
            observations.append(stopped)
        return original_command(*arguments)

    monkeypatch.setattr(worker, "native_command", after_session_selection)
    bindings_before = _bindings(runtime)
    rejected = None
    try:
        result = worker.run_once(env)
    except FirstPartyHostRuntimeRejected as error:
        rejected = error
    if refusal is None:
        assert rejected is None and result["ok"], (rejected, result)
        assert (project / "native-starts").read_text().splitlines() == ["native"]
        assert (project / "model-starts").read_text().splitlines() == ["model"]
        assert json.loads((project / "business.json").read_text()) == {"value": 5}
        return
    assert observations and mirror.read_bytes() == mirror_before
    assert not (project / "native-starts").exists(), "Current refusal was bypassed by the real native process."
    assert not (project / "model-starts").exists() and not (project / "business.json").exists()
    assert rejected is not None
    assert ("goal_stopped" if refusal == "stop" else "goal_launch_not_allowed") in str(rejected)
    assert _bindings(runtime) == bindings_before
    if refusal != "missing_state":
        assert state.read_bytes() == state_before
    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["heartbeat", "turn"])
def test_initial_benchmark_planner_can_launch_without_a_prior_heartbeat_receipt(tmp_path, monkeypatch, provider, mode):
    env, project, runtime, source, _state = _environment(tmp_path, monkeypatch, provider, replan="empty")
    packet = _quota(source, runtime, project)
    assert packet["should_run"] is True and packet.get("selected_todo") is None
    env.update(LOOPX_TASK_ENTRY="loopx-planned", LOOPX_TASK_STAGE="plan", LOOPX_EXECUTION_MODE=mode,
        LOOPX_PHASE_DEADLINE_EPOCH=str(time.time()+600), LOOPX_PLANNING_RESULT=str(project / "planning.json"))
    if mode == "turn":
        env["LOOPX_VALIDATION_COMMAND_JSON"] = json.dumps([sys.executable,"-c","pass"])
    source_code = r'''
import json, os, subprocess, sys
from pathlib import Path
packet = json.loads(sys.stdin.read().split("Host-supplied planning checkpoint:\n",1)[1])
assert packet["runnable_todo_ids"] == []
command = [os.environ["LOOPX_CLI"],"--format","json","--registry",os.environ["LOOPX_REGISTRY"],
    "--runtime-root",os.environ["LOOPX_RUNTIME_ROOT"],"todo","add","--goal-id",os.environ["LOOPX_GOAL_ID"],
    "--role","agent","--claimed-by",os.environ["LOOPX_AGENT_ID"],"--text","[P1] Validate the planned inputs.",
    "--task-class","advancement_task","--action-kind","validate","--execute"]
added = json.loads(subprocess.run(command,check=True,capture_output=True,text=True).stdout)
result = {"input_digest":packet["input_digest"],"status":"ready","todo_ids":[added["todo_id"]]}
Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps(result))
Path("initial-plan.json").write_text(json.dumps(result))
print(json.dumps({"type":"thread.started","thread_id":"initial-planning-session"}),flush=True)
'''
    env["CODEX_BIN"] = str(write_python_host_fixture(project / "initial-planner", f"#!{sys.executable}\n"+source_code))
    result = worker.run_once(env)
    assert result["ok"] and result["planning"]["state_readback_verified"] is True, result
    planned = json.loads((project / "initial-plan.json").read_text())
    assert len(planned["todo_ids"]) == 1 and result["planning"]["todo_ids"] == planned["todo_ids"]
    assert not fixture._heartbeat_receipt_events(runtime, result["turn_id"])
    assert fixture._spend_run_count(runtime) == 0
