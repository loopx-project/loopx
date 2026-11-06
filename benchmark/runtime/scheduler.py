"""The existing scheduler invocation shared by native benchmark transports."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import time

# Private process transport between the benchmark wake and its shell scheduler.
# The benchmark worker remains the owner of phase-budget exhaustion.
BUDGET_EXHAUSTED_EXIT_CODE = 75
PHASE_WORK_RESERVE_SECONDS = 160


def phase_work_reserve(mode: str) -> int:
    """Heartbeat cleanup must not consume the remaining solver allowance."""
    return 0 if mode == "heartbeat" else PHASE_WORK_RESERVE_SECONDS


def _physical_state(path: Path) -> Path:
    if path.is_symlink() or path.resolve() != path:
        raise ValueError("Scheduler phase state must be a physical path")
    return path


def _phase_states(root: Path) -> list[Path]:
    states = [root, *sorted(root.parent.glob(f"{root.stem}.phase-*{root.suffix}"))]
    return [_physical_state(path) for path in states if path.exists()]


def _state_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _latest_phase_state(root: Path, states: list[Path]) -> Path:
    """Follow explicit resume links, then use mtime only for legacy phase files."""
    documents = {path: state for path in states if (state := _state_object(path)) is not None}
    children: dict[Path, list[Path]] = {}
    for path, state in documents.items():
        parent = state.get("resume_from")
        if not isinstance(parent, str):
            continue
        parent_path = Path(parent)
        if (not parent_path.is_absolute() or parent_path.resolve() != parent_path
                or parent_path not in documents):
            continue
        children.setdefault(parent_path, []).append(path)
    current = root if root in documents else max(states, key=lambda path: path.stat().st_mtime_ns)
    seen = set()
    while current not in seen and children.get(current):
        seen.add(current)
        current = max(children[current], key=lambda path: path.stat().st_mtime_ns)
    unreadable = [path for path in states if path not in documents]
    if unreadable:
        newest = max(unreadable, key=lambda path: path.stat().st_mtime_ns)
        if newest.stat().st_mtime_ns > current.stat().st_mtime_ns:
            current = newest  # Unreadable current evidence must be held, not skipped.
    return current


def _phase_receipt(state_file: Path, wake_log_dir: str | None) -> dict | None:
    if wake_log_dir is None:
        return None
    # The scheduler writes its current stop after the worker receipt. Later
    # phases share this log directory, so the state mtime remains the fence.
    stop_time = state_file.stat().st_mtime_ns
    receipts = []
    for receipt in Path(wake_log_dir).glob("*/receipt.json"):
        if receipt.is_symlink() or receipt.resolve() != receipt:
            raise ValueError("Source wake receipt must be a physical file")
        if receipt.stat().st_mtime_ns <= stop_time:
            receipts.append(receipt)
    if not receipts:
        return None
    latest = max(receipts, key=lambda item: item.stat().st_mtime_ns)
    try:
        value = json.loads(latest.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _create_resume_state(target: Path, predecessor: Path, *, recovered: dict | None = None,
                         replan_attempts=None, handoff: dict | None = None) -> None:
    if target.is_symlink():
        raise ValueError("Resumed scheduler state must not be a symlink")
    try:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            value = {"resume_from": str(predecessor)}
            if recovered is not None:
                value.update(interruption_recovery=recovered,
                    recovered_session_id=recovered["native"]["session_id"],
                    replan_attempts=replan_attempts or [])
            if handoff is not None:
                value["recovery_handoff"] = handoff
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return


def resume_state_file(state_file: str, *, phase_deadline: float, agent_id: str,
                      wake_log_dir: str | None = None, env: dict[str, str] | None = None) -> str:
    """Select a new transport phase only after an explicit budget continuation.

    Kernel state and native sessions stay in place. The original scheduler
    record and its independent IO witness are retained, including failures.
    The caller owns authorization and the unchanged finite phase deadline.
    """
    if (isinstance(phase_deadline, bool) or not isinstance(phase_deadline, (int, float))
            or not math.isfinite(phase_deadline) or phase_deadline <= time.time()):
        raise ValueError("Budget continuation requires an open finite phase deadline")
    from .interruption_recovery import recovery_handoff
    handoff, _ = recovery_handoff(env or {})
    path = _physical_state(Path(state_file).absolute())
    from loopx.file_lock import try_exclusive_file_lock
    from scripts.external_scheduler_worker import HOST_LOCK_OPERATION, _host_attempt, _host_pending

    with ExitStack() as locks:
        # The root serializes phase selection. Locking the existing phase files
        # as well keeps a running/failed phase from moving under that decision.
        if locks.enter_context(try_exclusive_file_lock(
                path, agent_id=agent_id, operation=HOST_LOCK_OPERATION)) is None:
            return str(path)
        for phase in _phase_states(path):
            if phase == path:
                continue
            if locks.enter_context(try_exclusive_file_lock(
                    phase, agent_id=agent_id, operation=HOST_LOCK_OPERATION)) is None:
                return str(phase)
        states = _phase_states(path)
        if not states:
            return str(path)
        predecessor = _latest_phase_state(path, states)
        state = _state_object(predecessor)
        if state is None:
            return str(predecessor)
        prior = state.get("host_attempt")
        recovery = state.get("interruption_recovery")
        can_continue_stopped_phase = (isinstance(prior, dict) and prior.get("status") == "stopped"
                                      and prior.get("stop_exit_code") == BUDGET_EXHAUSTED_EXIT_CODE)
        if (recovery is not None and state.get("recovery_handoff") != handoff
                and (recovery.get("phase_deadline_epoch") == phase_deadline or not can_continue_stopped_phase)):
            raise ValueError("Recovered phase handoff is immutable; preserve its exact input")
        if (recovery is not None and recovery.get("phase_deadline_epoch") != phase_deadline
                and not (isinstance(prior, dict) and prior.get("status") == "stopped"
                         and prior.get("stop_exit_code") == BUDGET_EXHAUSTED_EXIT_CODE)):
            raise ValueError("Recovered phase deadline is immutable; inspect the current phase before continuing")
        if not isinstance(prior, dict):
            return str(predecessor)
        # Validate the captured record with its existing transport owner.
        scope = prior.get("scope")
        if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
            return str(predecessor)
        try:
            _host_attempt(state, scope)
        except (TypeError, ValueError):
            return str(predecessor)
        # A returned interrupted wake is distinct from a pre-dispatch stop.
        # Explicit continuation only reconciles transport; all Goal/Stop,
        # replan, lease and launch decisions remain with the normal next wake.
        recovered = None
        if env is not None and wake_log_dir and _host_pending(predecessor) is not None:
            from .interruption_recovery import read_interrupted_host

            try:
                recovered = read_interrupted_host(state, state_file=predecessor, env=env, wake_log_dir=wake_log_dir)
            except (OSError, ValueError, RuntimeError, TimeoutError, KeyError, TypeError) as error:
                print(f"Interrupted Host remains held: {type(error).__name__}: {error}", file=sys.stderr)
        if recovered is not None:
            suffix = hashlib.sha256(float(phase_deadline).hex().encode()).hexdigest()[:20]
            target = path.with_name(f"{path.stem}.phase-{suffix}{path.suffix}")
            recovered.update(source_state_sha256=hashlib.sha256(predecessor.read_bytes()).hexdigest(),
                             phase_deadline_epoch=phase_deadline)
            _create_resume_state(target, predecessor, recovered=recovered,
                                 replan_attempts=state.get("replan_attempts"), handoff=handoff)
            return str(target)
        if (prior.get("status") != "stopped"
                or prior.get("stop_exit_code") != BUDGET_EXHAUSTED_EXIT_CODE
                or "host_failure" in prior or _host_pending(predecessor) is None
                or state.get("last_wake_rc") != BUDGET_EXHAUSTED_EXIT_CODE
                or state.get("last_wake_status") != "wake_requested_stop"):
            return str(predecessor)
        # Only the immutable phase clock names the new state. Todo/reset-token
        # changes cannot clear a failure already recorded in this new phase.
        suffix = hashlib.sha256(float(phase_deadline).hex().encode()).hexdigest()[:20]
        target = path.with_name(f"{path.stem}.phase-{suffix}{path.suffix}")
        if target.is_symlink():
            raise ValueError("Resumed scheduler state must not be a symlink")
        if target.exists():
            return str(target)  # Never replace a retry/failure in this phase.
        # Only a stop before native dispatch admits this shortcut. An
        # interrupted native Turn still needs its original recovery/settlement.
        # Restrict legacy receipt discovery to the source scheduler snapshot;
        # later phases share the log directory but cannot replace this proof.
        result = _phase_receipt(predecessor, wake_log_dir)
        if (not isinstance(result, dict) or result.get("budget_exhausted") is not True
                or result.get("host_invoked") is not False or result.get("mode") != "heartbeat"
                or result.get("stage") != "execute"):
            return str(predecessor)
        _create_resume_state(target, predecessor)
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
        agent_id=os.environ["LOOPX_AGENT_ID"], wake_log_dir=os.environ.get("LOOPX_WAKE_LOG_DIR"),
        env=dict(os.environ))
    command = worker_command(dict(os.environ), python=args.python, source=args.source,
                             state_file=state, host_timeout=args.host_timeout)
    os.execvpe(command[0], command, os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
