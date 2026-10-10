#!/usr/bin/env python3
"""Execute one admitted wake in the task environment, through product APIs."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from benchmark.runtime.codex import Execution, prepare_codex_home, process_environment


def read_json(text: str) -> dict:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def loopx_command(env: dict[str, str]) -> list[str]:
    return [
        env["LOOPX_CLI"],
        "--format",
        "json",
        "--registry",
        env["LOOPX_REGISTRY"],
        "--runtime-root",
        env["LOOPX_RUNTIME_ROOT"],
    ]


def heartbeat_body(env: dict[str, str], turn_id: str, *, native_goal: bool) -> str:
    command = loopx_command(env) + [
        "heartbeat-prompt",
        "--thin",
        "--runtime-profile",
        "codex_app_ssh_goal" if native_goal else "generic_cli",
        "--goal-id",
        env["LOOPX_GOAL_ID"],
        "--agent-id",
        env["LOOPX_AGENT_ID"],
        "--cli-bin",
        env["LOOPX_CLI"],
        "--available-capability",
        "shell",
        "--available-capability",
        "filesystem_write",
    ]
    if not native_goal:
        command += ["--turn-instance-id", turn_id]
    if env.get("LOOPX_TURN_ENVELOPE", "0") not in {"0", "1"}:
        raise ValueError("LOOPX_TURN_ENVELOPE must be 0 or 1")
    if env.get("LOOPX_TURN_ENVELOPE") == "1":
        if native_goal:
            raise ValueError("TurnEnvelope capture requires a host-owned heartbeat Turn")
        # Wake identity comes from this worker, never from model-authored text.
        root = Path(env["LOOPX_WAKE_LOG_DIR"]) / turn_id / "decisions"
        root.mkdir(mode=0o700, exist_ok=False)
        command += ["--decision-output-root", str(root)]
    result = subprocess.run(
        command,
        cwd=env["LOOPX_PROJECT"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    payload = read_json(result.stdout)
    if payload.get("ok") is not True or not payload.get("task_body"):
        raise RuntimeError("formal LoopX heartbeat renderer is not ready")
    if not native_goal and payload.get("turn_instance_id") != turn_id:
        raise RuntimeError("heartbeat Turn identity mismatch")
    return payload["task_body"].replace(
        "$HOME/.codex/loopx/registry.global.json", env["LOOPX_REGISTRY"]
    )


@contextmanager
def child_process(command: list[str], *, env: dict[str, str], stdout, stderr):
    process = subprocess.Popen(
        command,
        cwd=env["LOOPX_PROJECT"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        text=True,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        # Reap descendants even when their leader exited or Harbor cancelled us.
        try:
            # Python's standard SIGINT handler lets the Turn host's finally
            # path reap its separately grouped Codex child before forced kill.
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def turn_command(
    env: dict[str, str],
    execution: Execution,
    turn_id: str,
    resume_turn_key: str | None = None,
) -> list[str]:
    return loopx_command(env) + [
        "turn",
        "run-once",
        "--execute",
        "--host",
        "codex-cli",
        "--project",
        env["LOOPX_PROJECT"],
        "--goal-id",
        env["LOOPX_GOAL_ID"],
        "--agent-id",
        env["LOOPX_AGENT_ID"],
        "--scheduler-owner",
        "outer_controller",
        *(
            ["--resume-turn-key", resume_turn_key, "--retry-failed-turn"]
            if resume_turn_key
            else ["--turn-instance-id", turn_id]
        ),
        "--iteration-context",
        execution.context,
        "--session-scope",
        "agent",
        "--codex-reasoning-effort",
        env["REASONING_EFFORT"],
        "--codex-bin",
        env["CODEX_BIN"],
        "--codex-model",
        env["MODEL_NAME"],
        "--codex-sandbox",
        execution.sandbox,
        *(["--timeout-seconds", str(execution.timeout_seconds)]
          if execution.timeout_seconds is not None else []),
        "--validation-command-json",
        json.dumps(execution.validation_command),
        "--available-capability",
        "shell",
        "--available-capability",
        "filesystem_write",
    ]


def run_native_goal(
    env: dict[str, str], execution: Execution, body: str, receipt: dict, stderr
) -> None:
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
        NativeGoalConfig,
        NativeGoalDeadlineExceeded,
        StdioNativeGoalTransport,
        compact_native_goal_receipt,
        run_native_goal_until_terminal,
    )

    required_skills = ()
    if execution.uses_loopx:
        from loopx.capabilities.benchmark_toolkit.native_codex_profile import (
            NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS,
        )

        required_skills = NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS

    observed = []
    config = NativeGoalConfig(
        cwd=env["LOOPX_PROJECT"],
        objective=body,
        task_instruction=Path(env["LOOPX_TASK_DOC"]).read_text(encoding="utf-8"),
        model=env["MODEL_NAME"],
        effort=env["REASONING_EFFORT"],
        sandbox=execution.sandbox,
        approval_policy="never",
        required_skill_ids=required_skills,
    )
    with child_process(
        [env["CODEX_BIN"], "app-server"], env=env, stdout=subprocess.PIPE, stderr=stderr
    ) as process:
        transport = StdioNativeGoalTransport(process, response_timeout_sec=120)
        try:
            run_native_goal_until_terminal(
                transport,
                config,
                timeout_sec=execution.timeout_seconds,
                on_turn_started=observed.append,
            )
            receipt["ok"] = True
        except NativeGoalDeadlineExceeded:
            receipt["timed_out"] = True
        finally:
            if observed:
                receipt["native_goal"] = compact_native_goal_receipt(observed[0])


def native_command(env, execution, stage, wake, session_wake):
    if session_wake is None:
        # Preserve the baseline/Goal-planning entrypoint and its transport inputs.
        return [
            env["CODEX_BIN"], "exec", "--json", "--skip-git-repo-check",
            "--sandbox", execution.sandbox, "--cd", env["LOOPX_PROJECT"],
            *(["-c", "features.goals=false", "--output-schema", str(wake / "planning-schema.json"),
               "--output-last-message", str(wake / "planning-result.json")] if stage == "plan" else []),
            "-",
        ]
    from loopx.control_plane.turn_driver.codex_cli import _codex_command

    command = _codex_command(
        codex_bin=env["CODEX_BIN"], project=Path(env["LOOPX_PROJECT"]),
        schema_path=wake / "planning-schema.json" if stage == "plan" else None,
        output_path=wake / "planning-result.json" if stage == "plan" else None,
        sandbox=execution.sandbox, model=env["MODEL_NAME"],
        reasoning_effort=env["REASONING_EFFORT"],
        session_id=session_wake.session_id if session_wake else None,
        mcp_server=None,
    )
    if stage == "plan":
        command[-1:-1] = ["-c", "features.goals=false"]
    return command


def run_once(env: dict[str, str]) -> dict:
    if env.get("LOOPX_TURN_ENVELOPE", "0") not in {"0", "1"}:
        raise ValueError("LOOPX_TURN_ENVELOPE must be 0 or 1")
    execution = Execution(
        mode=env.get("LOOPX_EXECUTION_MODE", "heartbeat"),
        context=env.get("LOOPX_ITERATION_CONTEXT", "fresh"),
        sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access"),
        timeout_seconds=(float(env["LOOPX_CODEX_TURN_TIMEOUT_SEC"])
                         if env.get("LOOPX_CODEX_TURN_TIMEOUT_SEC") else None),
        validation_command=json.loads(env.get("LOOPX_VALIDATION_COMMAND_JSON", "[]")),
        task_entry=env.get("LOOPX_TASK_ENTRY"),
        turn_envelope=env.get("LOOPX_TURN_ENVELOPE", "0") == "1",
    )
    stage = env.get("LOOPX_TASK_STAGE", "execute")
    if stage not in {"plan", "execute"} or (stage == "plan" and execution.task_entry != "loopx-planned"):
        raise ValueError("invalid task-entry stage")
    turn_id = f"wake-{time.time_ns()}-{uuid.uuid4().hex[:12]}"
    log_root = Path(env["LOOPX_WAKE_LOG_DIR"])
    wake = log_root / turn_id
    wake.mkdir(parents=True, exist_ok=False)
    home = Path(env["LOOPX_CODEX_HOME"])
    env = process_environment(home, base=env)
    env["LOOPX_TURN"] = turn_id
    receipt = {
        "turn_id": turn_id,
        "mode": execution.mode,
        "context": execution.context,
        "task_entry": execution.task_entry,
        **({"turn_envelope": True} if execution.turn_envelope else {}),
        "stage": stage,
        "home_scope": "trial",
        "ok": False,
        "timed_out": False,
    }
    session_wake = None
    pending_path = (
        Path(env.get("LOOPX_RUNTIME_ROOT", str(home))) / "benchmark-pending-turn.json"
    )
    try:
        if stage == "execute" and env.get("LOOPX_PHASE_DEADLINE_EPOCH"):
            remaining = float(env["LOOPX_PHASE_DEADLINE_EPOCH"]) - time.time()
            # Reserve startup and settlement on every wake, then allow the
            # remaining time for work instead of reusing the initial timeout.
            if remaining <= 160:
                receipt.update(ok=True, budget_exhausted=True, host_invoked=False)
                return receipt
            execution = replace(
                execution, timeout_seconds=(remaining - 160 if execution.timeout_seconds is None
                                             else min(execution.timeout_seconds, remaining - 160))
            )
        prepare_codex_home(
            home,
            execution=execution,
            workspace=Path(env["LOOPX_PROJECT"]),
            model=env["MODEL_NAME"],
            effort=env["REASONING_EFFORT"],
            base_url=env.get("OPENAI_BASE_URL", ""),
            api_key=env.get("OPENAI_API_KEY", ""),
            wire_api=env.get("CODEX_WIRE_API", "responses"),
            skills=Path(env["LOOPX_SHARED_SKILLS"]) if execution.uses_loopx else None,
        )
        body = "Finish the task."
        if stage == "plan":
            from benchmark.runtime.planning import task_plan_packet

            planning_before = task_plan_packet(env, loopx_command(env))
            body = "$loopx\n\nHost-supplied planning checkpoint:\n" + json.dumps(planning_before, ensure_ascii=False)
            (wake / "planning-input.json").write_text(body, encoding="utf-8")
            (wake / "planning-schema.json").write_text(json.dumps(planning_before["result_schema"]))
        elif execution.mode in {"heartbeat", "loopx-goal"}:
            body = heartbeat_body(env, turn_id, native_goal=execution.native_goal)
        elif execution.mode == "plain":
            body = Path(env["LOOPX_TASK_DOC"]).read_text(encoding="utf-8")
        session_wake = None
        if execution.mode in {"heartbeat", "turn"} and not (execution.mode == "turn" and stage == "execute"):
            from benchmark.runtime.sessions import BenchmarkSessionWake

            session_wake = BenchmarkSessionWake(env, execution, receipt)
        with (wake / "stderr.log").open("w") as stderr:
            if execution.native_goal and stage == "execute":
                run_native_goal(env, execution, body, receipt, stderr)
            else:
                pending = {}
                if execution.mode == "turn" and stage == "execute":
                    pending_path.parent.mkdir(parents=True, exist_ok=True)
                    if pending_path.exists():
                        pending = read_json(pending_path.read_text())
                    else:
                        pending = {"turn_instance_id": turn_id}
                        pending_path.write_text(json.dumps(pending))
                command = (
                    turn_command(
                        env,
                        execution,
                        pending["turn_instance_id"],
                        pending.get("resume_turn_key"),
                    )
                    if execution.mode == "turn" and stage == "execute"
                    else native_command(env, execution, stage, wake, session_wake)
                )
                with (wake / "stdout.jsonl").open("w") as stdout:
                    with child_process(
                        command, env=env, stdout=stdout, stderr=stderr
                    ) as process:
                        allowance = 150 if execution.mode == "turn" and stage == "execute" else 0
                        if stage == "plan":
                            limit = env.get("LOOPX_PLANNING_TIMEOUT_SEC")
                            # SForge planning has no stage cap: the same absolute
                            # trial deadline bounds this subprocess and its handoff.
                            timeout = (float(limit) if limit is not None else
                                       max(0, float(env["LOOPX_PHASE_DEADLINE_EPOCH"]) - time.time()))
                        else:
                            timeout = (None if execution.timeout_seconds is None
                                       else execution.timeout_seconds + allowance)
                        process.communicate(
                            input=body, timeout=timeout
                        )
                        receipt["return_code"] = process.returncode
                        receipt["ok"] = process.returncode == 0
                if stage == "plan" and receipt["ok"]:
                    from benchmark.runtime.planning import task_plan_packet, validate_plan_readback

                    result = read_json((wake / "planning-result.json").read_text())
                    receipt["planning"] = validate_plan_readback(
                        result, planning_before, task_plan_packet(env, loopx_command(env)),
                    )
                    target = Path(env["LOOPX_PLANNING_RESULT"])
                    temporary = target.with_suffix(".tmp")
                    temporary.write_text(json.dumps(receipt["planning"]))
                    temporary.replace(target)
                elif execution.mode == "turn" and stage == "execute":
                    result = read_json((wake / "stdout.jsonl").read_text())
                    receipt["turn_execution"] = result
                    receipt["ok"] = receipt["ok"] and result.get("ok") is True
                    if receipt["ok"]:
                        pending_path.unlink()
                    elif result.get("resume_turn_key"):
                        # Product recovery owns eligibility and retry limits.
                        # Never disguise a failed transaction as a fresh Turn.
                        pending["resume_turn_key"] = result["resume_turn_key"]
                        temporary = pending_path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(pending))
                        temporary.replace(pending_path)
    except subprocess.TimeoutExpired:
        receipt["ok"] = False
        receipt["timed_out"] = True
    except BaseException as exc:
        receipt["ok"] = False
        receipt["error_kind"] = type(exc).__name__
        raise
    finally:
        try:
            if session_wake is not None:
                session_wake.observe(wake / "stdout.jsonl")
        except BaseException as exc:
            receipt.update(ok=False, error_kind=type(exc).__name__)
            raise
        finally:
            if (home / "sessions").is_dir():
                # One authoritative copy; resumed prefixes are never double counted.
                shutil.copytree(
                    home / "sessions", log_root.parent / "sessions", dirs_exist_ok=True
                )
            (wake / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    def cancelled(signum, frame):
        raise KeyboardInterrupt("worker cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    receipt = run_once(dict(os.environ))
    print(json.dumps(receipt))
    if receipt.get("budget_exhausted") and receipt["mode"] in {"heartbeat", "turn"}:
        from benchmark.runtime.scheduler import BUDGET_EXHAUSTED_EXIT_CODE

        return BUDGET_EXHAUSTED_EXIT_CODE
    return 124 if receipt["timed_out"] else (0 if receipt["ok"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
