"""Host evidence for opt-in EdgeBench terminal reconciliation.

The runner keeps the binding and proof in its attempt directory. A controller
can pass the proof to ``loopx benchmark terminal-reconcile`` on the registered
project. The runner needs no project or Goal access.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import stat

from loopx.capabilities.benchmark_toolkit.experiment_identity import (
    ARM_ROLES,
    EXPERIMENT_TOKEN_PATTERN,
)
from loopx.capabilities.benchmark_toolkit.runtime_continuity import (
    BenchmarkEventWindowState,
    build_benchmark_runtime_continuity,
)
from loopx.control_plane.runtime.document_io import atomic_write_state_text
from loopx.file_lock import write_json_atomic
from loopx.public_safe_text import LETTER_LED_COMPACT_TOKEN_PATTERN
from .result_publication import native_grading_complete


BINDING_FILENAME = "managed-run.json"
TERMINAL_FILENAME = "managed-terminal.json"
RUNTIME_FILENAME = "runtime-receipt.json"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_HISTORY_BYTES = 16 * 1024 * 1024
# These two native artifacts carry every solver segment, including repeated
# resumes. Keep their budget separate from the compact managed control records.
_MAX_NATIVE_SOLVER_EVIDENCE_BYTES = 64 * 1024 * 1024
_IDENTITY_FIELDS = {"benchmark_id", "study_id", "case_id", "run_id", "arm_id", "arm_role", "attempt"}
_BOARD_STATUS = {"terminal": "completed", "runner_failed": "runner_invalid", "cancelled": "cancelled"}
_NATIVE_TERMINAL_STATUS = {"normal_exit", "nonzero_exit", "timed_out", "cancelled", "signaled"}
_NATIVE_HISTORY_FILES = {"run_history.json", "game_history.json"}


def _read_record(path: Path, *, private: bool = False, limit: int = _MAX_JSON_BYTES) -> tuple[dict, str]:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Managed run evidence must be a bounded regular file")
        if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("Managed run record must be owner-only")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Managed run evidence exceeds its size limit")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("Managed run evidence must be valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError("Managed run evidence must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _timestamp(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("Managed run evidence requires a timestamp with a timezone")
    return parsed


def _validate_binding(binding: dict, *, run_id: str, task_id: str) -> None:
    if set(binding) != {"goal_id", "project", "admission_receipt", "identity"}:
        raise ValueError("Managed run binding requires Goal, project, admission and identity")
    goal_id, project = binding["goal_id"], binding["project"]
    if not isinstance(goal_id, str) or not LETTER_LED_COMPACT_TOKEN_PATTERN.fullmatch(goal_id):
        raise ValueError("Invalid managed run Goal identity")
    if not isinstance(project, str) or "\0" in project or not Path(project).is_absolute():
        raise ValueError("Managed run project must be an absolute controller path")
    identity = binding["identity"]
    if not isinstance(identity, dict) or set(identity) != _IDENTITY_FIELDS:
        raise ValueError("Managed run requires the complete board identity")
    for field in _IDENTITY_FIELDS - {"attempt"}:
        if not isinstance(identity[field], str) or not EXPERIMENT_TOKEN_PATTERN.fullmatch(identity[field]):
            raise ValueError(f"Invalid managed run {field}")
    attempt = identity["attempt"]
    if type(attempt) is not int or not 0 <= attempt < 2 ** 53 or identity["arm_role"] not in ARM_ROLES:
        raise ValueError("Invalid managed run attempt or arm role")
    if (identity["run_id"], identity["case_id"]) != (run_id, task_id):
        raise ValueError("Managed run identity differs from the selected run and task")
    receipt = binding["admission_receipt"]
    if (not isinstance(receipt, dict) or receipt.get("ok") is not True
            or receipt.get("admitted") is not True or receipt.get("dry_run") is not False
            or receipt.get("idempotent") is not False or receipt.get("write_performed") is not True
            or receipt.get("goal_id") != goal_id):
        raise ValueError("Managed run requires the original new admission for the selected Goal")
    run = receipt.get("run")
    if not isinstance(run, dict) or any(run.get(key) != identity[key] for key in ("run_id", "case_id", "arm_role")):
        raise ValueError("Managed run admission differs from the selected identity")
    _timestamp(run.get("admitted_at"))


def read_managed_run(path: Path, *, run_id: str, task_id: str) -> dict:
    """Bind the explicit launch to its executed admission before starting it."""
    binding, _ = _read_record(path, private=True)
    _validate_binding(binding, run_id=run_id, task_id=task_id)
    return binding


def _runtime_digest(receipt: dict) -> str:
    source = receipt.get("sforge_source")
    pins = [receipt.get("loopx_commit"), receipt.get("runner_commit"),
            source.get("commit") if isinstance(source, dict) else None, receipt.get("task_sha256")]
    if any(not isinstance(pin, str) or not pin for pin in pins):
        raise ValueError("Managed terminal evidence requires the pinned launch sources and task")
    return hashlib.sha256(json.dumps(pins, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_runtime_receipt(trial: Path, receipt: dict, retained: dict) -> None:
    write_json_atomic(trial / RUNTIME_FILENAME, {**receipt, "managed_run": retained},
                      mode=0o600, fsync_directory=True)


def persist_managed_run(trial: Path, binding: dict, *, receipt: dict, game_mode: bool) -> dict:
    """Retain private launch provenance before starting the native solver."""
    path = trial / BINDING_FILENAME
    runtime_digest = _runtime_digest(receipt)
    write_json_atomic(path, binding, mode=0o600, fsync_directory=True)
    saved, digest = _read_record(path, private=True)
    if saved != binding:
        raise ValueError("Managed run binding readback mismatch")
    retained = {
        "binding_sha256": digest,
        "launch_runtime_sha256": runtime_digest,
        "history_file": "game_history.json" if game_mode else "run_history.json",
    }
    _write_runtime_receipt(trial, receipt, retained)
    return retained


def _solver_ended(execution: dict, *, admitted_at: float, observed_at: float) -> bool:
    segments = execution.get("segments")
    if type(execution.get("schema_version")) is not int or execution["schema_version"] != 1:
        return False
    if not isinstance(segments, list) or not segments:
        return False
    previous_end = admitted_at
    for index, segment in enumerate(segments, 1):
        if (not isinstance(segment, dict) or type(segment.get("index")) is not int
                or segment["index"] != index or segment.get("terminal_observed") is not True
                or segment.get("status") not in _NATIVE_TERMINAL_STATUS):
            return False
        started, finished = segment.get("started_at"), segment.get("finished_at")
        if (type(started) not in (int, float) or type(finished) not in (int, float)
                or not previous_end <= started <= finished <= observed_at):
            return False
        previous_end = finished
    return execution.get("status") == segments[-1]["status"]


def retain_managed_solver(trial: Path, binding: dict, launch: dict, *, receipt: dict, result) -> bool:
    """Bind the exact native solver snapshot to the returned runtime receipt."""
    retained = dict(launch)
    if receipt["status"] in _BOARD_STATUS and getattr(result, "solver_execution", None) is not None:
        execution, digest = _read_record(
            trial / "solver_execution.json", limit=_MAX_NATIVE_SOLVER_EVIDENCE_BYTES,
        )
        admitted = _timestamp(binding["admission_receipt"]["run"]["admitted_at"])
        if (execution == result.solver_execution and _solver_ended(
                execution, admitted_at=admitted.timestamp(), observed_at=datetime.now(UTC).timestamp())):
            retained["solver_execution_sha256"] = digest
    try:
        _write_runtime_receipt(trial, receipt, retained)
    except OSError:
        # An error after replacement can leave the exact terminal record visible.
        # An error before replacement allows one retry from the original launch.
        observed, _ = _read_record(trial / RUNTIME_FILENAME, private=True)
        expected = {**receipt, "managed_run": retained}
        if json.dumps(observed) != json.dumps(expected):
            if (observed.get("status") != "starting" or observed.get("managed_run") != launch
                    or _runtime_digest(observed) != launch["launch_runtime_sha256"]
                    or any(observed.get(key) != receipt.get(key) for key in ("run_id", "task", "feedback"))):
                raise
            _write_runtime_receipt(trial, receipt, retained)
    return "solver_execution_sha256" in retained


def _terminal_history_entry(entry, *, task_id: str) -> bool:
    if (not isinstance(entry, dict) or entry.get("task_id", task_id) != task_id
            or entry.get("type", "submission") not in ("submission", "game")):
        return False
    status = entry.get("status")
    # SForge treats its legacy scored entries as terminal when status is absent.
    return status in ("completed", "error") or (
        status is None and any(field in entry for field in ("score", "pass_rate", "max_score"))
    )


def native_grading_drained(trial: Path, *, history_file: str, run_id: str, task_id: str) -> bool:
    try:
        grading, _ = _read_record(trial / "grading_status.json")
    except FileNotFoundError:
        return False
    if (type(grading.get("schema_version")) is not int or grading["schema_version"] != 1
            or grading.get("status") != "complete" or grading.get("reason") != "drained"
            or type(grading.get("pending_submissions")) is not int or grading["pending_submissions"] != 0
            or grading.get("history_observed") is not True
            or grading.get("history_file") != history_file):
        return False
    history, digest = _read_record(trial / history_file, limit=_MAX_HISTORY_BYTES)
    entries = history.get("entries")
    return (grading.get("history_sha256") == digest and history.get("run_id") == run_id
            and isinstance(entries, list)
            and all(_terminal_history_entry(entry, task_id=task_id) for entry in entries))


def _retained_completed_result(trial: Path, receipt: dict) -> dict | None:
    """Read the runner's completion marker when its terminal receipt did not land."""
    try:
        result, _ = _read_record(trial / "final_result.json", limit=_MAX_NATIVE_SOLVER_EVIDENCE_BYTES)
    except FileNotFoundError:
        return None
    duration = result.get("runtime_seconds")
    if (any(result.get(field) != receipt.get(field) for field in ("run_id", "task", "model", "effort"))
            or result.get("runtime_status", "terminal") != "terminal"
            or result.get("evaluation_coverage") == "online_only"
            or not native_grading_complete(result)
            or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0):
        return None
    return result


def publish_managed_terminal(trial: Path, *, execute: bool = False) -> bool:
    """Check retained native evidence and optionally publish this attempt's proof."""
    if (trial.parent.parent.name != "runs" or not trial.is_dir()
            or any(path.is_symlink() for path in (trial, trial.parent, trial.parent.parent))):
        raise ValueError("Managed run requires its exact physical attempt directory")
    run_id, task_id = trial.parent.name, trial.name
    binding, binding_digest = _read_record(trial / BINDING_FILENAME, private=True)
    _validate_binding(binding, run_id=run_id, task_id=task_id)
    receipt, _ = _read_record(trial / RUNTIME_FILENAME, private=True)
    if (receipt.get("run_id"), receipt.get("task"), receipt.get("feedback")) != (run_id, task_id, "native"):
        raise ValueError("Managed runtime receipt differs from the selected attempt")
    status = _BOARD_STATUS.get(receipt.get("status"))
    retained = receipt.get("managed_run")
    if not isinstance(retained, dict):
        return False
    recovered_completion = None
    if receipt.get("status") == "starting":
        recovered_completion = _retained_completed_result(trial, receipt)
        if recovered_completion is None:
            return False
        status = _BOARD_STATUS["terminal"]
    elif status is None or "solver_execution_sha256" not in retained:
        return False
    history_file = retained.get("history_file")
    if history_file not in _NATIVE_HISTORY_FILES:
        raise ValueError("Retained managed grading history path differs")
    execution, solver_digest = _read_record(
        trial / "solver_execution.json", limit=_MAX_NATIVE_SOLVER_EVIDENCE_BYTES,
    )
    if recovered_completion is None:
        solver_matches = retained.get("solver_execution_sha256") == solver_digest
    else:
        solver_matches = json.dumps(execution, sort_keys=True) == json.dumps(
            recovered_completion.get("solver_execution"), sort_keys=True)
    admitted = _timestamp(binding["admission_receipt"]["run"]["admitted_at"])
    if not solver_matches or not _solver_ended(execution, admitted_at=admitted.timestamp(),
                                               observed_at=datetime.now(UTC).timestamp()):
        raise ValueError("Retained solver termination evidence differs")
    if not native_grading_drained(trial, history_file=history_file, run_id=run_id, task_id=task_id):
        return False
    continuity = build_benchmark_runtime_continuity(
        launch_runtime_digest=retained.get("launch_runtime_sha256"),
        closeout_runtime_digest=_runtime_digest(receipt),
        launch_generation_digest=retained.get("binding_sha256"),
        closeout_generation_digest=binding_digest,
        event_window_state=BenchmarkEventWindowState.QUALIFIED,
    )
    if continuity["closeout_write_allowed"] is not True:
        raise ValueError("Retained managed run continuity differs")

    observed = datetime.now(UTC)
    identity = binding["identity"]
    proof = {
        "admission_receipt": binding["admission_receipt"],
        "identity": identity,
        "terminal": {
            **identity, "status": status, "observed_at": observed.isoformat().replace("+00:00", "Z"),
            "solver_terminal": True, "grading_drained": True,
        },
    }
    path = trial / TERMINAL_FILENAME
    if execute:
        try:
            atomic_write_state_text(path, json.dumps(proof, ensure_ascii=False, indent=2) + "\n", create_only=True)
        except FileExistsError:
            pass  # A concurrent replay can only adopt the same published proof.
    if execute or path.exists() or path.is_symlink():
        existing, _ = _read_record(path, private=True)
        terminal = existing.get("terminal")
        finished = execution["segments"][-1]["finished_at"]
        if not isinstance(terminal, dict) or _timestamp(terminal.get("observed_at")).timestamp() < finished:
            raise ValueError("Existing managed terminal proof has an invalid timestamp")
        proof["terminal"]["observed_at"] = terminal["observed_at"]
        if existing != proof:
            raise ValueError("Existing managed terminal proof differs from the retained attempt")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, type=Path, help="Exact host-owned runs/RUN_ID/TASK_ID directory")
    parser.add_argument("--execute", action="store_true", help="Publish the proof after validating retained evidence")
    args = parser.parse_args(argv)
    try:
        ready = publish_managed_terminal(args.trial, execute=args.execute)
    except (OSError, TypeError, ValueError) as error:
        parser.error(f"Retained managed terminal evidence rejected ({type(error).__name__})")
    print(json.dumps({"ready": ready, "dry_run": not args.execute}))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
