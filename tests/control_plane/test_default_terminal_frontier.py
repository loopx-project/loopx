"""Default terminal/frontier recovery through disposable owning stores."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests/control_plane')]
from loopx.todos import add_goal_todo
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.status import parse_active_state_todos
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.control_plane.test_default_turn_recovery import _managed_recovery_call, _business_rows, _work_counts
from tests.test_goal_mode_mcp_settlement import _control, GOAL_ID, AGENT_ID

SCENARIOS = ['ordinary_successor', 'legal_no_followup', 'changed_during_direction',
             'changed_before_terminal', 'changed_after_terminal', 'terminal_loss_replay']

def rows(runtime, state):
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    return canonical['todos'] if canonical is not None else parse_active_state_todos(state.read_text(encoding='utf-8'))['agent_todos']['items']

def result_facts(row):
    # A terminal phase has its own receipt pointer; immutable result facts stay.
    return {key: row.get(key) for key in ('todo_id', 'status', 'evidence', 'completed_at', 'completion_turn_key')}

def add_obligation(registry, validator=None):
    validation = {} if validator is None else {'validation_command_json': json.dumps([sys.executable, str(validator)]), 'validation_timeout_seconds': 10}
    result = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role='agent',
        text='Verify the newly delivered dependency before the next product step.',
        task_class='advancement_task', claimed_by=AGENT_ID,
        continuation_policy='same_agent_non_delivery', **validation)
    return result['todo_id']

def journal(runtime):
    paths = [p for p in (runtime / 'goals' / GOAL_ID / 'turns').glob('*.json') if not p.name.endswith('.holder.json')]
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding='utf-8'))

def resume(args, original, *, retry=False):
    argv = [sys.executable, '-m', 'loopx.cli', *args, '--resume-turn-key', original['turn_key']]
    if retry:
        argv += ['--retry-failed-turn']
    result = subprocess.run(argv, capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value['status'] == 'committed' and value['resume_turn_key'] == original['turn_key'], value
    return value

def assert_once(project, runtime):
    counts = _work_counts(project)
    assert counts['implementation.txt'] == 1 and counts['validation.txt'] == 1, counts
    effects = _business_rows(runtime)
    assert effects['candidate_verified'] == 1 and effects['quota_slot_spent'] == 1, effects

def business_snapshot(project, runtime):
    return {'counts': _work_counts(project), 'effects': dict(_business_rows(runtime)),
            'candidate': (project / 'candidate.txt').read_bytes()}

def test_negative_oracle_sentinels(tmp_path):
    # The actual counter oracle must reject duplicated persisted work, not just a missing file.
    candidate = tmp_path / 'candidate.txt'
    candidate.write_text('valid', encoding='utf-8')
    (tmp_path / 'implementation.txt').write_text('1\n1\n', encoding='utf-8')
    (tmp_path / 'validation.txt').write_text('1\n', encoding='utf-8')
    with pytest.raises(AssertionError):
        counts = _work_counts(tmp_path)
        assert counts['implementation.txt'] == counts['validation.txt'] == 1
    before = {'quota_slot_spent': 1, 'candidate_verified': 1}
    broken = {**before, 'quota_slot_spent': 2}
    with pytest.raises(AssertionError):
        assert before == broken
    original = {'todo_id': 'fixture-original', 'status': 'done', 'evidence': 'valid',
                'completed_at': '2026-01-01T00:00:00Z', 'completion_turn_key': 'original-native-identity',
                'completion_receipt_id': 'ordinary-phase'}
    terminal = {**original, 'completion_receipt_id': 'terminal-phase'}
    assert result_facts(original) == result_facts(terminal)
    for key in ('evidence', 'completed_at', 'completion_turn_key'):
        damaged = {**terminal, key: 'replaced'}
        with pytest.raises(AssertionError):
            assert result_facts(original) == result_facts(damaged)

@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('scenario', SCENARIOS)
def test_default_terminal_frontier(provider, scenario, tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    if scenario in {'ordinary_successor', 'legal_no_followup', 'changed_during_direction'}:
        registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
        control = _control(registry)
        guard = json.loads(control.should_run())
        identity = guard['delivery_result_context']['settlement_identity']
        options = {'next_agent_todo': 'Verify the remaining required dependency.'} if scenario == 'ordinary_successor' else {'no_follow_up': True}
        first = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options))
        assert first.get('stage') == 'direction_pending' and first['settlement_identity'] == identity, first
        original_result = next(r for r in rows(runtime, state) if r['todo_id'] == todo)
        assert original_result['status'] == 'done'
        context = first['context']
        if scenario == 'changed_during_direction':
            new_todo = add_obligation(registry)
            before = (_business_rows(runtime), original_result['completion_receipt_id'])
            stale = json.loads(control.complete_task(todo, AGENT_ID, 'Candidate verified.', **options,
                read_context_id=context['read_context_id'], vision_unchanged_reason='The old frontier is unchanged.'))
            assert stale.get('ok') is False, stale
            current_result = next(r for r in rows(runtime, state) if r['todo_id'] == todo)
            assert before == (_business_rows(runtime), current_result['completion_receipt_id'])
            context = json.loads(_control(registry).review_task_vision(todo, AGENT_ID))
            assert context['read_context_id'] != first['context']['read_context_id']
            assert new_todo in json.dumps(context['basis']), 'Current Agent must receive the real new obligation'
        finished = json.loads(_control(registry).complete_task(todo, AGENT_ID, 'Candidate verified.', **options,
            read_context_id=context['read_context_id'],
            vision_unchanged_reason='The accepted candidate is preserved; verify all remaining current dependencies.'))
        assert finished.get('ok') is True and finished.get('settlement_complete') is True, finished
        assert finished['settlement_identity'] == identity
        assert finished.get('goal_completion_certified') is not True
        assert _business_rows(runtime)['quota_slot_spent'] == 1
        final = rows(runtime, state)
        assert result_facts(next(r for r in final if r['todo_id'] == todo)) == result_facts(original_result)
        if scenario == 'legal_no_followup':
            assert len(final) == 1, 'Legal closeout must not manufacture a successor'
        else:
            assert any(r['status'] == 'open' for r in final), 'Real remaining work must stay actionable'
        return

    fault = 'spend' if scenario == 'changed_before_terminal' else 'terminal'
    state, project, args, injection = _managed_recovery_call(provider, fault, tmp_path, monkeypatch)
    lost = subprocess.run([sys.executable, '-c', injection, *args], capture_output=True, text=True, timeout=150)
    assert lost.returncode == 97, lost.stdout + lost.stderr
    original = journal(runtime)
    assert original['host_result']['result_kind'] == 'validated_completion'
    registry = Path(args[args.index('--registry') + 1])
    original_rows = rows(runtime, state)
    original_todo = next(r['todo_id'] for r in original_rows if r['status'] == 'done')
    original_result_facts = result_facts(next(r for r in original_rows if r['todo_id'] == original_todo))
    if scenario != 'terminal_loss_replay':
        (project / 'new-dependency.txt').write_text('delivered-v2', encoding='utf-8')
        verifier = project / 'verify-new-dependency.py'
        verifier.write_text("from pathlib import Path\n"
            "assert Path('new-dependency.txt').read_text() == 'delivered-v2'\n"
            "assert Path('new-proof.txt').read_text() == 'delivered-v2 verified'\n"
            "with Path('new-validation.txt').open('a') as f: f.write('1\\n')\n", encoding='utf-8')
        new_todo = add_obligation(registry, verifier)
    else:
        new_todo = None
    before = business_snapshot(project, runtime)
    finished = resume(args, original, retry=True)
    assert_once(project, runtime)
    if scenario == 'changed_before_terminal' and _work_counts(project)['direction.txt'] > before['counts']['direction.txt']:
        assert _work_counts(project)['direction.txt'] == before['counts']['direction.txt'] + 1
        inputs = [json.loads(line) for line in (project / 'direction-inputs.jsonl').read_text(encoding='utf-8').splitlines()]
        assert new_todo in json.dumps(inputs[-1]['basis'])
    else:
        assert business_snapshot(project, runtime) == before, 'Historical terminal recovery must not repeat settled effects'
    accepted = next(r for r in rows(runtime, state) if r['todo_id'] == original_todo)
    assert result_facts(accepted) == original_result_facts
    after = business_snapshot(project, runtime)
    resume(args, original)
    assert business_snapshot(project, runtime) == after
    if new_todo:
        assert next(r for r in rows(runtime, state) if r['todo_id'] == new_todo)['status'] == 'open'
    if new_todo:
        caller = _control(registry)
        guard = json.loads(caller.should_run())
        assert guard['should_run'] is True and guard['selected_todo']['todo_id'] == new_todo, guard
        if guard['interaction_contract']['agent_channel'].get('selection_required') is True:
            # A displayed candidate is not admission. Execute the owner-returned
            # same-Turn selection contract, never invent another work identity.
            assert guard['interaction_contract']['cli_channel']['selection_command']['schema_version'] == 'action_selection_cli_command_v1'
            selection_turn = guard['heartbeat_receipt']['turn_instance_id']
            current_args, legacy_args = caller.should_run_args(GOAL_ID, AGENT_ID)
            suffix = ['--todo-id', new_todo, '--turn-instance-id', selection_turn]
            guard = json.loads(caller.run_cli([*current_args, *suffix], legacy_args=[*legacy_args, *suffix]))
            assert guard['heartbeat_receipt']['turn_instance_id'] == selection_turn
        assert guard['interaction_contract']['agent_channel']['delivery_allowed'] is True, guard
        assert guard['selected_todo']['todo_id'] == new_todo
        assert business_snapshot(project, runtime) == after, 'New work admission must not resettle old work'
        assert (project / 'new-dependency.txt').read_text() == 'delivered-v2'
        assert not (project / 'new-proof.txt').exists()
        (project / 'new-proof.txt').write_text('delivered-v2 verified', encoding='utf-8')
        first_new = json.loads(_control(registry).complete_task(new_todo, AGENT_ID,
            'The new delivered dependency was actually read and independently validated.', no_follow_up=True))
        assert first_new.get('ok') is True, first_new
        new_identity = first_new['settlement_identity']
        assert new_identity['todo_id'] == new_todo and new_identity['turn_instance_id'] != original['turn_key']
        if first_new.get('stage') == 'direction_pending':
            current = first_new['context']
            assert new_todo in json.dumps(current['basis'])
            # A required vision outcome cannot be discharged by an unchanged
            # reason alone, even after the new result's independent validator.
            denied = json.loads(_control(registry).complete_task(new_todo, AGENT_ID,
                'The new delivered dependency was actually read and independently validated.', no_follow_up=True,
                read_context_id=current['read_context_id'],
                vision_unchanged_reason='The latest new dependency is verified; retain prior accepted results and only close this work.'))
            assert denied['ok'] is False and denied['completed'] is True, denied
            assert denied['settlement']['failed_stage'] == 'durable_writeback'
            assert 'typed semantic delta' in denied['settlement']['reason']
            assert _business_rows(runtime)['quota_slot_spent'] == 1, 'Rejected direction cannot spend the new Turn'
            import hashlib
            proof_refs = [f'todo:{new_todo}'] + [
                f'file:{name}#sha256:{hashlib.sha256((project/name).read_bytes()).hexdigest()}'
                for name in ('new-dependency.txt', 'new-proof.txt')]
            vision = {'schema_version': 'goal_vision_replan_contract_v0',
                'state': 'vision_patch_proposed', 'vision_patch': {
                    'vision_summary': 'Verify the delivered dependency while preserving prior accepted results.',
                    'acceptance_summary': 'The new dependency was read and its independent validator passed; retain the original result and settlement identities.'},
                'path_delta': {'outcome': 'continue',
                    'prior_assumption': 'The original terminal route had no delivered follow-up dependency.',
                    'observed_reality': 'A new dependency and proof were read, and its native completion validator passed.',
                    'retained': ['Prior accepted results and all original settlement identities.'],
                    'changed': ['Include the independently verified dependency in the current direction.'],
                    'evidence_refs': proof_refs}}
            new_finished = json.loads(_control(registry).complete_task(new_todo, AGENT_ID,
                'The new delivered dependency was actually read and independently validated.', no_follow_up=True,
                read_context_id=current['read_context_id'], agent_vision=vision))
        else:
            new_finished = first_new
        assert new_finished.get('ok') is True and new_finished.get('settlement_complete') is True, new_finished
        assert new_finished['settlement_identity'] == new_identity
        assert (project / 'new-validation.txt').read_text().splitlines() == ['1']
        assert _work_counts(project) == after['counts'] and (project / 'candidate.txt').read_bytes() == after['candidate']
        effects = _business_rows(runtime)
        assert effects['candidate_verified'] == 1 and effects['quota_slot_spent'] == 2
        final_rows = rows(runtime, state)
        assert result_facts(next(r for r in final_rows if r['todo_id'] == original_todo)) == original_result_facts
        assert next(r for r in final_rows if r['todo_id'] == new_todo)['status'] == 'done'
        assert new_finished.get('goal_completion_certified') is not True

