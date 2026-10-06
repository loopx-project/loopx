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
from sforge.harness.run_agent import run_agent
from sforge.harness.task_spec import make_task_spec

from benchmark.runtime.sforge import DEFAULT_TIMEOUT_SECONDS, PROFILES, SForgeWorker
from benchmark.runtime.sforge_backend import RecordingDockerBackend
from benchmark.runtime.source import source_pins
from benchmark.edgebench.prompts import blind_task_prompt


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


def _write_native_final_result(trial, result, *, status, agent, task, run_id, model, effort):
    """Match the native CLI's visualizer handoff after a completed run only."""
    if status != "terminal":
        return
    final = dict(agent=agent, task=task, run_id=run_id, model=model, effort=effort,
                 **result.to_dict())
    pending = trial / "final_result.json.tmp"
    pending.write_text(json.dumps(final, indent=2, ensure_ascii=False))
    pending.replace(trial / "final_result.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--tasks-dir", required=True, type=Path)
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worker", choices=PROFILES, required=True)
    parser.add_argument("--feedback", choices=("native", "blind"), default="native")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--replan-after-turns", type=int, choices=range(1, 6),
                        help="Opt in to settled work Turn cadence for heartbeat profiles")
    parser.add_argument("--eval-interval", type=int, default=300)
    parser.add_argument("--submission-cooldown", type=int, default=120)
    parser.add_argument("--judge-url", required=True)
    parser.add_argument("--api-proxy-url", help="Operator-owned, OpenAI-only CONNECT proxy")
    args = parser.parse_args(argv)
    if args.replan_after_turns is not None and not args.worker.startswith("heartbeat-"):
        parser.error("--replan-after-turns requires a heartbeat worker")
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
    blind_prompt = None
    if args.feedback == "blind":
        proxy = urlsplit(args.api_proxy_url or "")
        judge = urlsplit(args.judge_url)
        if task.internet or task.game_mode or not proxy.hostname or not proxy.port:
            raise ValueError("Blind feedback requires a non-game isolated task and explicit API-only proxy")
        ipaddress.ip_address(proxy.hostname)  # No ambiguous DNS/network identity.
        judge_port = judge.port or (443 if judge.scheme == "https" else 80)
        if (proxy.hostname, proxy.port) == (judge.hostname, judge_port):
            raise ValueError("Blind API and judge endpoints must be distinct")
        blind_endpoint = (proxy.hostname, proxy.port)
        blind_prompt = blind_task_prompt(task.work.agent_query, task.submit_paths)
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
                         timeout_seconds=args.timeout, blind_prompt=blind_prompt,
                         replan_after_turns=args.replan_after_turns)
    if args.api_proxy_url:
        agent.default_api_base_url = args.api_proxy_url
    logger = logging.getLogger("edgebench-runtime")
    backend = RecordingDockerBackend(log_dir=trial / "collected", logger=logger,
                                     oauth_proxy=bool(args.api_proxy_url),
                                     blind_api_endpoint=blind_endpoint)
    for image in (task.work_image_key, task.judge_image_key):
        if not backend.image_exists(image):
            raise RuntimeError(f"Missing native image: {image}")
    receipt = {
        "run_id": args.run_id, "task": args.task, "worker": args.worker,
        "model": args.model, "effort": args.effort, "timeout_seconds": args.timeout,
        "loopx_commit": pins[0], "runner_commit": pins[1],
        "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "feedback": args.feedback, "internet": task.internet,
        "eval_interval": args.eval_interval, "submission_cooldown": args.submission_cooldown,
        "status": "starting", "score_countable": False,
        **({"replan_after_effective_turns": args.replan_after_turns}
           if args.replan_after_turns is not None else {}),
    }
    receipt_path = trial / "runtime-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2))
    try:
        result, was_interrupted, elapsed = _observe_run(lambda: run_agent(
            task_spec=task, agent=agent, config=config, backend=backend,
            run_id=args.run_id, model=args.model, timeout=args.timeout,
            judge_url=args.judge_url, eval_interval=args.eval_interval,
            submission_cooldown=args.submission_cooldown, internet=task.internet,
            disable_stop_hook=False, disable_auto_resume=agent.resume_cmd is None,
            max_submissions=0 if args.feedback == "blind" else None,
        ))
    except Exception as error:
        receipt.update(status="runner_failed", error_kind=type(error).__name__)
        receipt_path.write_text(json.dumps(receipt, indent=2))
        raise
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
                               model=args.model, effort=args.effort)
    receipt_path.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
    return 0 if status == "terminal" else 130 if status == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
