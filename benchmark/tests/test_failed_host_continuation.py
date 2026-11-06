"""Explicit recovery preserves a failed Host and its native session/finite phase."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time

import pytest

from benchmark.runtime.scheduler import resume_state_file
from loopx.control_plane.turn_driver.host_failure import (
    build_host_failure_record, explicit_host_failure_retry_available, host_failure_retry_available,
)
from loopx.hosts import codex_checkpoint as native


@pytest.mark.parametrize('kind,attempt,explicit,allowed', [
    ('quota_exhausted', 1, False, False), ('quota_exhausted', 1, True, True),
    ('quota_exhausted', 7, True, True), ('output_budget_exhausted', 1, True, False),
    ('provider_capacity', 2, True, True), ('provider_capacity', 3, True, False),
])
def test_explicit_retry_uses_shared_turn_rule_without_automatic_quota_retry(kind, attempt, explicit, allowed):
    failure = build_host_failure_record(kind, attempt=attempt)
    original = copy.deepcopy(failure)
    assert explicit_host_failure_retry_available(failure, retry_failed=explicit) is allowed
    if kind == 'quota_exhausted':
        assert host_failure_retry_available(failure) is False
    assert failure == original


@pytest.mark.parametrize('fault', [None, 'in_progress', 'pending_effect', 'wrong_session',
    'wrong_workspace', 'wrong_error', 'prose_only', 'changed_history', 'outside_window', 'empty'])
def test_failed_native_readback_preserves_strict_checkpoint_and_unknown_error_gate(tmp_path, monkeypatch, fault):
    turns = [dict(id='first', status='completed', items=[]), dict(id='last', status='failed',
        startedAt=110, completedAt=120, error={'codexErrorInfo': 'usageLimitExceeded'},
        items=[{'type': 'commandExecution', 'status': 'completed'}])]
    if fault == 'in_progress': turns[-1]['status'] = 'inProgress'
    elif fault == 'pending_effect': turns[-1]['items'][0]['status'] = 'inProgress'
    elif fault == 'wrong_error': turns[-1]['error']['codexErrorInfo'] = 'unknownFutureCode'
    elif fault == 'prose_only': turns[-1]['error'] = {'message': 'usageLimitExceeded'}
    elif fault == 'outside_window': turns[-1]['startedAt'] = 90
    elif fault == 'empty': turns = []
    calls = []
    class Transport:
        def request(self, method, params):
            calls.append(method)
            if method == 'thread/read':
                return {'thread': {'id': 'different' if fault == 'wrong_session' else 'original',
                    'cwd': '/different' if fault == 'wrong_workspace' else str(tmp_path)}}
            assert method == 'thread/turns/list'
            rows = copy.deepcopy(turns)
            if fault == 'changed_history' and calls.count(method) > 1:
                rows[0]['items'] = [{'type': 'agentMessage', 'text': 'new'}]
            return {'data': rows, 'nextCursor': None}
    @contextmanager
    def reader(*a, **kw): yield Transport()
    monkeypatch.setattr(native, 'native_checkpoint_reader', reader)
    def read():
        return native.native_failed_turn({'LOOPX_PROJECT': str(tmp_path)}, 'original',
            failure_kind='quota_exhausted', process_started_at=100, process_stopped_at=125)
    if fault:
        with pytest.raises(ValueError): read()
    else:
        result = read()
        assert result['status'] == 'failed' and result['turn_count'] == 2
        assert result['failure_kind'] == 'quota_exhausted' and result['session_id'] == 'original'
        with pytest.raises(ValueError, match='completed final cursor'):
            native._complete_history(Transport().request, 'original')
    assert not set(calls) - {'thread/read', 'thread/turns/list'}


@pytest.mark.skipif(os.name != 'posix', reason='Original process group readback requires POSIX')
@pytest.mark.parametrize('fault', [None, 'stop', 'profile', 'session', 'source_digest', 'exhausted', 'unknown_allowance', 'stop_after_prepare'])
def test_real_sqlite_failed_host_can_explicitly_resume_original_session(tmp_path, monkeypatch, fault):
    from benchmark.runtime import worker
    from benchmark.tests.test_worker_phase_budget import _phase_worker
    from tests import test_external_scheduler_worker as scheduler_fixture

    env, project, runtime, _source, goal_state, _clock = _phase_worker(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'packages/loopx-codex-provider-routing/src'))
    monkeypatch.setattr(worker, 'time', time)
    Path(env['CODEX_BIN']).write_text(f'#!{sys.executable}\n' + '''
import json,os,pathlib,sys,time
history=pathlib.Path('native-history.json')
if 'app-server' in sys.argv:
    for line in sys.stdin:
        message=json.loads(line)
        if 'id' not in message: continue
        method=message['method']
        if method=='initialize': result={}
        elif method=='account/read': result={'account':{'type':'chatgpt'}}
        elif method=='account/rateLimits/read':
            result=json.loads(pathlib.Path('observed-allowance.json').read_text()) if pathlib.Path('observed-allowance.json').exists() else {'rateLimits':{'primary':{'usedPercent':40}}}
        elif method=='thread/read': result={'thread':{'id':'original-native','cwd':str(pathlib.Path.cwd())}}
        elif method=='thread/turns/list': result={'data':[json.loads(history.read_text())],'nextCursor':None}
        else: raise AssertionError(method)
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
    sys.exit(0)
with pathlib.Path('native-argv').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
print(json.dumps({'type':'thread.started','thread_id':'original-native'}),flush=True)
payload=sys.stdin.read()
with pathlib.Path('native-inputs').open('a') as f: f.write(json.dumps(payload)+'\\n')
if pathlib.Path('allowance-repaired').exists():
    pathlib.Path('continued-work').write_text('next candidate on retained session')
    print(json.dumps({'type':'turn.completed'}),flush=True)
    sys.exit(0)
now=int(time.time())
history.write_text(json.dumps({'id':'quota-failed-work','status':'failed',
    'error':{'codexErrorInfo':'usageLimitExceeded'},'startedAt':now,'completedAt':now,
    'items':[{'type':'commandExecution','status':'completed'}]}))
print(json.dumps({'type':'turn.failed','error':{'codexErrorInfo':'usageLimitExceeded'}}),flush=True)
sys.exit(1)
''')
    for key, value in env.items(): monkeypatch.setenv(key, value)
    deadline = time.time() + 120
    monkeypatch.setenv('LOOPX_PHASE_DEADLINE_EPOCH', str(deadline))
    args, _ = scheduler_fixture._guarded_host(tmp_path / 'scheduler')
    args.once = True
    args.registry, args.runtime_root = env['LOOPX_REGISTRY'], env['LOOPX_RUNTIME_ROOT']
    args.goal_id, args.agent_id = env['LOOPX_GOAL_ID'], env['LOOPX_AGENT_ID']
    args.wake_cmd = shlex.join([sys.executable, '-m', 'benchmark.runtime.worker'])
    args.wake_stop_exit_code, args.wake_timeout_seconds = 75, 90
    monkeypatch.setattr(scheduler_fixture.worker, 'run_quota_should_run',
        lambda *a, **kw: scheduler_fixture._hint_payload(should_run=True))
    assert scheduler_fixture.worker.run_worker(args) == 1
    original = Path(args.state_file)
    original_bytes = original.read_bytes()
    old = json.loads(original_bytes)
    assert old['host_attempt']['host_failure']['kind'] == 'quota_exhausted'
    assert old['host_attempt']['status'] == 'failed'
    assert scheduler_fixture.worker.run_worker(args) == 2  # Still no automatic retry.
    assert len((project / 'native-argv').read_text().splitlines()) == 1
    receipts = list(Path(env['LOOPX_WAKE_LOG_DIR']).glob('*/receipt.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_bytes())
    assert receipt['native_process']['after_cleanup']['return_code'] == 1
    digest = hashlib.sha256(original_bytes).hexdigest()
    if fault == 'stop':
        from loopx.control_plane.goals.activation_service import set_goal_activation_state
        assert set_goal_activation_state(registry_path=Path(args.registry), goal_id=args.goal_id,
            state='stopped', reason='Owner stopped work', actor_kind='owner', execute=True)['ok']
    elif fault == 'profile': monkeypatch.setenv('MODEL_NAME', 'other-model')
    elif fault == 'session':
        receipt['session']['session_id'] = 'different-native'
        receipts[0].write_text(json.dumps(receipt))
    elif fault == 'source_digest': digest = 'a' * 64
    elif fault in ('exhausted', 'unknown_allowance'):
        (project / 'observed-allowance.json').write_text(json.dumps({'rateLimits': {}} if fault == 'unknown_allowance' else
            {'rateLimits': {'primary': {'usedPercent': 100, 'resetsAt': int(time.time()) + 1000}, 'credits': {'hasCredits': False}}}))
    def resume(**kwargs):
        return resume_state_file(str(original), phase_deadline=deadline, agent_id=args.agent_id,
            wake_log_dir=env['LOOPX_WAKE_LOG_DIR'], env=dict(os.environ),
            retry_failed_state_sha256=digest, **kwargs)
    if fault and fault != 'stop_after_prepare':
        with pytest.raises((ValueError, RuntimeError)): resume()
        assert original.read_bytes() == original_bytes
        assert not list(original.parent.glob('*.phase-*.json'))
        return
    goal_before = goal_state.read_bytes()
    phase = Path(resume())
    assert phase != original
    phase_initial = json.loads(phase.read_bytes())
    assert phase_initial['recovered_session_id'] == 'original-native'
    assert phase_initial['recovered_attempt_count'] == 1
    assert phase_initial['failed_host_recovery']['native']['status'] == 'failed'
    assert phase_initial['failed_host_recovery']['phase_deadline_epoch'] == deadline
    assert resume() == str(phase)
    if fault == 'stop_after_prepare':
        from loopx.control_plane.goals.activation_service import set_goal_activation_state
        assert set_goal_activation_state(registry_path=Path(args.registry), goal_id=args.goal_id,
            state='stopped', reason='Owner stopped before dispatch', actor_kind='owner', execute=True)['ok']
        args.state_file = str(phase)
        scheduler_fixture.worker.run_worker(args)
        assert len((project / 'native-argv').read_text().splitlines()) == 1
        assert original.read_bytes() == original_bytes
        return
    # Repeating the unrepaired failure stays held in this same phase and cannot
    # consume the original explicit recovery a second time.
    args.state_file = str(phase)
    assert scheduler_fixture.worker.run_worker(args) == 1
    failed_again = phase.read_bytes()
    assert json.loads(failed_again)['host_attempt']['attempt'] == 2
    assert resume() == str(phase) and phase.read_bytes() == failed_again
    assert scheduler_fixture.worker.run_worker(args) == 2
    # A later explicit repair names the new failure, rather than erasing it.
    (project / 'allowance-repaired').touch()
    next_deadline = deadline + 30
    next_phase = resume_state_file(str(original), phase_deadline=next_deadline, agent_id=args.agent_id,
        wake_log_dir=env['LOOPX_WAKE_LOG_DIR'], env=dict(os.environ),
        retry_failed_state_sha256=hashlib.sha256(failed_again).hexdigest())
    monkeypatch.setenv('LOOPX_PHASE_DEADLINE_EPOCH', str(next_deadline))
    args.state_file = next_phase
    assert scheduler_fixture.worker.run_worker(args) == 0
    argv = [json.loads(line) for line in (project / 'native-argv').read_text().splitlines()]
    assert len(argv) == 3 and all('resume' in a and 'original-native' in a for a in argv[1:])
    assert (project / 'continued-work').is_file()
    assert original.read_bytes() == original_bytes and phase.read_bytes() == failed_again
    assert goal_state.read_bytes() == goal_before


@pytest.mark.parametrize('fault', [None, 'unknown', 'missing_session', 'other_receipt', 'late_wake', 'live', 'wrong_container'])
def test_legacy_quota_failure_proof_retains_complete_same_container_source(tmp_path, monkeypatch, fault):
    from benchmark.tests.test_stopped_recovery import _source, _fake_container
    from benchmark.edgebench.stopped_recovery import prepare_stopped_recovery
    from benchmark.runtime.interruption_recovery import _stopped_source
    declaration = _source(tmp_path)
    box, client = _fake_container(monkeypatch)
    state_path = Path(declaration['scheduler_state'])
    failure = build_host_failure_record('unknown' if fault == 'unknown' else 'quota_exhausted', attempt=2)
    state = {'host_attempt': dict(status='failed', scope='a' * 64, attempt=2,
        host_failure=failure, session_id=None if fault == 'missing_session' else 'original', not_before=111),
        'last_wake_status': 'wake_failed', 'last_wake_rc': 1}
    state_path.write_text(json.dumps(state))
    os.utime(state_path, (111, 111))
    receipt_path = Path(declaration['wake_log_dir']) / declaration['wake_turn_id'] / 'receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt.pop('budget_exhausted')
    receipt.update(return_code=1, host_failure=build_host_failure_record(failure['kind'], attempt=1))
    if fault == 'other_receipt': receipt['session']['session_id'] = 'different'
    receipt_path.write_text(json.dumps(receipt));os.utime(receipt_path, (110, 110))
    if fault == 'late_wake':
        late = receipt_path.parent.with_name('later') / 'receipt.json'
        late.parent.mkdir();late.write_text('{}');os.utime(late, (112, 112))
    if fault == 'live': box.attrs['State'].update(Status='running', Running=True, Pid=44)
    before = state_path.read_bytes()
    output = tmp_path / 'quota-failure-proof.json'
    if fault not in (None, 'wrong_container'):
        with pytest.raises(ValueError):
            prepare_stopped_recovery(client, container_id=box.id, declaration=declaration, output=output)
        assert not output.exists()
        return
    proof = prepare_stopped_recovery(client, container_id=box.id, declaration=declaration, output=output)
    assert proof['execution_authority_granted'] is False
    env = {'LOOPX_INTERRUPTED_SOURCE': str(output), 'LOOPX_GOAL_ID': declaration['goal_id'],
        'LOOPX_AGENT_ID': declaration['agent_id'], 'LOOPX_PROJECT': declaration['workspace'],
        'LOOPX_RUNTIME_ROOT': declaration['runtime_root'], 'CODEX_HOME': declaration['native_home'],
        'LOOPX_REGISTRY': declaration['registry']}
    if fault == 'wrong_container':
        from types import SimpleNamespace
        monkeypatch.setattr(os, 'uname', lambda: SimpleNamespace(nodename='b' * 12))
        with pytest.raises(ValueError, match='original container'):
            _stopped_source(state_path, state, env, declaration['wake_log_dir'])
    else:
        assert _stopped_source(state_path, state, env, declaration['wake_log_dir'])[2]['host_failure']['kind'] == 'quota_exhausted'
    assert state_path.read_bytes() == before


@pytest.mark.parametrize('available', [False, True])
def test_strict_allowance_uses_isolated_account_only_owner(tmp_path, monkeypatch, available):
    from benchmark.tests.test_codex_readiness import native_fixture
    from benchmark.runtime.codex_readiness import require_native_receiver_allowance
    env, trace, home, pending = native_fixture(tmp_path, 'success')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'packages/loopx-codex-provider-routing/src'))
    before = {p: p.read_bytes() for p in (home / 'auth.json', pending)}
    if not available:
        # Deliberately hide the optional provider, without a substitute policy.
        monkeypatch.setitem(sys.modules, 'loopx_codex_provider_routing.native', None)
        with pytest.raises(ValueError, match='allowance_provider_unavailable'):
            require_native_receiver_allowance(env)
    else:
        observed = require_native_receiver_allowance(env)
        assert observed['availability'] == 'available'
        assert set(observed) == {'availability', 'observed_at'}
    assert {p: p.read_bytes() for p in before} == before
    methods = [json.loads(line)['method'] for line in trace.read_text().splitlines()]
    assert methods == ['initialize', 'initialized', 'account/read', 'account/rateLimits/read']
    assert not (home / 'skills').exists()
