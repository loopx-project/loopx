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
    if not math.isfinite(phase) or not started <= phase <= stopped:
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
