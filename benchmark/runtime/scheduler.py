"""The existing scheduler invocation shared by native benchmark transports."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import time

# Private process transport between the benchmark wake and its shell scheduler.
# The benchmark worker remains the owner of phase-budget exhaustion.
BUDGET_EXHAUSTED_EXIT_CODE = 75
PHASE_WORK_RESERVE_SECONDS = 160


def phase_work_reserve(mode: str) -> int:
    """Heartbeat cleanup must not consume the remaining solver allowance."""
    return 0 if mode == "heartbeat" else PHASE_WORK_RESERVE_SECONDS


def resume_state_file(state_file: str, *, phase_deadline: float, agent_id: str,
                      wake_log_dir: str | None = None) -> str:
    """Select a new transport phase only after an explicit budget continuation.

    Kernel state and native sessions stay in place. The original scheduler
    record and its independent IO witness are retained, including failures.
    The caller owns authorization and the unchanged finite phase deadline.
    """
    if (isinstance(phase_deadline, bool) or not isinstance(phase_deadline, (int, float))
            or not math.isfinite(phase_deadline) or phase_deadline <= time.time()):
        raise ValueError("Budget continuation requires an open finite phase deadline")
    path = Path(state_file).absolute()
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Scheduler phase state must be a physical path")
    from loopx.file_lock import try_exclusive_file_lock
    from scripts.external_scheduler_worker import HOST_LOCK_OPERATION, _host_attempt, _host_pending

    with try_exclusive_file_lock(path, agent_id=agent_id, operation=HOST_LOCK_OPERATION) as held:
        if held is None:
            return str(path)  # The ordinary scheduler retains its mutex behavior.
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            return str(path)
        if not isinstance(state, dict):
            raise ValueError("Scheduler phase state is invalid")
        prior = state.get("host_attempt")
        if not isinstance(prior, dict) or prior.get("status") != "stopped":
            return str(path)
        # Validate the captured record with its existing transport owner.
        scope = prior.get("scope")
        if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
            raise ValueError("Captured scheduler scope is invalid")
        _host_attempt(state, scope)
        if (prior.get("stop_exit_code") != BUDGET_EXHAUSTED_EXIT_CODE
                or "host_failure" in prior or _host_pending(path) is None
                or state.get("last_wake_rc") != BUDGET_EXHAUSTED_EXIT_CODE
                or state.get("last_wake_status") != "wake_requested_stop"):
            return str(path)
        # Only the immutable phase clock names the new state. Todo/reset-token
        # changes cannot clear a failure already recorded in this new phase.
        suffix = hashlib.sha256(float(phase_deadline).hex().encode()).hexdigest()[:20]
        target = path.with_name(f"{path.stem}.phase-{suffix}{path.suffix}")
        if target.is_symlink():
            raise ValueError("Resumed scheduler state must not be a symlink")
        if target.exists():
            return str(target)  # Never replace a retry/failure in this phase.
        if wake_log_dir is None:
            return str(path)
        # Only a stop before native dispatch admits this shortcut. An
        # interrupted native Turn still needs its original recovery/settlement.
        # Restrict legacy receipt discovery to the source scheduler snapshot;
        # later phases share the log directory but cannot replace this proof.
        stop_time = path.stat().st_mtime_ns
        receipts = []
        for receipt in Path(wake_log_dir).glob("*/receipt.json"):
            if receipt.is_symlink() or receipt.resolve() != receipt:
                raise ValueError("Source wake receipt must be a physical file")
            if receipt.stat().st_mtime_ns <= stop_time:
                receipts.append(receipt)
        if not receipts:
            return str(path)
        latest = max(receipts, key=lambda item: item.stat().st_mtime_ns)
        try:
            result = json.loads(latest.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            return str(path)
        if (not isinstance(result, dict) or result.get("budget_exhausted") is not True
                or result.get("host_invoked") is not False or result.get("mode") != "heartbeat"
                or result.get("stage") != "execute"):
            return str(path)
        return str(target)


def worker_command(env: dict[str, str], *, python: str, source: str,
                   state_file: str, host_timeout: float,
                   resume_budget_stop: bool = False) -> list[str]:
    if type(resume_budget_stop) is not bool:
        raise ValueError("resume_budget_stop must be a boolean")
    wake = [python, "-m", "benchmark.runtime.worker"]
    if env["LOOPX_EXECUTION_MODE"] not in {"heartbeat", "turn"}:
        return wake
    if resume_budget_stop:
        return [python, "-m", "benchmark.runtime.scheduler", "--python", python,
                "--source", source, "--state-file", state_file,
                "--host-timeout", str(host_timeout)]
    return [
        python, f"{source}/scripts/external_scheduler_worker.py",
        "--cli-bin", env["LOOPX_CLI"], "--registry", env["LOOPX_REGISTRY"],
        "--runtime-root", env["LOOPX_RUNTIME_ROOT"],
        "--runtime-profile", "generic_cli", "--goal-id", env["LOOPX_GOAL_ID"],
        "--agent-id", env["LOOPX_AGENT_ID"], "--state-file", state_file,
        "--wake-cmd", "exec " + shlex.join(wake),
        "--wake-stop-exit-code", str(BUDGET_EXHAUSTED_EXIT_CODE),
        "--wake-timeout-seconds", str(host_timeout + 150),
        "--quota-timeout-seconds", "30", "--error-backoff-seconds", "15",
        *(["--wake-host-failure"] if env["LOOPX_EXECUTION_MODE"] == "heartbeat" else []),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume one explicitly continued benchmark phase.")
    parser.add_argument("--python", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--host-timeout", required=True, type=float)
    args = parser.parse_args()
    state = resume_state_file(args.state_file,
        phase_deadline=float(os.environ["LOOPX_PHASE_DEADLINE_EPOCH"]),
        agent_id=os.environ["LOOPX_AGENT_ID"], wake_log_dir=os.environ.get("LOOPX_WAKE_LOG_DIR"))
    command = worker_command(dict(os.environ), python=args.python, source=args.source,
                             state_file=state, host_timeout=args.host_timeout)
    os.execvpe(command[0], command, os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
