"""Default post-result checkpoints through real MCP/CLI and owning stores."""
from __future__ import annotations

import json
import sys
import subprocess
import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo, update_goal_todo
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.control_plane.turn_driver.checkpoint_continuation import CurrentAgentCheckpoint
from loopx.control_plane.goals.checkpoint_context_io import _receipt_path
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from tests.test_goal_mode_mcp_settlement import _write_fixture, _control, GOAL_ID, AGENT_ID, _run_cli


def _fixture(provider, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = _write_fixture(tmp_path)
    doc = json.loads(registry.read_text())
    doc['goals'][0]['adapter'] = {'kind': 'fixture_v0', 'status': 'connected-delivery'}
    registry.write_text(json.dumps(doc), encoding='utf-8')
    rc, baseline = _run_cli(registry, 'refresh-state', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--vision-summary', 'Verify the original route.', '--vision-acceptance', 'Verified candidate.',
        '--no-global-sync', '--suppress-external-sinks')
    assert rc == 0, baseline
    todo = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role='agent',
        text='Verify one candidate.', task_class='advancement_task', claimed_by=AGENT_ID,
        continuation_policy='same_agent_non_delivery')
    if provider != 'legacy':
        todos = parse_active_state_todos(state.read_text(encoding='utf-8'))['agent_todos']['items']
        initialize_canonical_authority(tmp_path / 'runtime', GOAL_ID,
            build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode='soft_claim'),
            state_path=state, provider=provider)
    return registry, state, todo['todo_id']


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('terminal', [False, True, 'stored', 'cold_explicit'])
def test_default_mcp_uses_post_result_basis_and_original_turn(provider, terminal, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    if terminal == 'stored':
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=todo, role='agent',
            agent_id=AGENT_ID, no_followup=True)
    control = _control(registry)
    guard = json.loads(control.should_run())
    assert 'delivery_result_context' in guard, json.dumps(guard, indent=2)
    original = guard['delivery_result_context']['settlement_identity']
    options = {} if terminal == 'stored' else {'no_follow_up': True} if terminal else {'next_agent_todo': 'Verify remaining work.'}
    first = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options))
    assert first.get('stage') == 'direction_pending', json.dumps(first, indent=2)
    assert first['completed'] is True and first['settlement_complete'] is False
    assert first['settlement_identity'] == original
    context = first['context']
    assert context['basis']['todo']['status'] == 'done'
    assert context['basis']['frontier']['scope'] == 'goal'
    # The tool caller authors only after seeing the returned post-result facts.
    second = json.loads(_control(registry).complete_task(todo, AGENT_ID, 'Candidate verified.',
        **({} if terminal == 'cold_explicit' else options),
        read_context_id=context['read_context_id'],
        vision_unchanged_reason='The committed candidate and current obligations preserve the route.'))
    assert second.get('ok') is True and second.get('settlement_complete') is True, json.dumps(second, indent=2)
    assert second['settlement_identity'] == original


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
def test_default_mcp_rejects_stale_direction_preserves_result(provider, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    json.loads(control.should_run())
    options = {'next_agent_todo': 'Verify remaining work.'}
    first = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options))
    assert first.get('stage') == 'direction_pending', first
    old = first['context']['read_context_id']
    state.write_text(state.read_text(encoding='utf-8') + '\n## Acceptance\nCheck the revised obligation.\n', encoding='utf-8')
    stale = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options,
        read_context_id=old, vision_unchanged_reason='Old acceptance still applies.'))
    assert stale.get('ok') is False, stale
    reread = json.loads(control.review_task_vision(todo, AGENT_ID))
    assert reread['read_context_id'] != old, reread
    assert 'revised obligation' in json.dumps(reread['basis'])
    resumed = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options,
        read_context_id=reread['read_context_id'], vision_unchanged_reason='The revised obligation is the next step.'))
    assert resumed.get('settlement_complete') is True, resumed
    assert resumed['settlement_identity'] == first['settlement_identity']


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
def test_default_pending_readback_guides_the_original_caller(provider, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    json.loads(control.should_run())
    options = {'next_agent_todo': 'Verify remaining work.'}
    first = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options))
    identity = first['settlement_identity']
    for command in (['status', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID],
                    ['quota', 'should-run', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
                     '--todo-id', todo, '--turn-instance-id', identity['turn_instance_id']]):
        rc, observed = _run_cli(registry, *command)
        assert rc == 0, json.dumps(observed)
        item = observed['attention_queue']['items'][0] if command[0] == 'status' else observed
        progress = item['first_delivery_progress']
        assert progress['settlement_identity'] == identity
        assert progress['stage'] == 'direction_pending' and progress['result_committed'] is True
        assert progress['quota_spent'] is False and progress['goal_completion_certified'] is False
        assert item['recommended_action'] == progress['next_action']
    # A fresh facade consumes the direction operation, retaining the original Turn.
    resumed_control = _control(registry)
    context = json.loads(resumed_control.review_task_vision(todo, AGENT_ID))
    assert context['settlement_identity'] == identity
    resumed = json.loads(resumed_control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options,
        read_context_id=context['read_context_id'],
        vision_unchanged_reason='The committed candidate and remaining obligations preserve the route.'))
    assert resumed['settlement_complete'] is True and resumed['settlement_identity'] == identity
    rc, observed = _run_cli(registry, 'status', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID)
    assert rc == 0 and 'first_delivery_progress' not in observed['attention_queue']['items'][0], json.dumps(observed)
    rows = [json.loads(line) for line in (tmp_path / 'runtime/goals' / GOAL_ID / 'runs/index.jsonl').read_text().splitlines()]
    assert sum(row.get('classification') == 'quota_slot_spent' for row in rows) == 1


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('terminal', [False, True])
def test_managed_default_reuses_candidate_and_calls_current_agent_for_direction(provider, terminal, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    if terminal:
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=todo, role='agent',
            agent_id=AGENT_ID, no_followup=True)
    project = state.parent
    host = project / 'host.py'
    host.write_text('''import json,pathlib,sys
r=json.load(sys.stdin)
if r.get('phase') == 'checkpoint_direction':
    c=r['checkpoint_context']
    assert c['basis']['todo']['status'] == 'done'
    assert pathlib.Path('candidate.txt').read_text() == 'valid'
    with pathlib.Path('direction.txt').open('a') as f: f.write(c['read_context_id']+'\\n')
    json.dump({'read_context_id':c['read_context_id'], 'vision_unchanged_reason':'The committed candidate and latest obligations preserve the route.'},sys.stdout)
else:
    assert not pathlib.Path('candidate.txt').exists()
    pathlib.Path('candidate.txt').write_text('valid')
    json.dump({'schema_version':'loopx_turn_result_v0','turn_key':r['turn_key'],
        'result_kind':'validated_completion','completed_phases':['host_execute','typed_result'],
        'classification':'candidate_verified','recommended_action':'Read remaining work.',
        'next_action':'Read remaining work.','delivery_batch_scale':'implementation',
        'delivery_outcome':'outcome_progress','summary':'Candidate verified.',
        'vision_unchanged_reason':'Old pre-result judgment.'},sys.stdout)
''', encoding='utf-8')
    validator = project / 'verify.py'
    validator.write_text("import pathlib; assert pathlib.Path('candidate.txt').read_text()=='valid'", encoding='utf-8')
    rc, result = _run_cli(registry, 'turn', 'run-once', '--host', 'generic-cli', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--project', str(project), '--host-adapter-command-json',
        json.dumps([sys.executable, str(host)]), '--validation-command-json', json.dumps([sys.executable, str(validator)]),
        '--scan-root', str(project), '--no-global-sync', '--execute')
    assert rc == 0 and result.get('status') == 'committed', json.dumps(result, indent=2)
    assert len((project / 'direction.txt').read_text().splitlines()) == 1
    rows = [json.loads(line) for line in (tmp_path / 'runtime/goals' / GOAL_ID / 'runs/index.jsonl').read_text(encoding='utf-8').splitlines()]
    committed = [row for row in rows if row.get('classification') == 'candidate_verified']
    assert len(committed) == 1
    assert committed[0]['vision_checkpoint']['unchanged_reason'] != 'Old pre-result judgment.'


@pytest.mark.parametrize('iteration_context', ['fresh', 'resume'])
def test_default_dsh_continues_the_original_agent_session(iteration_context, tmp_path, monkeypatch):
    registry, state, _ = _fixture('file', tmp_path, monkeypatch)
    project = state.parent
    runner = project / 'runner.py'
    runner.write_text('''import json,pathlib
def run_dsh_turn(**kwargs):
    prompt=kwargs['prompt']; direction=prompt.startswith('Continue the current Agent')
    with pathlib.Path(kwargs['workspace'],'sessions.jsonl').open('a') as f:
        f.write(json.dumps({'session':kwargs['session_id'],'direction':direction})+'\\n')
    if direction:
        r=json.loads(prompt.split('\\n',1)[1]); c=r['checkpoint_context']
        assert c['basis']['frontier']['scope']=='goal'
        assert pathlib.Path(kwargs['workspace'],'candidate.txt').read_text()=='valid'
        return json.dumps({'read_context_id':c['read_context_id'],
            'vision_unchanged_reason':'Current evidence preserves this session route.'})
    pathlib.Path(kwargs['workspace'],'candidate.txt').write_text('valid')
    return json.dumps({'result_kind':'validated_progress','classification':'dsh_progress',
        'summary':'Verified one scoped candidate.','next_action':'Review remaining work.'})
''', encoding='utf-8')
    rc, result = _run_cli(registry, 'turn', 'run-once', '--host', 'dsh', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--project', str(project), '--dsh-runner', str(runner),
        '--iteration-context', iteration_context, '--validation-command-json', json.dumps([sys.executable, '-c', 'pass']),
        '--scan-root', str(project), '--no-global-sync', '--execute')
    assert rc == 0 and result['status'] == 'committed', result
    sessions = [json.loads(line) for line in (project / 'sessions.jsonl').read_text().splitlines()]
    assert [row['direction'] for row in sessions] == [False, True]
    assert sessions[0]['session'] == sessions[1]['session']
    rows = [json.loads(line) for line in (tmp_path / 'runtime/goals' / GOAL_ID / 'runs/index.jsonl').read_text().splitlines()]
    assert sum(row.get('classification') == 'quota_slot_spent' for row in rows) == 1


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('change', ['none', 'related', 'display', 'cached_binding'])
def test_direction_retry_keeps_input_response_binding_and_result(provider, change, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    json.loads(control.should_run())
    first = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', next_agent_todo='Verify remaining work.'))
    assert first['stage'] == 'direction_pending', first
    identity = SettlementIdentity.from_runtime_payload(first['settlement_identity'])
    seen = []
    def decide(context):
        seen.append(context)
        assert context['basis']['todo']['status'] == 'done'
        return {'read_context_id': context['read_context_id'], 'vision_unchanged_reason': 'Current evidence preserves the route.'}
    driver = CurrentAgentCheckpoint(registry, tmp_path / 'runtime', state, identity, decide)
    decided = driver.continue_direction({})
    if change == 'related':
        state.write_text(state.read_text(encoding='utf-8') + '\n## Acceptance\nRevised obligation.\n', encoding='utf-8')
    elif change == 'display':
        import re
        text = state.read_text(encoding='utf-8')
        text = re.sub(r'^updated_at:.*$', 'updated_at: 2026-10-08', text, flags=re.MULTILINE) if 'updated_at:' in text else text.replace('---\n', '---\nupdated_at: 2026-10-08\n', 1)
        state.write_text(text, encoding='utf-8')
    elif change == 'cached_binding':
        path = _receipt_path(tmp_path / 'runtime', identity)
        receipt = json.loads(path.read_text())
        receipt['current_agent_decision']['read_context_id'] = 'replacement-key'
        path.write_text(json.dumps(receipt), encoding='utf-8')
        with pytest.raises(ValueError, match='binding'):
            driver.continue_direction({})
        assert len(seen) == 1
        return
    cold = CurrentAgentCheckpoint(registry, tmp_path / 'runtime', state, identity, decide)
    replay = cold.continue_direction({})
    assert len(seen) == (2 if change == 'related' else 1)
    assert (replay['checkpoint_read_context_id'] != decided['checkpoint_read_context_id']) == (change == 'related')
    result = json.loads(_control(registry).complete_task(todo, AGENT_ID, 'Candidate verified.',
        next_agent_todo='Verify remaining work.', read_context_id=replay['checkpoint_read_context_id'],
        vision_unchanged_reason=replay['vision_unchanged_reason']))
    assert result['settlement_complete'] is True, result
    assert result['settlement_identity'] == first['settlement_identity']


@pytest.mark.parametrize('failure', ['timeout', 'stop'])
def test_direction_failure_retains_result_and_respects_existing_host_budget(failure, tmp_path, monkeypatch):
    registry, state, todo = _fixture('file', tmp_path, monkeypatch)
    control = _control(registry)
    json.loads(control.should_run())
    first = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', next_agent_todo='Verify remaining work.'))
    identity = SettlementIdentity.from_runtime_payload(first['settlement_identity'])
    calls = []
    def fail(context):
        calls.append(context['read_context_id'])
        if failure == 'timeout':
            raise BuiltInHostError('Synthetic timeout', failure_kind='executor_timeout')
        return {'result_kind': 'user_action_required'}
    driver = CurrentAgentCheckpoint(registry, tmp_path / 'runtime', state, identity, fail)
    for _ in range(3):
        with pytest.raises(ValueError):
            driver.continue_direction({})
    assert len(calls) == (2 if failure == 'timeout' else 1)
    receipt = json.loads(_receipt_path(tmp_path / 'runtime', identity).read_text())
    assert receipt['decision_basis']['todo']['status'] == 'done'
    assert 'current_agent_decision' not in receipt


@pytest.mark.parametrize('kind', ['validated_progress', 'repair_required', 'replan_required', 'wait'])
def test_managed_noncompletion_uses_original_outcome_or_exemption(kind, tmp_path, monkeypatch):
    registry, state, todo = _fixture('file', tmp_path, monkeypatch)
    project = state.parent
    host = project / 'host.py'
    host.write_text('''import json,pathlib,sys
r=json.load(sys.stdin)
if r.get('phase') == 'checkpoint_direction':
    c=r['checkpoint_context']
    assert c['basis']['todo']['status'] != 'done'
    with pathlib.Path('direction.txt').open('a') as f: f.write(c['read_context_id']+'\\n')
    decision={'read_context_id':c['read_context_id'],'vision_unchanged_reason':'Current evidence preserves the scoped route.'}
    if KIND == 'replan_required':
        decision={'read_context_id':c['read_context_id'],'agent_vision':{'schema_version':'goal_vision_replan_contract_v0',
            'state':'vision_patch_proposed','vision_patch':{'vision_summary':'Verify the revised scoped route.',
            'acceptance_summary':'Fixture evidence supports the revised route.'},'path_delta':{'schema_version':'goal_path_delta_v0',
            'outcome':'replan','prior_assumption':'Original scoped route.','observed_reality':'Revised route required.',
            'changed':['Scoped route'], 'evidence_refs':['evidence:fixture']}}}
    json.dump(decision,sys.stdout)
else:
    assert not pathlib.Path('candidate.txt').exists()
    pathlib.Path('candidate.txt').write_text('verified')
    value={'schema_version':'loopx_turn_result_v0','turn_key':r['turn_key'],
        'result_kind':KIND,'completed_phases':['host_execute','typed_result'],
        'classification':'fixture_outcome','recommended_action':'Check remaining scoped work.',
        'next_action':'Check remaining scoped work.','summary':'Bounded fixture outcome.'}
    if KIND != 'wait': value.update(delivery_batch_scale='implementation',delivery_outcome='outcome_progress',
        vision_unchanged_reason='Old pre-outcome judgment.')
    json.dump(value,sys.stdout)
'''.replace('KIND', repr(kind)), encoding='utf-8')
    rc, result = _run_cli(registry, 'turn', 'run-once', '--host', 'generic-cli', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--project', str(project), '--host-adapter-command-json',
        json.dumps([sys.executable, str(host)]), '--validation-command-json', json.dumps([sys.executable, '-c', 'pass']),
        '--scan-root', str(project), '--no-global-sync', '--execute')
    assert rc == 0 and result['status'] in {'committed','stopped'}, json.dumps(result, indent=2)
    assert (project / 'direction.txt').exists() == (kind != 'wait')
    rows = [json.loads(line) for line in (tmp_path / 'runtime/goals' / GOAL_ID / 'runs/index.jsonl').read_text(encoding='utf-8').splitlines()]
    assert sum(row.get('classification') == 'quota_slot_spent' for row in rows) == (0 if kind == 'wait' else 1)
    assert not any(row.get('completion_todo_id') == todo for row in rows)
