"""Launch one native EdgeBench trial; run the judge with native `sforge serve`."""

from __future__ import annotations

import argparse
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

from benchmark.runtime.codex import TASK_ENTRIES
from benchmark.runtime.sforge import DEFAULT_TIMEOUT_SECONDS, PROFILES, SForgeWorker
from benchmark.runtime.sforge_backend import RecordingDockerBackend
from benchmark.runtime.source import source_pins
from benchmark.edgebench.prompts import blind_task_prompt, best_only_task_prompt
from benchmark.edgebench.online_sampling import OnlineSampler
from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_MODES, validate_best_only


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


def _result_status(*, interrupted, started, runtime_seconds):
    if interrupted:
        return "cancelled"
    if not started:
        return "launch_failed"
    return "terminal" if runtime_seconds > 0 else "runner_failed"


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
    parser.add_argument("--feedback", choices=FEEDBACK_MODES, default="best-only",
                        help="New-run default: best-only; blind/native are explicit controls")
    parser.add_argument("--turn-envelope", action="store_true",
                        help="Opt-in short heartbeat context with same-invocation full captures")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), required=True)
    parser.add_argument("--timeout", type=int,
                        help="Total trial seconds; task-defaults.json overrides the 18h fallback")
    parser.add_argument("--task-entry", choices=TASK_ENTRIES,
                        help="Heartbeat default: loopx-planned; seeded-todo is an explicit ablation")
    cadence = parser.add_mutually_exclusive_group()
    cadence.add_argument("--replan-after-turns", type=int, choices=range(1, 6),
                         help="Heartbeat default: 3 settled effective work Turns")
    cadence.add_argument("--replan-after-todos", type=int, choices=range(1, 6),
                         help="Explicit completed-Todo cadence ablation for heartbeat profiles")
    parser.add_argument("--eval-interval", type=int,
                        help="Auto-evaluation seconds; task-defaults.json overrides the 300s fallback; 0 disables")
    parser.add_argument("--submission-cooldown", type=int, default=120)
    parser.add_argument("--judge-url", required=True)
    parser.add_argument("--api-proxy-url", help="Operator-owned, OpenAI-only CONNECT proxy")
    args = parser.parse_args(argv)
    args.timeout = _task_default(args.task, "timeout_seconds", args.timeout, DEFAULT_TIMEOUT_SECONDS)
    args.eval_interval = _task_default(args.task, "eval_interval_seconds", args.eval_interval, 300)
    if args.task_entry == "loopx-planned" and not args.worker.startswith("heartbeat-"):
        parser.error("--task-entry loopx-planned requires a heartbeat worker")
    if args.turn_envelope and args.worker not in {"heartbeat-resume", "heartbeat-explore"}:
        parser.error("--turn-envelope requires a heartbeat worker")
    if (args.replan_after_turns is not None or args.replan_after_todos is not None
            ) and not args.worker.startswith("heartbeat-"):
        parser.error("Replan cadence requires a heartbeat worker")
    # One directory is one attempt: never reuse native registration or overwrite
    # source/profile evidence after an ambiguous launch.
    trial = args.log_dir / "runs" / args.run_id / args.task
    trial.mkdir(parents=True, exist_ok=False)
    product = Path(os.environ["LOOPX_SRC_DIR"]).resolve()
    runner = Path(os.environ.get("LOOPX_RUNNER_SRC_DIR", str(product))).resolve()
    pins = source_pins(product, runner, os.environ["LOOPX_EXPECTED_COMMIT"],
                       os.environ.get("LOOPX_EXPECTED_RUNNER_COMMIT"))
    task_file = args.tasks_dir / f"{args.task}.json"
    task = make_task_spec(task_file, load_benchmark(args.tasks_dir))
    blind_endpoint = None
    feedback_prompt = None
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
    config = SForgeConfig(
        agent_model=args.model, agent_effort=args.effort,
        agent_timeout=args.timeout, log_dir=args.log_dir, tasks_dir=args.tasks_dir,
        work_cpu_limit=4, work_mem_limit="16g", judge_cpu_limit=4, judge_mem_limit="8g",
    )
    if args.api_proxy_url:
        config.agent_extra_env = {
            "HTTPS_PROXY": args.api_proxy_url, "HTTP_PROXY": args.api_proxy_url,
            "NO_PROXY": f"localhost,127.0.0.1,{urlsplit(args.judge_url).hostname}",
        }
    agent = SForgeWorker(config, profile=args.worker, cwd=task.cwd,
                         timeout_seconds=args.timeout, feedback_prompt=feedback_prompt, feedback=args.feedback,
                         task_entry=args.task_entry,
                         turn_envelope=args.turn_envelope,
                         replan_after_turns=args.replan_after_turns,
                         replan_after_todos=args.replan_after_todos)
    if args.api_proxy_url:
        agent.default_api_base_url = args.api_proxy_url
    logger = logging.getLogger("edgebench-runtime")
    sampler = None
    if args.feedback == "best-only":
        sampler = OnlineSampler(trial=trial, task=task, interval=args.eval_interval,
            judge_url=args.judge_url.replace("host.docker.internal", "127.0.0.1"),
            secret=get_admin_secret(args.log_dir), logger=logger)
        sampler.qualify()  # Fail before native registration, solver creation or token spend.
    feedback = (BestOnlyFeedback(
        trial=trial, run_id=args.run_id, task_id=args.task,
        direction=task.judge.score_direction,
        judge_url=args.judge_url.replace("host.docker.internal", "127.0.0.1"),
        admin_secret=get_admin_secret(args.log_dir), logger=logger, sampler=sampler,
    ) if args.feedback == "best-only" else None)
    backend = RecordingDockerBackend(log_dir=trial / "collected", logger=logger,
                                     oauth_proxy=bool(args.api_proxy_url),
                                     blind_api_endpoint=blind_endpoint, feedback=feedback)
    for image in (task.work_image_key, task.judge_image_key):
        if not backend.image_exists(image):
            raise RuntimeError(f"Missing native image: {image}")
    receipt = {
        "run_id": args.run_id, "task": args.task, "worker": args.worker,
        "task_entry": agent.task_entry,
        "model": args.model, "effort": args.effort, "timeout_seconds": args.timeout,
        "loopx_commit": pins[0], "runner_commit": pins[1],
        **({"turn_envelope": True} if args.turn_envelope else {}),
        "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "feedback": args.feedback, "internet": task.internet,
        "eval_interval": args.eval_interval, "submission_cooldown": args.submission_cooldown,
        "status": "starting", "score_countable": False,
        **({"online_admission_epoch": sampler.epoch, "offline_scoring_complete": False}
           if sampler is not None else {}),
        **({"replan_after_effective_turns": agent.replan_after_turns}
           if agent.replan_after_turns is not None else {}),
        **({"replan_after_completed_todos": agent.replan_after_todos}
           if agent.replan_after_todos is not None else {}),
    }
    receipt_path = trial / "runtime-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))
    try:
        result, was_interrupted, elapsed = _observe_run(lambda: run_agent(
            task_spec=task, agent=agent, config=config, backend=backend,
            run_id=args.run_id, model=args.model, timeout=args.timeout,
            judge_url=args.judge_url, eval_interval=args.eval_interval,
            submission_cooldown=args.submission_cooldown, internet=task.internet,
            disable_auto_eval=args.feedback == "best-only",
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
    status = _result_status(interrupted=was_interrupted,
                            started=(trial / "started_at").is_file(),
                            runtime_seconds=result.runtime_seconds)
    receipt.update(status=status, controller_elapsed_seconds=elapsed,
                   timed_out=result.timed_out, runtime_seconds=result.runtime_seconds,
                   best_score=result.best_score, total_rounds=result.total_rounds)
    _write_native_final_result(trial, result, status=status, agent=agent.name,
                               task=task.task_id, run_id=args.run_id,
                               model=args.model, effort=args.effort, feedback=args.feedback)
    receipt_path.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
    return 0 if status == "terminal" else 130 if status == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
