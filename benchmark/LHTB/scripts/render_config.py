#!/usr/bin/env python3
"""Render the immutable 46-task template for full, smoke, or preflight use."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml
from benchmark.runtime.codex import CONTEXTS, MODES, TASK_ENTRIES, Execution


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--execution-mode", choices=MODES, default="heartbeat")
    parser.add_argument("--iteration-context", choices=CONTEXTS, default="fresh")
    parser.add_argument("--task-entry", choices=TASK_ENTRIES, default="seeded-todo")
    parser.add_argument("--planning-timeout", type=float, default=300)
    parser.add_argument("--validation-command-json", default="[]")
    parser.add_argument("--turn-timeout", type=float, default=None)
    parser.add_argument("--scheduler-timeout", type=int, default=5080)
    args = parser.parse_args()

    if not 1 <= args.concurrency <= 64:
        parser.error("concurrency must be in 1..64")
    if args.timeout < 60:
        parser.error("timeout must be at least 60 seconds")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.job_name):
        parser.error("job-name contains unsafe characters")

    payload = yaml.safe_load(args.template.read_text(encoding="utf-8"))
    all_tasks = payload["datasets"][0]["task_names"]
    if len(all_tasks) != 46 or len(set(all_tasks)) != 46:
        parser.error(f"template must contain 46 unique tasks, got {len(all_tasks)}")
    selected = args.task or all_tasks
    unknown = sorted(set(selected) - set(all_tasks))
    if unknown:
        parser.error(f"unknown task(s): {', '.join(unknown)}")

    payload["job_name"] = args.job_name
    payload["jobs_dir"] = args.jobs_dir
    payload["n_concurrent_trials"] = min(args.concurrency, len(selected))
    payload["datasets"][0]["task_names"] = selected
    agent = payload["agents"][0]
    agent["model_name"] = args.model
    agent["override_timeout_sec"] = args.timeout
    agent["kwargs"]["reasoning_effort"] = args.effort
    execution = Execution(
        mode=args.execution_mode,
        context=args.iteration_context,
        timeout_seconds=args.turn_timeout,
        validation_command=json.loads(args.validation_command_json),
        task_entry=args.task_entry,
    )
    agent["kwargs"].update(
        execution_mode=execution.mode,
        iteration_context=execution.context,
        validation_command=list(execution.validation_command),
        turn_timeout_sec=execution.timeout_seconds,
        scheduler_timeout_sec=args.scheduler_timeout,
        task_entry=execution.task_entry,
        planning_timeout_sec=args.planning_timeout,
    )
    agent["kwargs"]["goals"] = str(execution.native_goal).lower()
    agent["kwargs"]["web_search"] = "disabled"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
