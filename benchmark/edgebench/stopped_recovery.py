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
import os
from pathlib import Path
import re
import tarfile
import tempfile
import time

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


def prepare_stopped_recovery(client, *, container_id: str, declaration: dict,
                             output: Path, timeout: float = 120, install: bool = False) -> dict:
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
    deadline = time.time() + timeout
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
        if (state.get('host_attempt', {}).get('status') != 'started'
                or json.loads(copied['host_pending'].read_bytes()) != {'pending': True}):
            raise ValueError('Stopped legacy recovery requires the original unresolved Host attempt')
        wake = copied['wake_log_dir'] / declaration['wake_turn_id'] / 'receipt.json'
        raw = wake.read_bytes()
        receipt = json.loads(raw)
        candidates = [p for p in copied['wake_log_dir'].glob('*/receipt.json')
                      if p.stat().st_mtime > copied['scheduler_state'].stat().st_mtime]
        if candidates != [wake]:
            raise ValueError('Stopped source has no unique returned wake after its Host attempt')
        if (receipt.get('turn_id') != declaration['wake_turn_id'] or receipt.get('host_invoked') is not True
                or receipt.get('budget_exhausted') is not True or receipt.get('mode') != 'heartbeat'
                or receipt.get('stage') != 'execute' or receipt.get('ok') is not False
                or 'host_failure' in receipt):
            raise ValueError('Stopped source lacks an exact interrupted budget receipt')
        start = int(declaration['wake_turn_id'].split('-')[1]) / 1_000_000_000
        phase = float(copied['phase_clock'].read_text())
        if not start <= phase <= finished:
            raise ValueError('Stopped source phase did not expire during the original wake')
        result = {'container_id': box.id, 'source_state': before, 'declaration': declaration,
                  'components': components, 'scheduler_state_sha256': hashlib.sha256(state_raw).hexdigest(),
                  'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                  'process_started_at': start, 'process_stopped_at': finished,
                  'source_phase_deadline_epoch': phase, 'session': receipt.get('session'),
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
                existing = _archive_component(box, str(parent / name), Path(directory) / 'proof', deadline=time.time() + timeout)
            except NotFound:
                existing = None
            if existing is not None and existing.read_bytes() != raw:
                raise ValueError('Original worker recovery proof path is already occupied')
        if existing is None and not box.put_archive(str(parent), package.getvalue()):
            raise ValueError('Original worker did not accept its inert recovery proof')
        with tempfile.TemporaryDirectory(prefix='.proof-readback-', dir=output.parent) as directory:
            copied = _archive_component(box, str(parent / name), Path(directory) / 'proof', deadline=time.time() + timeout)
            if copied.read_bytes() != raw:
                raise ValueError('Original worker recovery proof readback differs')
        box.reload()
        if box.attrs['State'] != before:
            raise ValueError('Original worker changed during proof installation')
        result['installed_proof_path'] = str(parent / name)
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
    args = parser.parse_args()
    import docker
    result = prepare_stopped_recovery(docker.from_env(timeout=30),
        container_id=args.source_container, declaration=json.loads(args.declaration.read_bytes()),
        output=args.output, install=args.install)
    print(json.dumps({'container_id': result['container_id'], 'output': str(args.output),
                      'installed_proof_path': result.get('installed_proof_path'), 'execution_authority_granted': False}))


if __name__ == '__main__':
    main()
