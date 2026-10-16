"""Launch one native EdgeBench trial; run the judge with native `sforge serve`."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import logging
import ipaddress
import os
import signal
import time
from pathlib import Path
from urllib.parse import urlsplit

from sforge.harness.benchmark import load_benchmark
from sforge.harness.config import SForgeConfig
from sforge.harness.constants import get_admin_secret
from sforge.harness.run_agent import run_agent
from sforge.harness.task_spec import make_task_spec

from benchmark.runtime.codex import TASK_ENTRIES, turn_envelope_receipt
from loopx.control_plane.work_items.stride_policy_codec import stride_strategy_options
from benchmark.runtime.sforge import (
    DEFAULT_TIMEOUT_SECONDS, PROFILES, SForgeWorker,
)
from benchmark.runtime.sforge_backend import RecordingDockerBackend
from benchmark.runtime.source import source_pins
from benchmark.edgebench.prompts import (
    blind_task_prompt, best_only_task_prompt, native_task_prompt, native_task_instructions,
    native_task_objective,
)
from benchmark.edgebench.online_sampling import OnlineSampler
from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_MODES, validate_best_only
from benchmark.edgebench.feedback_hook import FEEDBACK_PAYLOAD


def _observe_run(call):
    """Observe Ctrl+C even when the native runner consumes KeyboardInterrupt."""
    interrupted = False
    previous = signal.getsignal(signal.SIGINT)

    def on_interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
        if callable(previous):
            previous(signum, frame)
        else:
            signal.default_int_handler(signum, frame)

    # Respect an embedding host that intentionally ignores SIGINT.
    if previous != signal.SIG_IGN:
        signal.signal(signal.SIGINT, on_interrupt)
    started = time.monotonic()
    try:
        result = call()
        return result, interrupted, time.monotonic() - started
    finally:
        signal.signal(signal.SIGINT, previous)


def _result_status(*, interrupted, started, runtime_seconds, exit_code, timed_out=False,
                   execution_started=True):
    if interrupted:
        return "cancelled"
    if not started:
        return "launch_failed"
    if not execution_started or runtime_seconds <= 0 or (not timed_out and exit_code != 0):
        return "runner_failed"
    return "terminal"


def _write_native_final_result(trial, result, *, status, agent, task, run_id, model, effort, feedback="native"):
    """Match the native CLI's visualizer handoff after a completed run only."""
    if status != "terminal":
        return
    final = dict(agent=agent, task=task, run_id=run_id, model=model, effort=effort,
                 **result.to_dict())
    if feedback == "best-only":
        final["evaluation_coverage"] = "online_only"
        final["offline_scoring_complete"] = False
    pending = trial / "final_result.json.tmp"
    pending.write_text(json.dumps(final, indent=2, ensure_ascii=False))
    pending.replace(trial / "final_result.json")


def _task_default(task_id: str, key: str, explicit: int | None, fallback: int) -> int:
    """Resolve EdgeBench task settings; explicit values retain runtime semantics."""
    if explicit is not None:
        return explicit
    defaults = json.loads(Path(__file__).with_name("task-defaults.json").read_text())
    value = defaults.get(task_id, {}).get(key, fallback)
    if type(value) is not int or value <= 0:
        raise ValueError(f"Invalid EdgeBench {key} default for {task_id}: {value!r}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--tasks-dir", required=True, type=Path)
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worker", choices=PROFILES, required=True)
    parser.add_argument("--feedback", choices=FEEDBACK_MODES, default="native",
                        help="New-run default: native public feedback; blind/best-only are explicit controls")
    parser.add_argument("--turn-envelope", action="store_true",
                        help="Opt-in short heartbeat context with same-invocation full captures")
    parser.add_argument("--feedback-first", action="store_true",
                        help="Opt-in host planning constraint: obtain one accepted native "
                             "evaluation before extended local calibration (native, loopx-planned)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), required=True)
    parser.add_argument("--timeout", type=int,
                        help="Total trial seconds; task-defaults.json overrides the 18h fallback")
    parser.add_argument("--task-entry", choices=TASK_ENTRIES,
                        help="Heartbeat default: loopx-planned; seeded-todo is an explicit ablation")
    cadence = parser.add_mutually_exclusive_group()
    cadence.add_argument("--replan-after-turns", type=int, choices=range(1, 7),
                         help="Heartbeat default: 6 settled effective work Turns")
    cadence.add_argument("--replan-after-todos", type=int, choices=range(1, 6),
                         help="Explicit completed-Todo cadence ablation for heartbeat profiles")
    parser.add_argument("--stride-strategy", choices=stride_strategy_options(),
                        help="Explicit heartbeat Goal-review strategy; omitted keeps the ordinary off treatment")
    parser.add_argument("--checkpoint-controller-file", type=Path,
                        help="Host-private native checkpoint controller; requires full fresh-replan handoff")
    parser.add_argument("--checkpoint-request", type=Path,
                        help="Explicit host JSON declaration staged into a heartbeat worker; omitted disables capture")
    parser.add_argument("--restore-checkpoint", type=Path,
                        help="Explicit complete checkpoint to continue under fresh native/Goal identities")
    parser.add_argument("--restore-sha256")
    parser.add_argument("--restore-config", type=Path)
    parser.add_argument("--restore-goal")
    parser.add_argument("--restore-agent")
    parser.add_argument("--restore-source-todo")
    parser.add_argument("--restore-research-component", action="append", default=[],
                        help="Opt-in captured file component ID to restore at its original research path; repeat per component")
    parser.add_argument("--restore-phase-extension-seconds", type=int, default=0,
                        help="Explicit authorized extra continuation time; omitted preserves the checkpoint's remaining phase")
    parser.add_argument("--eval-interval", type=int,
                        help="Native default: 0 (no periodic evaluation); other modes use task defaults; explicit 0 disables")
    parser.add_argument("--submission-cooldown", type=int,
                        help="Native default: 3600s between accepted submissions; other modes retain 120s")
    parser.add_argument("--judge-url", required=True)
    parser.add_argument("--api-proxy-url", help="Operator-owned, OpenAI-only CONNECT proxy")
    args = parser.parse_args(argv)
    api_base_url = os.environ.get("OPENAI_BASE_URL") or None
    api_key = os.environ.get("OPENAI_API_KEY") if api_base_url else None
    if api_base_url and not api_key:
        parser.error("OPENAI_BASE_URL requires OPENAI_API_KEY; no login fallback")
    args.timeout = _task_default(args.task, "timeout_seconds", args.timeout, DEFAULT_TIMEOUT_SECONDS)
    args.eval_interval = (0 if args.feedback == "native" and args.eval_interval is None else
                         _task_default(args.task, "eval_interval_seconds", args.eval_interval, 300))
    if args.submission_cooldown is None:
        args.submission_cooldown = 3600 if args.feedback == "native" else 120
    if args.eval_interval < 0 or args.submission_cooldown < 0:
        parser.error("Evaluation interval and submission cooldown must be non-negative")
    if args.task_entry == "loopx-planned" and not args.worker.startswith("heartbeat-"):
        parser.error("--task-entry loopx-planned requires a heartbeat worker")
    if args.turn_envelope and args.worker not in {"heartbeat-resume", "heartbeat-explore"}:
        parser.error("--turn-envelope requires a heartbeat worker")
    if args.feedback_first and (args.feedback != "native" or args.task_entry != "loopx-planned"
                                or not args.worker.startswith("heartbeat-")):
        parser.error("--feedback-first requires --feedback native, --task-entry loopx-planned and a heartbeat worker")
    if (args.replan_after_turns is not None or args.replan_after_todos is not None
            ) and not args.worker.startswith("heartbeat-"):
        parser.error("Replan cadence requires a heartbeat worker")
    if args.stride_strategy is not None and not args.worker.startswith("heartbeat-"):
        parser.error("--stride-strategy requires a heartbeat worker")
    checkpoint_request = None
    if args.checkpoint_request is not None:
        if not args.worker.startswith("heartbeat-"):
            parser.error("--checkpoint-request requires a heartbeat worker")
        from benchmark.runtime.checkpoint import read_checkpoint_request
        declaration, checkpoint_request = read_checkpoint_request(str(args.checkpoint_request))
    handoff = checkpoint_request is not None and declaration.get("controller_handoff", False)
    if bool(args.checkpoint_controller_file) != handoff or (handoff and args.feedback != "native"):
        parser.error("controller file and full controller_handoff must be enabled together with native feedback")
    restore_values = (args.restore_sha256, args.restore_config, args.restore_goal,
                      args.restore_agent, args.restore_source_todo)
    if bool(args.restore_checkpoint) != all(restore_values) or (not args.restore_checkpoint and any(restore_values)):
        parser.error("Warm continuation requires checkpoint, digest, config, fresh Goal/Agent and source Todo together")
    if (not 0 <= args.restore_phase_extension_seconds < 2 ** 53 or
            (args.restore_phase_extension_seconds and not args.restore_checkpoint)):
        parser.error("A non-negative phase extension requires an explicit warm checkpoint continuation")
    if args.restore_research_component and not args.restore_checkpoint:
        parser.error("Research component restoration requires an explicit warm checkpoint continuation")
    if args.restore_checkpoint and (not handoff or args.eval_interval != 0 or args.replan_after_todos is not None):
        parser.error("Warm continuation requires full native checkpoint capture, no periodic evaluation and effective-Turn cadence")
    # One directory is one attempt: never reuse native registration or overwrite
    # source/profile evidence after an ambiguous launch.
    trial = args.log_dir / "runs" / args.run_id / args.task
    if args.restore_checkpoint is None:
        trial.mkdir(parents=True, exist_ok=False)
    else:
        # Native restore exclusively reserves runs/<run_id>. Host launch state
        # must not create that directory before admission, including its clock.
        if trial.parent.exists():
            raise FileExistsError("Warm destination run already exists")
        attempt = args.log_dir / ".warm-attempts" / args.run_id
        attempt.mkdir(mode=0o700, parents=True, exist_ok=False)
    product = Path(os.environ["LOOPX_SRC_DIR"]).resolve()
    runner = Path(os.environ.get("LOOPX_RUNNER_SRC_DIR", str(product))).resolve()
    pins = source_pins(product, runner, os.environ["LOOPX_EXPECTED_COMMIT"],
                       os.environ.get("LOOPX_EXPECTED_RUNNER_COMMIT"))
    task_file = args.tasks_dir / f"{args.task}.json"
    task = make_task_spec(task_file, load_benchmark(args.tasks_dir))
    blind_endpoint = None
    feedback_prompt = None
    workspace_instructions = None
    task_objective = None
    if args.feedback != "native":
        proxy = urlsplit(args.api_proxy_url or "")
        judge = urlsplit(args.judge_url)
        if task.internet or task.game_mode or not proxy.hostname or not proxy.port:
            raise ValueError("Restricted feedback requires a non-game isolated task and explicit API-only proxy")
        ipaddress.ip_address(proxy.hostname)  # No ambiguous DNS/network identity.
        judge_port = judge.port or (443 if judge.scheme == "https" else 80)
        if (proxy.hostname, proxy.port) == (judge.hostname, judge_port):
            raise ValueError("Restricted API and judge endpoints must be distinct")
        blind_endpoint = (proxy.hostname, proxy.port)
        if args.feedback == "best-only":
            validate_best_only(task, args.eval_interval)
        render_prompt = best_only_task_prompt if args.feedback == "best-only" else blind_task_prompt
        feedback_prompt = render_prompt(task.work.agent_query, task.submit_paths)
    elif not task.game_mode:
        native_policy = dict(submission_cooldown=args.submission_cooldown,
            eval_interval=args.eval_interval, internet=task.internet,
            selection=task.judge.selection, score_direction=task.judge.score_direction)
        feedback_prompt = native_task_prompt(task.work.agent_query, task.submit_paths, **native_policy)
        workspace_instructions = native_task_instructions(task.submit_paths, **native_policy)
        task_objective = native_task_objective()
    config = SForgeConfig(
        agent_api_base_url=api_base_url, agent_api_key=api_key,
        agent_model=args.model, agent_effort=args.effort,
        agent_timeout=args.timeout, log_dir=args.log_dir, tasks_dir=args.tasks_dir,
        work_cpu_limit=4, work_mem_limit="16g",
        # A restored judge already owns its captured resource profile. Cold
        # defaults must not replace or conflict with that live registration.
        judge_cpu_limit=None if args.restore_checkpoint else 4,
        judge_mem_limit=None if args.restore_checkpoint else "8g",
    )
    if args.api_proxy_url:
        config.agent_extra_env = {
            "HTTPS_PROXY": args.api_proxy_url, "HTTP_PROXY": args.api_proxy_url,
            "NO_PROXY": f"localhost,127.0.0.1,{urlsplit(args.judge_url).hostname}",
        }
    warm_start = None
    restored_registration = None
    if args.restore_checkpoint is not None:
        from benchmark.runtime.sforge_warm_start import WarmStart
        from benchmark.edgebench.checkpoint_controller import restore
        # The native API version is a required provider prerequisite. Do not
        # cold-register if an installed provider cannot continue the checkpoint.
        from sforge.harness.restored_registration import RestoredRegistration
        # Validate session/workspace participants before native registration.
        candidate = WarmStart(args.restore_checkpoint, args.restore_sha256, args.restore_config,
            args.restore_goal, args.restore_agent, args.restore_source_todo,
            int(time.time()) + args.timeout, args.replan_after_turns or 6,
            tuple(args.restore_research_component))
        metadata = candidate.verify(task.cwd)
        snapshot = metadata["worker_file_snapshot"]
        capsule = args.restore_checkpoint / "components" / snapshot["native_capsule_component_id"]
        clock = attempt / "restored-phase-deadline"
        warm_start = candidate
    agent = SForgeWorker(config, profile=args.worker, cwd=task.cwd,
                         warm_start=warm_start,
                         timeout_seconds=args.timeout, feedback_prompt=feedback_prompt, feedback=args.feedback,
                         workspace_instructions=workspace_instructions,
                         task_objective=task_objective,
                         task_entry=args.task_entry,
                         turn_envelope=args.turn_envelope,
                         feedback_first=args.feedback_first,
                         replan_after_turns=args.replan_after_turns,
                         replan_after_todos=args.replan_after_todos,
                         **({"stride_strategy": args.stride_strategy}
                            if args.stride_strategy is not None else {}),
                         **({"checkpoint_request": checkpoint_request}
                            if checkpoint_request is not None else {}))
    if args.api_proxy_url:
        agent.default_api_base_url = args.api_proxy_url
    logger = logging.getLogger("edgebench-runtime")
    sampler = None
    if args.feedback == "best-only":
        sampler = OnlineSampler(trial=trial, task=task, interval=args.eval_interval,
            judge_url=args.judge_url.replace("host.docker.internal", "127.0.0.1"),
            task_sha256=hashlib.sha256(task_file.read_bytes()).hexdigest(),
            secret=get_admin_secret(args.log_dir), logger=logger)
        sampler.qualify()  # Fail before native registration, solver creation or token spend.
    feedback = (BestOnlyFeedback(
        trial=trial, run_id=args.run_id, task_id=args.task,
        direction=task.judge.score_direction,
        selection=task.judge.selection,
        judge_url=args.judge_url.replace("host.docker.internal", "127.0.0.1"),
        admin_secret=get_admin_secret(args.log_dir), logger=logger, sampler=sampler,
    ) if args.feedback == "best-only" else None)
    checkpoint = None
    if args.checkpoint_controller_file is not None:
        from benchmark.edgebench.checkpoint_supervisor import CheckpointSupervisor
        checkpoint = CheckpointSupervisor(args.checkpoint_controller_file, trial / "checkpoints")
    backend = RecordingDockerBackend(log_dir=trial / "collected", logger=logger,
                                     oauth_proxy=bool(args.api_proxy_url or api_base_url),
                                     blind_api_endpoint=blind_endpoint, feedback=feedback,
                                     **({"checkpoint": checkpoint} if checkpoint is not None else {}))
    for image in (task.work_image_key, task.judge_image_key):
        if not backend.image_exists(image):
            raise RuntimeError(f"Missing native image: {image}")
    if warm_start is not None:
        activated = restore(args.checkpoint_controller_file, root=capsule,
            expected_sha256=snapshot["native_capsule_sha256"], run_id=args.run_id,
            phase_deadline_file=clock, phase_extension_seconds=args.restore_phase_extension_seconds)
        epoch = int(clock.read_text())
        warm_start = replace(candidate, deadline_epoch=epoch)
        reference = json.loads((capsule / "checkpoint.json").read_bytes())["point_metadata"]["native_reference"]
        restored_registration = RestoredRegistration(reference["checkpoint_id"],
            reference["manifest_sha256"], epoch, capsule / "components/native_judge/manifest.json")
        # The native owner may already have materialized preserved submissions.
        trial.mkdir(parents=True, exist_ok=True)
        # Deliberately do not persist activation credentials in the public run receipt.
        del activated
        agent.warm_start = warm_start
    receipt = {
        "run_id": args.run_id, "task": args.task, "worker": args.worker,
        "task_entry": agent.task_entry,
        **({"planning_timeout_seconds": None}
           if agent.task_entry == "loopx-planned" else {}),
        "model": args.model, "effort": args.effort, "timeout_seconds": args.timeout,
        "loopx_commit": pins[0], "runner_commit": pins[1],
        **turn_envelope_receipt(explicit=args.turn_envelope,
            mode="heartbeat" if args.worker.startswith("heartbeat-") else "plain",
            checkpoint_request=declaration if checkpoint_request is not None else None),
        **({"planning_feedback_first": True} if args.feedback_first else {}),
        "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "feedback": args.feedback, "internet": task.internet,
        "eval_interval": args.eval_interval, "submission_cooldown": args.submission_cooldown,
        "status": "starting", "score_countable": False,
        **({"restore_phase_extension_seconds": args.restore_phase_extension_seconds}
           if args.restore_phase_extension_seconds else {}),
        **({"restored_checkpoint_sha256": warm_start.sha256,
            "original_deadline_epoch": warm_start.deadline_epoch,
            "receiver_preparation_cadence": 1, "normal_replan_cadence": warm_start.normal_cadence}
           if warm_start is not None else {}),
        **({"online_admission_epoch": sampler.epoch, "offline_scoring_complete": False}
           if sampler is not None else {}),
        **({"feedback_payload": FEEDBACK_PAYLOAD, "evaluator": sampler.evaluators[args.task]}
           if sampler is not None else {}),
        **({"replan_after_effective_turns": agent.replan_after_turns}
           if agent.replan_after_turns is not None else {}),
        **({"replan_after_completed_todos": agent.replan_after_todos}
           if agent.replan_after_todos is not None else {}),
        **({"stride_strategy": args.stride_strategy}
           if args.stride_strategy is not None else {}),
        **({"checkpoint_request_sha256": hashlib.sha256(checkpoint_request).hexdigest()}
           if checkpoint_request is not None else {}),
    }
    receipt_path = trial / "runtime-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))
    try:
        result, was_interrupted, elapsed = _observe_run(lambda: run_agent(
            task_spec=task, agent=agent, config=config, backend=backend,
            run_id=args.run_id, model=args.model, timeout=None if warm_start else args.timeout,
            judge_url=args.judge_url, eval_interval=args.eval_interval,
            submission_cooldown=args.submission_cooldown, internet=task.internet,
            disable_auto_eval=args.feedback == "best-only" or warm_start is not None,
            **({"restored_registration": restored_registration} if restored_registration is not None else {}),
            disable_stop_hook=False, disable_auto_resume=agent.resume_cmd is None,
            max_submissions=0 if args.feedback != "native" else None,
        ))
    except Exception as error:
        receipt.update(status="runner_failed", error_kind=type(error).__name__)
        receipt_path.write_text(json.dumps(receipt, indent=2))
        raise
    finally:
        if feedback is not None:
            feedback.close()
    # Native cancellation and swallowed Docker failures return runtime=0.
    # Observe the signal independently; never infer cancellation from output prose.
    execution = backend.execution_receipt or {}
    status = _result_status(interrupted=was_interrupted,
                            started=(trial / "started_at").is_file(),
                            runtime_seconds=result.runtime_seconds,
                            execution_started=execution.get("execution_started", False),
                            exit_code=execution.get("exit_code"),
                            timed_out=result.timed_out or execution.get("timed_out", False))
    receipt.update(status=status, controller_elapsed_seconds=elapsed,
                   timed_out=result.timed_out, runtime_seconds=result.runtime_seconds,
                   best_score=result.best_score, total_rounds=result.total_rounds)
    receipt["execution"] = execution
    _write_native_final_result(trial, result, status=status, agent=agent.name,
                               task=task.task_id, run_id=args.run_id,
                               model=args.model, effort=args.effort, feedback=args.feedback)
    receipt_path.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
    return 0 if status == "terminal" else 130 if status == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
