"""Target selection must preserve an accepted Monitor poll's identity."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.scheduler.monitor_todo import monitor_todo_is_due
from loopx.control_plane.scheduler.monitor_poll_writeback import write_monitor_poll_todo_state
from loopx.control_plane.scheduler.provider_monitor_poll import poll_canonical_monitor_if_promoted
from loopx.control_plane.quota import monitor_poll as monitor_runtime
from loopx.control_plane.quota.accounting_admission import quota_accounting_admission
from loopx.control_plane.testing.canary_harness import read_run_index
from loopx.todos import add_goal_todo, list_goal_todos, update_goal_todo
from test_monitor_poll_transaction_recovery import AGENT, GOAL, MONITOR_TARGET, REPO, _cli, _fixture, _poll_events

_NULL_TODO_WRAPPER = """
import hashlib, os, runpy, sys
import loopx.control_plane.quota.monitor_poll as monitor
original = monitor._native_result
def historical_request(request):
    target = request['observation']['target_key']
    effect = 'quota-monitor-poll:{}:{}:{}:target:sha256:{}'.format(
        request['goal_id'], request['decision']['agent_id'], request['turn_instance_id'],
        hashlib.sha256(target.encode()).hexdigest())
    request = {**request, 'effect_id': effect,
        'observation': {**request['observation'], 'todo_id': None}}
    if request.get('phase') == 'commit' and request.get('provider_receipt'):
        os._exit(73)
    return original(request)
monitor._native_result = historical_request
sys.argv = ['loopx.cli', *sys.argv[1:]]
runpy.run_module('loopx.cli', run_name='__main__')
"""

_CONCURRENT_RETRY_WRAPPER = """
import json, os, runpy, sys, time
from pathlib import Path
import loopx.quota as quota
original = quota.resolve_monitor_poll_target
def synchronized_target(**kwargs):
    route = original(**kwargs)
    barrier = Path(os.environ['SYNTHETIC_RETRY_BARRIER'])
    marker = barrier / os.environ['SYNTHETIC_RETRY_ACTOR']
    marker.write_text(json.dumps({'route': route, 'pid': os.getpid()}))
    deadline = time.monotonic() + 15
    while len(list(barrier.glob('ready-*'))) != 2:
        if time.monotonic() >= deadline:
            raise RuntimeError('synthetic retry barrier timed out')
        time.sleep(0.01)
    return route
quota.resolve_monitor_poll_target = synchronized_target
sys.argv = ['loopx.cli', *sys.argv[1:]]
runpy.run_module('loopx.cli', run_name='__main__')
"""


def _target_poll(poll: tuple[str, ...], *, material: bool = True) -> tuple[str, ...]:
    args = list(poll)
    index = args.index('--todo-id')
    del args[index:index + 2]
    if not material:
        args.remove('--material-change')
        for flag in ('--next-agent-todo', '--next-action-kind', '--next-user-todo', '--next-user-task-class'):
            index = args.index(flag)
            del args[index:index + 2]
    return tuple(args)


def _poll_receipts(runtime: Path) -> dict[str, bytes]:
    directory = runtime / 'goals' / GOAL / 'runs' / '.transactions' / 'quota-monitor-poll'
    return {path.name: path.read_bytes() for path in directory.glob('*.json')}


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('completion', ['committed', 'interrupted'])
@pytest.mark.parametrize('material', [False, True])
def test_target_only_monitor_poll_replays_accepted_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, completion: str, material: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    other = add_goal_todo(registry_path=registry, goal_id=GOAL, role='agent',
        text='Observe a different public synthetic target', task_class='continuous_monitor', claimed_by=AGENT,
        monitor_metadata={'target_key': 'synthetic-other-target', 'cadence': '1h',
                          'next_due_at': '2000-01-01T00:00:00+00:00', 'watch_only': 'true'})
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    target_poll = _target_poll(poll, material=material)
    rc, first = _cli(registry, runtime, *target_poll,
                     fault='quota_commit' if completion == 'interrupted' else None)
    assert rc == (73 if completion == 'interrupted' else 0), first
    committed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    assert committed is not None
    observed = next(todo for todo in committed['todos'] if todo['todo_id'] == monitor)
    assert not monitor_todo_is_due(observed)
    assert len(_poll_events(runtime)) == (0 if completion == 'interrupted' else 1)
    # Changed content must not recover a pending provider effect or overwrite
    # the accepted operation, even before its quota ledger is complete.
    frozen_receipts = _poll_receipts(runtime)
    frozen_index = read_run_index(runtime, GOAL)
    changed = list(target_poll)
    changed[changed.index('--result-hash') + 1] = 'synthetic-head-2'
    changed_rc, changed_reply = _cli(registry, runtime, *changed)
    assert changed_rc != 0 and changed_reply['error_code'] == 'heartbeat_receipt_identity_conflict', changed_reply
    assert _poll_receipts(runtime) == frozen_receipts
    assert read_run_index(runtime, GOAL) == frozen_index
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed
    retry_rc, retry = _cli(registry, runtime, *target_poll)
    assert retry_rc == 0, {'code': retry.get('error_code'), 'reason': retry.get('reason')}
    assert len(_poll_events(runtime)) == 1
    assert _poll_events(runtime)[0]['todo_id'] == monitor
    assert _poll_events(runtime)[0]['agent_id'] == AGENT
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed
    index_after = read_run_index(runtime, GOAL)
    replay_rc, replay = _cli(registry, runtime, *target_poll)
    assert replay_rc == 0 and replay['replayed'] is True, replay
    assert read_run_index(runtime, GOAL) == index_after
    assert not any(row['classification'] == 'quota_slot_spent' for row in index_after)
    receipts_after = _poll_receipts(runtime)
    for field, value in (('--result-hash', 'synthetic-head-2'), ('--target-key', 'synthetic-other-target')):
        changed = list(target_poll)
        changed[changed.index(field) + 1] = value
        rc, rejected = _cli(registry, runtime, *changed)
        assert rc != 0, rejected
        if field == '--result-hash':
            assert rejected['error_code'] == 'heartbeat_receipt_identity_conflict', rejected
    rc, rejected = _cli(registry, runtime, *target_poll, '--todo-id', other['todo_id'])
    assert rc != 0, rejected
    assert _poll_receipts(runtime) == receipts_after
    assert read_run_index(runtime, GOAL) == index_after
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == committed


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('completion', ['committed', 'interrupted'])
def test_target_only_retry_rejects_changed_provider_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    provider: str, completion: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    peer = 'synthetic-registered-observer'
    data = json.loads(registry.read_text())
    data['goals'][0]['coordination']['registered_agents'].append(peer)
    registry.write_text(json.dumps(data))
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    target_poll = _target_poll(poll, material=False)
    rc, original = _cli(registry, runtime, *target_poll,
        fault='quota_commit' if completion == 'interrupted' else None)
    assert rc == (73 if completion == 'interrupted' else 0), original
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    observed = next(todo for todo in before['todos'] if todo['todo_id'] == monitor)
    assert not monitor_todo_is_due(observed)
    receipts = _poll_receipts(runtime)
    (receipt,) = receipts.values()
    effect = json.loads(receipt)['effect_id']
    index_path = runtime / 'goals' / GOAL / 'runs' / 'index.jsonl'
    index = index_path.read_bytes() if index_path.exists() else None
    native = monitor_runtime._native_result
    attempts = []

    def replace_actor(request):
        # Keep the accounting Agent, Turn and recovered operation unchanged.
        # The registered provider actor is the only altered wire field.
        assert request['phase'] == 'preflight'
        assert request['effect_id'] == effect
        assert request['decision']['agent_id'] == AGENT
        assert request['observation']['actor_agent_id'] == AGENT
        result = native({**request, 'observation': {
            **request['observation'], 'actor_agent_id': peer}})
        attempts.append(result)
        return result

    with monkeypatch.context() as context:
        context.chdir(registry.parent.parent)
        context.setenv('LOOPX_USAGE_PING', '0')
        context.setattr(monitor_runtime, '_native_result', replace_actor)
        rc = cli_main(['--registry', str(registry), '--runtime-root', str(runtime),
            '--format', 'json', *target_poll])
        rejected = json.loads(capsys.readouterr().out)
    assert rc != 0 and rejected['error_code'] == 'heartbeat_receipt_identity_conflict', rejected
    assert len(attempts) == 1 and attempts[0]['reason_code'] == 'effect_id_conflict'
    assert _poll_receipts(runtime) == receipts
    assert (index_path.read_bytes() if index_path.exists() else None) == index
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
    rc, recovered = _cli(registry, runtime, *target_poll)
    assert rc == 0, recovered
    events = _poll_events(runtime)
    assert len(events) == 1 and events[0]['todo_id'] == monitor and events[0]['agent_id'] == AGENT
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
    recovered_receipts, recovered_index = _poll_receipts(runtime), index_path.read_bytes()
    rc, replayed = _cli(registry, runtime, *target_poll)
    assert rc == 0 and replayed['replayed'] is True, replayed
    assert _poll_receipts(runtime) == recovered_receipts and index_path.read_bytes() == recovered_index
    assert not any(row['classification'] == 'quota_slot_spent' for row in read_run_index(runtime, GOAL))


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('competitor', ['duplicate', 'changed_hash'])
def test_target_only_concurrent_pending_retries_keep_one_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, competitor: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    target_poll = _target_poll(poll, material=False)
    rc, interrupted = _cli(registry, runtime, *target_poll, fault='quota_commit')
    assert (rc, interrupted) == (73, None)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    assert not monitor_todo_is_due(next(todo for todo in before['todos'] if todo['todo_id'] == monitor))
    assert _poll_events(runtime) == []
    (receipt,) = _poll_receipts(runtime).values()
    pending = json.loads(receipt)
    assert pending['status'] == 'provider_pending'
    effect = pending['effect_id']
    other_poll = list(target_poll)
    if competitor == 'changed_hash':
        other_poll[other_poll.index('--result-hash') + 1] = 'synthetic-other-head'
    barrier = tmp_path / 'retry-barrier'
    barrier.mkdir()
    processes = []
    try:
        # Both real CLI writers resolve the same pending route before either
        # enters quota admission. The barrier changes no business/native rule.
        for index, args in enumerate((target_poll, other_poll)):
            processes.append(subprocess.Popen([sys.executable, '-c', _CONCURRENT_RETRY_WRAPPER,
                '--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json', *args],
                cwd=REPO, env={**os.environ, 'LOOPX_USAGE_PING': '0',
                    'SYNTHETIC_RETRY_BARRIER': str(barrier), 'SYNTHETIC_RETRY_ACTOR': f'ready-{index}'},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        replies = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            replies.append((process.returncode, json.loads(stdout) if stdout.strip() else {'stderr': stderr}))
    finally:
        # Clean up only this fixture's two children if its bounded wait failed.
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    arrivals = [json.loads((barrier / f'ready-{index}').read_text()) for index in range(2)]
    assert [arrival['route'] for arrival in arrivals] == [[monitor, effect], [monitor, effect]]
    assert [arrival['pid'] for arrival in arrivals] == [process.pid for process in processes]
    assert arrivals[0]['pid'] != arrivals[1]['pid']
    assert replies[0][0] == 0, replies
    if competitor == 'duplicate':
        assert replies[1][0] == 0, replies
    else:
        assert replies[1][0] != 0 and replies[1][1]['error_code'] == 'heartbeat_receipt_identity_conflict', replies
    events = _poll_events(runtime)
    assert len(events) == 1 and events[0]['todo_id'] == monitor
    (receipt,) = _poll_receipts(runtime).values()
    assert json.loads(receipt)['record']['monitor_event']['result_hash'] == 'synthetic-head-1'
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
    receipts, index = _poll_receipts(runtime), read_run_index(runtime, GOAL)
    rc, replayed = _cli(registry, runtime, *target_poll)
    assert rc == 0 and replayed['replayed'] is True, replayed
    assert _poll_receipts(runtime) == receipts and read_run_index(runtime, GOAL) == index
    assert not any(row['classification'] == 'quota_slot_spent' for row in index)


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_target_only_new_observation_remains_rejected_when_not_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    # Another legitimate business observation advances cadence without this
    # Turn accepting a quota poll. No historical quota receipt may be inferred.
    write_monitor_poll_todo_state(registry_path=registry, runtime_root=runtime, goal_id=GOAL,
        todo_id=monitor, target_key=MONITOR_TARGET, result_hash='independent-synthetic-head',
        generated_at='2026-10-01T00:00:00Z', next_due_at='2099-01-01T00:00:00Z', execute=True,
        monitor_effect_id='independent-synthetic-observation', agent_id=AGENT)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    observed = next(todo for todo in before['todos'] if todo['todo_id'] == monitor)
    assert not monitor_todo_is_due(observed)
    before_index = read_run_index(runtime, GOAL)
    assert _poll_receipts(runtime) == {}
    rc, rejected = _cli(registry, runtime, *_target_poll(poll, material=False))
    assert rc != 0 and rejected['error_code'] == 'heartbeat_receipt_identity_conflict', rejected
    assert _poll_receipts(runtime) == {}
    assert read_run_index(runtime, GOAL) == before_index
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('historical_null_todo', [False, True])
def test_target_only_auxiliary_recovery_preserves_advancement_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, historical_null_todo: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    rc, original = _cli(registry, runtime, *_target_poll(poll, material=False))
    assert rc == 0, original
    rc, advancement = _cli(registry, runtime, 'todo', 'add', '--goal-id', GOAL, '--role', 'agent',
        '--text', 'Inspect an independent synthetic artifact', '--task-class', 'advancement_task',
        '--claimed-by', AGENT, '--operation-id', 'synthetic-advancement')
    assert rc == 0, advancement
    other = add_goal_todo(registry_path=registry, goal_id=GOAL, role='agent',
        text='Observe independent auxiliary evidence', task_class='continuous_monitor', claimed_by=AGENT,
        monitor_metadata={'target_key': 'synthetic-auxiliary-target', 'cadence': '1h',
                          'next_due_at': '2000-01-01T00:00:00+00:00', 'watch_only': 'true'},
        operation_id='synthetic-auxiliary')
    turn = 'synthetic-auxiliary-turn'
    rc, guard = _cli(registry, runtime, 'quota', 'should-run', '--goal-id', GOAL,
        '--agent-id', AGENT, '--runtime-profile', 'generic_cli', '--turn-instance-id', turn)
    assert rc == 0 and guard['work_lane_contract']['lane'] == 'advancement_task', guard
    bound = guard['heartbeat_receipt']['settlement_identity']['todo_id']
    assert bound != other['todo_id'] and bound != monitor
    aux = list(_target_poll(poll, material=False))
    aux[aux.index('--turn-instance-id') + 1] = turn
    aux[aux.index('--target-key') + 1] = 'synthetic-auxiliary-target'
    if historical_null_todo:
        interrupted_cli = subprocess.run([sys.executable, '-c', _NULL_TODO_WRAPPER,
            '--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json', *aux],
            cwd=REPO, env={**os.environ, 'LOOPX_USAGE_PING': '0'},
            capture_output=True, text=True, check=False, timeout=60)
        rc, interrupted = interrupted_cli.returncode, None
    else:
        rc, interrupted = _cli(registry, runtime, *aux, fault='quota_commit')
    assert (rc, interrupted) == (73, None)
    business = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    for _ in range(2):
        rc, recovered = _cli(registry, runtime, *aux)
        assert rc == 0, recovered
        assert recovered['turn_continuation']['settlement_binding_matches_observation'] is False
        assert recovered['settlement_resume']['identity']['todo_id'] == bound
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == business
    events = _poll_events(runtime)
    assert len(events) == 2 and events[-1]['todo_id'] == other['todo_id']
    assert not any(row['classification'] == 'quota_slot_spent' for row in read_run_index(runtime, GOAL))


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_target_recovery_hint_cannot_create_poll_after_no_effect_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor, poll = _fixture(tmp_path)
    peer = 'synthetic-registered-peer'
    data = json.loads(registry.read_text())
    data['goals'][0]['coordination']['registered_agents'].append(peer)
    registry.write_text(json.dumps(data))
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL)['todos']
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode='soft_claim'), state_path=state, provider=provider)
    target_poll = _target_poll(poll, material=False)
    captured = {}
    native = monitor_runtime._native_result

    def cli(*args):
        with monkeypatch.context() as context:
            context.chdir(registry.parent.parent)
            rc = cli_main(['--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json', *args])
            return rc, json.loads(capsys.readouterr().out)

    def stop_before_provider(request):
        result = native(request)
        if request['phase'] == 'preflight' and result['status'] == 'provider_required':
            captured.update(request=request, plan=result['provider_plan'])
            raise ValueError('synthetic interruption before provider dispatch')
        return result

    with monkeypatch.context() as context:
        context.setattr(monitor_runtime, '_native_result', stop_before_provider)
        rc, paused = cli(*target_poll)
    assert rc != 0 and len(_poll_receipts(runtime)) == 1, paused
    assert _poll_events(runtime) == []
    # A separate legitimate observation advances cadence. The original poll
    # has not dispatched; a real canonical planning rejection can prove no effect.
    write_monitor_poll_todo_state(registry_path=registry, runtime_root=runtime, goal_id=GOAL,
        todo_id=monitor, target_key=MONITOR_TARGET, result_hash='independent-synthetic-head',
        generated_at='2026-10-01T00:00:00Z', next_due_at='2099-01-01T00:00:00Z', execute=True,
        monitor_effect_id='independent-synthetic-observation', agent_id=AGENT)
    update_goal_todo(registry_path=registry, goal_id=GOAL, todo_id=monitor,
        excluded_agents=[AGENT], clear_claim=True, agent_id=AGENT, update_operation_id='synthetic-exclusion')
    plan = captured['plan']
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as no_effect:
        poll_canonical_monitor_if_promoted(registry_path=registry, runtime_root=runtime, goal_id=GOAL,
            execute=True, monitor_effect_id=plan['monitor_effect_id'], agent_id=AGENT,
            observation={key: plan[key] for key in ('todo_id', 'target_key', 'result_hash', 'material_change',
                'generated_at', 'cadence', 'next_due_at', 'reason_summary')},
            intent={key: plan[key] for key in ('next_agent_todo', 'next_action_kind', 'next_task_repository',
                'next_required_capabilities', 'next_continuation_policy', 'next_target_key', 'next_claimed_by',
                'next_user_todo', 'next_user_task_class')})
    proof = no_effect.value.payload
    assert proof['no_effect']['operation_id'] == plan['monitor_effect_id']
    update_goal_todo(registry_path=registry, goal_id=GOAL, todo_id=monitor,
        clear_excluded_agents=True, agent_id=peer, update_operation_id='synthetic-exclusion-cleared')
    update_goal_todo(registry_path=registry, goal_id=GOAL, todo_id=monitor,
        claimed_by=AGENT, agent_id=AGENT, claim_only=True, claim_operation_id='synthetic-reclaim')
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    before_index = read_run_index(runtime, GOAL)
    route = monitor_runtime.resolve_monitor_poll_target
    new_poll_requests = []

    def trace_new_poll(request):
        new_poll_requests.append(request['phase'])
        return native(request)

    def route_then_abort(**kwargs):
        try:
            resolved = route(**kwargs)
            assert resolved == (monitor, plan['monitor_effect_id'])
            with quota_accounting_admission(runtime_root=runtime, registry_path=registry, goal_id=GOAL,
                    goal_ref=captured['request'].get('goal_ref'), operation='quota_monitor_poll_index',
                    lock_legacy_index=True) as admission:
                abort_request = {**captured['request'], 'phase': 'provider_rejected', 'provider_receipt': proof}
                if admission is not None:
                    abort_request['source_admission'] = admission
                else:
                    abort_request.pop('source_admission', None)
                aborted = native(abort_request)
            assert aborted['status'] == 'aborted'
            assert _poll_receipts(runtime) == {}
            return resolved
        except Exception as exc:
            captured['abort_error'] = repr(exc)
            raise

    with monkeypatch.context() as context:
        context.setattr(monitor_runtime, 'resolve_monitor_poll_target', route_then_abort)
        # quota imports this adapter by name, so patch its shipped call site too.
        context.setattr('loopx.quota.resolve_monitor_poll_target', route_then_abort)
        context.setattr(monitor_runtime, '_native_result', trace_new_poll)
        rc, rejected = cli(*target_poll)
    assert new_poll_requests == [], 'a lost recovery route must not become a fresh native preflight'
    assert rc != 0 and 'original Monitor poll transaction is unavailable' in rejected['reason'], {
        'reply': rejected, 'abort_error': captured.get('abort_error')}
    assert _poll_receipts(runtime) == {}
    assert read_run_index(runtime, GOAL) == before_index
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
