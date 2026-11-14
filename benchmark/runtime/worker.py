#!/usr/bin/env python3
"""Execute one admitted wake in the task environment, through product APIs."""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import time
import uuid
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path

from benchmark.runtime.codex import Execution, explicit_turn_display, prepare_codex_home, process_environment

from benchmark.runtime.scheduler import PHASE_WORK_RESERVE_SECONDS as PHASE_WORK_RESERVE_SECONDS
from benchmark.runtime.scheduler import phase_work_reserve


def read_json(text: str) -> dict:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def host_output_paths(wake: Path) -> tuple[Path, Path]:
    """Paths written by a native Host attempt, in stdout/stderr order."""
    return wake / "stdout.jsonl", wake / "stderr.log"


def heartbeat_host_failure(wake: Path, receipt: dict) -> dict:
    """Adapt completed native process errors to the existing Host policy record.

    A missing/truncated exchange or a harness/session error stays unknown; the
    shell supervisor must not replay it as a transient provider failure.
    """
    from loopx.control_plane.turn_driver.codex_cli import CodexFailureObservation
    from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
    from loopx.control_plane.turn_driver.host_process_transport import HostOutputLines

    failure = CodexFailureObservation()
    complete = not receipt.get("timed_out") and not receipt.get("error_kind")

    def observe_stdout(line: str) -> None:
        nonlocal complete
        if line.strip() and not failure.observe_stdout(line):
            complete = False

    for path, consume in zip(host_output_paths(wake), (observe_stdout, failure.observe_stderr)):
        lines = HostOutputLines(consume)
        try:
            with path.open(encoding="utf-8") as stream:
                while chunk := stream.read(65536):
                    lines.feed(chunk)
            lines.finish()
        except (OSError, UnicodeError):
            complete = False
        complete = complete and lines.complete
    return build_host_failure_record(failure.failure_kind(complete=bool(complete)), attempt=1)


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


def heartbeat_body(env: dict[str, str], turn_id: str, *, native_goal: bool,
                   decision_handoff_socket: str | None = None) -> str:
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
    display = explicit_turn_display(env)
    context_dir = env.get("LOOPX_CODEX_CONTEXT_DIR") if env.get("LOOPX_CONTEXT_DELIVERY") == "on" else None
    if display.complete_turn or display.turn_envelope or decision_handoff_socket is not None or context_dir:
        if native_goal:
            raise ValueError("TurnEnvelope capture requires a host-owned heartbeat Turn")
        # Wake identity comes from this worker, never from model-authored text.
        root = Path(env["LOOPX_WAKE_LOG_DIR"]) / turn_id / "decisions"
        root.mkdir(mode=0o700, exist_ok=False)
        command += ["--decision-output-root", str(root)]
        if display.turn_envelope:
            command += ["--turn-envelope"]
        if display.complete_turn:
            command += ["--complete-turn"]
        if decision_handoff_socket is not None:
            command += ["--decision-handoff-socket", decision_handoff_socket]
        if context_dir:
            command += ["--context-delivery", "on", "--codex-context-dir", context_dir]
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
    body = payload["task_body"].replace(
        "$HOME/.codex/loopx/registry.global.json", env["LOOPX_REGISTRY"]
    )
    from .research_probe import runtime_execution_guidance

    from .interruption_recovery import recovery_handoff
    _, handoff = recovery_handoff(env)
    return body + runtime_execution_guidance(env) + ("\nRecovery handoff (content only):\n" + handoff if handoff else "")


@contextmanager
def child_process(command: list[str], *, env: dict[str, str], stdout, stderr,
                  goal_admission=None, check_current_launch=None, process_observation=None):
    process = None
    scope_error = None
    try:
        if check_current_launch is not None and goal_admission is None:
            raise ValueError("current Host launch check requires its original Goal admission")
        if check_current_launch is not None:
            goal_admission = goal_admission.with_launch_decision(check_current_launch)
        # Session selection may precede rendering/capture. Serialize only actual
        # launch with the existing Goal lifetime owner, never the whole model run.
        with (goal_admission.launch_lifetime(operation="codex_agent_host_launch")
              if goal_admission is not None else nullcontext()):
            # The child can write a native event before Popen returns.
            launch_started_at_unix = time.time()
            process = subprocess.Popen(
                command,
                cwd=env["LOOPX_PROJECT"],
                env=env,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
                # Opted-in replay checkpoints require owner-only native writes.
                # This affects the isolated child, never the parent or off mode.
                umask=0o077 if os.name == "posix" and env.get("LOOPX_CONTEXT_DELIVERY") == "on" else -1,
            )
        if process_observation is not None:
            process_observation.started(process, launch_started_at_unix=launch_started_at_unix)
        yield process
    except BaseException as exc:
        scope_error = type(exc).__name__
        raise
    finally:
        if process is not None:
            cleanup_error = None
            try:
                if process_observation is not None:
                    process_observation.before_cleanup(process, scope_error)
            finally:
                try:
                    _reap_child_process(process, process_observation=process_observation)
                except BaseException as exc:
                    cleanup_error = type(exc).__name__
                    raise
                finally:
                    if process_observation is not None:
                        process_observation.after_cleanup(process, cleanup_error)


def heartbeat_launch_check(env, session_wake, *, original_quota=None):
    """Carry the Host's real Turn inputs into the product's current quota owner."""
    if session_wake is None:
        return None  # Plain/native-Goal transports have no LoopX heartbeat owner.
    from loopx.cli_commands.turn_decision import current_heartbeat_host_launch_decision

    def check(registry_path):
        return current_heartbeat_host_launch_decision(
            registry_path=registry_path, runtime_root=Path(env["LOOPX_RUNTIME_ROOT"]),
            project=Path(env["LOOPX_PROJECT"]), goal_id=env["LOOPX_GOAL_ID"],
            agent_id=env["LOOPX_AGENT_ID"], turn_instance_id=env["LOOPX_TURN"],
            original_quota=original_quota, goal_ref=session_wake.goal_ref,
        )
    return check


@contextmanager
def native_child_process(command, *, env, stdout, stderr, session_wake, receipt, wake,
                         timeout=None, original_quota=None):
    """Share invocation ownership across ordinary and checkpoint launches."""
    guard = None
    scope = nullcontext(command)
    if env.get("LOOPX_PROVIDER_REQUEST_ADMISSION", "off") == "on":
        from .provider_request_admission import ProviderRequestAdmission

        guard = ProviderRequestAdmission(env, session_wake, receipt, wake,
            timeout=timeout, original_quota=original_quota)
        scope = guard.serve(command)
    native_observation = None
    if env.get("LOOPX_HOST_ATTEMPT_ID") and session_wake is not None:
        from loopx.capabilities.benchmark_toolkit.native_codex_process import NativeCodexProcessObservation

        native_observation = NativeCodexProcessObservation(lambda value: receipt.update(native_process=value))
    observation = None
    if native_observation is not None or guard is not None:
        from .interruption_recovery import ProcessObservers

        observation = ProcessObservers(native_observation, guard)
    with scope as routed:
        with child_process(routed, env=env, stdout=stdout, stderr=stderr,
                goal_admission=session_wake.admission if session_wake is not None else None,
                check_current_launch=heartbeat_launch_check(env, session_wake, original_quota=original_quota),
                **({"process_observation": observation} if observation is not None else {})) as process:
            yield process
    if guard is not None and guard.refusal_code is not None:
        from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected

        raise FirstPartyHostRuntimeRejected(guard.refusal_code)


def _reap_child_process(process, *, process_observation=None):
    """Reap the process group created by child_process, including descendants."""
    def signal_group(number):
        if process_observation is not None:
            process_observation.signal_requested(number)
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass
        except PermissionError:
            from loopx.extensions.process_runtime import _darwin_owned_group_has_exited

            # Darwin may refuse a signal to a reaped group. The shared
            # observer checks descendants before accepting that as cleanup.
            if not _darwin_owned_group_has_exited(process):
                raise

    signal_group(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    signal_group(signal.SIGKILL)
    process.wait(timeout=10)


def interrupt_checkpoint_process(process, *, timeout: float = 10) -> int:
    """Close an owned native CLI before inspecting a mid-wake checkpoint.

    A held tool response is not a native execution barrier: the native tool can
    yield and schedule more inference. Require graceful leader exit so native
    history can flush, then reap its group. An unacknowledged interruption is a
    missed capture, not a completed cursor or permission to continue elsewhere.
    This does not freeze independent workspace writers.
    """
    import math

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("checkpoint interruption requires a finite positive timeout")
    if (not isinstance(process, subprocess.Popen) or process.poll() is not None or
            os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid):
        raise ValueError("checkpoint interruption requires a live owned child process group")
    try:
        os.killpg(process.pid, signal.SIGINT)
        return process.wait(timeout=timeout)
    finally:
        _reap_child_process(process)


def pending_turn(path: Path, turn_id: str, execution: Execution) -> dict:
    """The caller holds the trial lock until the product returns or the wake exits."""
    from loopx.control_plane.runtime.document_io import atomic_write_private_json

    validator = execution.validation_receipt()
    if not path.exists():
        pending = {"turn_instance_id": turn_id, **validator}
        atomic_write_private_json(path, pending)
        return pending
    pending = read_json(path.read_text())
    if pending.get("validation_argv_sha256") != validator["validation_argv_sha256"]:
        raise ValueError("Pending Turn requires its original validation command")
    return pending


def turn_command(
    env: dict[str, str],
    execution: Execution,
    turn_id: str,
    resume_turn_key: str | None = None,
    *, recover_instance: bool = False,
) -> list[str]:
    address = ["--turn-instance-id", turn_id]
    if resume_turn_key:
        address = ["--resume-turn-key", resume_turn_key, "--retry-failed-turn"]
    elif recover_instance:
        address = ["--resume-turn-instance-id", turn_id, "--retry-failed-turn"]
    return loopx_command(env) + [
        "turn",
        "run-once",
        "--execute",
        "--no-global-sync",
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
        *address,
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


NATIVE_GOAL_CONTINUE_RESERVE_SECONDS = 300


def native_goal_rounds(run_round, *, budget_seconds, continue_after_complete, clock=time.monotonic,
                       reserve_seconds=NATIVE_GOAL_CONTINUE_RESERVE_SECONDS):
    """Drive one native Goal and, when opted in, continuation Goals until the budget ends.

    ``run_round(objective_suffix, timeout_sec)`` runs one Goal to a terminal status and
    returns its typed outcome. Only ``complete`` may start an explicitly opted-in
    continuation. Failure, blocking, cancellation and unknown outcomes stop.
    """
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import NativeGoalDeadlineExceeded

    deadline = None if budget_seconds is None else clock() + budget_seconds
    rounds = 0
    suffix = ""
    while True:
        remaining = None if deadline is None else deadline - clock()
        if remaining is not None and remaining <= 0:
            return rounds, "budget_exhausted"
        try:
            status = run_round(suffix, remaining)
        except NativeGoalDeadlineExceeded:
            return rounds + 1, "budget_exhausted"
        rounds += 1
        if status != "complete" or not continue_after_complete:
            return rounds, status
        remaining = None if deadline is None else deadline - clock()
        if remaining is not None and remaining <= reserve_seconds:
            return rounds, "complete"
        suffix = (
            f"\n\nContinuation {rounds}: the time budget is not exhausted "
            f"({int(remaining) if remaining is not None else 'unbounded'} seconds remain). "
            "The previous Goal declared completion. Continue the original task under "
            "its existing requirements and evaluation rules. Submission eligibility "
            "is determined by those rules, not by a prior improvement claim."
        )


def run_native_goal(
    env: dict[str, str], execution: Execution, body: str, receipt: dict, stderr,
    *, process_evidence_path: Path | None = None,
) -> None:
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
        NativeGoalConfig,
        compact_native_goal_receipt,
        native_goal_outcome,
        refresh_native_goal_status,
        run_native_goal_until_terminal,
    )
    from loopx.capabilities.benchmark_toolkit.native_codex_process import (
        NativeCodexProcessObservation, ObservedStdioNativeGoalTransport,
        write_process_observation,
    )

    def publish_process(snapshot):
        receipt["native_process"] = snapshot
        if process_evidence_path is not None:
            write_process_observation(process_evidence_path, snapshot)

    process_observation = NativeCodexProcessObservation(publish_process)

    required_skills = ()
    if execution.uses_loopx:
        from loopx.capabilities.benchmark_toolkit.native_codex_profile import (
            NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS,
        )

        required_skills = NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS
    if env.get("LOOPX_NATIVE_GOAL_CONTINUE", "0") not in {"0", "1"}:
        raise ValueError("LOOPX_NATIVE_GOAL_CONTINUE must be 0 or 1")
    continue_after_complete = env.get("LOOPX_NATIVE_GOAL_CONTINUE") == "1"

    observed = []
    base = dict(
        cwd=env["LOOPX_PROJECT"],
        task_instruction=Path(env["LOOPX_TASK_DOC"]).read_text(encoding="utf-8"),
        model=env["MODEL_NAME"],
        effort=env["REASONING_EFFORT"],
        sandbox=execution.sandbox,
        approval_policy="never",
        required_skill_ids=required_skills,
    )
    with child_process(
        [env["CODEX_BIN"], "app-server"], env=env, stdout=subprocess.PIPE, stderr=stderr,
        process_observation=process_observation,
    ) as process:
        transport = ObservedStdioNativeGoalTransport(
            process, observation=process_observation, response_timeout_sec=120,
        )

        def run_round(suffix, timeout_sec):
            turn = run_native_goal_until_terminal(
                transport, NativeGoalConfig(objective=body + suffix, **base),
                timeout_sec=timeout_sec, on_turn_started=observed.append,
            )
            if not turn.post_goal_status:
                refresh_native_goal_status(transport, turn)
            return native_goal_outcome(turn)

        try:
            rounds, outcome = native_goal_rounds(
                run_round, budget_seconds=execution.timeout_seconds,
                continue_after_complete=continue_after_complete,
            )
            receipt["native_goal_rounds"] = rounds
            receipt["native_goal_outcome"] = outcome
            if outcome == "budget_exhausted":
                # Reaching the budget with an active Goal is normal exhaustion for
                # a native-Goal worker, not a runner failure.
                receipt["budget_exhausted"] = True
            receipt["ok"] = outcome in {"complete", "blocked", "paused", "budget_exhausted"}
        finally:
            if observed:
                receipt["native_goal"] = compact_native_goal_receipt(observed[-1])


def native_command(env, execution, stage, wake, session_wake, *, native_hook_args=()):
    if session_wake is None:
        # Preserve the baseline/Goal-planning entrypoint and its transport inputs.
        return [
            env["CODEX_BIN"], "exec", "--json", "--skip-git-repo-check",
            "--sandbox", execution.sandbox, "--cd", env["LOOPX_PROJECT"],
            *(["-c", "features.goals=false", "--output-schema", str(wake / "planning-schema.json"),
               "--output-last-message", str(wake / "planning-result.json")] if stage == "plan" else []),
            *native_hook_args,
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
    if execution.context_delivery != "off":
        command[-1:-1] = json.loads(env.get("LOOPX_CODEX_CONTEXT_TRUST_ARGS", "[]"))
        if env.get("LOOPX_CONTEXT_DELIVERY") == "off":
            # Setup failure narrows this wake to full delivery, including the
            # native shell whose frozen settings still record the requested on.
            command[-1:-1] = ["-c", 'shell_environment_policy.set.LOOPX_CONTEXT_DELIVERY="off"']
    if stage == "plan":
        command[-1:-1] = ["-c", "features.goals=false"]
    return command


class _InertCheckpointWake:
    """No checkpoint listener: plain, native-Goal, Turn and loopx-goal wakes."""

    socket_path = None
    listener = None


def _error_tail(exc: BaseException) -> dict[str, str]:
    """Private wake evidence: the class plus a bounded message, never a traceback."""
    return {"error_kind": type(exc).__name__, "error": " ".join(str(exc).split())[:500]}


def _phase_timeout(timeout: float | None, deadline: float | None) -> tuple[float | None, bool]:
    """Keep the phase clock absolute across preparation and launch admission."""
    if deadline is not None:
        remaining = max(0, deadline - time.time())
        if timeout is None or remaining <= timeout:
            return remaining, True
    return timeout, False


def run_once(env: dict[str, str]) -> dict:
    if env.get("LOOPX_CONTEXT_DELIVERY", "off") not in {"off", "on"}:
        raise ValueError("LOOPX_CONTEXT_DELIVERY supports only off or on")
    stop_hook = env.get("LOOPX_OFFICIAL_STOP_HOOK", "0")
    if stop_hook not in {"0", "1"}:
        raise ValueError("LOOPX_OFFICIAL_STOP_HOOK must be 0 or 1")
    display = explicit_turn_display(env)
    execution = Execution(
        mode=env.get("LOOPX_EXECUTION_MODE", "heartbeat"),
        context=env.get("LOOPX_ITERATION_CONTEXT"),
        sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access"),
        timeout_seconds=(float(env["LOOPX_CODEX_TURN_TIMEOUT_SEC"])
                         if env.get("LOOPX_CODEX_TURN_TIMEOUT_SEC") else None),
        validation_command=json.loads(env.get("LOOPX_VALIDATION_COMMAND_JSON", "[]")),
        task_entry=env.get("LOOPX_TASK_ENTRY"),
        turn_envelope=display.turn_envelope,
        context_delivery=env.get("LOOPX_CONTEXT_DELIVERY"),
        native_child_limit=(int(env["LOOPX_NATIVE_CHILD_LIMIT"])
                            if env.get("LOOPX_NATIVE_CHILD_LIMIT") else None),
        complete_turn=display.complete_turn,
        official_stop_hook=stop_hook == "1",
    )
    request_admission = False
    if env.get("LOOPX_PROVIDER_REQUEST_ADMISSION", "off") != "off":
        from .provider_request_admission import request_admission_enabled

        request_admission = request_admission_enabled(env, execution.mode)
    stage = env.get("LOOPX_TASK_STAGE", "execute")
    if stage not in {"plan", "execute"} or (stage == "plan" and execution.task_entry != "loopx-planned"):
        raise ValueError("invalid task-entry stage")
    from .research_probe import execution_observation_from_env

    observation_producer = execution_observation_from_env(env)
    if observation_producer is not None and execution.mode != "heartbeat":
        raise ValueError("execution_observation requires heartbeat execution")
    turn_id = f"wake-{time.time_ns()}-{uuid.uuid4().hex[:12]}"
    log_root = Path(env["LOOPX_WAKE_LOG_DIR"])
    wake = log_root / turn_id
    wake.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = host_output_paths(wake)
    home = Path(env["LOOPX_CODEX_HOME"])
    env = process_environment(home, base=env)
    # Preserve this trial's resolved off/on choice in both preflight and solver
    # subprocesses; an explicit off must not inherit a machine-wide default.
    env["LOOPX_CONTEXT_DELIVERY"] = execution.context_delivery
    # The resolved trial treatment must not inherit an ambient parent opt-in.
    if request_admission:
        env["LOOPX_PROVIDER_REQUEST_ADMISSION"] = "on"
    else:
        env.pop("LOOPX_PROVIDER_REQUEST_ADMISSION", None)
    if execution.context_delivery == "on":
        # The CLI validates this declaration before enabling native transport.
        # Carry the resolved default as well as an explicitly supplied mode.
        env["LOOPX_EXECUTION_MODE"] = execution.mode
    # Internal transport arguments are derived afresh, never ambient authority.
    env.pop("LOOPX_CODEX_CONTEXT_DIR", None)
    env.pop("LOOPX_CODEX_CONTEXT_TRUST_ARGS", None)
    env["LOOPX_TURN"] = turn_id
    receipt = {
        "turn_id": turn_id,
        "mode": execution.mode,
        **execution.validation_receipt(),
        "context": execution.context,
        "task_entry": execution.task_entry,
        **({"execution_observation": observation_producer}
           if observation_producer is not None else {}),
        **({"turn_envelope": True, "turn_envelope_source": "explicit"}
           if execution.turn_envelope else {}),
        **({"complete_turn": True, "complete_turn_source": "explicit"}
           if execution.complete_turn else {}),
        **({"context_delivery": "on"} if execution.context_delivery != "off" else {}),
        **({"official_stop_hook": True} if execution.official_stop_hook else {}),
        **({"native_child_limit": execution.native_child_limit}
           if execution.native_child_limit is not None else {}),
        **({"provider_request_admission": "on"} if request_admission else {}),
        "stage": stage,
        "home_scope": "trial",
        "ok": False,
        "timed_out": False,
    }
    if env.get("LOOPX_HOST_ATTEMPT_ID"):
        # The scheduler supplies the transport identity, never model prose.
        receipt.update(host_attempt_id=env["LOOPX_HOST_ATTEMPT_ID"],
            goal_id=env.get("LOOPX_GOAL_ID"), agent_id=env.get("LOOPX_AGENT_ID"),
            phase_deadline_epoch=env.get("LOOPX_PHASE_DEADLINE_EPOCH"))
    session_wake = None
    phase_work_deadline = None
    official_worker_deadline = (
        time.time() + execution.timeout_seconds
        if execution.official_stop_hook and execution.timeout_seconds is not None
        else None
    )
    wake_context = ExitStack()
    pending_path = (
        Path(env.get("LOOPX_RUNTIME_ROOT", str(home))) / "benchmark-pending-turn.json"
    )
    try:
        from .interruption_recovery import recovery_handoff
        handoff, _ = recovery_handoff(env)
        if handoff is not None:
            receipt["recovery_handoff_sha256"] = handoff["sha256"]
        if stage == "execute" and env.get("LOOPX_PHASE_DEADLINE_EPOCH"):
            phase_deadline = float(env["LOOPX_PHASE_DEADLINE_EPOCH"])
            if execution.official_stop_hook and not math.isfinite(phase_deadline):
                raise ValueError("official_stop_hook requires a finite phase deadline")
            remaining = phase_deadline - time.time()
            reserve = phase_work_reserve(execution.mode, official_stop_hook=execution.official_stop_hook)
            if remaining <= reserve:
                receipt.update(ok=True, budget_exhausted=True, host_invoked=False)
                return receipt
            if execution.mode == "heartbeat" or execution.official_stop_hook:
                phase_work_deadline = phase_deadline - reserve
            execution = replace(
                execution, timeout_seconds=(remaining - reserve if execution.timeout_seconds is None
                                             else min(execution.timeout_seconds, remaining - reserve))
            )
        if execution.official_stop_hook and phase_work_deadline is None and official_worker_deadline is None:
            raise ValueError("official_stop_hook requires a finite worker or phase deadline")
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
            web_search=env.get("LOOPX_WEB_SEARCH", "disabled"),
            model_catalog=(
                Path(env["LOOPX_MODEL_CATALOG"]) if env.get("LOOPX_MODEL_CATALOG") else None
            ),
        )
        native_hook_args = ()
        if execution.official_stop_hook:
            from benchmark.runtime.native_hooks import prepare_official_stop_hook

            worker_remaining = (
                None if official_worker_deadline is None else official_worker_deadline - time.time()
            )
            remaining, phase_limited = _phase_timeout(worker_remaining, phase_work_deadline)
            assert remaining is not None  # The Stop-hook path requires a finite clock.
            try:
                native_hook_args = prepare_official_stop_hook(env, home, deadline=time.monotonic() + remaining)
            except subprocess.TimeoutExpired:
                receipt["host_invoked"] = False
                if phase_limited:
                    receipt["budget_exhausted"] = True
                raise
            receipt["official_stop_hook_verified"] = True
        if execution.mode == "heartbeat":
            # Checkpoint capture needs the LoopX control plane, which only
            # heartbeat workers stage; other modes must not import it.
            from benchmark.runtime.checkpoint_wake import CheckpointWake

            checkpoint_wake = wake_context.enter_context(CheckpointWake(env, receipt))
        else:
            checkpoint_wake = _InertCheckpointWake()
        if execution.context_delivery != "off":
            from benchmark.runtime.context_delivery import prepare_context_delivery

            try:
                context_env = dict(env)
                prepare_context_delivery(context_env, home)
                env.update(context_env)
                receipt["context_transport"] = {"available": True}
            except Exception as exc:
                # Optional presentation cannot block an otherwise admitted
                # trial. Unavailable native hooks keep ordinary full IO.
                env.pop("LOOPX_CODEX_CONTEXT_DIR", None)
                env.pop("LOOPX_CODEX_CONTEXT_TRUST_ARGS", None)
                env["LOOPX_CONTEXT_DELIVERY"] = "off"
                receipt["context_transport"] = {"available": False, **_error_tail(exc)}
        body = "Finish the task."
        if stage == "plan":
            from benchmark.runtime.planning import render_planning_prompt, task_plan_packet

            planning_before = task_plan_packet(env, loopx_command(env))
            body = render_planning_prompt(planning_before, env)
            (wake / "planning-input.json").write_text(body, encoding="utf-8")
            (wake / "planning-schema.json").write_text(json.dumps(planning_before["result_schema"]))
        elif execution.mode in {"heartbeat", "loopx-goal"}:
            body = heartbeat_body(env, turn_id, native_goal=execution.native_goal,
                                  decision_handoff_socket=checkpoint_wake.socket_path)
        elif execution.mode == "plain":
            body = Path(env["LOOPX_TASK_DOC"]).read_text(encoding="utf-8")
        session_wake = None
        if execution.mode in {"heartbeat", "turn"} and not (execution.mode == "turn" and stage == "execute"):
            from benchmark.runtime.sessions import BenchmarkSessionWake

            expected_session = env.get("LOOPX_HOST_RETRY_SESSION_ID")
            # A failed fresh wake may already have a durable native identity.
            # Retry that conversation; ordinary fresh wakes still start fresh.
            session_execution = replace(execution, context="resume") if expected_session else execution
            session_wake = BenchmarkSessionWake(env, session_execution, receipt)
            if expected_session and session_wake.session_id != expected_session:
                raise ValueError("Host retry requires its original Codex session")
            receipt["host_invoked"] = False
            if stage == "execute" and execution.mode == "heartbeat" and env.get("LOOPX_WARM_START"):
                from benchmark.runtime.checkpoint_session_refresh import prepare_session_refresh

                body = prepare_session_refresh(env, session_wake, wake) + body
                if expected_session and session_wake.session_id != expected_session:
                    raise ValueError("Host retry cannot replace its original Codex session")
        if stage == "execute" and execution.mode == "heartbeat" and env.get("LOOPX_CHECKPOINT_REQUEST"):
            from benchmark.runtime.checkpoint import capture_before_replan

            # The prepared Codex exec session carries the complete read guide
            # in developer instructions, including after compaction.
            body = capture_before_replan(env, receipt, body,
                guide_in_host=not execution.native_goal and receipt.get("context_transport", {}).get("available") is True)
        if phase_work_deadline is not None and time.time() >= phase_work_deadline:
            receipt.update(ok=True, budget_exhausted=True, host_invoked=False)
            return receipt
        if official_worker_deadline is not None and time.time() >= official_worker_deadline:
            raise subprocess.TimeoutExpired(env["CODEX_BIN"], execution.timeout_seconds)
        with stderr_path.open("w") as stderr:
            if execution.native_goal and stage == "execute":
                run_native_goal(env, execution, body, receipt, stderr,
                                process_evidence_path=wake / "native-process.json")
            else:
                pending = {}
                if execution.mode == "turn" and stage == "execute":
                    from loopx.file_lock import try_exclusive_file_lock

                    lock = wake_context.enter_context(try_exclusive_file_lock(pending_path))
                    if lock is None:
                        raise RuntimeError("Another benchmark wake owns the pending Turn")
                    pending = pending_turn(pending_path, turn_id, execution)
                    receipt["turn_instance_id"] = pending["turn_instance_id"]
                command = (
                    turn_command(
                        env,
                        execution,
                        pending["turn_instance_id"],
                        pending.get("resume_turn_key"),
                        recover_instance=pending["turn_instance_id"] != turn_id,
                    )
                    if execution.mode == "turn" and stage == "execute"
                    else native_command(
                        env, execution, stage, wake, session_wake, native_hook_args=native_hook_args
                    )
                )
                with stdout_path.open("w") as stdout:
                    allowance = 150 if execution.mode == "turn" and stage == "execute" else 0
                    if stage == "plan":
                        # Planning and execution consume the same phase budget.
                        timeout = max(0, float(env["LOOPX_PHASE_DEADLINE_EPOCH"]) - time.time())
                    else:
                        timeout = execution.timeout_seconds
                        if execution.official_stop_hook:
                            timeout = (None if official_worker_deadline is None
                                       else max(0, official_worker_deadline - time.time()))
                        elif timeout is not None:
                            timeout += allowance
                    if checkpoint_wake.listener is not None:
                        timeout, phase_limited = _phase_timeout(timeout, phase_work_deadline)
                        receipt["return_code"] = checkpoint_wake.run(
                            execution, body, wake, session_wake, stdout, stderr, timeout,
                            phase_limited=phase_limited)
                        receipt["ok"] = receipt["return_code"] == 0
                    else:
                        with native_child_process(command, env=env, stdout=stdout, stderr=stderr,
                                session_wake=session_wake, receipt=receipt, wake=wake,
                                timeout=timeout) as process:
                            if session_wake is not None:
                                receipt["host_invoked"] = True
                            timeout, phase_limited = _phase_timeout(timeout, phase_work_deadline)
                            try:
                                process.communicate(input=body, timeout=timeout)
                            except subprocess.TimeoutExpired:
                                # This specific wait selected the phase cap. A
                                # renderer/provider/independent timeout is not it.
                                if phase_limited:
                                    receipt["budget_exhausted"] = True
                                raise
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
                    result = read_json(stdout_path.read_text())
                    receipt["turn_execution"] = result
                    receipt["ok"] = receipt["ok"] and result.get("ok") is True
                    if receipt["ok"]:
                        pending_path.unlink()
                    elif result.get("resume_turn_key"):
                        # Product recovery owns eligibility and retry limits.
                        # Never disguise a failed transaction as a fresh Turn.
                        pending["resume_turn_key"] = result["resume_turn_key"]
                        from loopx.control_plane.runtime.document_io import atomic_write_private_json

                        atomic_write_private_json(pending_path, pending)
    except subprocess.TimeoutExpired:
        receipt["ok"] = False
        receipt["timed_out"] = True
    except BaseException as exc:
        receipt["ok"] = False
        receipt.update(_error_tail(exc))
        raise
    finally:
        wake_context.close()
        try:
            if session_wake is not None and receipt.get("host_invoked") is True:
                session_wake.observe(stdout_path)
        except BaseException as exc:
            receipt.update(ok=False, **_error_tail(exc))
            raise
        finally:
            if env.get("LOOPX_CHECKPOINT_REQUEST"):
                from benchmark.runtime.checkpoint import capture_after_wake

                capture_after_wake(env, receipt)
            if (home / "sessions").is_dir():
                # One authoritative copy; resumed prefixes are never double counted.
                shutil.copytree(
                    home / "sessions", log_root.parent / "sessions", dirs_exist_ok=True
                )
            if (execution.mode == "heartbeat" and stage == "execute"
                    and not receipt["ok"] and not receipt.get("budget_exhausted")):
                receipt["host_failure"] = heartbeat_host_failure(wake, receipt)
            (wake / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    def cancelled(signum, frame):
        raise KeyboardInterrupt("worker cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    if (os.name == "posix" and os.environ.get("LOOPX_CHECKPOINT_REQUEST")
            and os.environ.get("LOOPX_EXECUTION_MODE", "heartbeat") == "heartbeat"):
        # This entrypoint owns one disposable worker process. Its native Host
        # and tools inherit the soft limit; the parent launcher and hard limit
        # stay unchanged. Crash memory is not a resumable file participant.
        import resource

        _, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
    receipt = run_once(dict(os.environ))
    print(json.dumps(receipt))
    if receipt.get("native_goal_outcome") == "cancelled":
        return 130
    if receipt.get("budget_exhausted") and receipt["mode"] in {"heartbeat", "turn"}:
        from benchmark.runtime.scheduler import BUDGET_EXHAUSTED_EXIT_CODE

        return BUDGET_EXHAUSTED_EXIT_CODE
    return 124 if receipt["timed_out"] else (0 if receipt["ok"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
