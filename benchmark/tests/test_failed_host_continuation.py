"""Explicit recovery preserves a failed Host and its native session/finite phase."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from benchmark.runtime.scheduler import resume_state_file
from loopx.control_plane.turn_driver.host_failure import (
    build_host_failure_record, explicit_host_failure_retry_available, host_failure_retry_available,
)
from loopx.hosts import codex_checkpoint as native


def _run_scheduler_probe(tmp_path, state, *, deadline, digest):
    marker = tmp_path / "worker-invoked"
    worker = tmp_path / "scripts" / "external_scheduler_worker.py"
    worker.parent.mkdir()
    worker.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("LOOPX_")}
    env.update(LOOPX_PHASE_DEADLINE_EPOCH=str(deadline), LOOPX_AGENT_ID="fixture-agent",
        LOOPX_GOAL_ID="fixture-goal", LOOPX_EXECUTION_MODE="heartbeat", LOOPX_ITERATION_CONTEXT="resume",
        LOOPX_CLI=str(tmp_path / "fixture-cli"), LOOPX_REGISTRY=str(tmp_path / "registry.json"),
        LOOPX_RUNTIME_ROOT=str(tmp_path / "runtime"), LOOPX_WAKE_LOG_DIR=str(tmp_path / "wakes"))
    command = [sys.executable, "-m", "benchmark.runtime.scheduler",
        "--python", sys.executable, "--source", str(tmp_path), "--state-file", str(state), "--host-timeout", "10"]
    if digest is not None:
        command.extend(["--retry-failed-state-sha256", digest])
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    return result, marker.exists()


@pytest.mark.parametrize("source", [
    pytest.param(None, id="missing"),
    pytest.param(b"\xff", id="unreadable"),
    pytest.param(b"{}", id="no-attempt"),
    pytest.param(b'{"host_attempt":{"scope":"unknown"}}', id="invalid-scope"),
    pytest.param(json.dumps({"host_attempt": {"scope": "a" * 64, "attempt": False}}).encode(),
                 id="invalid-attempt"),
])
def test_explicit_scheduler_never_launches_without_qualified_source(tmp_path, source):
    state = tmp_path / "scheduler.json"
    if source is not None:
        state.write_bytes(source)
    digest = hashlib.sha256(source).hexdigest() if source is not None else "a" * 64
    result, invoked = _run_scheduler_probe(tmp_path, state, deadline=time.time() + 60, digest=digest)
    assert result.returncode != 0 and "Explicit failed continuation" in result.stderr
    assert not invoked and not list(tmp_path.glob("scheduler.phase-*.json"))
    assert (state.read_bytes() if state.exists() else None) == source


def test_ordinary_scheduler_can_launch_without_explicit_source(tmp_path):
    state = tmp_path / "scheduler.json"
    result, invoked = _run_scheduler_probe(tmp_path, state, deadline=time.time() + 60, digest=None)
    assert result.returncode == 0 and invoked and not state.exists()


@pytest.mark.parametrize("locked_file", ["scheduler.json", "scheduler.phase-other.json"])
def test_explicit_scheduler_does_not_launch_when_phase_selection_is_locked(tmp_path, locked_file):
    from loopx.file_lock import try_exclusive_file_lock
    from scripts.external_scheduler_worker import HOST_LOCK_OPERATION

    held = tmp_path / locked_file
    held.write_bytes(b"{}")
    with try_exclusive_file_lock(held, agent_id="fixture-agent", operation=HOST_LOCK_OPERATION) as lock:
        assert lock is not None
        result, invoked = _run_scheduler_probe(tmp_path, tmp_path / "scheduler.json",
            deadline=time.time() + 60, digest=hashlib.sha256(b"{}").hexdigest())
    assert result.returncode != 0 and "Explicit failed continuation" in result.stderr
    assert not invoked and held.read_bytes() == b"{}"


@pytest.mark.parametrize("original_returned", [False, True])
def test_explicit_scheduler_requires_complete_same_phase_evidence(tmp_path, original_returned):
    state = tmp_path / "scheduler.json"
    session = "original-native"
    failure = build_host_failure_record("quota_exhausted", attempt=1)
    source = {
        "last_wake_status": "wake_failed", "last_wake_rc": 1,
        "host_attempt": {"scope": "a" * 64, "status": "failed", "attempt": 1,
                         "session_id": session, "host_failure": failure, "not_before": time.time()},
    } if original_returned else {}
    original = json.dumps(source).encode()
    state.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    deadline = time.time() + 60
    suffix = hashlib.sha256(deadline.hex().encode()).hexdigest()[:20]
    phase = tmp_path / f"scheduler.phase-{suffix}.json"
    phase.write_text(json.dumps({
        "resume_from": str(state), "recovered_session_id": session, "recovered_attempt_count": 1,
        "failed_host_recovery": {
            "phase_deadline_epoch": deadline, "source_state_sha256": digest, "host_failure": failure,
            "retry_failed": True, "native": {"session_id": session, "status": "failed"},
        },
    }))
    before = phase.read_bytes()
    result, invoked = _run_scheduler_probe(tmp_path, state, deadline=deadline, digest=digest)
    assert result.returncode != 0 and "Explicit failed continuation" in result.stderr
    assert not invoked and phase.read_bytes() == before and state.read_bytes() == original


@pytest.mark.parametrize('kind,attempt,explicit,allowed', [
    ('quota_exhausted', 1, False, False), ('quota_exhausted', 1, True, True),
    ('quota_exhausted', 7, True, True), ('output_budget_exhausted', 1, True, False),
    ('unknown', 2, False, False), ('unknown', 2, True, True),
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
    'wrong_workspace', 'wrong_error', 'prose_only', 'changed_history', 'outside_window', 'empty', 'pending_prefix'])
def test_failed_native_readback_preserves_strict_checkpoint_and_unknown_error_gate(tmp_path, monkeypatch, fault):
    turns = [dict(id='first', status='completed', items=[]), dict(id='last', status='failed',
        startedAt=110, completedAt=120, error={'codexErrorInfo': 'usageLimitExceeded'},
        items=[{'type': 'commandExecution', 'status': 'completed'}])]
    if fault == 'in_progress':
        turns[-1]['status'] = 'inProgress'
    elif fault == 'pending_effect':
        turns[-1]['items'][0]['status'] = 'inProgress'
    elif fault == 'pending_prefix':
        turns[0]['items'] = [{'type': 'commandExecution', 'status': 'pending'}]
    elif fault == 'wrong_error':
        turns[-1]['error']['codexErrorInfo'] = 'unknownFutureCode'
    elif fault == 'prose_only':
        turns[-1]['error'] = {'message': 'usageLimitExceeded'}
    elif fault == 'outside_window':
        turns[-1]['startedAt'] = 90
    elif fault == 'empty':
        turns = []
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
    def reader(*a, **kw):
        yield Transport()
    monkeypatch.setattr(native, 'native_checkpoint_reader', reader)
    def read():
        return native.native_failed_turn({'LOOPX_PROJECT': str(tmp_path)}, 'original',
            failure_kind='quota_exhausted', process_started_at=100, process_stopped_at=125)
    if fault not in {None, 'pending_prefix'}:
        with pytest.raises(ValueError):
            read()
    else:
        result = read()
        assert result['status'] == 'failed' and result['turn_count'] == 2
        assert result['failure_kind'] == 'quota_exhausted' and result['session_id'] == 'original'
        with pytest.raises(ValueError, match='completed final cursor'):
            native._complete_history(Transport().request, 'original')
    assert not set(calls) - {'thread/read', 'thread/turns/list'}


@pytest.mark.parametrize('fault', [None, 'no_witness', 'missing_error', 'wrong_error', 'new_error_field',
    'structured_conflict', 'running', 'pending_prefix', 'outside_window', 'changed_history', 'changed_source',
    'missing_rollout', 'later_foreign_tail', 'same_second_foreign_tail', 'late_rollout', 'coarse_rollout',
    'ambiguous_rollout'])
def test_returned_unknown_failure_binds_error_and_isolated_stable_native_store(tmp_path, monkeypatch, fault):
    home = tmp_path / 'native'
    home.mkdir()
    (home / 'config.toml').write_text('')
    database = home / 'state_5.sqlite'
    database.write_bytes(b'original retained native store')
    original = database.read_bytes()
    # Diagnostic-looking words never reclassify this retained typed unknown.
    cli_error = {'message': 'Synthetic failure mentioning quota and idle'}
    native_error = {**cli_error, 'codexErrorInfo': 'other', 'additionalDetails': None, 'misalignment': None}
    turns = [dict(id='prefix', status='completed', items=[]),
             dict(id='original-failed-turn', status='failed', startedAt=110, completedAt=120,
                  error=copy.deepcopy(native_error), items=[])]
    rollout = home / 'sessions' / 'rollout-fixture-original.jsonl'
    rollout.parent.mkdir()
    markers = [
        (110.5, {'type': 'task_started', 'turn_id': 'original-failed-turn'}),
        ({'late_rollout': 120.5, 'coarse_rollout': 120.0}.get(fault, 120.125),
         {'type': 'task_complete', 'turn_id': 'original-failed-turn', 'error': cli_error}),
    ]
    if fault == 'ambiguous_rollout':
        markers.insert(1, (112.0, {'type': 'task_started', 'turn_id': 'foreign-turn'}))
    records = [{'type': 'session_meta', 'payload': {'id': 'original', 'cwd': str(tmp_path)}}]
    records.extend({'type': 'event_msg', 'timestamp': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                    'payload': payload} for stamp, payload in markers)
    if fault != 'missing_rollout':
        rollout.write_text(''.join(json.dumps(record) + '\n' for record in records))
    if fault == 'missing_error':
        turns[-1].pop('error')
    elif fault == 'wrong_error':
        turns[-1]['error']['message'] = 'A different failure'
    elif fault == 'new_error_field':
        turns[-1]['error']['futureField'] = 'additional cause'
    elif fault == 'structured_conflict':
        turns[-1]['error']['codexErrorInfo'] = 'usageLimitExceeded'
    elif fault == 'running':
        turns[-1]['status'] = 'inProgress'
    elif fault == 'pending_prefix':
        turns[0]['items'] = [{'type': 'commandExecution', 'status': 'pending'}]
    elif fault == 'outside_window':
        turns[-1]['startedAt'] = 99
    elif fault in ('later_foreign_tail', 'same_second_foreign_tail'):
        stamp = 121 if fault == 'later_foreign_tail' else 120
        turns.append(dict(turns[-1], id='foreign-turn', startedAt=stamp, completedAt=stamp))
    calls = []
    class Transport:
        def request(self, method, params):
            calls.append(method)
            if method == 'thread/read':
                return {'thread': {'id': 'original', 'cwd': str(tmp_path), 'path': str(rollout)}}
            assert method == 'thread/turns/list'
            rows = copy.deepcopy(turns)
            if fault == 'changed_history' and calls.count(method) > 1:
                rows[0]['items'] = [{'type': 'agentMessage', 'text': 'later work'}]
            return {'data': rows, 'nextCursor': None}
    @contextmanager
    def reader(env, **kwargs):
        observer = Path(env['CODEX_HOME'])
        assert observer != home and (observer / database.name).read_bytes() == original
        # Native app-server may write its own index, never the retained source.
        (observer / database.name).write_bytes(b'observer index update')
        if fault == 'changed_source':
            database.write_bytes(b'concurrent original writer')
        yield Transport()
    monkeypatch.setattr(native, 'native_checkpoint_reader', reader)
    returned_digest = hashlib.sha256(json.dumps(cli_error, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    kwargs = {} if fault == 'no_witness' else {'returned_error_sha256': returned_digest}
    def read():
        return native.native_failed_turn({'LOOPX_PROJECT': str(tmp_path), 'CODEX_HOME': str(home)},
            'original', failure_kind='unknown', process_started_at=100.25, process_stopped_at=120.25, **kwargs)
    if fault:
        with pytest.raises(ValueError):
            read()
    else:
        result = read()
        assert result['failure_kind'] == 'unknown' and result['status'] == 'failed'
        assert result['turn_id'] == 'original-failed-turn' and result['turn_count'] == 2
        assert result['returned_error_sha256'] == returned_digest
        assert result['error_sha256'] == hashlib.sha256(json.dumps(native_error, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        assert result['rollout_cursor'] == {'turn_id': 'original-failed-turn', 'started_at': 110.5,
            'completed_at': 120.125, 'record_sha256': hashlib.sha256(
                b''.join(rollout.read_bytes().splitlines(keepends=True)[1:])).hexdigest()}
        with pytest.raises(ValueError, match='completed final cursor'):
            native._complete_history(Transport().request, 'original')
    if fault != 'changed_source':
        assert database.read_bytes() == original
    assert not set(calls) - {'thread/read', 'thread/turns/list'}


@pytest.mark.skipif(os.name != 'posix', reason='Original process group readback requires POSIX')
@pytest.mark.parametrize('kind,prior_attempt,fault', [
    ('quota_exhausted', 0, fault) for fault in
    (None, 'stop', 'profile', 'session', 'source_digest', 'exhausted', 'unknown_allowance', 'stop_after_prepare')
] + [
    ('unknown', 1, fault) for fault in
    (None, 'stop', 'profile', 'session', 'source_digest', 'exhausted', 'unknown_allowance',
     'stop_after_prepare', 'no_return', 'after_return', 'missing_error', 'native_error', 'pending_native',
     'stdout_session', 'stdout_truncated')
])
def test_real_sqlite_failed_host_can_explicitly_resume_original_session(tmp_path, monkeypatch, kind, prior_attempt, fault):
    from benchmark.runtime import worker
    from benchmark.tests.test_worker_phase_budget import _phase_worker
    from tests import test_external_scheduler_worker as scheduler_fixture

    env, project, runtime, _source, goal_state, _clock = _phase_worker(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'packages/loopx-codex-provider-routing/src'))
    monkeypatch.setattr(worker, 'time', time)
    failure_error = ({'codexErrorInfo': 'usageLimitExceeded'} if kind == 'quota_exhausted'
                     else {'message': 'Synthetic native task failed'})
    (project / 'failure-error.json').write_text(json.dumps(failure_error))
    native_home = Path(env['LOOPX_CODEX_HOME'])
    native_home.mkdir(parents=True, exist_ok=True)
    rollout = native_home / 'sessions' / 'rollout-fixture-original-native.jsonl'
    (native_home / 'state_5.sqlite').write_bytes(b'retained native fixture')
    retained_store = (native_home / 'state_5.sqlite').read_bytes()
    Path(env['CODEX_BIN']).write_text(f'#!{sys.executable}\n' + '''
import json,os,pathlib,sys,time
from datetime import datetime, timezone
history=pathlib.Path('native-history.json')
rollout=pathlib.Path(os.environ['LOOPX_CODEX_HOME'])/'sessions'/'rollout-fixture-original-native.jsonl'
if 'app-server' in sys.argv:
    for line in sys.stdin:
        message=json.loads(line)
        if 'id' not in message: continue
        method=message['method']
        if method=='initialize': result={}
        elif method=='account/read': result={'account':{'type':'chatgpt'}}
        elif method=='account/rateLimits/read':
            result=json.loads(pathlib.Path('observed-allowance.json').read_text()) if pathlib.Path('observed-allowance.json').exists() else {'rateLimits':{'primary':{'usedPercent':40}}}
        elif method=='thread/read': result={'thread':{'id':'original-native','cwd':str(pathlib.Path.cwd()),'path':str(rollout)}}
        elif method=='thread/turns/list': result={'data':[json.loads(history.read_text())],'nextCursor':None}
        else: raise AssertionError(method)
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
    sys.exit(0)
with pathlib.Path('native-argv').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
started=time.time()
print(json.dumps({'type':'thread.started','thread_id':'original-native'}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)
payload=sys.stdin.read()
with pathlib.Path('native-inputs').open('a') as f: f.write(json.dumps(payload)+'\\n')
if pathlib.Path('allowance-repaired').exists():
    pathlib.Path('continued-work').write_text('next candidate on retained session')
    print(json.dumps({'type':'turn.completed'}),flush=True)
    sys.exit(0)
completed=time.time()
error=json.loads(pathlib.Path('failure-error.json').read_text())
native_error={**error,'additionalDetails':None}
if 'codexErrorInfo' not in native_error:
    native_error.update(codexErrorInfo='other',misalignment=None)
history.write_text(json.dumps({'id':'failed-work','status':'failed',
    'error':native_error,'startedAt':int(started),'completedAt':int(completed),
    'items':[{'type':'commandExecution','status':'completed'}]}))
rollout.parent.mkdir(parents=True, exist_ok=True)
meta={'type':'session_meta','payload':{'id':'original-native','cwd':str(pathlib.Path.cwd())}}
def marker(stamp, kind, **payload):
    return {'type':'event_msg','timestamp':datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            'payload':{'type':kind,'turn_id':'failed-work',**payload}}
rollout.write_text(''.join(json.dumps(record)+'\\n' for record in (meta,
    marker(started,'task_started'),marker(completed,'task_complete',error=error))))
print(json.dumps({'type':'turn.failed','error':error}),flush=True)
sys.exit(1)
''')
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    deadline = time.time() + 120
    monkeypatch.setenv('LOOPX_PHASE_DEADLINE_EPOCH', str(deadline))
    args, _ = scheduler_fixture._guarded_host(tmp_path / 'scheduler')
    args.once = True
    args.registry, args.runtime_root = env['LOOPX_REGISTRY'], env['LOOPX_RUNTIME_ROOT']
    args.goal_id, args.agent_id = env['LOOPX_GOAL_ID'], env['LOOPX_AGENT_ID']
    args.wake_cmd = shlex.join([sys.executable, '-m', 'benchmark.runtime.worker'])
    args.wake_stop_exit_code, args.wake_timeout_seconds = 75, 90
    if prior_attempt:
        Path(args.state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.state_file).write_text(json.dumps({'recovered_attempt_count': prior_attempt}))
    monkeypatch.setattr(scheduler_fixture.worker, 'run_quota_should_run',
        lambda *a, **kw: scheduler_fixture._hint_payload(should_run=True))
    assert scheduler_fixture.worker.run_worker(args) == 1
    original = Path(args.state_file)
    original_bytes = original.read_bytes()
    old = json.loads(original_bytes)
    assert old['host_attempt']['host_failure']['kind'] == kind
    assert old['host_attempt']['attempt'] == prior_attempt + 1
    assert old['host_attempt']['status'] == 'failed'
    assert scheduler_fixture.worker.run_worker(args) == 2  # Still no automatic retry.
    assert len((project / 'native-argv').read_text().splitlines()) == 1
    receipts = list(Path(env['LOOPX_WAKE_LOG_DIR']).glob('*/receipt.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_bytes())
    assert receipt['native_process']['after_cleanup']['return_code'] == 1
    assert receipt['host_failure']['attempt'] == 1
    digest = hashlib.sha256(original_bytes).hexdigest()
    if fault == 'stop':
        from loopx.control_plane.goals.activation_service import set_goal_activation_state
        assert set_goal_activation_state(registry_path=Path(args.registry), goal_id=args.goal_id,
            state='stopped', reason='Owner stopped work', actor_kind='owner', execute=True)['ok']
    elif fault == 'profile':
        monkeypatch.setenv('MODEL_NAME', 'other-model')
    elif fault == 'session':
        receipt['session']['session_id'] = 'different-native'
        receipts[0].write_text(json.dumps(receipt))
    elif fault == 'source_digest':
        digest = 'a' * 64
    elif fault in ('exhausted', 'unknown_allowance'):
        (project / 'observed-allowance.json').write_text(json.dumps({'rateLimits': {}} if fault == 'unknown_allowance' else
            {'rateLimits': {'primary': {'usedPercent': 100, 'resetsAt': int(time.time()) + 1000}, 'credits': {'hasCredits': False}}}))
    elif fault == 'no_return':
        receipt['native_process']['before_cleanup']['return_code'] = None
        receipts[0].write_text(json.dumps(receipt))
    elif fault == 'after_return':
        task_start = json.loads(rollout.read_text().splitlines()[1])['timestamp']
        receipt['native_process']['before_cleanup']['observed_at_unix'] = datetime.fromisoformat(task_start).timestamp()
        receipts[0].write_text(json.dumps(receipt))
    elif fault in ('missing_error', 'native_error', 'pending_native'):
        history = json.loads((project / 'native-history.json').read_text())
        if fault == 'missing_error':
            history.pop('error')
        elif fault == 'native_error':
            history['error']['message'] = 'A different failed native task'
        else:
            history['items'][0]['status'] = 'inProgress'
        (project / 'native-history.json').write_text(json.dumps(history))
    elif fault in ('stdout_session', 'stdout_truncated'):
        output = receipts[0].parent / 'stdout.jsonl'
        raw_output = output.read_text()
        output.write_text(raw_output.replace('original-native', 'another-native')
                          if fault == 'stdout_session' else raw_output[:-5])
    def resume(**kwargs):
        return resume_state_file(str(original), phase_deadline=deadline, agent_id=args.agent_id,
            wake_log_dir=env['LOOPX_WAKE_LOG_DIR'], env=dict(os.environ),
            retry_failed_state_sha256=digest, **kwargs)
    if fault and fault != 'stop_after_prepare':
        with pytest.raises((ValueError, RuntimeError)):
            resume()
        assert original.read_bytes() == original_bytes
        assert not list(original.parent.glob('*.phase-*.json'))
        return
    goal_before = goal_state.read_bytes()
    phase = Path(resume())
    assert phase != original
    phase_initial = json.loads(phase.read_bytes())
    assert phase_initial['recovered_session_id'] == 'original-native'
    assert phase_initial['recovered_attempt_count'] == prior_attempt + 1
    assert phase_initial['failed_host_recovery']['native']['status'] == 'failed'
    assert phase_initial['failed_host_recovery']['phase_deadline_epoch'] == deadline
    assert phase_initial['failed_host_recovery']['host_failure']['kind'] == kind
    assert (native_home / 'state_5.sqlite').read_bytes() == retained_store
    if kind == 'unknown':
        proof = phase_initial['failed_host_recovery']['returned_failure']
        error_digest = hashlib.sha256(json.dumps(failure_error, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        expected_native_error = {**failure_error, 'codexErrorInfo': 'other', 'additionalDetails': None,
                                 'misalignment': None}
        native_error_digest = hashlib.sha256(json.dumps(expected_native_error, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        assert proof['error_sha256'] == error_digest
        assert proof['error_projection_sha256'] == error_digest
        assert proof['stdout_sha256'] == hashlib.sha256(
            (receipts[0].parent / 'stdout.jsonl').read_bytes()).hexdigest()
        assert phase_initial['failed_host_recovery']['native']['turn_id'] == 'failed-work'
        assert phase_initial['failed_host_recovery']['native']['error_sha256'] == native_error_digest
        assert phase_initial['failed_host_recovery']['native']['returned_error_sha256'] == error_digest
        cursor = phase_initial['failed_host_recovery']['native']['rollout_cursor']
        assert cursor['turn_id'] == 'failed-work'
        assert proof['process_started_at'] <= cursor['started_at'] <= cursor['completed_at'] <= proof['process_returned_at']
    assert resume() == str(phase)
    if fault is None:
        from benchmark.runtime import codex_readiness

        def unexpected_observer(*args, **kwargs):
            pytest.fail("Exact replay must reuse the original native and allowance observations")

        with monkeypatch.context() as replay:
            replay.setattr(native, "native_failed_turn", unexpected_observer)
            replay.setattr(codex_readiness, "require_native_receiver_allowance", unexpected_observer)
            assert resume() == str(phase)
        original.write_bytes(original_bytes + b"\n")
        with pytest.raises(ValueError, match="replay source changed"):
            resume()
        original.write_bytes(original_bytes)
        receipt_path = receipts[0]
        returned_bytes = receipt_path.read_bytes()
        receipt_path.write_bytes(returned_bytes + b"\n")
        with pytest.raises(ValueError, match="wake receipt changed"):
            resume()
        receipt_path.write_bytes(returned_bytes)
        if kind == 'unknown':
            output = receipt_path.parent / 'stdout.jsonl'
            returned_output = output.read_bytes()
            output.write_bytes(returned_output + b'\n')
            with pytest.raises(ValueError, match='returned failure'):
                resume()
            output.write_bytes(returned_output)
            incomplete = copy.deepcopy(phase_initial)
            del incomplete['failed_host_recovery']['native']['rollout_cursor']
            phase.write_text(json.dumps(incomplete))
            with pytest.raises(ValueError, match='returned failure'):
                resume()
        for field in ("allowance", "native", "receipt_sha256", "session_profile_digest",
                      *(("returned_failure",) if kind == 'unknown' else ())):
            incomplete = copy.deepcopy(phase_initial)
            del incomplete["failed_host_recovery"][field]
            phase.write_text(json.dumps(incomplete))
            with pytest.raises(ValueError, match="Explicit failed continuation"):
                resume()
        phase.write_text(json.dumps(phase_initial))
        assert json.loads(phase.read_bytes()) == phase_initial
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
    assert json.loads(failed_again)['host_attempt']['attempt'] == prior_attempt + 2
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
    if fault == 'other_receipt':
        receipt['session']['session_id'] = 'different'
    receipt_path.write_text(json.dumps(receipt))
    os.utime(receipt_path, (110, 110))
    if fault == 'late_wake':
        late = receipt_path.parent.with_name('later') / 'receipt.json'
        late.parent.mkdir()
        late.write_text('{}')
        os.utime(late, (112, 112))
    if fault == 'live':
        box.attrs['State'].update(Status='running', Running=True, Pid=44)
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
