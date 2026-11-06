"""Reconcile one interrupted benchmark Host, without certifying its work.

This is Python provider/process IO around the existing Codex session, native
history and external scheduler owners. It does not decide Goal/Todo recovery,
grant budget, replay a tool, restore judge authority or manufacture a checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat


def recovery_handoff(env: dict[str, str]) -> tuple[dict | None, str]:
    """Read content through the existing handoff owner; never adopt ownership."""
    path, digest = env.get('LOOPX_RECOVERY_HANDOFF'), env.get('LOOPX_RECOVERY_HANDOFF_SHA256')
    if path is None and digest is None:
        return None, ''
    if not path or not re.fullmatch(r'[0-9a-f]{64}', digest or ''):
        raise ValueError('Recovery handoff requires its exact path and digest')
    source = Path(path)
    if not source.is_absolute() or source.resolve() != source or source.is_symlink():
        raise ValueError('Recovery handoff must be a physical absolute file')
    # Reject special files before reading, including a raced FIFO replacement.
    descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Recovery handoff must be a regular file')
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Recovery handoff bytes changed or exceed the transport bound')
    from loopx.cli_commands.handoff_restore import restore_handoff_input
    text = restore_handoff_input(raw.decode('utf-8'), input_format='json')
    return {'path': path, 'sha256': digest}, text


class ProcessObservers:
    """Preserve request-admission cleanup while retaining process observations."""
    def __init__(self, *observers):
        self.observers = [observer for observer in observers if observer is not None]

    def started(self, process):
        for observer in self.observers:
            observer.started(process)

    def before_cleanup(self, process, error):
        for observer in self.observers:
            observer.before_cleanup(process, error)

    def signal_requested(self, number):
        for observer in self.observers:
            observer.signal_requested(number)

    def after_cleanup(self, process, error):
        for observer in self.observers:
            observer.after_cleanup(process, error)


def _physical_read(path: Path) -> bytes:
    if path.is_symlink() or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("Interruption evidence must be a physical regular file")
    return path.read_bytes()


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Interruption evidence requires finite process times")
    return value


def _process_window(receipt):
    process = receipt.get("native_process", {})
    after = process.get("after_cleanup") or {}
    if (process.get("schema_version") != "native_codex_process_observation_v1"
            or type(process.get("pid")) is not int or process["pid"] <= 1
            or type(after.get("return_code")) is not int
            or process.get("cleanup_error_type") is not None
            or process.get("persistence_error_type") is not None):
        raise ValueError("Original native process termination is unproven")
    started = _number(process.get("started_at_unix"))
    stopped = _number(after.get("observed_at_unix"))
    if started > stopped:
        raise ValueError("Original native process window is invalid")
    # The worker owns and reaps this process group. A still-live group is a
    # conservative hold even if the PID has since been reused.
    if os.name != "posix":
        raise ValueError("Original process-group termination is unavailable")
    try:
        os.killpg(process["pid"], 0)
    except ProcessLookupError:
        return started, stopped
    except PermissionError as error:
        raise ValueError("Original process-group termination is unavailable") from error
    raise ValueError("Original native process group is still alive")


def _stopped_source(state_file: Path, state: dict, env: dict[str, str], wake_log_dir: str):
    """Consume the Docker provider's exact, explicitly prepared legacy source.

    This is only valid on that unchanged filesystem. A proof cannot be combined
    with an older checkpoint or with a newly created Goal/native home.
    """
    from benchmark.edgebench.stopped_recovery import file_inventory, source_paths

    proof_path = Path(env['LOOPX_INTERRUPTED_SOURCE']).absolute()
    raw = _physical_read(proof_path)
    proof = json.loads(raw)
    declaration = proof['declaration']
    stopped = proof['source_state']
    expected = {'goal_id': env['LOOPX_GOAL_ID'], 'agent_id': env['LOOPX_AGENT_ID'],
        'workspace': env['LOOPX_PROJECT'], 'runtime_root': env['LOOPX_RUNTIME_ROOT'],
        'native_home': env['CODEX_HOME'], 'registry': env['LOOPX_REGISTRY'],
        'scheduler_state': str(state_file), 'wake_log_dir': wake_log_dir}
    if (not re.fullmatch(r'[a-f0-9]{64}', proof.get('container_id', ''))
            or stopped.get('Status') != 'exited' or stopped.get('Running') is not False
            or stopped.get('Pid') != 0 or stopped.get('Restarting') or stopped.get('Paused')
            or stopped.get('Dead') or proof.get('execution_authority_granted') is not False
            or any(declaration.get(k) != v for k, v in expected.items())
            or hashlib.sha256(_physical_read(state_file)).hexdigest() != proof['scheduler_state_sha256']):
        raise ValueError('Stopped recovery proof differs from the original worker')
    # Derive identity from the current UTS namespace, never a caller env value.
    # The Docker producer only accepts the original default hostname. This
    # prevents accidentally consuming copied evidence in a fresh worker; the
    # trusted host/operator remains responsible for Docker namespace authority.
    if os.name != 'posix' or os.uname().nodename != proof['container_id'][:12]:
        raise ValueError('Stopped recovery is only valid in the original container namespace')
    required = set(source_paths(declaration).values())
    supplied = {part['source_path'] for part in proof['components']}
    if not required <= supplied or any(p not in required and not any(
            p == base + suffix for base in required for suffix in ('-wal', '-journal')) for p in supplied):
        raise ValueError('Stopped recovery proof omits or adds source participants')
    if len(supplied) != len(proof['components']):
        raise ValueError('Stopped recovery proof repeats source participants')
    for base in required:
        for suffix in ('-wal', '-journal'):
            if Path(base + suffix).exists() != (base + suffix in supplied):
                raise ValueError('Stopped recovery source SQLite companions changed')
    for component in proof['components']:
        root = Path(component['source_path'])
        if root.resolve(strict=True) != root or root.is_symlink() or root.is_dir() != component['directory']:
            raise ValueError('Stopped recovery source participant moved')
        if file_inventory(root) != component['files']:
            raise ValueError('Stopped recovery source bytes changed')
    path = Path(wake_log_dir) / declaration['wake_turn_id'] / 'receipt.json'
    receipt_raw = _physical_read(path)
    if hashlib.sha256(receipt_raw).hexdigest() != proof['receipt_sha256']:
        raise ValueError('Stopped recovery original wake changed')
    receipt = json.loads(receipt_raw)
    return path, receipt_raw, receipt, proof


def read_interrupted_host(state: dict, *, state_file: Path, env: dict[str, str], wake_log_dir: str) -> dict:
    """Qualify an exact returned wake, then inspect its existing native session.

    No caller-supplied terminal boolean or inferred last score authorizes this
    path. Missing legacy evidence remains a recovery gap, not a new attempt.
    """
    from .scheduler import BUDGET_EXHAUSTED_EXIT_CODE
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    from loopx.hosts.codex_checkpoint import native_interrupted_turn
    from .codex import process_environment
    from scripts.external_scheduler_worker import _host_pending

    # Match the worker's original isolated home, not an operator's ambient one.
    env = process_environment(Path(env["LOOPX_CODEX_HOME"]), base=env)
    if _host_pending(state_file) is not True:
        raise ValueError("Original Host pending witness is unresolved or already cleared")

    prior = state.get("host_attempt", {})
    attempt = prior.get("attempt_id", "")
    legacy = bool(env.get('LOOPX_INTERRUPTED_SOURCE')) and not attempt
    if ((not legacy and not re.fullmatch(r"[0-9a-f]{32}", attempt))
            or prior.get("status") not in {"started", "stopped"}
            or "host_failure" in prior
            or (prior["status"] == "stopped" and prior.get("stop_exit_code") != BUDGET_EXHAUSTED_EXIT_CODE)):
        raise ValueError("Original Host interruption identity is unavailable")
    source = None
    if legacy:
        path, raw, receipt, source = _stopped_source(state_file, state, env, wake_log_dir)
    else:
        matches = []
        root = Path(wake_log_dir).absolute()
        for path in root.glob("*/receipt.json"):
            raw = _physical_read(path)
            try:
                receipt = json.loads(raw)
            except ValueError:
                continue
            if isinstance(receipt, dict) and receipt.get("host_attempt_id") == attempt:
                matches.append((path, raw, receipt))
        if len(matches) != 1:
            raise ValueError("Original Host interruption must have one exact wake receipt")
        path, raw, receipt = matches[0]
    if (receipt.get("turn_id") != path.parent.name or receipt.get("mode") != "heartbeat"
            or receipt.get("stage") != "execute" or receipt.get("host_invoked") is not True
            or receipt.get("budget_exhausted") is not True or receipt.get("ok") is not False
            or "host_failure" in receipt
            or (not legacy and any(receipt.get(key) != env["LOOPX_" + key.upper()]
                                   for key in ("goal_id", "agent_id")))):
        raise ValueError("Original Host receipt is not this lane's interrupted budget stop")
    started, stopped = ((_number(source['process_started_at']), _number(source['process_stopped_at']))
                        if legacy else _process_window(receipt))
    try:
        phase = float(source['source_phase_deadline_epoch'] if legacy else receipt["phase_deadline_epoch"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Original Host phase clock is unavailable") from error
    returned_legacy_stop = source is not None and prior.get('status') == 'stopped'
    if (returned_legacy_stop
            and stopped != math.floor(path.stat().st_mtime)):
        raise ValueError('Returned legacy Host window differs from its receipt')
    if (not math.isfinite(phase) or not started <= phase
            or (not returned_legacy_stop and phase > stopped)):
        raise ValueError("Original Host stopped outside its phase deadline")
    session = receipt.get("session") or {}
    if session.get("binding_scope") != "agent" or not session.get("session_id"):
        raise ValueError("Original Host session is unavailable")
    current = CodexAgentSession(env, context="resume",
        sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access"), receipt={})
    with current.admission.launch_lifetime(operation="benchmark_interrupted_host_recovery"):
        pass  # Also checks legacy source/mirror activation, unlike require_current alone.
    if (not current.binding or current.session_id != session["session_id"]
            or prior.get("session_id") not in (None, current.session_id)):
        raise ValueError("Original Host session binding changed")
    native = native_interrupted_turn(env, current.session_id,
        process_started_at=started, process_stopped_at=stopped)
    with current.admission.launch_lifetime(operation="benchmark_interrupted_host_recovery"):
        pass  # A concurrent Stop or registry change cannot authorize the new phase.
    if _physical_read(path) != raw:
        raise ValueError("Original Host receipt changed during readback")
    return {**({'source_container_id': source['container_id'],
                'source_proof_sha256': hashlib.sha256(_physical_read(Path(env['LOOPX_INTERRUPTED_SOURCE']))).hexdigest()}
               if legacy else {'host_attempt_id': attempt}), "wake_turn_id": receipt["turn_id"],
            "receipt_path": str(path), "receipt_sha256": hashlib.sha256(raw).hexdigest(),
            "source_phase_deadline_epoch": phase, "session_profile_digest": current.digest,
            "native": native}


def read_failed_host(state: dict, *, state_file: Path, env: dict[str, str],
                     wake_log_dir: str, source_state_sha256: str) -> dict:
    """Qualify an explicit same-session retry of one returned quota failure.

    The digest binds a caller's fresh repair/allowance decision to this exact
    failed attempt, not to every subsequent failure. The ordinary scheduler and
    Turn launch still own current Goal, Stop, work and budget admission.
    """
    from loopx.control_plane.turn_driver.host_failure import (
        build_host_failure_record, explicit_host_failure_retry_available, normalize_host_failure_record,
    )
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    from loopx.hosts.codex_checkpoint import native_failed_turn
    from .codex import process_environment
    from .codex_readiness import require_native_receiver_allowance
    from scripts.external_scheduler_worker import _host_pending

    original_state = _physical_read(state_file)
    if (env.get('LOOPX_EXECUTION_MODE') != 'heartbeat' or env.get('LOOPX_ITERATION_CONTEXT') != 'resume'
            or not re.fullmatch(r'[a-f0-9]{64}', source_state_sha256)
            or hashlib.sha256(original_state).hexdigest() != source_state_sha256
            or _host_pending(state_file) is not True):
        raise ValueError('Explicit Host recovery no longer names the original failed state')
    prior = state.get('host_attempt', {})
    failure = normalize_host_failure_record(prior.get('host_failure'))
    if (prior.get('status') != 'failed' or failure['kind'] != 'quota_exhausted'
            or state.get('last_wake_status') != 'wake_failed'
            or type(state.get('last_wake_rc')) is not int or state['last_wake_rc'] != 1
            or prior.get('session_id') is None
            or not explicit_host_failure_retry_available(failure, retry_failed=True)):
        raise ValueError('Original Host is not an explicitly repairable returned quota failure')
    env = process_environment(Path(env['LOOPX_CODEX_HOME']), base=env)
    attempt = prior.get('attempt_id', '')
    legacy = bool(env.get('LOOPX_INTERRUPTED_SOURCE')) and not attempt
    source = None
    if legacy:
        path, raw, receipt, source = _stopped_source(state_file, state, env, wake_log_dir)
    else:
        if not re.fullmatch(r'[0-9a-f]{32}', attempt):
            raise ValueError('Failed Host attempt has no exact returned identity')
        matches = []
        for path in Path(wake_log_dir).absolute().glob('*/receipt.json'):
            raw = _physical_read(path)
            receipt = json.loads(raw)
            if isinstance(receipt, dict) and receipt.get('host_attempt_id') == attempt:
                matches.append((path, raw, receipt))
        if len(matches) != 1:
            raise ValueError('Failed Host must have one exact returned wake')
        path, raw, receipt = matches[0]
    if (receipt.get('turn_id') != path.parent.name or receipt.get('mode') != 'heartbeat'
            or receipt.get('stage') != 'execute' or receipt.get('ok') is not False
            or receipt.get('host_invoked') is not True or receipt.get('budget_exhausted')
            or type(receipt.get('return_code')) is not int or receipt['return_code'] != 1
            or normalize_host_failure_record(receipt.get('host_failure'))
                != build_host_failure_record(failure['kind'], attempt=1)
            or (not legacy and any(receipt.get(key) != env['LOOPX_' + key.upper()]
                                   for key in ('goal_id', 'agent_id')))):
        raise ValueError('Failed Host receipt differs from this lane and failure')
    started, stopped = ((_number(source['process_started_at']), _number(source['process_stopped_at']))
                        if legacy else _process_window(receipt))
    phase = _number(float(source['source_phase_deadline_epoch'] if legacy else receipt['phase_deadline_epoch']))
    if phase < started:
        raise ValueError('Original failure phase predates its Host invocation')
    session = receipt.get('session') or {}
    current = CodexAgentSession(env, context='resume',
        sandbox=env.get('LOOPX_CODEX_SANDBOX', 'danger-full-access'), receipt={})
    with current.admission.launch_lifetime(operation='benchmark_failed_host_recovery'):
        pass
    if (session.get('binding_scope') != 'agent' or not current.binding
            or not current.session_id or current.session_id != session.get('session_id')
            or prior['session_id'] != current.session_id):
        raise ValueError('Failed Host session binding changed')
    native = native_failed_turn(env, current.session_id, failure_kind=failure['kind'],
                               process_started_at=started, process_stopped_at=stopped)
    allowance = require_native_receiver_allowance(env)
    with current.admission.launch_lifetime(operation='benchmark_failed_host_recovery'):
        pass
    if _physical_read(path) != raw or _physical_read(state_file) != original_state:
        raise ValueError('Original failure changed during recovery readback')
    return {**({'source_container_id': source['container_id'],
                'source_proof_sha256': hashlib.sha256(_physical_read(Path(env['LOOPX_INTERRUPTED_SOURCE']))).hexdigest()}
               if legacy else {'host_attempt_id': attempt}),
            'wake_turn_id': receipt['turn_id'], 'receipt_path': str(path),
            'receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'source_phase_deadline_epoch': phase, 'session_profile_digest': current.digest,
            'host_failure': failure, 'retry_failed': True, 'native': native, 'allowance': allowance}
