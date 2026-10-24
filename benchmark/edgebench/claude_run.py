"""Run an independent native Claude trial with the selected SForge provider.

The judge must already be running. This entrypoint neither schedules a cohort
nor changes another experiment's sources, credentials, containers or board.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from benchmark.edgebench.source import active_source_receipt, bootstrap
from benchmark.runtime.source import checked_revision

if __name__ == "__main__":
    bootstrap()


def main(argv=None):
    from sforge.harness.benchmark import load_benchmark
    from sforge.harness.config import SForgeConfig
    from sforge.harness.run_agent import run_agent
    from sforge.harness.task_spec import make_task_spec
    from benchmark.runtime.sforge_claude import (
        CLAUDE_EFFORTS, NativeClaudeDockerBackend, NativeClaudeWorker,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--tasks-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runner-revision", required=True,
                        help="Expected clean research-runner commit")
    parser.add_argument("--judge-url", required=True)
    parser.add_argument("--claude-binary", type=Path, required=True)
    parser.add_argument("--claude-sha256", required=True)
    parser.add_argument("--claude-version", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=CLAUDE_EFFORTS, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--submission-cooldown", type=int, required=True)
    parser.add_argument("--worker-cpus", type=int, default=2)
    parser.add_argument("--worker-memory", default="4g")
    parser.add_argument("--judge-cpus", type=int, default=1)
    parser.add_argument("--judge-memory", default="2g")
    args = parser.parse_args(argv)
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
           for value in (args.task, args.run_id)):
        parser.error("task and run identifiers must be simple names")
    if args.timeout <= 0 or args.submission_cooldown < 0:
        parser.error("timeout must be positive and submission cooldown non-negative")
    if min(args.worker_cpus, args.judge_cpus) <= 0:
        parser.error("resource limits must be positive")
    runner_revision = checked_revision(Path(__file__).resolve().parents[2], args.runner_revision)
    config = SForgeConfig(
        agent_api_base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        agent_api_key=os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"),
        agent_model=args.model, agent_effort=args.effort, agent_timeout=args.timeout,
        log_dir=args.log_dir, tasks_dir=args.tasks_dir,
        work_cpu_limit=args.worker_cpus, work_mem_limit=args.worker_memory,
        judge_cpu_limit=args.judge_cpus, judge_mem_limit=args.judge_memory,
    )
    agent = NativeClaudeWorker(config, binary=args.claude_binary,
                              sha256=args.claude_sha256, version=args.claude_version)
    task = make_task_spec(args.tasks_dir / f"{args.task}.json", load_benchmark(args.tasks_dir))
    if task.game_mode:
        parser.error("This entrypoint qualifies file-submission tasks only")
    os.umask(0o077)
    # Admission, total deadline, submission limits, scoring, auto-resume and
    # cleanup are native provider responsibilities. No extra model loop here.
    result = run_agent(task_spec=task, agent=agent, config=config, backend=NativeClaudeDockerBackend(),
        run_id=args.run_id, model=args.model, timeout=args.timeout,
        judge_url=args.judge_url, eval_interval=0, disable_auto_eval=True,
        disable_stop_hook=False, disable_auto_resume=False,
        submission_cooldown=args.submission_cooldown, internet=task.internet)
    receipt = {"agent": agent.name, "model": args.model, "effort": args.effort,
               "claude_version": args.claude_version, "binary_sha256": args.claude_sha256,
               "runner_revision": runner_revision,
               "provider": active_source_receipt(), "result": result.to_dict()}
    target = args.log_dir / "runs" / args.run_id / task.task_id / "claude-result.json"
    target.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    # SForge preserves structured execution evidence even when it catches an
    # exception. Do not turn a swallowed launch failure into CLI success.
    execution = result.solver_execution or {}
    return 0 if (result.runtime_seconds > 0 and result.grading_status == "complete"
                 and execution.get("status") in {"normal_exit", "timed_out"}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
