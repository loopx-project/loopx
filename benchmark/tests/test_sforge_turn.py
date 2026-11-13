"""EdgeBench Turn arm through its shared scheduler, real CLI and isolated SQLite Goal."""
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import time

import pytest

from benchmark.runtime.scheduler import worker_command


@pytest.mark.parametrize("args", [
    ["--worker", "turn-resume"],
    ["--worker", "turn-controller-worker"],
    ["--worker", "turn-controller-worker", "--validation-command-json", '["check"]', "--container-cpus", "0"],
    ["--worker", "turn-resume", "--validation-command-json", '"shell text"'],
    ["--worker", "turn-resume", "--validation-command-json", '[""]'],
    ["--worker", "single", "--validation-command-json", '["validator"]'],
    ["--worker", "turn-resume", "--validation-command-json", '["validator"]', "--turn-envelope"],
    ["--worker", "turn-resume", "--validation-command-json", '["validator"]', "--complete-turn"],
    ["--worker", "turn-resume", "--validation-command-json", '["validator"]', "--context-delivery", "on"],
])
def test_unsupported_arm_rejected_before_trial_reservation(tmp_path, args, capsys):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import main

    with pytest.raises(SystemExit) as error:
        main(["--log-dir", str(tmp_path), "--task", "fixture", "--tasks-dir", str(tmp_path),
              "--run-id", "fixture", "--model", "fixture", "--effort", "high",
              "--judge-url", "http://example.invalid", *args])
    assert error.value.code == 2
    if "--complete-turn" in args:
        assert "complete_turn requires heartbeat execution" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("scenario", ["reject-invalid-artifact", "recover-committed",
                                      "correct-receipt", "hold-repeated-rejection"])
def test_turn_arm_settles_only_independently_validated_original_work(tmp_path, monkeypatch, scenario):
    lose_reply = scenario == "recover-committed"
    correct_receipt = scenario in {"correct-receipt", "hold-repeated-rejection"}
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import BenchmarkCodex, SForgeWorker
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture

    # Use the public Turn fixture's artifact and Host. Only the model and
    # container install are substituted. No live judge or credential is used.
    src = Path(__file__).resolve().parents[2]
    fixture = runpy.run_path(str(src / "examples/loopx-turn-codex-cli-e2e-smoke.py"))
    project, runtime, _, registry = fixture["_write_fixture"](tmp_path, turn_count=2)
    goal, agent = fixture["GOAL_ID"], fixture["AGENT_ID"]
    initialize_canonical_source_fixture(runtime, goal,
        state_path=project / ".codex" / "goals" / goal / "ACTIVE_GOAL_STATE.md", provider="sqlite")
    codex = fixture["_write_fake_codex"](tmp_path)
    if correct_receipt:
        # The real Codex adapter supplies its structured request to the fixture.
        # Repeating the same input cannot repair this Host's receipt.
        host = codex.read_text()
        evidence = '"work_evidence": ["artifact:turn-e2e-marker"]'
        assert evidence in host
        corrected_input = repr('"correction":')
        allow_correction = scenario == "correct-receipt"
        response = f'{evidence} if {allow_correction} and {corrected_input} in prompt else ["/tmp/private-trial-artifact"]'
        codex.write_text(host.replace(evidence, response))
    skills = tmp_path / "skills"
    skills.mkdir()
    workspace_marker = project / fixture["MARKER_NAME"]
    cli = tmp_path / "loopx"
    # Lose the stdout acknowledgement after the real CLI committed.
    drop = tmp_path / "drop-reply"
    cli.write_text("#!/bin/sh\n" + 'case " $* " in *" turn run-once "*)\n'
        + f'if test -f {shlex.quote(str(drop))}; then rm {shlex.quote(str(drop))}; '
        + f'{shlex.quote(sys.executable)} -m loopx.cli "$@" >/dev/null; exit 1; fi;; esac\n'
        + f'exec {shlex.quote(sys.executable)} -m loopx.cli "$@"\n')
    cli.chmod(0o755)
    if lose_reply:
        drop.touch()
    validator = fixture["_validator_command"](fixture["_marker_value"](1 if lose_reply else 2))
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(tmp_path / "unused"))
    async def installed(self, environment):
        pass  # The test supplies the task environment's executable paths below.
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="high"),
        profile="turn-resume", cwd=str(project), validation_command=validator)
    assert worker.task_entry == "loopx-planned" and worker.execution.context == "resume"
    assert worker.resume_cmd is None and worker.stop_hook is None
    worker.install_stop_hook(None, None, tmp_path, None)
    settings = worker.runtime._worker_env(cwd=str(project))
    assert settings["LOOPX_EXECUTION_MODE"] == "turn"
    assert json.loads(settings["LOOPX_VALIDATION_COMMAND_JSON"]) == validator
    profile = json.loads((tmp_path / "worker-profile.json").read_text())
    assert profile["replan_after_effective_turns"] == 6 and profile["planning_horizon_goal"] is True
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    wake_logs = tmp_path / "logs" / "wakes"
    env = dict(os.environ) | settings | {
        "HOME": str(tmp_path / "isolated-home"), "PATH": os.environ["PATH"], "PYTHONPATH": str(src),
        "LOOPX_CLI": str(cli), "LOOPX_GOAL_ID": goal, "LOOPX_AGENT_ID": agent,
        "LOOPX_REGISTRY": str(registry), "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_CODEX_HOME": str(tmp_path / "codex-home"), "LOOPX_SHARED_SKILLS": str(skills),
        "LOOPX_WAKE_LOG_DIR": str(wake_logs), "LOOPX_CODEX_SANDBOX": "workspace-write",
        "OPENAI_BASE_URL": "http://127.0.0.1:1/v1", "OPENAI_API_KEY": "fixture",
        # The real CLI must still validate or recover with less than the former 160s reserve.
        "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 120),
        "CODEX_BIN": str(codex), "LOOPX_USAGE_PING": "0",
    }
    if correct_receipt:
        # Planned entry binds this native thread before the first execution wake.
        from dataclasses import replace
        from benchmark.runtime.codex import prepare_codex_home
        from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession

        prepare_codex_home(Path(env["LOOPX_CODEX_HOME"]),
            execution=replace(worker.execution, sandbox=env["LOOPX_CODEX_SANDBOX"]), workspace=project,
            model=env["MODEL_NAME"], effort=env["REASONING_EFFORT"], base_url=env["OPENAI_BASE_URL"],
            api_key=env["OPENAI_API_KEY"], wire_api=env.get("CODEX_WIRE_API", "responses"), skills=skills)

        planning_output = tmp_path / "planning-output.jsonl"
        planning_output.write_text(json.dumps({"type": "thread.started", "thread_id": "session-fixture-0001"}))
        planning_env = env | {"CODEX_HOME": env["LOOPX_CODEX_HOME"]}
        CodexAgentSession(planning_env, context="resume", sandbox=env["LOOPX_CODEX_SANDBOX"],
                          receipt={}).observe(planning_output)
    command = worker_command(env, python=sys.executable, source=str(src),
        state_file=str(tmp_path / "scheduler.json"), host_timeout=90) + ["--once"]
    def tick():
        subprocess.run(command, cwd=project, env=env, capture_output=True, timeout=120)
        return json.loads(sorted(wake_logs.glob("*/receipt.json"))[-1].read_text())

    first = tick()
    assert first["ok"] is False and first["validation_argv_sha256"] == profile["validation_argv_sha256"]
    assert workspace_marker.read_text() == fixture["_marker_value"](1)
    assert fixture["_quota_spend_count"](runtime) == int(lose_reply)
    pending = runtime / "benchmark-pending-turn.json"
    if correct_receipt:
        assert first["turn_execution"]["validation_stage"] == "host_result_contract"
        if scenario == "correct-receipt":
            drop.touch()  # Recover a committed correction after losing the reply.
        corrected = tick()
        assert corrected["turn_instance_id"] == first["turn_instance_id"]
        assert workspace_marker.read_text() == fixture["_marker_value"](2)
        if scenario == "correct-receipt":
            assert not corrected["ok"] and fixture["_quota_spend_count"](runtime) == 1
            replay = tick()
            assert replay["ok"] and replay["turn_execution"]["replayed"] and not pending.exists()
            assert not any(replay["turn_execution"]["effects"].values())
        else:
            assert corrected["turn_repair_required"] and not corrected["ok"]
            assert json.loads(pending.read_text())["correction_started"]
            assert fixture["_quota_spend_count"](runtime) == 0
            readback = tick()
            assert readback["turn_repair_required"] and not readback["turn_execution"]["effects"]["host_invoked"]
        assert workspace_marker.read_text() == fixture["_marker_value"](2)
        return
    if not lose_reply:
        assert first["turn_execution"]["validation"]["status"] == "failed"
        assert json.loads(pending.read_text())["resume_turn_key"] == first["turn_execution"]["resume_turn_key"]
        return

    original = json.loads(pending.read_text())
    assert "resume_turn_key" not in original
    env["LOOPX_VALIDATION_COMMAND_JSON"] = json.dumps(["different-validator"])
    rejected = tick()
    assert rejected["ok"] is False and "original validation command" in rejected["error"]
    assert json.loads(pending.read_text()) == original
    env["LOOPX_VALIDATION_COMMAND_JSON"] = json.dumps(validator)
    replay = tick()
    assert replay["ok"], replay
    assert replay["turn_execution"]["status"] == "committed" and replay["turn_execution"]["replayed"] is True
    assert replay["turn_instance_id"] == first["turn_id"] != replay["turn_id"]
    assert replay["turn_execution"]["effects"]["host_invoked"] is False
    assert replay["turn_execution"]["effects"]["quota_spent"] is False
    assert not pending.exists()
    assert workspace_marker.read_text() == fixture["_marker_value"](1)
    assert fixture["_quota_spend_count"](runtime) == 1
