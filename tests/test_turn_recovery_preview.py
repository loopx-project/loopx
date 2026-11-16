import copy
import json
import pytest
from loopx.control_plane.turn_driver import build_loopx_turn_plan, run_loopx_turn_once
from loopx.control_plane.turn_driver.executor import turn_journal_path
from loopx.control_plane.turn_driver.recovery import assess_existing_turn_recovery
from loopx.control_plane.turn_driver.transaction import TRANSACTION_PHASES

def plan():
    return build_loopx_turn_plan({
        'ok': True, 'schema_version': 'loopx_turn_envelope_v0',
        'goal_id': 'fixture-goal', 'agent_id': 'fixture-agent',
        'should_run': True, 'effective_action': 'normal_run',
        'action': {'must_attempt': True, 'delivery_allowed': True,
                   'quiet_noop_allowed': False, 'selected_todo': {'todo_id': 'todo_fixture0001', 'text': 'fixture'}},
        'user': {'action_required': False, 'open_count': 0, 'notify': 'DONT_NOTIFY'},
        'writeback': {'spend_after_validation': True}, 'scheduler': {'action': 'run_now'},
        'action_signature': {'matches': True, 'source_hash': 'sha256:fixture', 'envelope_hash': 'sha256:fixture'},
        'compaction': {'within_budget': True},
    }, host='generic-cli', execution_mode='isolated-headless')

def forbidden(*args, **kwargs):
    pytest.fail('read-only preview invoked an execution callback')

@pytest.mark.parametrize('prefix,retry', [(0, True), (3, True), (4, True), (5, True), (3, False)])
def test_preview_uses_existing_typed_phase_without_effects(tmp_path, prefix, retry):
    original = plan()
    key = original['transaction']['turn_key']
    runtime = tmp_path / 'runtime'
    path = turn_journal_path(runtime, goal_id='fixture-goal', turn_key=key)
    journal = {'schema_version': 'loopx_turn_journal_v0', 'goal_id': 'fixture-goal',
               'turn_key': key, 'plan': original, 'status': 'failed',
               'completed_phases': list(TRANSACTION_PHASES[:prefix]),
               'receipt': {'turn_key': key, 'failed_phase': TRANSACTION_PHASES[prefix]}}
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(journal))
    before = path.read_bytes()
    old_plan = copy.deepcopy(original)
    decision = assess_existing_turn_recovery(journal, {}, goal_id='fixture-goal',
        agent_id='fixture-agent', turn_key=key, retry_failed=retry, session_binding_resolver=None).decision
    assert decision['reason'] != 'journal_inconsistent'
    result = run_loopx_turn_once(original, host_runner=forbidden, project=tmp_path,
        runtime_root=runtime, goal_id='fixture-goal', timeout_seconds=5,
        execute=False, retry_failed=retry, task_validator=forbidden,
        writeback=forbidden, spend=forbidden, scheduler=forbidden)
    assert result['dry_run'] and result['status'] == 'preview'
    assert result['route']['would_invoke_host'] == decision['reinvoke_host']
    assert result['recovery']['planned'] == decision
    assert result['recovery']['actual']['journal_status'] == 'failed'
    assert result['recovery']['actual']['host_invoked'] is False
    assert not any(result['effects'].values())
    assert path.read_bytes() == before and original == old_plan
    assert len(list(path.parent.iterdir())) == 1

@pytest.mark.parametrize('case', ['committed', 'foreign_goal', 'foreign_turn', 'session_blocked'])
def test_preview_returns_retained_or_blocked_without_host(tmp_path, case):
    original = plan()
    key = original['transaction']['turn_key']
    path = turn_journal_path(tmp_path, goal_id='fixture-goal', turn_key=key)
    journal = {'schema_version': 'loopx_turn_journal_v0', 'goal_id': 'fixture-goal', 'turn_key': key,
        'plan': original, 'status': 'failed', 'completed_phases': list(TRANSACTION_PHASES[:3]),
        'receipt': {'turn_key': key, 'failed_phase': 'durable_writeback'}}
    if case == 'committed':
        journal.update(status='committed', completed_phases=list(TRANSACTION_PHASES))
        journal.pop('receipt')
    elif case == 'foreign_goal':
        journal['goal_id'] = 'other'
    elif case == 'foreign_turn':
        journal['turn_key'] = 'sha256:' + 'f' * 64
    else:
        journal['host_recovery'] = {'unavailable': True}
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(journal))
    before = path.read_bytes()
    result = run_loopx_turn_once(original, host_runner=forbidden, project=tmp_path,
        runtime_root=tmp_path, goal_id='fixture-goal', timeout_seconds=5, execute=False, retry_failed=True)
    assert result['route']['would_invoke_host'] is False
    assert result['recovery']['planned']['action'] == ('return_existing' if case == 'committed' else 'blocked')
    assert not any(result['effects'].values()) and path.read_bytes() == before

def test_new_turn_preview_keeps_the_host_plan_and_writes_nothing(tmp_path):
    result = run_loopx_turn_once(plan(), host_runner=forbidden, project=tmp_path,
        runtime_root=tmp_path, goal_id='fixture-goal', timeout_seconds=5, execute=False)
    assert result['route']['would_invoke_host'] is True
    assert 'recovery' not in result and not any(result['effects'].values())
    assert list(tmp_path.iterdir()) == []
