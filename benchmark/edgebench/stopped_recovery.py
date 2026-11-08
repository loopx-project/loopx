"""Prepare inert evidence from one stopped worker's unchanged filesystem.

The Docker provider proves namespace termination. The runtime recovery owner
checks these exact bytes and the native session before any new admission.
This command never starts a container, restores a judge, or invokes a model.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tarfile
import tempfile
import time

from .checkpoint_controller import _remaining
from .checkpoint_worker import _archive_component, _guest_path


def file_inventory(root: Path) -> list[dict]:
    entries = [root] if root.is_file() else sorted(root.rglob('*'))
    result = []
    for path in entries:
        if path.is_symlink():
            raise ValueError('Interrupted source inventory cannot contain symlinks')
        if path.name.endswith('-shm'):
            database = path.with_name(path.name[:-4])
            if database.is_file() and not database.is_symlink():
                with database.open('rb') as source:
                    if source.read(16) == b'SQLite format 3\0':
                        continue  # Only a SQLite participant's reader index is rebuilt.
        if path.is_file():
            h = hashlib.sha256()
            with path.open('rb') as stream:
                while data := stream.read(1024 * 1024):
                    h.update(data)
            result.append({'path': '' if path == root else str(path.relative_to(root)),
                           'bytes': path.stat().st_size, 'sha256': h.hexdigest()})
        elif not path.is_dir():
            raise ValueError('Interrupted source inventory contains a special file')
    return result


def source_paths(declaration: dict) -> dict[str, str]:
    required = {'goal_id', 'agent_id', 'workspace', 'runtime_root', 'native_home',
                'registry', 'scheduler_state', 'wake_log_dir', 'phase_clock', 'wake_turn_id'}
    if set(declaration) != required:
        raise ValueError('Stopped recovery requires its exact lane and original source paths')
    for key in required - {'goal_id', 'agent_id', 'wake_turn_id'}:
        _guest_path(declaration[key])
    if not re.fullmatch(r'wake-[0-9]+-[a-f0-9]{12}', declaration['wake_turn_id']):
        raise ValueError('Stopped recovery requires the original worker wake identity')
    home = declaration['native_home']
    # Explicit native participants exclude auth, caches and credential stores.
    return {**{k: declaration[k] for k in ('workspace', 'runtime_root', 'registry',
                'scheduler_state', 'wake_log_dir', 'phase_clock')},
            'host_pending': declaration['scheduler_state'] + '.host-attempt',
            'native_sessions': home + '/sessions', 'native_state': home + '/state_5.sqlite',
            'native_history': home + '/thread_history_1.sqlite', 'native_config': home + '/config.toml'}


def validate_idle_host_return(state: dict, receipt: dict, *, stdout: bytes, stderr: bytes,
                              lineage: dict) -> None:
    """Qualify only an explicitly selected legacy empty-output budget return.

    The unknown failure remains unknown. Live native history must independently
    prove an idle cursor before this attempt; this predicate cannot grant work.
    """
    from loopx.control_plane.turn_driver.host_failure import normalize_host_failure_record
    from scripts.external_scheduler_worker import _host_attempt

    prior = state.get('host_attempt', {})
    scope = prior.get('scope')
    if not isinstance(scope, str) or not re.fullmatch(r'[a-f0-9]{64}', scope):
        raise ValueError('Idle handoff requires its original Host attempt scope')
    _host_attempt(state, scope)
    failure = normalize_host_failure_record(prior.get('host_failure'))
    session = receipt.get('session', {})
    if (prior.get('status') != 'failed' or prior.get('attempt_id') is not None
            or failure['kind'] != 'unknown' or failure['attempt'] != prior.get('attempt')
            or state.get('last_wake_status') != 'wake_failed'
            or type(state.get('last_wake_rc')) is not int or state['last_wake_rc'] != 1
            or receipt.get('mode') != 'heartbeat' or receipt.get('stage') != 'execute'
            or receipt.get('ok') is not False or receipt.get('host_invoked') is not True
            or receipt.get('budget_exhausted') is not True or receipt.get('timed_out') is not True
            or 'host_failure' in receipt or 'return_code' in receipt
            or stdout or stderr or session.get('binding_scope') != 'agent'
            or any(key in receipt and receipt[key] != lineage[key] for key in ('goal_id', 'agent_id'))
            or not isinstance(session.get('session_id'), str) or not session['session_id'].strip()
            or prior.get('session_id') not in (None, session['session_id'])):
        raise ValueError('Idle handoff requires its original empty-output legacy Host return')


def prepare_stopped_recovery(client, *, container_id: str, declaration: dict,
                             output: Path, timeout: float = 120, install: bool = False,
                             idle_host_handoff: bool = False) -> dict:
    if type(idle_host_handoff) is not bool:
        raise ValueError('Idle Host handoff requires an explicit boolean')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Stopped recovery timeout must be finite and positive')
    deadline = time.monotonic() + timeout
    if not re.fullmatch(r'[a-f0-9]{64}', container_id):
        raise ValueError('Stopped recovery requires one full container identity')
    paths = source_paths(declaration)
    if output.exists() or output.is_symlink() or output.parent.resolve() != output.parent:
        raise ValueError('Stopped recovery output must be fresh and physical')
    box = client.containers.get(container_id)
    box.reload()
    before = dict(box.attrs['State'])
    if (box.id != container_id or before.get('Status') != 'exited'
            or before.get('Running') is not False or before.get('Pid') != 0
            or before.get('Restarting') or before.get('Paused') or before.get('Dead')):
        raise ValueError('Original worker namespace is not demonstrably stopped')
    # This source can be restarted as an inert shell, never by replaying its
    # original install/run command. Actual restart remains the caller's action.
    if box.attrs['Config'].get('Entrypoint') or box.attrs['Config'].get('Cmd') != ['tail', '-f', '/dev/null']:
        raise ValueError('Original worker must have an inert restart command')
    if box.attrs['Config'].get('Hostname') != box.id[:12]:
        raise ValueError('Stopped recovery requires the original default Docker hostname')
    if box.attrs.get('Mounts'):
        raise ValueError('Stopped worker has externally writable mounts')
    finished = datetime.fromisoformat(before['FinishedAt'].replace('Z', '+00:00')).timestamp()
    components = []
    with tempfile.TemporaryDirectory(prefix='.stopped-source-', dir=output.parent) as temporary:
        copied = {}
        for key, remote in paths.items():
            local = _archive_component(box, remote, Path(temporary) / key, deadline=deadline)
            copied[key] = local
            # SQLite companions are declared separately: a WAL is committed
            # source data, not an optional display/cache file.
            for suffix in ('', '-wal', '-journal') if local.is_file() else ('',):
                member = Path(str(local) + suffix)
                if member.exists():
                    components.append({'source_path': remote + suffix,
                                       'files': file_inventory(member), 'directory': member.is_dir()})
        state_raw = copied['scheduler_state'].read_bytes()
        state = json.loads(state_raw)
        prior = state.get('host_attempt', {})
        failed = prior.get('status') == 'failed'
        returned_budget_stop = prior.get('status') == 'stopped'
        if (prior.get('status') not in {'started', 'failed', 'stopped'}
                or json.loads(copied['host_pending'].read_bytes()) != {'pending': True}):
            raise ValueError('Stopped legacy recovery requires the original unresolved Host attempt')
        wake = copied['wake_log_dir'] / declaration['wake_turn_id'] / 'receipt.json'
        raw = wake.read_bytes()
        receipt = json.loads(raw)
        candidates = [p for p in copied['wake_log_dir'].glob('*/receipt.json')
                      if p.stat().st_mtime > copied['scheduler_state'].stat().st_mtime]
        if returned_budget_stop:
            from benchmark.runtime.scheduler import BUDGET_EXHAUSTED_EXIT_CODE

            session = receipt.get('session', {})
            if (prior.get('attempt_id') is not None or 'host_failure' in prior
                    or type(prior.get('stop_exit_code')) is not int
                    or prior['stop_exit_code'] != BUDGET_EXHAUSTED_EXIT_CODE
                    or type(state.get('last_wake_rc')) is not int
                    or state['last_wake_rc'] != BUDGET_EXHAUSTED_EXIT_CODE
                    or state.get('last_wake_status') != 'wake_requested_stop'
                    or state.get('last_wake_failure_kind') is not None
                    or receipt.get('timed_out') is not True
                    or type(receipt.get('return_code', BUDGET_EXHAUSTED_EXIT_CODE)) is not int
                    or receipt.get('return_code', BUDGET_EXHAUSTED_EXIT_CODE) != BUDGET_EXHAUSTED_EXIT_CODE
                    or session.get('binding_scope') != 'agent'
                    or not isinstance(session.get('session_id'), str) or not session['session_id'].strip()
                    or prior.get('session_id') not in (None, session['session_id'])
                    or any(key in receipt and receipt[key] != declaration[key]
                           for key in ('goal_id', 'agent_id'))):
                raise ValueError('Stopped source lacks its exact returned budget stop')
        if idle_host_handoff:
            validate_idle_host_return(state, receipt, stdout=(wake.parent / 'stdout.jsonl').read_bytes(),
                                      stderr=(wake.parent / 'stderr.txt').read_bytes(), lineage=declaration)
        elif failed:
            from loopx.control_plane.turn_driver.host_failure import build_host_failure_record, normalize_host_failure_record

            failure = normalize_host_failure_record(prior.get('host_failure'))
            if (failure['kind'] != 'quota_exhausted' or prior.get('session_id') is None
                    or failure['attempt'] != prior.get('attempt')
                    or state.get('last_wake_status') != 'wake_failed'
                    or type(state.get('last_wake_rc')) is not int or state['last_wake_rc'] != 1
                    or normalize_host_failure_record(receipt.get('host_failure'))
                        != build_host_failure_record(failure['kind'], attempt=1)
                    or receipt.get('session', {}).get('session_id') != prior['session_id']
                    or receipt.get('session', {}).get('binding_scope') != 'agent'
                    or type(receipt.get('return_code')) is not int or receipt['return_code'] != 1
                    or receipt.get('budget_exhausted')):
                raise ValueError('Stopped source lacks its exact returned quota failure')
        if failed or returned_budget_stop:
            # Returned failures and budget stops are committed after their
            # receipt. A still-started legacy attempt has the opposite order.
            # Neither path may borrow an earlier wake or skip a later one.
            all_receipts = list(copied['wake_log_dir'].glob('*/receipt.json'))
            newest = max((p.stat().st_mtime for p in all_receipts), default=0)
            if candidates or [p for p in all_receipts if p.stat().st_mtime == newest] != [wake]:
                raise ValueError('Stopped source is not the last returned wake')
            candidates = [wake]
        if candidates != [wake]:
            raise ValueError('Stopped source has no unique returned wake after its Host attempt')
        if (receipt.get('turn_id') != declaration['wake_turn_id'] or receipt.get('host_invoked') is not True
                or (not failed and receipt.get('budget_exhausted') is not True) or receipt.get('mode') != 'heartbeat'
                or receipt.get('stage') != 'execute' or receipt.get('ok') is not False
                or (not failed and 'host_failure' in receipt)):
            raise ValueError('Stopped source lacks an exact interrupted budget receipt')
        start = int(declaration['wake_turn_id'].split('-')[1]) / 1_000_000_000
        phase = float(copied['phase_clock'].read_text())
        # A returned stop proves the process was reaped before its receipt.
        # Legacy workers could reserve cleanup time before the hard deadline;
        # do not enlarge that native Turn's window to a later container stop.
        process_stopped = math.floor(wake.stat().st_mtime) if returned_budget_stop or idle_host_handoff else finished
        if (not math.isfinite(phase) or not start <= min(phase, process_stopped)
                or process_stopped > finished
                or (not failed and not returned_budget_stop and phase > finished)):
            raise ValueError('Stopped source phase did not expire during the original wake')
        result = {'container_id': box.id, 'source_state': before, 'declaration': declaration,
                  'components': components, 'scheduler_state_sha256': hashlib.sha256(state_raw).hexdigest(),
                  'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                  'process_started_at': start, 'process_stopped_at': process_stopped,
                  'source_phase_deadline_epoch': phase, 'session': receipt.get('session'),
                  **({'idle_host_handoff': True} if idle_host_handoff else {}),
                  'execution_authority_granted': False}
        box.reload()
        if box.attrs['State'] != before:
            raise ValueError('Original worker changed during stopped-source readback')
    if install:
        from docker.errors import NotFound

        raw = json.dumps(result, sort_keys=True).encode()
        name = 'interruption-source-' + hashlib.sha256(raw).hexdigest() + '.json'
        parent = Path(declaration['scheduler_state']).parent
        if any(parent == Path(p) or Path(p) in parent.parents for p in
               (declaration['workspace'], declaration['runtime_root'], declaration['wake_log_dir'], declaration['native_home'])):
            raise ValueError('Recovery proof must not mutate its inventoried source directories')
        # A stopped worker's inert control directory is the sole destination;
        # never place the proof in a different container or overwrite old state.
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode='w') as archive:
            member = tarfile.TarInfo(name)
            member.mode, member.size = 0o600, len(raw)
            archive.addfile(member, io.BytesIO(raw))
        box.reload()
        if box.attrs['State'] != before:
            raise ValueError('Original worker changed before proof installation')
        with tempfile.TemporaryDirectory(prefix='.proof-existing-', dir=output.parent) as directory:
            try:
                existing = _archive_component(box, str(parent / name), Path(directory) / 'proof', deadline=deadline)
            except NotFound:
                existing = None
            if existing is not None and existing.read_bytes() != raw:
                raise ValueError('Original worker recovery proof path is already occupied')
        if existing is None:
            _remaining(deadline)
            if not box.put_archive(str(parent), package.getvalue()):
                raise ValueError('Original worker did not accept its inert recovery proof')
        with tempfile.TemporaryDirectory(prefix='.proof-readback-', dir=output.parent) as directory:
            copied = _archive_component(box, str(parent / name), Path(directory) / 'proof', deadline=deadline)
            if copied.read_bytes() != raw:
                raise ValueError('Original worker recovery proof readback differs')
        box.reload()
        if box.attrs['State'] != before:
            raise ValueError('Original worker changed during proof installation')
        result['installed_proof_path'] = str(parent / name)
    _remaining(deadline)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(result, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-container', required=True)
    parser.add_argument('--declaration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--install', action='store_true', help='Place the inert proof only in this same stopped worker')
    parser.add_argument('--idle-host-handoff', action='store_true',
                        help='Explicitly seal an empty-output legacy Host failure for separate idle-history qualification')
    args = parser.parse_args()
    import docker
    result = prepare_stopped_recovery(docker.from_env(timeout=30),
        container_id=args.source_container, declaration=json.loads(args.declaration.read_bytes()),
        output=args.output, install=args.install, idle_host_handoff=args.idle_host_handoff)
    print(json.dumps({'container_id': result['container_id'], 'output': str(args.output),
                      'installed_proof_path': result.get('installed_proof_path'), 'execution_authority_granted': False}))


if __name__ == '__main__':
    main()
