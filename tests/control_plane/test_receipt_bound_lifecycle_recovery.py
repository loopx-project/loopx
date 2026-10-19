"""Admission-source identity stays immutable; proven lifecycle recovery may advance it."""
import copy
import json
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tests/control_plane'))
import pytest
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.work_items import semantic_replan_writeback as writer

OID = 'replan-0123456789abcdef'
SOURCE = {'obligation_id': OID, 'required': True,
          'resolution_mode': 'todo_lifecycle_settlement',
          'satisfying_semantic_outcomes': ['new_runnable_successor']}
RECOVERED = {**SOURCE, 'resolution_mode': 'receipt_bound_replan_settlement',
             'satisfying_semantic_outcomes': ['fresh_vision_path_outcome']}


@pytest.mark.parametrize('case', ['recovered', 'unsettled', 'wrong_candidate',
    'no_candidate', 'unguarded', 'wrong_source', 'no_source', 'non_boolean'])
def test_selection_boundary(case):
    candidate = {'lifecycle_settled': True, 'obligation': copy.deepcopy(RECOVERED)}
    source = copy.deepcopy(SOURCE)
    context = {'replan_obligation': {'obligation_id': 'replan-fedcba9876543210'},
               'replan_transition_candidates': [candidate]}
    if case == 'unsettled': candidate['lifecycle_settled'] = False
    if case == 'non_boolean': candidate['lifecycle_settled'] = 'true'
    if case == 'wrong_candidate': candidate['obligation']['obligation_id'] = 'replan-fedcba9876543210'
    if case == 'no_candidate': context['replan_transition_candidates'] = []
    if case == 'wrong_source': source['obligation_id'] = 'replan-fedcba9876543210'
    if case == 'no_source': source = None
    with patch.object(writer, 'build_goal_frontier_projection_context_from_status', return_value=context), \
         patch.object(writer, 'autonomous_replan_obligation_from_runs', return_value=None), \
         patch.object(writer, 'guarded_replan_transition_delta', return_value=None), \
         patch.object(writer, 'semantic_delta_from_writeback', return_value={'outcomes': []}):
        call = lambda: writer.qualify_replan_writeback(newest_first_runs=[], state_text='',
            agent_id='fixture-agent', goal_id='fixture-goal', todo_fields={},
            guard_scoped=case != 'unguarded', guard_semantic_replan_obligation_id=OID,
            receipt_bound_source=source)
        if case == 'wrong_source':
            with pytest.raises(ValueError, match='source identity differs'): call()
            return
        chosen, _ = call()
    expected = (RECOVERED if case in ['recovered', 'no_source'] else
                context['replan_obligation'] if case == 'unguarded' else SOURCE)
    assert chosen == expected


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_native_lifecycle_writeback_and_replay(tmp_path, monkeypatch, provider):
    import test_quota_settlement_cli as cli
    from test_declared_terminal_settlement_cli import _fixture
    project, workspace, runtime, registry, run, lease, marker, _, original = _fixture(tmp_path, monkeypatch, provider)
    base = ['--goal-id', cli.GOAL_ID, '--agent-id', cli.AGENT_ID]
    original_binding = [*base, '--todo-id', cli.TODO_ID, '--turn-instance-id', cli.TURN_ID]
    code, guard = run('quota', 'should-run', '--codex-app', *original_binding,
        '--scan-path', str(project), '--available-capability', 'shell', '--available-capability', 'filesystem_write')
    assert code == 0 and guard['ok'], guard
    # Only this isolated fixture seeds the already-supported historical protocol.
    cli._historical_turn(runtime)
    code, completed = run('todo', 'complete', *original_binding, *lease,
        '--project', str(project), '--state-file', str(project / f'.codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md'),
        '--evidence', 'Original bounded fixture validator passed.')
    assert code == 0 and completed['status'] == 'done', completed
    code, refreshed = run('refresh-state', *original_binding,
        '--classification', 'declared_deliverable_complete', '--delivery-batch-scale', 'implementation',
        '--delivery-outcome', 'outcome_progress', '--vision-state', 'vision_closed',
        '--vision-summary', 'The original bounded fixture deliverable passed.',
        '--vision-acceptance', 'Retain original acceptance and exact result.', '--no-global-sync', '--suppress-external-sinks')
    assert code == 0 and refreshed['ok'], refreshed
    code, spent = run('quota', 'spend-slot', *original_binding, '--execute', '--scan-path', str(project))
    assert code == 0 and spent['ok'] and cli._spend_run_count(runtime) == 1, spent

    turn = 'receipt-bound-review'
    guardargs = ['quota', 'should-run', '--codex-app', *base, '--turn-instance-id', turn]
    code, admitted = run(*guardargs)
    assert code == 0 and admitted['ok'], admitted
    receipt = admitted['heartbeat_receipt']
    oid = receipt['semantic_replan_obligation_id']
    assert receipt['semantic_replan_source']['obligation']['resolution_mode'] == 'todo_lifecycle_settlement', admitted
    source_bytes = json.dumps(receipt['semantic_replan_source'], sort_keys=True)
    closeargs = ['todo', 'complete', *base, '--todo-id', cli.TODO_ID,
        '--completion-identity-key', completed['completion_identity_key'], '--no-follow-up',
        '--evidence', 'Original bounded fixture validator passed.']
    code, closed = run(*closeargs)
    assert code == 0 and closed['completion_recovery'] == 'lifecycle_reentry_terminal_closeout', closed
    assert closed['completion_receipt_id'] == completed['completion_receipt_id']
    code, recovered = run(*guardargs)
    assert code == 0 and recovered['ok'], recovered
    assert recovered['autonomous_replan_obligation']['obligation_id'] == oid
    assert recovered['autonomous_replan_obligation']['resolution_mode'] == 'receipt_bound_replan_settlement', recovered
    assert recovered['heartbeat_receipt']['event_id'] == receipt['event_id']
    assert json.dumps(recovered['heartbeat_receipt']['semantic_replan_source'], sort_keys=True) == source_bytes
    vision = {'schema_version': 'goal_vision_replan_contract_v0', 'state': 'vision_patch_proposed',
        'vision_patch': {'acceptance_summary': 'Original deliverable is verified; retain acceptance and evidence.',
                        'advancement_policy': 'repeat_until_closed', 'replan_trigger_summary': 'Audit any later scope independently.'},
        'path_delta': {'schema_version': 'goal_path_delta_v0', 'outcome': 'replan',
            'prior_assumption': 'The completed Todo still needed lifecycle closeout.',
            'observed_reality': 'Native original-identity terminal reentry is now proven.',
            'retained': ['Original acceptance and immutable admitted source.'],
            'evidence_refs': ['native:' + closed['completion_receipt_id']]}}
    vp = tmp_path / 'vision173.json'; vp.write_text(json.dumps(vision), encoding='utf-8')
    binding = [*base, '--turn-instance-id', turn, '--replan-obligation-id', oid]
    refresh = ['refresh-state', *binding, '--classification', 'receipt-bound-review',
        '--progress-scope', 'agent_lane', '--delivery-batch-scale', 'single_surface',
        '--delivery-outcome', 'outcome_gap', '--progress-result-class', 'blocked',
        '--progress-blocker-id', 'blocker:later-independent-scope',
        '--progress-evidence-id', 'native:' + closed['completion_receipt_id'],
        '--agent-vision-json', str(vp), '--no-global-sync', '--suppress-external-sinks']
    code, written = run(*refresh)
    assert code == 0 and written['ok'], written
    delta = written['autonomous_replan_ack']['semantic_delta']
    assert delta['accepted'] is True and delta['obligation_id'] == oid, written
    assert cli._spend_run_count(runtime) == 1
    spend = ['quota', 'spend-slot', *binding, '--execute', '--scan-path', str(project)]
    code, settled = run(*spend)
    assert code == 0 and settled['ok'] and settled['settlement_progress']['state'] == 'settled', settled
    assert cli._spend_run_count(runtime) == 2
    for args in (refresh, spend, closeargs):
        code, replay = run(*args)
        assert code == 0 and replay['idempotent_replay'] is True, replay
    assert cli._spend_run_count(runtime) == 2 and marker.read_text() == 'run\n'
    code, current = run('quota', 'should-run', '--codex-app', *base, '--turn-instance-id', 'lifecycle173-later')
    assert code == 0 and current['ok'], current
    assert current['heartbeat_receipt'].get('semantic_replan_obligation_id') != oid


@pytest.mark.parametrize('case', ['matching', 'wrong_turn', 'wrong_actor', 'unsettled', 'closed_before_guard', 'wrong_todo'])
def test_original_source_cardinality(case):
    items = [{'todo_id':f'todo_{i}', 'index':i, 'status':'done', 'done':True,
        'task_class':'advancement_task', 'claimed_by':'fixture-agent', 'last_actor_agent_id':'fixture-agent',
        'completed_at':f'2026-08-13T04:0{9-i}:00Z', 'updated_at':'2026-08-13T05:01:00Z',
        'no_followup':i<3, 'completion_continuation':'no_followup' if i<3 else 'active_goal',
        'completion_recovery':'lifecycle_reentry_terminal_closeout' if i<3 else None,
        'completion_turn_key':f'original-turn-{i}'} for i in range(7)]
    source = {**SOURCE, 'agent_id':'fixture-agent', 'trigger_count':5,
        'triggers':[{'kind':'completed_advancement_without_successor','agent_id':'fixture-agent',
                     'todo_id':f'todo_{i}','completion_turn_key':f'original-turn-{i}'} for i in range(3)]}
    if case=='wrong_turn':source['triggers'][0]['completion_turn_key']='wrong'
    if case=='wrong_actor':items[0]['last_actor_agent_id']='peer'
    if case=='unsettled':items[0]['no_followup']=False
    if case=='closed_before_guard':items[0]['updated_at']='2026-08-13T04:50:00Z'
    if case=='wrong_todo':source['triggers'][0]['todo_id']='wrong'
    with patch.object(writer,'build_goal_frontier_projection_context_from_status',return_value={}), \
         patch.object(writer,'autonomous_replan_obligation_from_runs',return_value=None), \
         patch.object(writer,'guarded_replan_transition_delta',return_value=None), \
         patch.object(writer,'select_planning_inventory_source_items',return_value=items), \
         patch.object(writer,'semantic_delta_from_writeback',return_value={'outcomes':[]}):
        call=lambda: writer.qualify_replan_writeback(newest_first_runs=[],state_text='',agent_id='fixture-agent',
            goal_id='fixture-goal',todo_fields={},guard_scoped=True,guard_semantic_replan_obligation_id=OID,
            receipt_bound_guard_recorded_at='2026-08-13T05:00:00Z',receipt_bound_source=source)
        if case!='matching':
            with pytest.raises(EffectRuntimeRejected,match='lifecycle source does not match'):call()
            return
        chosen,_=call()
    assert chosen['obligation_id']==OID
    if case=='matching':
        assert chosen['resolution_mode']=='receipt_bound_replan_settlement'
        assert 'fresh_vision_path_outcome' in chosen['satisfying_semantic_outcomes']
    else:assert chosen==source
