"""Default managed original-Turn recovery through real isolated authority stores."""
from __future__ import annotations

from collections import Counter
import json
import re
import subprocess
import sys

import pytest

from loopx.todos import update_goal_todo
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.projection_document import recovered_todo_projection_skeleton
from loopx.control_plane.goals.checkpoint_context_io import (
    CheckpointReadContextRejected, _checkpoint_effect,
)
from loopx.control_plane.quota.settlement import SettlementIdentity
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import GOAL_ID, AGENT_ID


def _managed_recovery_call(provider, fault, tmp_path, monkeypatch):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=todo, role='agent',
        agent_id=AGENT_ID, no_followup=True)
    project = state.parent
    host = project / 'host.py'
    host.write_text('''import json,pathlib,sys
r=json.load(sys.stdin)
def count(name):
    with pathlib.Path(name).open('a') as f: f.write('1\\n')
if r.get('phase') == 'checkpoint_direction':
    c=r['checkpoint_context']; assert c['basis']['todo']['status']=='done'
    count('direction.txt')
    with pathlib.Path('direction-inputs.jsonl').open('a') as f: f.write(json.dumps(c)+'\\n')
    if STALE and len(pathlib.Path('direction.txt').read_text().splitlines())==1:
        p=pathlib.Path(STATE); p.write_text(p.read_text()+'\\n## Acceptance\\nVerify the revised obligation.\\n')
    if STALE and pathlib.Path('direction.txt').read_text().count('1')==2:
        assert 'revised obligation' in json.dumps(c['basis'])
    adopted='revised obligation' in json.dumps(c['basis']).lower()
    json.dump({'read_context_id':c['read_context_id'],'vision_unchanged_reason':'Current obligations preserve the route; revised='+str(adopted)},sys.stdout)
else:
    assert not pathlib.Path('candidate.txt').exists()
    pathlib.Path('candidate.txt').write_text('valid'); count('implementation.txt')
    json.dump({'schema_version':'loopx_turn_result_v0','turn_key':r['turn_key'],
        'result_kind':'validated_completion','completed_phases':['host_execute','typed_result'],
        'classification':'candidate_verified','recommended_action':'Read remaining work.',
        'next_action':'Read remaining work.','delivery_batch_scale':'implementation',
        'delivery_outcome':'outcome_progress','summary':'Candidate verified.',
        'vision_unchanged_reason':'Old pre-result judgment.'},sys.stdout)
'''.replace('STALE', repr(fault == 'stale')).replace('STATE', repr(str(state))), encoding='utf-8')
    validator = project / 'verify.py'
    validator.write_text("import pathlib; assert pathlib.Path('candidate.txt').read_text()=='valid'\n"
        "with pathlib.Path('validation.txt').open('a') as f: f.write('1\\n')", encoding='utf-8')
    args = ['--registry', str(registry), '--format', 'json', 'turn', 'run-once', '--host', 'generic-cli',
        '--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--project', str(project),
        '--host-adapter-command-json', json.dumps([sys.executable, str(host)]),
        '--validation-command-json', json.dumps([sys.executable, str(validator)]),
        '--scan-root', str(project), '--no-global-sync', '--execute']
    injection = '''import os,sys
from loopx.cli import main
from loopx.cli_commands import turn_run_once as tr
from loopx.control_plane.turn_driver import checkpoint_continuation as cp
FAULT=SELECTED
if FAULT in {'result','terminal'}:
    original=tr.write_turn_validated_completion
    def lose(*a,**kw):
        value=original(*a,**kw)
        if value.get('ok') and bool(kw.get('no_followup'))==(FAULT=='terminal'): os._exit(97)
        return value
    tr.write_turn_validated_completion=lose
elif FAULT=='read':
    original=cp.read_checkpoint_context
    def lose(*a,**kw):
        original(*a,**kw); os._exit(97)
    cp.read_checkpoint_context=lose
elif FAULT=='decision':
    original=cp.CurrentAgentCheckpoint._save
    def lose(self,path,receipt):
        original(self,path,receipt)
        if receipt.get('current_agent_decision'): os._exit(97)
    cp.CurrentAgentCheckpoint._save=lose
elif FAULT=='dispatch':
    original=cp.CurrentAgentCheckpoint._save
    def lose(self,path,receipt):
        original(self,path,receipt)
        if receipt.get('direction_host_attempt_count') and not receipt.get('current_agent_decision'): os._exit(97)
    cp.CurrentAgentCheckpoint._save=lose
elif FAULT in {'response','bound_response'}:
    original=cp._checkpoint_effect
    def lose(method,params):
        response=params.get('phase')=='direction_response'
        if response and FAULT=='response': os._exit(97)
        value=original(method,params)
        if response and value.get('ok') and FAULT=='bound_response': os._exit(97)
        return value
    cp._checkpoint_effect=lose
elif FAULT.startswith('append_'):
    from pathlib import Path
    from loopx.control_plane.goals import checkpoint_context_io as context_io
    from tests.control_plane.checkpoint_process import start_probe
    original=context_io.effect_runtime_result
    def lose(method,params,**kw):
        if method!='goal.checkpoint_read_context.commit': return original(method,params,**kw)
        barrier=Path(PROJECT)/'append-fault'; barrier.mkdir(); (barrier/'release').touch()
        child=start_probe({'mode':'checkpoint','provider':PROVIDER,'barrier':str(barrier.resolve()),
            'params':params,'fault':FAULT.removeprefix('append_')})
        out,err=child.communicate(timeout=25)
        assert child.returncode==86, out+err
        os._exit(97)
    context_io.effect_runtime_result=lose
elif FAULT=='checkpoint':
    original=tr.refresh_state_run
    def lose(*a,**kw):
        value=original(*a,**kw)
        if value.get('ok') and value.get('vision_checkpoint',{}).get('satisfied'): os._exit(97)
        return value
    tr.refresh_state_run=lose
elif FAULT=='spend':
    original=tr.spend_quota_slot
    def lose(*a,**kw):
        value=original(*a,**kw)
        if value.get('ok'): os._exit(97)
        return value
    tr.spend_quota_slot=lose
raise SystemExit(main(sys.argv[1:]))
'''.replace('SELECTED', repr(fault)).replace('PROVIDER', repr(provider)).replace('PROJECT', repr(str(project)))
    return state, project, args, injection


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('fault', ['result', 'read', 'decision', 'checkpoint', 'spend', 'terminal', 'stale'])
def test_default_managed_cold_recovery_resumes_only_unfinished_checkpoint(provider, fault, tmp_path, monkeypatch):
    state, project, args, injection = _managed_recovery_call(provider, fault, tmp_path, monkeypatch)
    lost = subprocess.run([sys.executable, '-c', injection, *args], capture_output=True, text=True)
    assert lost.returncode == (1 if fault == 'stale' else 97), lost.stdout + lost.stderr
    journal_path = next(path for path in (tmp_path / 'runtime/goals' / GOAL_ID / 'turns').glob('*.json')
        if not path.name.endswith('.holder.json'))
    journal = json.loads(journal_path.read_text())
    assert journal['host_result']['result_kind'] == 'validated_completion'
    before_rows = [json.loads(line) for line in (tmp_path / 'runtime/goals' / GOAL_ID / 'runs/index.jsonl').read_text().splitlines()]
    if fault in {'result','read','decision','stale'}:
        assert not any(row.get('classification') == 'quota_slot_spent' for row in before_rows)
    recovery = subprocess.run([sys.executable, '-m', 'loopx.cli', *args, '--resume-turn-key', journal['turn_key'],
        '--retry-failed-turn'], capture_output=True, text=True)
    assert recovery.returncode == 0, recovery.stdout + recovery.stderr
    result = json.loads(recovery.stdout)
    assert result['status'] == 'committed' and result['resume_turn_key'] == journal['turn_key'], result
    assert len((project / 'implementation.txt').read_text().splitlines()) == 1
    assert len((project / 'validation.txt').read_text().splitlines()) == 1
    assert len((project / 'direction.txt').read_text().splitlines()) == (2 if fault == 'stale' else 1)
    rows = [json.loads(line) for line in (tmp_path / 'runtime/goals' / GOAL_ID / 'runs/index.jsonl').read_text().splitlines()]
    assert sum(row.get('classification') == 'quota_slot_spent' for row in rows) == 1
    assert sum(row.get('classification') == 'candidate_verified' for row in rows) == 1
    assert 'no_followup=true' in state.read_text()
    replay = subprocess.run([sys.executable, '-m', 'loopx.cli', *args, '--resume-turn-key', journal['turn_key']],
        capture_output=True, text=True)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert len((project / 'direction.txt').read_text().splitlines()) == (2 if fault == 'stale' else 1)


def _business_rows(runtime):
    rows = [json.loads(line) for line in
            (runtime / 'goals' / GOAL_ID / 'runs/index.jsonl').read_text().splitlines()]
    return Counter(row['classification'] for row in rows
                   if row.get('classification') in {'candidate_verified', 'quota_slot_spent'})


def _work_counts(project):
    return {name: len((project / name).read_text().splitlines())
            if (project / name).exists() else 0
            for name in ('implementation.txt', 'validation.txt', 'direction.txt')}


def _provider_revision(runtime):
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    return snapshot['provider_revision'] if snapshot is not None else None


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('fault', [
    'result', 'read', 'dispatch', 'response', 'bound_response',
    'decision', 'checkpoint', 'spend', 'terminal',
])
@pytest.mark.parametrize('source_loss', ['missing', 'todo_only'])
def test_original_turn_restores_real_goal_after_response_and_source_loss(
        provider, fault, source_loss, tmp_path, monkeypatch):
    state, project, args, injection = _managed_recovery_call(provider, fault, tmp_path, monkeypatch)
    lost = subprocess.run([sys.executable, '-c', injection, *args], capture_output=True, text=True)
    assert lost.returncode == 97, lost.stdout + lost.stderr
    runtime = tmp_path / 'runtime'
    journal_path = next(path for path in (runtime / 'goals' / GOAL_ID / 'turns').glob('*.json')
                        if not path.name.endswith('.holder.json'))
    journal = json.loads(journal_path.read_text())
    assert journal['host_result']['result_kind'] == 'validated_completion'
    original_turn = journal['turn_key']
    actual_source = state.read_bytes()
    before_effects, before_work = _business_rows(runtime), _work_counts(project)
    before_revision = _provider_revision(runtime)
    if source_loss == 'missing':
        state.unlink()
    else:
        state.write_text(recovered_todo_projection_skeleton(GOAL_ID), encoding='utf-8')

    resume_args = [*args, '--resume-turn-key', original_turn, '--retry-failed-turn']
    incomplete = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args],
                                capture_output=True, text=True)
    assert incomplete.returncode in {0, 1}, incomplete.stdout + incomplete.stderr
    if incomplete.returncode == 0:
        observed = json.loads(incomplete.stdout)
        # Historical terminal replay may finish its journal. Incomplete intent
        # never authorizes completion of a previously unfinished phase.
        assert fault == 'terminal' and observed['status'] == 'committed', observed
        assert observed['resume_turn_key'] == original_turn
    after_effects = _business_rows(runtime)
    assert after_effects['candidate_verified'] == before_effects['candidate_verified']
    # The successful indexed checkpoint authorizes its original owed spend,
    # not a new judgment or terminal write against incomplete Goal intent.
    allowed_spends = {before_effects['quota_slot_spent']}
    if fault == 'checkpoint':
        assert before_effects['candidate_verified'] == 1
        allowed_spends.add(1)
    assert after_effects['quota_slot_spent'] in allowed_spends
    assert _provider_revision(runtime) == before_revision
    assert _work_counts(project) == before_work
    assert (project / 'candidate.txt').read_text() == 'valid'

    # Restore the actual source captured at loss, including its committed Todo
    # projection. Never fabricate Goal intent or restore an earlier lifecycle.
    state.write_bytes(actual_source)
    restored = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args],
                              capture_output=True, text=True)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    observed = json.loads(restored.stdout)
    assert observed['status'] == 'committed' and observed['resume_turn_key'] == original_turn
    # A response lost before durable decision persistence requires one new
    # judgment. It must never repeat implementation, validation or settlement.
    direction_calls = 2 if fault in {'response', 'bound_response'} else 1
    assert _work_counts(project) == {
        'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': direction_calls}
    assert _business_rows(runtime) == {'candidate_verified': 1, 'quota_slot_spent': 1}
    assert 'no_followup=true' in state.read_text()

    replay = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args],
                            capture_output=True, text=True)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert _business_rows(runtime) == {'candidate_verified': 1, 'quota_slot_spent': 1}
    assert _work_counts(project) == {
        'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': direction_calls}


def _original_checkpoint_receipt(runtime):
    receipts = [json.loads(path.read_text()) for path in
                (runtime / 'goals' / GOAL_ID / 'checkpoint-contexts').glob('*.json')]
    return next(receipt for receipt in receipts if receipt.get('purpose') == 'first_delivery')


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize(('fault', 'change'), [
    ('dispatch', 'none'), ('dispatch', 'related'),
    ('response', 'none'), ('response', 'related'),
    ('bound_response', 'none'), ('bound_response', 'related'), ('bound_response', 'display'),
])
def test_unpersisted_direction_response_rejudges_without_repeating_work(
        provider, fault, change, tmp_path, monkeypatch):
    state, project, args, injection = _managed_recovery_call(provider, fault, tmp_path, monkeypatch)
    lost = subprocess.run([sys.executable, '-c', injection, *args], capture_output=True, text=True)
    assert lost.returncode == 97, lost.stdout + lost.stderr
    runtime = tmp_path / 'runtime'
    journal_path = next(path for path in (runtime / 'goals' / GOAL_ID / 'turns').glob('*.json')
                        if not path.name.endswith('.holder.json'))
    journal = json.loads(journal_path.read_text())
    receipt = _original_checkpoint_receipt(runtime)
    old_key = receipt['read_context_id']
    assert 'current_agent_decision' not in receipt
    assert journal['host_result']['result_kind'] == 'validated_completion'
    before_calls = 0 if fault == 'dispatch' else 1
    assert _work_counts(project) == {'implementation.txt': 1, 'validation.txt': 1,
                                    'direction.txt': before_calls}
    assert _business_rows(runtime) == Counter()
    if change == 'related':
        state.write_text(state.read_text() + '\n## Acceptance\nVerify the revised obligation.\n', encoding='utf-8')
    elif change == 'display':
        before = state.read_text()
        changed = re.sub(r'^updated_at:.*$', 'updated_at: 2026-10-10', before, flags=re.MULTILINE)
        if changed == before:
            changed = before.replace('---\n', '---\nupdated_at: 2026-10-10\n', 1)
        assert changed != before, 'Display-only update must actually occur'
        state.write_text(changed, encoding='utf-8')
    resume_args = [*args, '--resume-turn-key', journal['turn_key'], '--retry-failed-turn']
    recovered = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args], capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    result = json.loads(recovered.stdout)
    assert result['status'] == 'committed' and result['resume_turn_key'] == journal['turn_key']
    inputs = [json.loads(line) for line in (project / 'direction-inputs.jsonl').read_text().splitlines()]
    assert len(inputs) == before_calls + 1
    assert ('revised obligation' in json.dumps(inputs[-1]['basis']).lower()) == (change == 'related')
    assert (inputs[-1]['read_context_id'] != old_key) == (change == 'related')
    final_receipt = _original_checkpoint_receipt(runtime)
    assert final_receipt['current_agent_binding']['read_context_id'] == inputs[-1]['read_context_id']
    assert final_receipt['current_agent_decision']['vision_unchanged_reason'].endswith(str(change == 'related'))
    after_work, after_effects = _work_counts(project), _business_rows(runtime)
    assert after_work == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': before_calls + 1}
    assert after_effects == Counter({'candidate_verified': 1, 'quota_slot_spent': 1})
    replay = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args], capture_output=True, text=True)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert _work_counts(project) == after_work and _business_rows(runtime) == after_effects


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
@pytest.mark.parametrize('fault', ['before_artifacts', 'after_json', 'after_markdown', 'after_index'])
def test_default_managed_native_append_recovery_never_treats_unknown_as_absent(
        provider, fault, tmp_path, monkeypatch):
    _, project, args, injection = _managed_recovery_call(provider, 'append_' + fault, tmp_path, monkeypatch)
    lost = subprocess.run([sys.executable, '-c', injection, *args], capture_output=True, text=True)
    assert lost.returncode == 97, lost.stdout + lost.stderr
    runtime = tmp_path / 'runtime'
    journal_path = next(path for path in (runtime / 'goals' / GOAL_ID / 'turns').glob('*.json')
                        if not path.name.endswith('.holder.json'))
    original_turn = json.loads(journal_path.read_text())['turn_key']
    identity = SettlementIdentity.from_runtime_payload(_original_checkpoint_receipt(runtime)['identity'])
    original_work = _work_counts(project)
    assert original_work == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 1}
    resume_args = [*args, '--resume-turn-key', original_turn, '--retry-failed-turn']
    recovered = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args], capture_output=True, text=True)
    if fault in {'after_json', 'after_markdown'}:
        assert recovered.returncode != 0, recovered.stdout + recovered.stderr
        for candidate in (identity, SettlementIdentity(goal_id=GOAL_ID, agent_id=AGENT_ID,
                todo_id=identity.todo_id, turn_instance_id='foreign-turn')):
            with pytest.raises(CheckpointReadContextRejected) as error:
                _checkpoint_effect('goal.checkpoint_read_context.inspect_attempt', {
                    'runtime_root': str(runtime.resolve()), 'identity': candidate.as_dict(),
                    'check_other_attempts': True,
                })
            assert error.value.code == 'checkpoint_commit_unknown'
        assert _business_rows(runtime) == Counter()
        again = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args], capture_output=True, text=True)
        assert again.returncode != 0
        assert _work_counts(project) == original_work and _business_rows(runtime) == Counter()
    else:
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        result = json.loads(recovered.stdout)
        assert result['status'] == 'committed' and result['resume_turn_key'] == original_turn
        assert _work_counts(project) == original_work
        after_effects = _business_rows(runtime)
        assert after_effects == Counter({'candidate_verified': 1, 'quota_slot_spent': 1})
        replay = subprocess.run([sys.executable, '-m', 'loopx.cli', *resume_args], capture_output=True, text=True)
        assert replay.returncode == 0, replay.stdout + replay.stderr
        assert _work_counts(project) == original_work and _business_rows(runtime) == after_effects
