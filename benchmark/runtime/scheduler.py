"""The existing scheduler invocation shared by native benchmark transports."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path, PurePath
import re
import shlex
import sys
import time

# Private process transport between the benchmark wake and its shell scheduler.
# The benchmark worker remains the owner of phase-budget exhaustion.
BUDGET_EXHAUSTED_EXIT_CODE = 75
PHASE_WORK_RESERVE_SECONDS = 160


def phase_work_reserve(mode: str, *, official_stop_hook: bool = False) -> int:
    """Give deadline-driven workers the full phase; bound cleanup separately."""
    return 0 if mode == "heartbeat" or official_stop_hook else PHASE_WORK_RESERVE_SECONDS


def read_phase_deadline(raw: bytes) -> int | float:
    """Read the initial integer epoch or the scheduler's continued phase clock."""
    if len(raw) > 64 or not re.fullmatch(rb"[0-9]+(?:\.[0-9]+)?\n?", raw):
        raise ValueError("Host phase deadline must be an integer or canonical decimal epoch file")
    body = raw.removesuffix(b"\n")
    deadline = float(body) if b"." in body else int(body)
    if not 0 < deadline < 2 ** 53 or (
            isinstance(deadline, float) and body != repr(deadline).encode("ascii")):
        raise ValueError("Host phase deadline cannot be represented exactly")
    return deadline


def phase_state_name(root: PurePath, phase_deadline: float) -> str:
    """Name a scheduler phase by the finite clock that owns its allowance."""
    if (isinstance(phase_deadline, bool) or not isinstance(phase_deadline, (int, float))
            or not math.isfinite(phase_deadline) or phase_deadline <= 0):
        raise ValueError("Scheduler phase clock must be finite and positive")
    suffix = hashlib.sha256(float(phase_deadline).hex().encode()).hexdigest()[:20]
    return f"{root.stem}.phase-{suffix}{root.suffix}"


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
                         replan_attempts=None, handoff: dict | None = None,
                         failed_recovery: bool = False, idle_recovery: bool = False) -> None:
    if target.is_symlink():
        raise ValueError("Resumed scheduler state must not be a symlink")
    try:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            value = {"resume_from": str(predecessor)}
            if recovered is not None:
                key = 'idle_host_recovery' if idle_recovery else 'failed_host_recovery' if failed_recovery else 'interruption_recovery'
                value[key] = recovered
                value.update(recovered_session_id=recovered["native"]["session_id"],
                    replan_attempts=replan_attempts or [])
                if failed_recovery:
                    value['recovered_attempt_count'] = recovered['host_failure']['attempt']
            if handoff is not None:
                value["recovery_handoff"] = handoff
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if failed_recovery:
            raise ValueError("Explicit failed continuation phase already exists") from None
        if idle_recovery:
            raise ValueError('Explicit idle handoff phase already exists') from None


def _verify_failed_phase_replay(phase: Path, state: dict, recovery: dict, *,
                                states: list[Path], digest: str, env: dict[str, str],
                                wake_log_dir: str) -> None:
    from .interruption_recovery import _physical_read, verify_failed_host_replay_witness
    from loopx.control_plane.turn_driver.host_failure import (
        explicit_host_failure_retry_available, normalize_host_failure_record,
    )
    from scripts.external_scheduler_worker import _host_attempt

    original_ref = state.get("resume_from")
    original = Path(original_ref) if isinstance(original_ref, str) else None
    if original is None or original == phase or original not in states:
        raise ValueError("Explicit failed continuation requires the original replay source")
    raw = _physical_read(original)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Explicit failed continuation replay source changed")
    saved = json.loads(raw)
    prior = saved.get("host_attempt") if isinstance(saved, dict) else None
    native = recovery.get("native")
    if not isinstance(prior, dict) or not isinstance(native, dict):
        raise ValueError("Explicit failed continuation requires the original Host and native session")
    scope = prior.get("scope")
    if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
        raise ValueError("Explicit failed continuation replay requires the original Host attempt scope")
    _host_attempt(saved, scope)
    failure = normalize_host_failure_record(prior.get("host_failure"))
    session = prior.get("session_id")
    if (prior.get("status") != "failed" or saved.get("last_wake_status") != "wake_failed"
            or type(saved.get("last_wake_rc")) is not int or saved["last_wake_rc"] != 1
            or not explicit_host_failure_retry_available(failure, retry_failed=True)
            or failure != recovery.get("host_failure")
            or recovery.get("retry_failed") is not True or native.get("status") != "failed"
            or not isinstance(session, str) or not session or native.get("session_id") != session
            or state.get("recovered_session_id") != session
            or type(state.get("recovered_attempt_count")) is not int
            or state["recovered_attempt_count"] != failure["attempt"]):
        raise ValueError("Explicit failed continuation replay differs from the original returned failure")
    verify_failed_host_replay_witness(original, prior, recovery, env=env, wake_log_dir=wake_log_dir)


def resume_state_file(state_file: str, *, phase_deadline: float, agent_id: str,
                      wake_log_dir: str | None = None, env: dict[str, str] | None = None,
                      retry_failed_state_sha256: str | None = None,
                      idle_host_state_sha256: str | None = None) -> str:
    """Select a new transport phase only after an explicit budget continuation.

    Kernel state and native sessions stay in place. The original scheduler
    record and its independent IO witness are retained, including failures.
    The caller owns authorization and the unchanged finite phase deadline.
    """
    if (isinstance(phase_deadline, bool) or not isinstance(phase_deadline, (int, float))
            or not math.isfinite(phase_deadline) or phase_deadline <= time.time()):
        raise ValueError("Budget continuation requires an open finite phase deadline")
    if retry_failed_state_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}', retry_failed_state_sha256):
        raise ValueError('Explicit failed continuation requires its exact source-state digest')
    if idle_host_state_sha256 is not None and (
            not re.fullmatch(r'[a-f0-9]{64}', idle_host_state_sha256) or retry_failed_state_sha256 is not None):
        raise ValueError('Explicit idle handoff requires one exact source-state digest')
    from .interruption_recovery import recovery_handoff
    handoff, _ = recovery_handoff(env or {})
    path = _physical_state(Path(state_file).absolute())
    from loopx.file_lock import try_exclusive_file_lock
    from scripts.external_scheduler_worker import HOST_LOCK_OPERATION, _host_attempt, _host_pending

    def hold(state: Path, reason: str) -> str:
        if retry_failed_state_sha256 is not None:
            raise ValueError(f"Explicit failed continuation {reason}")
        if idle_host_state_sha256 is not None:
            raise ValueError(f'Explicit idle handoff {reason}')
        return str(state)

    def activate(state: Path) -> str:
        # The retained clock is an immutable recovery witness. Publish only
        # this already-admitted phase for both Host capture paths, with no
        # rounding up or new scheduler/native allowance.
        from .layout import _ACTIVE_PHASE_DEADLINE_NAME
        from loopx.file_lock import write_bytes_atomic

        clock = _physical_state(path.with_name(_ACTIVE_PHASE_DEADLINE_NAME))
        write_bytes_atomic(clock, f"{float(phase_deadline)!r}\n".encode(),
                           mode=0o600, fsync_directory=True)
        return str(state)

    with ExitStack() as locks:
        # The root serializes phase selection. Locking the existing phase files
        # as well keeps a running/failed phase from moving under that decision.
        if locks.enter_context(try_exclusive_file_lock(
                path, agent_id=agent_id, operation=HOST_LOCK_OPERATION)) is None:
            return hold(path, "requires the scheduler root lock")
        for phase in _phase_states(path):
            if phase == path:
                continue
            if locks.enter_context(try_exclusive_file_lock(
                    phase, agent_id=agent_id, operation=HOST_LOCK_OPERATION)) is None:
                return hold(phase, "requires the retained phase lock")
        states = _phase_states(path)
        if not states:
            return hold(path, "requires the retained scheduler state")
        predecessor = _latest_phase_state(path, states)
        state = _state_object(predecessor)
        if state is None:
            return hold(predecessor, "requires a readable retained scheduler state")
        prior = state.get("host_attempt")
        recovery = state.get("interruption_recovery") or state.get('failed_host_recovery') or state.get('idle_host_recovery')
        can_continue_stopped_phase = (isinstance(prior, dict) and prior.get("status") == "stopped"
                                      and prior.get("stop_exit_code") == BUDGET_EXHAUSTED_EXIT_CODE)
        target = path.with_name(phase_state_name(path, phase_deadline))
        same_phase = predecessor == target
        if idle_host_state_sha256 is not None:
            if (env is None or not wake_log_dir or env.get('LOOPX_EXECUTION_MODE') != 'heartbeat'
                    or env.get('LOOPX_ITERATION_CONTEXT') != 'resume' or not env.get('LOOPX_INTERRUPTED_SOURCE')):
                raise ValueError('Explicit idle handoff requires its original native and stopped-source routes')
            retained = state.get('idle_host_recovery')
            if same_phase and isinstance(retained, dict):
                from .interruption_recovery import _physical_read, verify_idle_host_replay

                parent = Path(state.get('resume_from', ''))
                if (parent not in states or parent == predecessor
                        or retained.get('source_state_sha256') != idle_host_state_sha256
                        or hashlib.sha256(_physical_read(parent)).hexdigest() != idle_host_state_sha256
                        or retained.get('phase_deadline_epoch') != phase_deadline
                        or state.get('recovery_handoff') != handoff
                        or state.get('recovered_session_id') != retained.get('native', {}).get('session_id')
                        or hashlib.sha256(_physical_read(Path(env['LOOPX_INTERRUPTED_SOURCE']))).hexdigest()
                            != retained.get('source_proof_sha256')):
                    raise ValueError('Explicit idle handoff replay differs from its original phase')
                verify_idle_host_replay(parent, retained, env=env)
                return activate(predecessor)  # Keep any later work or failure in this same phase.
            if (same_phase or hashlib.sha256(predecessor.read_bytes()).hexdigest() != idle_host_state_sha256):
                raise ValueError('Explicit idle handoff source changed; inspect the retained phase')
            from .interruption_recovery import read_idle_host

            recovered = read_idle_host(state, state_file=predecessor, env=env, wake_log_dir=wake_log_dir)
            if phase_deadline <= time.time():
                raise ValueError('Explicit idle handoff exhausted its finite phase during qualification')
            recovered.update(source_state_sha256=idle_host_state_sha256, phase_deadline_epoch=phase_deadline)
            _create_resume_state(target, predecessor, recovered=recovered,
                replan_attempts=state.get('replan_attempts'), handoff=handoff, idle_recovery=True)
            return activate(target)
        explicit_failed_phase = (retry_failed_state_sha256 is not None and not same_phase
            and hashlib.sha256(predecessor.read_bytes()).hexdigest() == retry_failed_state_sha256)
        if ((recovery is not None or state.get("resume_from"))
                and state.get("recovery_handoff") != handoff
                and (same_phase or not (can_continue_stopped_phase or explicit_failed_phase))):
            raise ValueError("Recovered phase handoff is immutable; preserve its exact input")
        if (recovery is not None and recovery.get("phase_deadline_epoch") != phase_deadline
                and not (can_continue_stopped_phase or explicit_failed_phase)):
            raise ValueError("Recovered phase deadline is immutable; inspect the current phase before continuing")
        if retry_failed_state_sha256 is not None:
            if (env is None or not wake_log_dir or env.get("LOOPX_EXECUTION_MODE") != "heartbeat"
                    or env.get("LOOPX_ITERATION_CONTEXT") != "resume"):
                raise ValueError("Explicit failed continuation requires original native and wake routes")
            failed_phase = state.get("failed_host_recovery")
            if (same_phase and isinstance(failed_phase, dict)
                    and failed_phase.get("source_state_sha256") == retry_failed_state_sha256):
                _verify_failed_phase_replay(predecessor, state, failed_phase,
                                            states=states, digest=retry_failed_state_sha256,
                                            env=env, wake_log_dir=wake_log_dir)
                return activate(predecessor)  # Replay never erases a later failure in this phase.
            if not explicit_failed_phase:
                raise ValueError('Explicit failed continuation source changed; inspect the retained phase')
        if not isinstance(prior, dict):
            return hold(predecessor, "requires the original Host attempt")
        # Validate the captured record with its existing transport owner.
        scope = prior.get("scope")
        if not isinstance(scope, str) or not re.fullmatch(r"[0-9a-f]{64}", scope):
            return hold(predecessor, "requires the original Host attempt scope")
        try:
            _host_attempt(state, scope)
        except (TypeError, ValueError):
            return hold(predecessor, "requires a valid original Host attempt")
        # A returned interrupted wake is distinct from a pre-dispatch stop.
        # Explicit continuation only reconciles transport; all Goal/Stop,
        # replan, lease and launch decisions remain with the normal next wake.
        recovered = None
        failed_recovery = False
        if retry_failed_state_sha256 is not None:
            assert env is not None and wake_log_dir is not None
            from .interruption_recovery import read_failed_host
            recovered = read_failed_host(state, state_file=predecessor, env=env,
                wake_log_dir=wake_log_dir, source_state_sha256=retry_failed_state_sha256)
            failed_recovery = True
        if recovered is None and env is not None and wake_log_dir and _host_pending(predecessor) is not None:
            from .interruption_recovery import read_interrupted_host

            try:
                recovered = read_interrupted_host(state, state_file=predecessor, env=env, wake_log_dir=wake_log_dir)
            except (OSError, ValueError, RuntimeError, TimeoutError, KeyError, TypeError) as error:
                print(f"Interrupted Host remains held: {type(error).__name__}: {error}", file=sys.stderr)
        if recovered is not None:
            if failed_recovery and phase_deadline <= time.time():
                raise ValueError('Explicit failed continuation exhausted its phase during qualification')
            recovered.update(source_state_sha256=hashlib.sha256(predecessor.read_bytes()).hexdigest(),
                             phase_deadline_epoch=phase_deadline)
            _create_resume_state(target, predecessor, recovered=recovered,
                                 replan_attempts=state.get("replan_attempts"), handoff=handoff,
                                 failed_recovery=failed_recovery)
            return activate(target)
        if (prior.get("status") != "stopped"
                or prior.get("stop_exit_code") != BUDGET_EXHAUSTED_EXIT_CODE
                or "host_failure" in prior or _host_pending(predecessor) is None
                or state.get("last_wake_rc") != BUDGET_EXHAUSTED_EXIT_CODE
                or state.get("last_wake_status") != "wake_requested_stop"):
            return str(predecessor)
        # Only the immutable phase clock names the new state. Todo/reset-token
        # changes cannot clear a failure already recorded in this new phase.
        if target.is_symlink():
            raise ValueError("Resumed scheduler state must not be a symlink")
        if target.exists():
            return activate(target)  # Never replace a retry/failure in this phase.
        # Only a stop before native dispatch admits this shortcut. An
        # interrupted native Turn still needs its original recovery/settlement.
        # Restrict legacy receipt discovery to the source scheduler snapshot;
        # later phases share the log directory but cannot replace this proof.
        result = _phase_receipt(predecessor, wake_log_dir)
        if (not isinstance(result, dict) or result.get("budget_exhausted") is not True
                or result.get("host_invoked") is not False or result.get("mode") != "heartbeat"
                or result.get("stage") != "execute"):
            return str(predecessor)
        _create_resume_state(target, predecessor, handoff=handoff)
        return activate(target)


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
    parser.add_argument('--retry-failed-state-sha256',
        help='Explicitly continue only this exact returned failure after operator repair')
    parser.add_argument('--idle-host-state-sha256',
        help='Explicitly hand off this sealed returned Host to its verified idle native session')
    args = parser.parse_args()
    state = resume_state_file(args.state_file,
        phase_deadline=float(os.environ["LOOPX_PHASE_DEADLINE_EPOCH"]),
        agent_id=os.environ["LOOPX_AGENT_ID"], wake_log_dir=os.environ.get("LOOPX_WAKE_LOG_DIR"),
        env=dict(os.environ), retry_failed_state_sha256=args.retry_failed_state_sha256,
        idle_host_state_sha256=args.idle_host_state_sha256)
    command = worker_command(dict(os.environ), python=args.python, source=args.source,
                             state_file=state, host_timeout=args.host_timeout)
    os.execvpe(command[0], command, os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
