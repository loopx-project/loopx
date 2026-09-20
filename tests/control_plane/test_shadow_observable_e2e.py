"""Caller semantics across capture activation, with optional base/head evidence.

LOOPX_SHADOW_COMPARISON_SOURCE selects an immutable checkout for every child;
LOOPX_SHADOW_COMPARISON_OUTPUT retains complete responses and persisted bytes.
Neither changes the oracle. No product result or persistence operation is mocked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.stage2c_e2e
SOURCE = Path(os.environ.get('LOOPX_SHADOW_COMPARISON_SOURCE', Path(__file__).resolve().parents[2])).resolve()


class Caller:
    def __init__(self, path: Path, mode: str, name: str):
        self.path, self.mode, self.name = path, mode, name
        self.state, self.registry, self.root = path / 'STATE.md', path / 'registry.json', path / 'runtime'
        self.env = dict(os.environ, PYTHONPATH=str(SOURCE))
        self.rows: list[dict] = []
        self.state.write_text('---\ngoal_id: observable\nhandoff_mode: soft_claim\n'
            'updated_at: 2026-09-01T00:00:00+00:00\n---\n\n## Agent Todo\n\n'
            '## Progress Ledger\n\n## Next Action\n\n- Inspect.\n')
        coordination = {'agent_model': 'peer_v1', 'registered_agents': ['agent-a', 'agent-b']}
        if mode != 'absent':
            coordination['runtime_shadow'] = {'schema_version': 'loopx_coordination_runtime_shadow_config_v0',
                'enabled': mode == 'enabled', 'provider': 'file_v0'}
        self.registry.write_text(json.dumps({'common_runtime_root': str(self.root), 'goals': [{
            'id': 'observable', 'status': 'active', 'repo': str(path), 'state_file': self.state.name,
            'coordination': coordination}]}))
        actual = subprocess.check_output([sys.executable, '-c', 'import loopx; print(loopx.__file__)'],
            env=self.env, cwd=path, text=True).strip()
        assert Path(actual).resolve().is_relative_to(SOURCE)
        if mode == 'enabled':
            assert self.call('coordination-shadow', 'bootstrap', '--execute')['bootstrap']['status'] == 'applied'

    def files(self) -> dict[str, str]:
        # All fixture files, including receipts. Locks are retained in raw evidence too.
        return {str(p.relative_to(self.path)): p.read_bytes().hex()
                for p in sorted(self.path.rglob('*')) if p.is_file()}

    def primary(self) -> dict[str, str]:
        return {k: v for k, v in self.files().items()
                if k == 'STATE.md' or k.startswith('runtime/goals/') and not k.endswith('.lock')}

    def call(self, *args: str, goal: str = 'observable') -> dict:
        command = [sys.executable, '-m', 'loopx.cli', '--registry', str(self.registry),
            '--runtime-root', str(self.root), '--format', 'json', *args, '--goal-id', goal]
        return self.invoke(command, command[3:])

    def invoke(self, command: list[str], arguments: list[str], *, cwd: Path | None = None) -> dict:
        before = self.files()
        result = subprocess.run(command, env=self.env, cwd=cwd or self.path, capture_output=True, text=True, timeout=45)
        assert 'Traceback' not in result.stderr, result.stderr
        row = {'arguments': arguments, 'exit': result.returncode, 'stdout': result.stdout,
               'stderr': result.stderr, 'files_before': before, 'files_after': self.files()}
        self.rows.append(row)
        destination = os.environ.get('LOOPX_SHADOW_COMPARISON_OUTPUT')
        if destination:
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            (output / (self.name + '.json')).write_text(json.dumps({
                'source': str(SOURCE), 'workspace': str(self.path), 'mode': self.mode, 'rows': self.rows}, indent=2))
        return json.loads(result.stdout) if result.stdout else {'stderr': result.stderr, 'exit': result.returncode}

    def add(self, text: str, *extra: str) -> str:
        result = self.call('todo', 'add', '--role', 'agent', '--text', text, *extra)
        assert result['ok'] is True, result
        return result['todo_id']

    def read(self, todo: str) -> dict:
        result = self.call('todo', 'list')
        assert result['ok'] is True, result
        return next(item for item in result['todos'] if item['todo_id'] == todo)


@pytest.fixture(params=['absent', 'disabled', 'enabled'])
def caller(tmp_path: Path, request: pytest.FixtureRequest) -> Caller:
    return Caller(tmp_path, request.param, request.node.name)


def test_todo_argument_intent_and_rejections(caller: Caller) -> None:
    w = caller
    before = w.primary()
    preview = w.call('todo', 'add', '--role', 'agent', '--text', 'Retain operator intent', '--dry-run')
    assert preview['ok'] is True and w.primary() == before
    todo = w.add('Retain operator intent', '--claimed-by', 'agent-a', '--note', 'Initial context')
    assert w.read(todo)['claimed_by'] == 'agent-a'
    for args, expected_note in [(('--note', 'Updated context'), 'Updated context'),
                                (('--text', 'Corrected operator intent'), 'Updated context')]:
        result = w.call('todo', 'update', '--todo-id', todo, '--agent-id', 'agent-a', *args)
        assert result['ok'] is True, result
        current = w.read(todo)
        assert current['note'] == expected_note and current['claimed_by'] == 'agent-a'
    assert w.read(todo)['text'] == 'Corrected operator intent'
    # CLI mutable-field validation treats an empty note as absent, not a clear.
    before = w.primary()
    empty = w.call('todo', 'update', '--todo-id', todo, '--agent-id', 'agent-a', '--note', '')
    assert empty['error'] == 'todo update requires at least one mutable todo field'
    assert w.primary() == before
    assert w.call('todo', 'update', '--todo-id', todo, '--agent-id', 'agent-a', '--clear-claim')['ok'] is True
    assert not w.read(todo).get('claimed_by')
    before = w.primary()
    # Overlapping invalid target and actor: preserve the complete diagnostic/priority.
    rejected = w.call('todo', 'update', '--todo-id', 'todo_missing', '--agent-id', 'unknown', '--note', 'Must not write')
    assert rejected['ok'] is False and w.primary() == before, rejected
    assert w.call('todo', 'claim', '--todo-id', todo, '--claimed-by', 'agent-a', '--agent-id', 'agent-a')['ok'] is True
    complete = w.call('todo', 'complete', '--todo-id', todo, '--agent-id', 'agent-a',
                      '--evidence', 'validation://caller', '--no-follow-up')
    assert complete['ok'] is True, complete
    assert w.read(todo)['done'] is True
    archived = w.call('todo', 'archive-completed', '--max-active-done', '0', '--execute')
    assert archived['ok'] is True, archived
    assert 'Corrected operator intent' in w.state.read_text()


def test_handoff_mode_quiescence(caller: Caller) -> None:
    w = caller
    assert w.call('handoff-mode', 'set', '--mode', 'hard_lease')['changed'] is True
    before = w.primary()
    assert w.call('handoff-mode', 'set', '--mode', 'hard_lease')['changed'] is False
    assert w.primary() == before
    todo = w.add('Quiescence ownership')
    acquired = w.call('task-lease', 'acquire', '--todo-id', todo, '--owner', 'agent-a', '--idempotency-key', 'quiescence')
    assert acquired['acquired'] is True, acquired
    before = w.primary()
    blocked = w.call('handoff-mode', 'set', '--mode', 'soft_claim')
    assert blocked['error_code'] == 'handoff_mode_not_quiescent' and w.primary() == before
    malformed = w.call('handoff-mode', 'set', '--mode', 'invalid')
    assert malformed['exit'] == 2 and w.primary() == before


def test_lease_arguments_cas_transfer_and_replay(caller: Caller) -> None:
    w = caller
    assert w.call('handoff-mode', 'set', '--mode', 'hard_lease')['ok'] is True
    todo = w.add('Lease argument round trip')
    args = ('task-lease', 'acquire', '--todo-id', todo, '--owner', 'agent-a',
            '--idempotency-key', 'caller-acquire', '--ttl-seconds', '3600', '--write-scope', 'src/**')
    result = w.call(*args)
    assert result['acquired'] is True, result
    lease_path = w.root / 'goals' / 'observable' / 'task-leases' / f'{todo}.json'
    persisted = json.loads(lease_path.read_bytes())
    assert persisted['owner'] == 'agent-a' and persisted['write_scopes'] == ['src/**']
    assert persisted['acquire_ttl_seconds'] == 3600
    before = lease_path.read_bytes()
    assert w.call(*args)['ok'] is True
    assert lease_path.read_bytes() == before
    before = w.primary()
    rejected = w.call('task-lease', 'renew', '--todo-id', todo, '--owner', 'agent-b',
        '--idempotency-key', 'wrong-key', '--expected-version', '999')
    assert rejected['ok'] is False and w.primary() == before, rejected
    renewed = w.call('task-lease', 'renew', '--todo-id', todo, '--owner', 'agent-a',
        '--idempotency-key', 'caller-acquire', '--expected-version', str(persisted['version']), '--ttl-seconds', '7200')
    assert renewed['ok'] is True, renewed
    transferred = w.call('task-lease', 'transfer', '--todo-id', todo, '--owner', 'agent-a',
        '--idempotency-key', 'caller-acquire', '--expected-version', str(renewed['lease']['version']),
        '--new-owner', 'agent-b', '--new-idempotency-key', 'caller-transfer')
    assert transferred['ok'] is True, transferred
    assert json.loads(lease_path.read_bytes())['owner'] == 'agent-b'
    released = w.call('task-lease', 'release', '--todo-id', todo, '--owner', 'agent-b',
        '--idempotency-key', 'caller-transfer', '--expected-version', str(transferred['lease']['version']))
    assert released['ok'] is True, released
    assert json.loads(lease_path.read_bytes())['status'] == 'released'
    assert w.call('task-lease', 'inspect', '--todo-id', todo)['ok'] is True


def test_refresh_and_reward_owned_prose(caller: Caller) -> None:
    w = caller
    todo = w.add('Canonical record must survive prose')
    record = w.read(todo)
    args = ('refresh-state', '--agent-id', 'agent-a', '--progress-scope', 'goal', '--classification', 'continue',
            '--recommended-action', 'Inspect persisted arguments.', '--vision-unchanged-reason', 'Same bounded validation.',
            '--next-action', 'Read the independent lease snapshot.', '--no-global-sync')
    before = w.primary()
    assert w.call(*args, '--dry-run')['ok'] is True
    assert w.primary() == before
    refreshed = w.call(*args)
    assert refreshed['ok'] is True, refreshed
    assert 'Read the independent lease snapshot.' in w.state.read_text()
    assert w.read(todo) == record
    args = ('reward', '--actor-kind', 'owner', '--recorded-at', '2026-09-01T12:00:00+00:00', '--decision', 'continue',
        '--reward', 'positive', '--reason-summary', 'Retained argument evidence.', '--write-active-state-summary')
    before = w.primary()
    assert w.call(*args, '--dry-run')['ok'] is True
    assert w.primary() == before
    reward = w.call(*args)
    assert reward['ok'] is True, reward
    assert 'Retained argument evidence.' in w.state.read_text()
    index = w.root / 'goals' / 'observable' / 'runs' / 'index.jsonl'
    assert 'Retained argument evidence.' in index.read_text()
    assert w.read(todo) == record
    before = w.primary()
    rejected = w.call('reward', '--run-generated-at', 'missing', '--decision', '', '--reward', 'positive', '--reason-summary', '')
    assert rejected['ok'] is False and w.primary() == before, rejected


@pytest.mark.parametrize('replacement', ['force', 'missing'])
def test_bootstrap_replacement_preserves_existing_authority(caller: Caller, replacement: str) -> None:
    w = caller
    w.add('Existing canonical state')
    args = ('bootstrap', '--project', str(w.path), '--state-file', 'STATE.md',
            '--objective', 'Replacement objective', '--no-global-sync')
    assert w.call(*args, '--dry-run')['ok'] is True
    if replacement == 'missing':
        w.state.unlink()
    before = w.registry.read_bytes(), w.state.read_bytes() if w.state.exists() else None
    result = w.call(*args, *(['--force'] if replacement == 'force' else []))
    if w.mode == 'enabled':
        assert result['ok'] is False, result
        assert (w.registry.read_bytes(), w.state.read_bytes() if w.state.exists() else None) == before
    else:
        assert result['ok'] is True, result
        assert 'Replacement objective' in w.state.read_text()
        assert json.loads(w.registry.read_bytes())['common_runtime_root'] == str(w.root)


def test_project_registration_and_missing_state_reconstruction(caller: Caller) -> None:
    w = caller
    args = ('project', 'register', '--project-id', 'registered-project', '--project-kind', 'work',
        '--knowledge-root', str(w.path / 'knowledge'), '--objective', 'Preserve project intent',
        '--acceptance', 'Independent state readback', '--non-goal', 'Remote promotion',
        '--next-effect', 'Inspect the source', '--stop-condition', 'Readback matches')
    created = w.call(*args, goal='registered')
    assert created['ok'] is True, created
    state = Path(created['state_file'])
    assert 'Preserve project intent' in state.read_text()
    assert 'Independent state readback' in state.read_text()
    assert w.call(*args, goal='registered')['ok'] is True
    if w.mode == 'enabled':
        registry = json.loads(w.registry.read_bytes())
        goal = next(g for g in registry['goals'] if g['id'] == 'registered')
        goal.setdefault('coordination', {})['runtime_shadow'] = {
            'schema_version': 'loopx_coordination_runtime_shadow_config_v0', 'enabled': True, 'provider': 'file_v0'}
        w.registry.write_text(json.dumps(registry))
        assert w.call('coordination-shadow', 'bootstrap', '--execute', goal='registered')['bootstrap']['status'] == 'applied'
    state.unlink()
    before = w.registry.read_bytes()
    rebuilt = w.call(*args, goal='registered')
    if w.mode == 'enabled':
        assert rebuilt['ok'] is False, rebuilt
        assert not state.exists() and w.registry.read_bytes() == before
    else:
        assert rebuilt['ok'] is True, rebuilt
        assert 'Preserve project intent' in state.read_text()


def test_migration_target_and_preview_ownership(caller: Caller) -> None:
    w = caller
    legacy = w.path / 'legacy'
    legacy.mkdir()
    source = legacy / 'STATE.md'
    source.write_text('---\ngoal_id: legacy\nhandoff_mode: soft_claim\n---\n\n## Agent Todo\n\n- [ ] Migrated source.\n')
    source_registry = legacy / 'registry.json'
    source_registry.write_text(json.dumps({'goals': [{'id': 'legacy', 'repo': str(legacy), 'state_file': 'STATE.md'}]}))
    args = ('migrate-state', '--legacy-registry', str(source_registry),
        '--legacy-runtime-root', str(legacy / 'runtime'), '--target-runtime-root', str(w.root),
        '--goal-id-map', 'legacy=observable', '--path-map', f'{legacy}={w.path}',
        '--copy-active-state', '--no-global-sync')
    before = w.registry.read_bytes(), w.state.read_bytes()
    preview = w.call(*args, goal='legacy')
    assert preview['ok'] is True, preview
    assert (w.registry.read_bytes(), w.state.read_bytes()) == before
    result = w.call(*args, '--execute', goal='legacy')
    if w.mode == 'enabled':
        assert result['ok'] is False, result
        assert (w.registry.read_bytes(), w.state.read_bytes()) == before
    else:
        assert result['ok'] is True, result
        assert 'goal_id: observable' in w.state.read_text() and 'Migrated source.' in w.state.read_text()
        assert json.loads(w.registry.read_bytes())['goals'][0]['id'] == 'observable'


def test_operator_reads_and_invalid_selector_have_no_primary_effect(caller: Caller) -> None:
    w = caller
    todo = w.add('Read policy fixture')
    before = w.primary()
    for command in ['inspect', 'qualify', 'read-candidate']:
        args = ('--todo-id', todo) if command == 'read-candidate' else ()
        result = w.call('coordination-shadow', command, *args)
        assert result['ok'] is (w.mode == 'enabled' and command == 'inspect'), result
        assert w.primary() == before
    invalid = w.call('coordination-shadow', 'rollback', '--provider-revision', '', '--execute')
    assert invalid['ok'] is False and w.primary() == before, invalid
    # Availability alone never constructs a candidate/outbox in the disabled goal.
    if w.mode != 'enabled':
        assert not (w.root / 'authority-shadow').exists()


def test_monitor_successor_retains_caller_routing(caller: Caller) -> None:
    w = caller
    todo = w.add('Observe the public release', '--task-class', 'continuous_monitor', '--action-kind', 'monitor',
        '--claimed-by', 'agent-a', '--target-key', 'release:bounded', '--cadence', '30m',
        '--next-due-at', '2000-01-01T00:00:00+00:00', '--watch-only')
    result = w.call('quota', 'monitor-poll', '--agent-id', 'agent-a', '--runtime-profile', 'generic_cli',
        '--available-capability', 'network', '--todo-id', todo, '--target-key', 'release:bounded',
        '--result-hash', 'release-v1', '--material-change', '--next-agent-todo', 'Validate released head',
        '--next-action-kind', 'validate_release_head', '--next-task-repository', 'git:github.com/huangruiteng/loopx',
        '--next-required-capability', 'network', '--next-continuation-policy', 'same_agent_non_delivery',
        '--next-claimed-by', 'agent-a', '--execute')
    assert result['ok'] is True and len(result['successor_todo_ids']) == 1, result
    successor = w.read(result['successor_todo_ids'][0])
    assert successor['text'] == 'Validate released head' and successor['claimed_by'] == 'agent-a'
    assert successor['action_kind'] == 'validate_release_head' and successor['required_capabilities'] == ['network']


def test_observation_remains_independent_of_runtime_capture(caller: Caller) -> None:
    w = caller
    before = w.primary()
    args = ('configure-goal', '--local-authority-shadow-file')
    assert w.call(*args)['ok'] is True
    assert w.primary() == before
    assert w.call(*args, '--execute')['ok'] is True
    todo = w.add('Independent observation contract')
    assert w.read(todo)['text'] == 'Independent observation contract'
    retained = sorted((w.root / 'authority-shadow' / 'file' / 'observable').glob('authority-store-*.json'))
    assert len(retained) == 1
    snapshot = retained[0].read_bytes()
    assert w.call('configure-goal', '--clear-local-authority-shadow', '--execute')['ok'] is True
    assert retained[0].read_bytes() == snapshot
    if w.mode != 'enabled':
        assert not (w.root / 'authority-shadow' / 'file-v0').exists()


def test_turn_input_rejection_has_no_host_or_primary_effect(caller: Caller) -> None:
    w = caller
    before = w.primary()
    result = w.call('turn', 'run-once', '--project', str(w.path), '--agent-id', 'unknown', '--no-global-sync')
    assert result['ok'] is False, result
    assert result['effects']['host_invoked'] is False and result['effects']['state_written'] is False
    assert w.primary() == before


def test_registry_relative_root_does_not_depend_on_callers_cwd(caller: Caller) -> None:
    w = caller
    registry = json.loads(w.registry.read_bytes())
    registry['common_runtime_root'] = 'runtime'
    w.registry.write_text(json.dumps(registry))
    cwd = w.path / 'unrelated-cwd'
    cwd.mkdir()
    command = [sys.executable, '-m', 'loopx.cli', '--registry', str(w.registry), '--format', 'json',
        'todo', 'add', '--goal-id', 'observable', '--role', 'agent', '--text', 'Registry relative root']
    added = w.invoke(command, command[3:], cwd=cwd)
    assert added['ok'] is True, added
    assert w.read(added['todo_id'])['text'] == 'Registry relative root'
    assert (w.root / 'goals' / 'observable' / 'rollout-event-log.jsonl').exists()
    assert not (cwd / 'runtime').exists()
    assert w.call('handoff-mode', 'set', '--mode', 'hard_lease')['ok'] is True
    command = [*command[:7], 'task-lease', 'acquire', '--goal-id', 'observable',
        '--todo-id', added['todo_id'], '--owner', 'agent-a', '--idempotency-key', 'relative-lease']
    lease = w.invoke(command, command[3:], cwd=cwd)
    assert lease['ok'] is True, lease
    assert (w.root / 'goals' / 'observable' / 'task-leases' / f"{added['todo_id']}.json").exists()
    assert not (cwd / 'runtime').exists()


def test_legacy_holder_verify_and_terminal_release(caller: Caller) -> None:
    w = caller
    w.call('handoff-mode', 'set', '--mode', 'hard_lease')
    todo = w.add('Holder and terminal write boundaries')
    before = w.primary()
    rejected_claim = w.call('todo', 'claim', '--todo-id', todo, '--claimed-by', 'agent-a', '--agent-id', 'agent-a',
        '--task-lease-idempotency-key', 'caller-holder', '--task-lease-expected-version', '0')
    assert rejected_claim['error'] == '--task-lease-idempotency-key on todo claim requires promoted canonical authority; no legacy write attempted'
    assert w.primary() == before
    assert w.call('task-lease', 'acquire', '--todo-id', todo, '--owner', 'agent-a', '--idempotency-key', 'caller-holder')['ok'] is True
    claim = w.call('todo', 'claim', '--todo-id', todo, '--claimed-by', 'agent-a', '--agent-id', 'agent-a')
    assert claim['ok'] is True, claim
    path = w.root / 'goals' / 'observable' / 'task-leases' / f'{todo}.json'
    lease = json.loads(path.read_bytes())
    assert lease['owner'] == 'agent-a' and lease['status'] == 'active'
    before = w.primary()
    rejected = w.call('todo', 'complete', '--todo-id', todo, '--agent-id', 'agent-a',
        '--evidence', 'validation://terminal', '--no-follow-up')
    assert rejected['error_code'] == 'lease_fence_required', rejected
    after = w.primary()
    assert {key: after[key] for key in before} == before
    # The existing verify protocol retains an acquired intent, never a lease or success receipt.
    added = set(after) - set(before)
    assert len(added) == 1
    intent = json.loads(bytes.fromhex(after[added.pop()]))
    assert intent['schema_version'] == 'task_lease_fence_receipt_v0' and intent['state'] == 'acquired'
    assert intent['lease'] is None and intent['response'] is None and intent['verify_response'] is None
    result = w.call('todo', 'complete', '--todo-id', todo, '--agent-id', 'agent-a',
        '--task-lease-idempotency-key', 'caller-holder', '--task-lease-expected-version', str(lease['version']),
        '--evidence', 'validation://terminal', '--no-follow-up')
    assert result['ok'] is True and result['task_lease_fence']['released'] is True, result
    assert json.loads(path.read_bytes())['status'] == 'released'
    assert w.read(todo)['done'] is True
