"""Score every preserved best-only capture after the online cohort has stopped.

Uses the native grader and selector, with a separate host-only result tree.
Never rewrites online admission, its incumbent, notifications or native receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from sforge.harness import grading, run_evaluation, selection, task_spec
from sforge.harness.benchmark import load_benchmark
from sforge.harness.config import load_config, create_backend_from_config
from sforge.harness.run_evaluation import judge_submission
from sforge.harness.selection import select_best
from sforge.harness.task_spec import make_task_spec
from .online_judge import cohort_lock, resource_preflight


def score_captures(trial, task, config, backend):
    """Called under the host cohort lock; each native evaluation is sequential."""
    receipt = json.loads((trial / "runtime-receipt.json").read_text())
    if receipt.get("status") not in {"terminal", "cancelled"} or receipt.get("feedback") != "best-only":
        raise ValueError("Offline scoring requires a terminal best-only solver")
    if receipt.get("task") != task.task_id:
        raise ValueError("Offline task differs from the trial")
    index = json.loads((trial / "online-captures/index.json").read_text())
    records = index["captures"]
    if not records or not any(r["status"] == "final_offline" for r in records):
        raise ValueError("Final capture is missing; preserve/reconcile it before scoring")
    identities = [r["capture_id"] for r in records]
    if len(set(identities)) != len(identities) or any(not re.fullmatch(r"capture-[0-9]+", x) for x in identities):
        raise ValueError("Capture index has duplicate or unsafe identities")
    evaluator_digest = hashlib.sha256(b"".join(Path(module.__file__).read_bytes()
        for module in (grading, run_evaluation, selection, task_spec))).hexdigest()
    destination = trial / "offline-scoring"
    destination.mkdir(exist_ok=True)
    entries = []
    for record in records:
        identifier = record["capture_id"]
        source = trial / "online-captures" / (identifier + ".tar.gz")
        archive = source.read_bytes()
        digest = hashlib.sha256(archive).hexdigest()
        if digest != record["source_sha256"]:
            raise ValueError("Capture archive differs from its original digest")
        directory = destination / identifier
        directory.mkdir(exist_ok=True)
        result_path = directory / "score.json"
        if result_path.is_file():
            entry = json.loads(result_path.read_text())
            if (entry["source_sha256"] != digest or entry["task_sha256"] != receipt["task_sha256"]
                    or entry.get("evaluator_sha256") != evaluator_digest):
                raise ValueError("Offline score provenance differs from the trial")
        else:
            entry = dict(type="submission", round=identifier, source_sha256=digest,
                         task_sha256=receipt["task_sha256"], evaluator_sha256=evaluator_digest,
                         captured_at=record["captured_at"])
            try:
                report = judge_submission(task_spec=task, archive=archive, config=config,
                    backend=backend, submission_id=identifier, log_dir=directory / "native",
                    submitted_at=record["captured_at"])
                entry.update(report.to_dict(), status="completed")
            except Exception as error:
                entry.update(status="error", error_kind=type(error).__name__)
            pending = result_path.with_suffix(".tmp")
            pending.write_text(json.dumps(entry, allow_nan=False))
            pending.replace(result_path)
        entries.append(entry)
    result = dict(run_id=receipt["run_id"], task=task.task_id,
                  offline_scoring_complete=all(e["status"] == "completed" for e in entries),
                  evaluated_captures=len(entries), failed_captures=sum(e["status"] == "error" for e in entries),
                  **select_best(entries, task.judge.score_direction, task.judge.selection), entries=entries)
    path = destination / "result.json"
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(result, allow_nan=False))
    pending.replace(path)
    return result


def main():
    import docker
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--tasks-dir", type=Path, required=True)
    args = parser.parse_args()
    trial = args.trial.resolve()
    receipt = json.loads((trial / "runtime-receipt.json").read_text())
    task_file = args.tasks_dir / (receipt["task"] + ".json")
    if hashlib.sha256(task_file.read_bytes()).hexdigest() != receipt["task_sha256"]:
        parser.error("Task source changed since the frozen trial")
    task = make_task_spec(task_file, load_benchmark(args.tasks_dir))
    config = replace(load_config(), backend="docker", tasks_dir=args.tasks_dir,
                     judge_cpu_limit=4, judge_mem_limit="8g")
    try:
        with cohort_lock():
            client = docker.from_env()
            try:
                resource_preflight(client, 1, worker_cpu=0, worker_memory=0)
            finally:
                client.close()
            result = score_captures(trial, task, config, create_backend_from_config(config))
    except BlockingIOError:
        parser.error("Online cohort still owns resources; defer offline scoring")
    print(json.dumps({k: v for k, v in result.items() if k != "entries"}))
    return 0 if result["offline_scoring_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
