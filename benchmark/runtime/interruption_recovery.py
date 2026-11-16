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
    def __init__(self, native_observation, request_admission):
        self._native_observation = native_observation
        self._request_admission = request_admission
        self._observers = tuple(
            observer for observer in (native_observation, request_admission) if observer is not None
        )

    def started(self, process, *, launch_started_at_unix):
        if self._native_observation is not None:
            self._native_observation.started(process, launch_started_at_unix=launch_started_at_unix)
        if self._request_admission is not None:
            self._request_admission.started(process)

    def before_cleanup(self, process, error):
        for observer in self._observers:
            observer.before_cleanup(process, error)

    def signal_requested(self, number):
        for observer in self._observers:
            observer.signal_requested(number)

    def after_cleanup(self, process, error):
        for observer in self._observers:
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


def _stopped_source(state_file: Path, state: dict, env: dict[str, str], wake_log_dir: str,
                    *, observer_locks: dict[Path, Path] | None = None):
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
        actual, expected_files = file_inventory(root), component['files']
        if observer_locks:
            from loopx.file_lock import (
                LOCK_HOLDER_SCHEMA_VERSION, _effect_mutation_lock_path, _lock_id,
                cross_runtime_lock_witness, lock_holder_host_label,
            )

            # Only the current admission's exact owned diagnostic records may
            # change. Registry/state bytes and unrelated *.lock files stay in
            # the original byte comparison; no filename pattern grants this.
            for record_path, target in observer_locks.items():
                if record_path == root or record_path.is_relative_to(root):
                    record = json.loads(_physical_read(record_path))
                    if record_path == _effect_mutation_lock_path(target):
                        witness = cross_runtime_lock_witness(target)
                        if record != {key: value for key, value in witness.items() if key != 'target'}:
                            raise ValueError('Idle observer mutation lock differs from its owned witness')
                    elif (record.get('schema_version') != LOCK_HOLDER_SCHEMA_VERSION
                            or record.get('pid') != os.getpid() or record.get('host') != lock_holder_host_label()
                            or record.get('lock_id') != _lock_id(target)
                            or record.get('operation') != 'benchmark_idle_host_handoff'):
                        raise ValueError('Idle observer does not own its admission lock diagnostic')
            def source_files(entries):
                return [item for item in entries if (root / item['path'] if root.is_dir() else root)
                        not in observer_locks]
            actual, expected_files = source_files(actual), source_files(expected_files)
        if actual != expected_files:
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


def read_idle_host(state: dict, *, state_file: Path, env: dict[str, str], wake_log_dir: str) -> dict:
    """Qualify the stopped Host's unchanged idle session and current authority."""
    from benchmark.edgebench.stopped_recovery import (
        completed_host_window, native_source_paths, require_sealed_host_pending, validate_idle_host_return,
    )
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    from loopx.hosts.codex_checkpoint import native_idle_turn
    from .codex import process_environment
    from .codex_readiness import require_native_receiver_allowance
    from scripts.external_scheduler_worker import _host_pending_path
    from .worker import host_output_paths

    env = process_environment(Path(env['LOOPX_CODEX_HOME']), base=env)
    path, raw, receipt, source = _stopped_source(state_file, state, env, wake_log_dir)
    if source.get('idle_host_handoff') is not True:
        raise ValueError('Idle handoff requires its explicitly sealed stopped source')
    completed = source.get('completed_host') is True
    pending_path = _host_pending_path(state_file)
    require_sealed_host_pending(_physical_read(pending_path), proof=source, source_path=pending_path,
                                pending=not completed)
    lineage = {'goal_id': env['LOOPX_GOAL_ID'], 'agent_id': env['LOOPX_AGENT_ID']}
    started = _number(source['process_started_at'])
    stopped = _number(source['process_stopped_at'])
    if completed:
        clock = _number(source['source_phase_deadline_epoch'])
        if completed_host_window(state, receipt, lineage=lineage, phase=clock) != (started, stopped):
            raise ValueError('Idle handoff differs from its original completed Host window')
    else:
        stdout, stderr = host_output_paths(path.parent)
        validate_idle_host_return(state, receipt, stdout=_physical_read(stdout),
                                  stderr=_physical_read(stderr), lineage=lineage)
        if stopped != math.floor(path.stat().st_mtime) or started > stopped:
            raise ValueError('Idle handoff differs from its original returned Host window')
    if receipt.get('turn_id') != path.parent.name:
        raise ValueError('Idle handoff wake differs from its original receipt')
    session = receipt['session']['session_id']
    current = CodexAgentSession(env, context='resume',
        sandbox=env.get('LOOPX_CODEX_SANDBOX', 'danger-full-access'), receipt={})
    with current.admission.launch_lifetime(operation='benchmark_idle_host_handoff'):
        pass
    if not current.binding or current.session_id != session:
        raise ValueError('Idle handoff native session binding changed')
    allowance = require_native_receiver_allowance(env)
    native = native_idle_turn(env, session, process_started_at=started,
        sealed_store_paths=tuple(native_source_paths(source['declaration']).values()),
        **({'process_stopped_at': stopped} if completed else {}))
    with current.admission.launch_lifetime(operation='benchmark_idle_host_handoff'):
        from loopx.file_lock import _effect_mutation_lock_path, lock_holder_paths
        from loopx.control_plane.goals.source_session_registry_state import guard_path

        admission, observer_locks = current.admission, {}
        while admission is not None:
            target = (guard_path(admission.registry_path, admission.goal_id)
                      if admission.source_profile else admission.registry_path)
            observer_locks.update((path, target) for path in lock_holder_paths(target))
            observer_locks[_effect_mutation_lock_path(target)] = target
            admission = admission.legacy_source_admission
        # Current admission updates only its own holder diagnostics. Check
        # every durable source byte again while those exact locks remain held.
        again = _stopped_source(state_file, state, env, wake_log_dir, observer_locks=observer_locks)
    if again[1] != raw or again[3] != source:
        raise ValueError('Idle handoff source changed during native readback')
    return {'source_container_id': source['container_id'],
            'source_proof_sha256': hashlib.sha256(_physical_read(Path(env['LOOPX_INTERRUPTED_SOURCE']))).hexdigest(),
            'receipt_path': str(path), 'receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'wake_turn_id': receipt['turn_id'], 'process_started_at': started,
            'source_phase_deadline_epoch': source['source_phase_deadline_epoch'],
            'session_profile_digest': current.digest, 'allowance': allowance,
            'native': native, **({'process_stopped_at': stopped} if completed
                                 else {'original_host_failure': state['host_attempt']['host_failure']})}


def verify_idle_host_replay(state_file: Path, recovery: dict, *, env: dict[str, str]) -> None:
    """A repeated phase selection retains its original qualification and clock."""
    from benchmark.edgebench.stopped_recovery import (
        completed_host_window, require_sealed_host_pending, validate_idle_host_return,
    )
    from scripts.external_scheduler_worker import _host_pending_path
    from .worker import host_output_paths

    native, allowance = recovery.get('native'), recovery.get('allowance')
    if (not isinstance(native, dict) or not isinstance(allowance, dict)
            or allowance.get('availability') != 'available'
            or not 0 < _number(allowance.get('observed_at')) <= _number(recovery.get('phase_deadline_epoch'))
            or any(not isinstance(recovery.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', recovery[key])
                   for key in ('receipt_sha256', 'session_profile_digest', 'source_proof_sha256'))):
        raise ValueError('Idle handoff replay requires its complete original qualification')
    source = json.loads(_physical_read(state_file))
    raw = _physical_read(Path(recovery['receipt_path']))
    receipt = json.loads(raw)
    lineage = {'goal_id': env['LOOPX_GOAL_ID'], 'agent_id': env['LOOPX_AGENT_ID']}
    started = _number(recovery.get('process_started_at'))
    completed_at = _number(native.get('completed_at'))
    completed = 'process_stopped_at' in recovery
    pending_path = _host_pending_path(state_file)
    try:
        proof_raw = _physical_read(Path(env['LOOPX_INTERRUPTED_SOURCE']))
        if hashlib.sha256(proof_raw).hexdigest() != recovery['source_proof_sha256']:
            raise ValueError('Original stopped-source proof changed')
        require_sealed_host_pending(_physical_read(pending_path), proof=json.loads(proof_raw),
                                    source_path=pending_path, pending=not completed)
    except (OSError, ValueError) as error:
        raise ValueError('Idle handoff replay differs from its original pending Host witness') from error
    if completed:
        stopped = _number(recovery['process_stopped_at'])
        clock = _number(recovery.get('source_phase_deadline_epoch'))
        window_ok = (
            completed_host_window(source, receipt, lineage=lineage, phase=clock) == (started, stopped)
            and math.floor(started) <= _number(native.get('started_at')) <= completed_at <= stopped
        )
    else:
        stdout, stderr = host_output_paths(Path(recovery['receipt_path']).parent)
        validate_idle_host_return(source, receipt, stdout=_physical_read(stdout),
                                  stderr=_physical_read(stderr), lineage=lineage)
        window_ok = (source['host_attempt']['host_failure'] == recovery['original_host_failure']
                     and completed_at < math.floor(started))
    if (not window_ok or hashlib.sha256(raw).hexdigest() != recovery['receipt_sha256']
            or native.get('status') != 'completed' or native.get('session_id') != receipt['session']['session_id']
            or type(native.get('turn_count')) is not int or native['turn_count'] < 1
            or not re.fullmatch(r'[a-f0-9]{64}', native.get('visible_history_sha256', ''))):
        raise ValueError('Idle handoff replay differs from the original qualified source')


def _returned_failure_witness(path: Path, receipt: dict, session_id: str,
                              started: float, stopped: float) -> dict:
    """Seal one complete Codex returned error, not a diagnostic classification."""
    from loopx.control_plane.turn_driver.codex_cli import codex_cli_event_session_id
    from loopx.hosts.codex_checkpoint import native_failure_error_digest
    from .worker import host_output_paths

    process = receipt.get('native_process') or {}
    before, after = process.get('before_cleanup') or {}, process.get('after_cleanup') or {}
    returned_at = _number(before.get('observed_at_unix'))
    if (receipt.get('timed_out') or receipt.get('error_kind')
            or any(type(frame.get('return_code')) is not int or frame['return_code'] != 1
                   for frame in (before, after))
            or not started <= returned_at <= stopped
            or before.get('signal') is not None or after.get('signal') is not None
            or process.get('cleanup_trigger') != 'scope_completed'
            or process.get('scope_error_type') is not None):
        raise ValueError('Explicit returned failure requires its original native return')
    stdout, stderr = host_output_paths(path.parent)
    raw, diagnostic = _physical_read(stdout), _physical_read(stderr)
    try:
        events = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    except (ValueError, UnicodeError) as error:
        raise ValueError('Explicit returned failure requires complete native stdout') from error
    if not events or any(not isinstance(event, dict) for event in events):
        raise ValueError('Explicit returned failure requires native event objects')
    threads = [index for index, event in enumerate(events)
               if event.get('type') in {'thread.started', 'thread_started'}]
    sessions = [codex_cli_event_session_id(events[index]) for index in threads]
    starts = [index for index, event in enumerate(events) if event.get('type') == 'turn.started']
    failures = [index for index, event in enumerate(events) if event.get('type') == 'turn.failed']
    if (sessions != [session_id] or len(starts) != 1 or failures != [len(events) - 1]
            or not threads[0] < starts[0] < failures[0]
            or any(event.get('type') == 'turn.completed' for event in events)):
        raise ValueError('Explicit returned failure differs from its original native invocation')
    error = events[-1].get('error')
    return {'session_id': session_id, 'process_started_at': started, 'process_stopped_at': stopped,
            'process_returned_at': returned_at,
            'stdout_sha256': hashlib.sha256(raw).hexdigest(), 'stderr_sha256': hashlib.sha256(diagnostic).hexdigest(),
            'error_sha256': native_failure_error_digest(error),
            'error_projection_sha256': native_failure_error_digest(error, cli_projection=True)}


def _verify_failed_wake(path: Path, receipt: dict, failure: dict, env: dict[str, str], *,
                        legacy: bool) -> None:
    from loopx.control_plane.turn_driver.host_failure import (
        build_host_failure_record, normalize_host_failure_record,
    )

    if (not isinstance(receipt, dict) or receipt.get('turn_id') != path.parent.name
            or receipt.get('mode') != 'heartbeat' or receipt.get('stage') != 'execute'
            or receipt.get('ok') is not False or receipt.get('host_invoked') is not True
            or receipt.get('budget_exhausted')
            or type(receipt.get('return_code')) is not int or receipt['return_code'] != 1
            or normalize_host_failure_record(receipt.get('host_failure'))
                != build_host_failure_record(failure['kind'], attempt=1)
            or (not legacy and any(receipt.get(key) != env['LOOPX_' + key.upper()]
                                   for key in ('goal_id', 'agent_id')))):
        raise ValueError('Failed Host receipt differs from this lane and failure')


def verify_failed_host_replay_witness(state_file: Path, prior: dict, recovery: dict, *,
                                      env: dict[str, str], wake_log_dir: str) -> None:
    """Verify the already qualified phase using its retained, local evidence."""
    from loopx.control_plane.content_digest import BARE_SHA256_PATTERN
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    from scripts.external_scheduler_worker import _host_pending
    from .codex import process_environment

    def digest(value):
        return isinstance(value, str) and BARE_SHA256_PATTERN.fullmatch(value) is not None

    native = recovery['native']
    allowance = recovery.get('allowance')
    failure = recovery['host_failure']
    if (not isinstance(allowance, dict) or allowance.get('availability') != 'available'
            or type(allowance.get('observed_at')) not in (int, float)
            or not 0 < _number(allowance['observed_at']) <= recovery['phase_deadline_epoch']
            or native.get('failure_kind') != failure['kind']
            or not isinstance(native.get('turn_id'), str) or not native['turn_id']
            or type(native.get('turn_count')) is not int or native['turn_count'] < 1
            or not digest(native.get('visible_history_sha256'))
            or not digest(recovery.get('session_profile_digest'))
            or not digest(recovery.get('receipt_sha256')) or _host_pending(state_file) is not True):
        raise ValueError('Explicit failed continuation requires the complete retained qualification')

    receipt_ref = recovery.get('receipt_path')
    path = Path(receipt_ref) if isinstance(receipt_ref, str) else None
    if (path is None or path.name != 'receipt.json' or path.parent.name != recovery.get('wake_turn_id')
            or path.parent.parent != Path(wake_log_dir).absolute()):
        raise ValueError('Explicit failed continuation requires the original wake receipt path')
    raw = _physical_read(path)
    if hashlib.sha256(raw).hexdigest() != recovery['receipt_sha256']:
        raise ValueError('Explicit failed continuation wake receipt changed')
    receipt = json.loads(raw)

    attempt = prior.get('attempt_id', '')
    legacy_source = env.get('LOOPX_INTERRUPTED_SOURCE')
    legacy = not attempt and bool(legacy_source)
    _verify_failed_wake(path, receipt, failure, env, legacy=legacy)
    if legacy:
        assert legacy_source is not None
        raw_proof = _physical_read(Path(legacy_source).absolute())
        proof = json.loads(raw_proof)
        container = recovery.get('source_container_id')
        if (not isinstance(proof, dict)
                or hashlib.sha256(raw_proof).hexdigest() != recovery.get('source_proof_sha256')
                or not digest(container) or container != proof.get('container_id')
                or proof.get('receipt_sha256') != recovery['receipt_sha256']
                or os.name != 'posix' or os.uname().nodename != container[:12]):
            raise ValueError('Explicit failed continuation stopped-source proof changed')
        source_deadline = proof['source_phase_deadline_epoch']
    else:
        if (not isinstance(attempt, str) or not re.fullmatch(r'[a-f0-9]{32}', attempt)
                or recovery.get('host_attempt_id') != attempt or receipt.get('host_attempt_id') != attempt):
            raise ValueError('Explicit failed continuation returned attempt changed')
        source_deadline = receipt['phase_deadline_epoch']

    session = receipt.get('session')
    if (not isinstance(session, dict) or session.get('binding_scope') != 'agent'
            or session.get('session_id') != prior['session_id']
            or _number(recovery.get('source_phase_deadline_epoch')) != _number(float(source_deadline))):
        raise ValueError('Explicit failed continuation retained wake scope changed')
    returned = recovery.get('returned_failure')
    if failure['kind'] != 'quota_exhausted' or returned is not None:
        started, stopped = _process_window(receipt)
        cursor = native.get('rollout_cursor')
        if (not isinstance(returned, dict)
                or returned != _returned_failure_witness(path, receipt, prior['session_id'], started, stopped)
                or not digest(native.get('error_sha256'))
                or native.get('returned_error_sha256') != returned.get('error_projection_sha256')
                or not isinstance(cursor, dict) or cursor.get('turn_id') != native['turn_id']
                or not digest(cursor.get('record_sha256'))
                or not started <= _number(cursor.get('started_at')) <= _number(cursor.get('completed_at'))
                    <= returned['process_returned_at']):
            raise ValueError('Explicit failed continuation returned failure proof changed')
    current_env = process_environment(Path(env['LOOPX_CODEX_HOME']), base=env)
    current = CodexAgentSession(current_env, context='resume',
        sandbox=env.get('LOOPX_CODEX_SANDBOX', 'danger-full-access'), receipt={})
    if current.session_id != prior['session_id'] or current.digest != recovery['session_profile_digest']:
        raise ValueError('Explicit failed continuation native session profile changed')


def read_failed_host(state: dict, *, state_file: Path, env: dict[str, str],
                     wake_log_dir: str, source_state_sha256: str) -> dict:
    """Qualify an explicit same-session retry of one exact returned failure.

    The digest binds a caller's fresh repair/allowance decision to this exact
    failed attempt, not to every subsequent failure. The ordinary scheduler and
    Turn launch still own current Goal, Stop, work and budget admission.
    """
    from loopx.control_plane.turn_driver.host_failure import (
        explicit_host_failure_retry_available, normalize_host_failure_record,
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
    if (prior.get('status') != 'failed'
            or state.get('last_wake_status') != 'wake_failed'
            or type(state.get('last_wake_rc')) is not int or state['last_wake_rc'] != 1
            or prior.get('session_id') is None
            or not explicit_host_failure_retry_available(failure, retry_failed=True)):
        raise ValueError('Original Host is not an explicitly repairable returned failure')
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
    _verify_failed_wake(path, receipt, failure, env, legacy=legacy)
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
    returned = (_returned_failure_witness(path, receipt, current.session_id, started, stopped)
                if failure['kind'] != 'quota_exhausted' else None)
    native = native_failed_turn(env, current.session_id, failure_kind=failure['kind'],
        process_started_at=started, process_stopped_at=returned['process_returned_at'] if returned else stopped,
        **({'returned_error_sha256': returned['error_projection_sha256']} if returned is not None else {}))
    allowance = require_native_receiver_allowance(env)
    with current.admission.launch_lifetime(operation='benchmark_failed_host_recovery'):
        pass
    if (_physical_read(path) != raw or _physical_read(state_file) != original_state
            or (returned is not None and returned != _returned_failure_witness(
                path, receipt, current.session_id, started, stopped))):
        raise ValueError('Original failure changed during recovery readback')
    return {**({'source_container_id': source['container_id'],
                'source_proof_sha256': hashlib.sha256(_physical_read(Path(env['LOOPX_INTERRUPTED_SOURCE']))).hexdigest()}
               if legacy else {'host_attempt_id': attempt}),
            'wake_turn_id': receipt['turn_id'], 'receipt_path': str(path),
            'receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'source_phase_deadline_epoch': phase, 'session_profile_digest': current.digest,
            **({'returned_failure': returned} if returned is not None else {}),
            'host_failure': failure, 'retry_failed': True, 'native': native, 'allowance': allowance}
