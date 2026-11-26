"""Compute dynamic CI shard session deadlines from remaining outer job budget."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

DEFAULT_JOB_START_ENV = "LOOPX_SHARD_JOB_START_EPOCH"
DIAGNOSTICS_SCHEMA_VERSION = "loopx_ci_pytest_diagnostics_v1"


def atomic_write_text(path: str | Path, content: str) -> None:
    """Atomically write UTF-8 text via a unique same-directory temporary file."""
    abs_path = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{os.path.basename(abs_path)}.",
            suffix=".tmp",
            dir=parent or ".",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, abs_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@dataclass(frozen=True)
class ShardBudgetDecision:
    """Computed remaining session budget and pre-shard admission outcome."""

    job_budget_seconds: float
    upload_reserve_seconds: float
    min_session_seconds: float
    job_start_epoch: float | None
    now_epoch: float
    elapsed_prep_seconds: float
    remaining_session_seconds: float
    admitted: bool
    reason: str | None


def _require_positive_finite(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return float(value)


def _require_nonnegative_finite(name: str, value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative, got {value!r}")
    return float(value)


def parse_job_start_epoch(
    raw_value: str | None,
    *,
    required: bool,
    source_name: str = DEFAULT_JOB_START_ENV,
) -> float | None:
    """Parse an optional or CI-required Unix epoch timestamp."""
    if raw_value is None:
        if required:
            raise ValueError(f"{source_name} is required in CI to measure setup elapsed time")
        return None
    stripped = raw_value.strip()
    if not stripped:
        raise ValueError(f"{source_name} must be a positive Unix epoch timestamp, got empty string")
    try:
        parsed = float(stripped)
    except ValueError as exc:
        raise ValueError(f"{source_name} must be a numeric Unix epoch timestamp, got {raw_value!r}") from exc
    return _require_positive_finite(source_name, parsed)


def compute_shard_budget(
    *,
    job_budget_seconds: float,
    upload_reserve_seconds: float,
    min_session_seconds: float,
    job_start_epoch: float | None,
    now_epoch: float | None = None,
) -> ShardBudgetDecision:
    """Compute remaining pytest deadline after subtracting prep time and upload reserve."""
    budget = _require_positive_finite("job_budget_seconds", job_budget_seconds)
    reserve = _require_nonnegative_finite("upload_reserve_seconds", upload_reserve_seconds)
    minimum = _require_positive_finite("min_session_seconds", min_session_seconds)
    effective_now = time.time() if now_epoch is None else now_epoch
    _require_positive_finite("now_epoch", effective_now)

    if job_start_epoch is not None:
        start = _require_positive_finite("job_start_epoch", job_start_epoch)
        elapsed_prep = max(0.0, effective_now - start)
    else:
        start = None
        elapsed_prep = 0.0

    remaining = budget - elapsed_prep - reserve
    admitted = remaining >= minimum
    reason = (
        None
        if admitted
        else (
            f"Refusing to start pytest shard: insufficient remaining job budget "
            f"({remaining:.2f}s remaining < {minimum:.2f}s minimum viable; "
            f"job_budget={budget:.2f}s, elapsed_prep={elapsed_prep:.2f}s, "
            f"upload_reserve={reserve:.2f}s)."
        )
    )
    return ShardBudgetDecision(
        job_budget_seconds=budget,
        upload_reserve_seconds=reserve,
        min_session_seconds=minimum,
        job_start_epoch=start,
        now_epoch=float(effective_now),
        elapsed_prep_seconds=elapsed_prep,
        remaining_session_seconds=remaining,
        admitted=admitted,
        reason=reason,
    )


def build_pre_shard_budget_diagnostics(decision: ShardBudgetDecision) -> dict[str, object]:
    """Build diagnostics JSON when prep consumes too much budget to launch pytest safely."""
    return {
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "execution_complete": False,
        "deadline_exceeded": True,
        "pre_shard_budget_exhausted": True,
        "deadline_seconds": 0.0,
        "deadline_elapsed_seconds": 0.0,
        "pytest_exit_code": 2,
        "selected_cases": None,
        "observed_cases": 0,
        "finished_cases": 0,
        "in_flight_count": 0,
        "in_flight_cases": [],
        "invalid_duration_count": 0,
        "observed_elapsed_seconds": 0.0,
        "measured_worker_seconds": 0.0,
        "phase_seconds": {"setup": 0.0, "call": 0.0, "teardown": 0.0},
        "longest_cases": [],
        "active_child_processes": [],
        "job_budget_seconds": round(decision.job_budget_seconds, 6),
        "elapsed_prep_seconds": round(decision.elapsed_prep_seconds, 6),
        "upload_reserve_seconds": round(decision.upload_reserve_seconds, 6),
        "min_session_seconds": round(decision.min_session_seconds, 6),
        "remaining_session_seconds": round(decision.remaining_session_seconds, 6),
        "failure_reason": decision.reason,
    }


def _extract_ci_diagnostics_file(pytest_args: Sequence[str]) -> str | None:
    resolved: str | None = None
    index = 0
    while index < len(pytest_args):
        token = pytest_args[index]
        if token == "--ci-diagnostics-file" and index + 1 < len(pytest_args):
            resolved = pytest_args[index + 1]
            index += 2
            continue
        if token.startswith("--ci-diagnostics-file="):
            resolved = token.split("=", 1)[1]
        index += 1
    cleaned = (resolved or "").strip()
    return cleaned or None


def _has_explicit_session_deadline(pytest_args: Sequence[str]) -> bool:
    return any(
        token == "--ci-session-deadline" or token.startswith("--ci-session-deadline=")
        for token in pytest_args
    )


def _split_argv(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        raise ValueError("Expected '--' separator before pytest arguments")
    split_index = argv.index("--")
    wrapper_args = list(argv[:split_index])
    pytest_args = list(argv[split_index + 1 :])
    if not pytest_args:
        raise ValueError("Expected at least one pytest argument after '--'")
    return wrapper_args, pytest_args


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-budget-seconds", type=float, required=True)
    parser.add_argument("--upload-reserve-seconds", type=float, default=300.0)
    parser.add_argument("--min-session-seconds", type=float, default=300.0)
    parser.add_argument("--job-start-epoch", type=str, default=None)
    parser.add_argument("--job-start-epoch-env", type=str, default=DEFAULT_JOB_START_ENV)
    parser.add_argument("--require-job-start-epoch", action="store_true", default=False)
    parser.add_argument("--diagnostics-file", type=str, default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        wrapper_args, pytest_args = _split_argv(raw_argv)
        if _has_explicit_session_deadline(pytest_args):
            raise ValueError("Do not pass hardcoded --ci-session-deadline after '--'; shard_budget computes it")
        options = _build_parser().parse_args(wrapper_args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 4
    except ValueError as exc:
        print(f"[ci-shard-budget] {exc}", file=sys.stderr, flush=True)
        return 4

    in_github_actions = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
    required = bool(options.require_job_start_epoch or in_github_actions)
    if options.job_start_epoch is not None:
        raw_start: str | None = options.job_start_epoch
        source_name = "--job-start-epoch"
    else:
        raw_start = os.environ.get(options.job_start_epoch_env)
        source_name = options.job_start_epoch_env

    try:
        job_start_epoch = parse_job_start_epoch(raw_start, required=required, source_name=source_name)
        decision = compute_shard_budget(
            job_budget_seconds=options.job_budget_seconds,
            upload_reserve_seconds=options.upload_reserve_seconds,
            min_session_seconds=options.min_session_seconds,
            job_start_epoch=job_start_epoch,
        )
    except ValueError as exc:
        print(f"[ci-shard-budget] {exc}", file=sys.stderr, flush=True)
        return 4

    if not decision.admitted:
        print(f"[ci-shard-budget] {decision.reason}", file=sys.stderr, flush=True)
        diag_path = options.diagnostics_file.strip() or _extract_ci_diagnostics_file(pytest_args)
        if diag_path:
            payload = build_pre_shard_budget_diagnostics(decision)
            try:
                atomic_write_text(diag_path, json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2) + "\n")
            except OSError as exc:
                print(
                    f"[ci-shard-budget] Warning: failed to write diagnostics to {diag_path}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        return 2

    deadline_arg = f"--ci-session-deadline={decision.remaining_session_seconds:.3f}"
    print(
        f"[ci-shard-budget] job_budget={decision.job_budget_seconds:.2f}s "
        f"elapsed_prep={decision.elapsed_prep_seconds:.2f}s "
        f"upload_reserve={decision.upload_reserve_seconds:.2f}s "
        f"session_deadline={decision.remaining_session_seconds:.3f}s",
        file=sys.stderr,
        flush=True,
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *pytest_args, deadline_arg],
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
