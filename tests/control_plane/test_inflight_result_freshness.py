"""In-flight writebacks retain the original work basis on real providers."""
from __future__ import annotations

import json

import pytest

from tests.control_plane.test_default_result_freshness import _default_fixture
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count,
)


def _admit(provider, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch)
    rc, guard = _run_cli(registry, runtime, 'quota', 'should-run', '--codex-app',
        '--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--todo-id', TODO_ID, cwd=project)
    assert rc == 0, guard
    context = guard['delivery_result_context']
    args = ('refresh-state', '--goal-id', GOAL_ID, '--agent-id', AGENT_ID,
        '--todo-id', TODO_ID, '--turn-instance-id', context['settlement_identity']['turn_instance_id'],
        '--classification', 'verified_inflight_candidate', '--delivery-outcome', 'outcome_progress',
        '--delivery-batch-scale', 'implementation', '--delivery-boundary', 'in_flight_continuation',
        '--delivery-workspace-path', str(project), '--no-global-sync', '--suppress-external-sinks')
    return project, runtime, registry, state, context, args


def _runs(runtime):
    return {p.name: p.read_bytes() for p in (runtime / 'goals' / GOAL_ID / 'runs').glob('*') if p.is_file() and p.suffix in {'.json', '.jsonl', '.md'}}


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('change', ['revised', 'missing'])
def test_supplement_direction_keeps_primary_goal_source_fresh(provider, change, tmp_path, monkeypatch):
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch)
    owner = project / 'GOAL.md'
    owner.write_text('Initial independent owner requirement.\n', encoding='utf-8')
    document = json.loads(registry.read_text())
    document['goals'][0]['authority_sources'] = [{'kind': 'goal_doc', 'path': 'GOAL.md'}]
    registry.write_text(json.dumps(document), encoding='utf-8')
    identity = ('--goal-id', GOAL_ID, '--agent-id', AGENT_ID, '--todo-id', TODO_ID)
    rc, guard = _run_cli(registry, runtime, 'quota', 'should-run', '--codex-app', *identity, cwd=project)
    assert rc == 0, guard
    turn = guard['delivery_result_context']['settlement_identity']['turn_instance_id']
    args = ('refresh-state', *identity, '--turn-instance-id', turn,
        '--classification', 'verified_progress', '--delivery-outcome', 'outcome_progress',
        '--delivery-batch-scale', 'implementation', '--delivery-boundary', 'semantic_closeout',
        '--delivery-workspace-path', str(project), '--no-global-sync', '--suppress-external-sinks')
    rc, result = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and result['vision_checkpoint']['decision'] == 'missing_required', result
    read = ('checkpoint-context', *identity, '--turn-instance-id', turn)
    rc, context = _run_cli(registry, runtime, *read, cwd=project)
    assert rc == 0, context
    assert 'Initial independent owner requirement.' in context['basis']['goal']['prose']
    assert context['basis']['goal']['primary_documents']
    before = _runs(runtime)
    if change == 'missing':
        owner.unlink()
    else:
        owner.write_text('Revised independent owner requirement.\n', encoding='utf-8')
    decision = ('--vision-summary', 'Continue the freshly checked requirement route.',
        '--vision-acceptance', 'The retained candidate and remaining work satisfy the current requirements.')
    rc, rejected = _run_cli(registry, runtime, *args, *decision,
        '--checkpoint-read-context', context['read_context_id'], cwd=project)
    assert rc == 1, rejected
    assert _runs(runtime) == before and _spend_run_count(runtime) == 0
    if change == 'missing':
        owner.write_text('Revised independent owner requirement.\n', encoding='utf-8')
    rc, current = _run_cli(registry, runtime, *read, cwd=project)
    assert rc == 0 and current['read_context_id'] != context['read_context_id'], current
    assert 'Revised independent owner requirement.' in current['basis']['goal']['prose']
    commit = (*args, *decision, '--checkpoint-read-context', current['read_context_id'])
    rc, settled = _run_cli(registry, runtime, *commit, cwd=project)
    assert rc == 0 and settled['vision_checkpoint']['satisfied'], settled
    after = _runs(runtime)
    owner.unlink()
    rc, replay = _run_cli(registry, runtime, *commit, cwd=project)
    assert rc == 0 and replay['idempotent_replay'], replay
    assert _runs(runtime) == after and _spend_run_count(runtime) == 0


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('vision', [False, True])
def test_inflight_rejects_changed_basis_then_revalidates_same_turn(provider, vision, tmp_path, monkeypatch):
    project, runtime, registry, state, context, args = _admit(provider, tmp_path, monkeypatch)
    if vision:
        args += ('--vision-summary', 'Continue the originally accepted reporting route.',
                 '--vision-acceptance', 'The retained candidate must satisfy the current owner requirement.')
    state.write_text(state.read_text(encoding='utf-8') + '\n## Requirement B\nUse the revised reporting contract.\n', encoding='utf-8')
    before = _runs(runtime)
    contexts = {p: p.read_bytes() for p in (runtime / 'goals' / GOAL_ID / 'checkpoint-contexts').glob('*.json')}
    original_state = state.read_bytes()
    rc, rejected = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 1 and rejected.get('error_code') == 'checkpoint_read_context_stale', json.dumps(rejected, ensure_ascii=False)
    assert _runs(runtime) == before
    assert state.read_bytes() == original_state
    assert all(p.read_bytes() == data for p, data in contexts.items())
    assert _spend_run_count(runtime) == 0
    rc, reread = _run_cli(registry, runtime, 'checkpoint-context', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
        '--turn-instance-id', context['settlement_identity']['turn_instance_id'],
        '--purpose', 'delivery_result', cwd=project)
    assert rc == 0 and reread['read_context_id'] != context['read_context_id'], reread
    assert 'Requirement B' in json.dumps(reread['basis'])
    assert 'in-flight progress' in reread['instructions']
    rc, committed = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and committed['ok'], committed
    assert committed['vision_checkpoint']['required'] is False
    assert committed['vision_checkpoint']['decision'] == ('patched' if vision else 'not_required')
    assert 'read_context' not in committed['vision_checkpoint'], 'A work check is not a direction decision'
    assert committed['result_work_context']['read_context_id'] == reread['read_context_id']
    after = _runs(runtime)
    state.write_text(state.read_text(encoding='utf-8') + '\n## Requirement C\nA later owner change.\n', encoding='utf-8')
    rc, replay = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and replay.get('idempotent_replay') is True, replay
    assert replay['result_work_context'] == committed['result_work_context']
    assert _runs(runtime) == after
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_inflight_unchanged_basis_does_not_require_new_direction(provider, tmp_path, monkeypatch):
    project, runtime, registry, _, context, args = _admit(provider, tmp_path, monkeypatch)
    rc, committed = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0, committed
    assert committed['vision_checkpoint']['required'] is False
    assert committed['vision_checkpoint']['decision'] == 'not_required'
    assert committed['result_work_context']['read_context_id'] == context['read_context_id']
    assert committed['result_work_context']['purpose'] == 'delivery_result'
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_inflight_missing_work_receipt_cannot_mint_a_new_read(provider, tmp_path, monkeypatch):
    from loopx.control_plane.goals.checkpoint_context_io import _receipt_path
    from loopx.control_plane.quota.settlement import SettlementIdentity
    project, runtime, registry, _, context, args = _admit(provider, tmp_path, monkeypatch)
    path = _receipt_path(runtime, SettlementIdentity.from_runtime_payload(context['settlement_identity']), 'delivery_result')
    path.unlink()
    before = _runs(runtime)
    rc, rejected = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 1 and rejected.get('error_code') == 'delivery_work_context_required', rejected
    assert not path.exists()
    assert _runs(runtime) == before


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_historical_inflight_admission_keeps_its_original_policy(provider, tmp_path, monkeypatch):
    from tests.control_plane.test_quota_settlement_cli import _historical_turn
    project, runtime, registry, state, context, args = _admit(provider, tmp_path, monkeypatch)
    # A synthetic persisted pre-freshness admission; never downgrade active state.
    _historical_turn(runtime, context['settlement_identity']['turn_instance_id'])
    state.write_text(state.read_text(encoding='utf-8') + '\nA later requirement.\n', encoding='utf-8')
    rc, saved = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and saved['ok'], saved
    assert 'result_work_context' not in saved
    assert saved['vision_checkpoint']['decision'] == 'not_required'


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('fault', ['before_artifacts', 'after_json', 'after_index'])
def test_inflight_native_append_failure_retains_original_work(provider, fault, tmp_path, monkeypatch):
    import subprocess
    from loopx.control_plane.goals import checkpoint_context_io as io
    from loopx.state_refresh import refresh_state_run
    from tests.control_plane.test_quota_settlement_cli import REPO_ROOT

    project, runtime, registry, state, context, args = _admit(provider, tmp_path, monkeypatch)
    barrier = tmp_path / 'barrier'
    barrier.mkdir()
    (barrier / 'release').touch()
    original_effect = io._checkpoint_effect

    def lose_native_response(method, request):
        if method != 'goal.checkpoint_read_context.commit':
            return original_effect(method, request)
        packet = {'mode': 'checkpoint', 'barrier': str(barrier), 'provider': provider,
                  'method': method, 'params': request, 'fault': fault}
        result = subprocess.run(['node', '--no-warnings', '--experimental-strip-types',
            str(REPO_ROOT / 'tests/control_plane_ts/checkpoint_commit_probe.ts')],
            input=json.dumps(packet), capture_output=True, text=True, cwd=REPO_ROOT, timeout=45)
        assert result.returncode == 86, result.stdout + result.stderr
        raise OSError('Synthetic lost native append response')

    with monkeypatch.context() as patch:
        patch.setattr(io, '_checkpoint_effect', lose_native_response)
        with pytest.raises(OSError, match='Synthetic lost native'):
            refresh_state_run(registry_path=registry, runtime_root_override=str(runtime),
                goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
                turn_instance_id=context['settlement_identity']['turn_instance_id'],
                project=None, state_file=None, classification='verified_inflight_candidate',
                recommended_action=None, delivery_outcome='outcome_progress',
                delivery_batch_scale='implementation', delivery_boundary='in_flight_continuation',
                delivery_workspace_path=project, dry_run=False, sync_global=False,
                external_delivery={'suppress': True, 'resume_key': None})
    assert _spend_run_count(runtime) == 0
    if fault == 'after_index':
        state.write_text(state.read_text(encoding='utf-8') + '\nChange after committed progress.\n', encoding='utf-8')
        committed_runs = _runs(runtime)
        rc, replay = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0 and replay['appended'] is False, replay
        assert replay['refresh_recovery']['decision'] == 'repair_receipt', replay
        assert replay['result_work_context']['read_context_id'] == context['read_context_id']
        assert _runs(runtime) == committed_runs
        rc, repeated = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0 and repeated['idempotent_replay'], repeated
        assert _runs(runtime) == committed_runs
    else:
        rc, reread = _run_cli(registry, runtime, 'checkpoint-context', '--goal-id', GOAL_ID,
            '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
            '--turn-instance-id', context['settlement_identity']['turn_instance_id'],
            '--purpose', 'delivery_result', cwd=project)
        if fault == 'after_json':
            assert rc == 1 and reread['error_code'] == 'checkpoint_commit_unknown', reread
        else:
            assert rc == 0 and reread['read_context_id'] != context['read_context_id'], reread
            rc, saved = _run_cli(registry, runtime, *args, cwd=project)
            assert rc == 0 and saved['result_work_context']['read_context_id'] == reread['read_context_id'], saved


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_inflight_native_append_rechecks_captured_goal_bytes(provider, tmp_path, monkeypatch):
    from loopx.control_plane.goals import checkpoint_context_io as io
    from loopx.state_refresh import refresh_state_run

    project, runtime, registry, state, context, _ = _admit(provider, tmp_path, monkeypatch)
    before = _runs(runtime)
    original_effect = io._checkpoint_effect

    def change_after_preview(method, request):
        if method == 'goal.checkpoint_read_context.commit':
            # An owner publication between the preliminary check and native save.
            state.write_text(state.read_text(encoding='utf-8') + '\nLate requirement B.\n', encoding='utf-8')
        return original_effect(method, request)

    monkeypatch.setattr(io, '_checkpoint_effect', change_after_preview)
    with pytest.raises(io.CheckpointReadContextRejected, match='sources changed'):
        refresh_state_run(registry_path=registry, runtime_root_override=str(runtime),
            goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            turn_instance_id=context['settlement_identity']['turn_instance_id'],
            project=None, state_file=None, classification='verified_inflight_candidate', recommended_action=None,
            delivery_outcome='outcome_progress', delivery_batch_scale='implementation',
            delivery_boundary='in_flight_continuation', delivery_workspace_path=project,
            dry_run=False, sync_global=False, external_delivery={'suppress': True, 'resume_key': None})
    # Reserving empty paths is not a committed run; the index must stay unchanged.
    assert _runs(runtime).get('index.jsonl') == before.get('index.jsonl')
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('outcome', ['outcome_progress', 'outcome_gap'])
def test_semantic_writeback_also_checks_original_work_basis(provider, outcome, tmp_path, monkeypatch):
    project, runtime, registry, state, context, args = _admit(provider, tmp_path, monkeypatch)
    args = tuple('semantic_closeout' if arg == 'in_flight_continuation' else
                 outcome if arg == 'outcome_progress' else arg for arg in args)
    if outcome == 'outcome_gap':
        args += ('--progress-result-class', 'blocked', '--progress-work-item-id', TODO_ID,
                 '--progress-blocker-id', 'blocker_fixture', '--progress-evidence-id', 'evidence_fixture')
    state.write_text(state.read_text(encoding='utf-8') + '\n## Requirement B\nUse the revised contract.\n', encoding='utf-8')
    before = _runs(runtime)
    rc, rejected = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 1 and rejected.get('error_code') == 'checkpoint_read_context_stale', json.dumps(rejected, ensure_ascii=False)
    assert _runs(runtime) == before
    rc, reread = _run_cli(registry, runtime, 'checkpoint-context', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
        '--turn-instance-id', context['settlement_identity']['turn_instance_id'], '--purpose', 'delivery_result', cwd=project)
    assert rc == 0, reread
    rc, saved = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and saved['result_work_context']['read_context_id'] == reread['read_context_id'], saved
    # The original blocked-retry cadence stays exempt from direction review,
    # while its result still passes the work-basis check.
    assert saved['vision_checkpoint']['satisfied'] is (outcome == 'outcome_gap')
    assert saved['vision_checkpoint']['decision'] == ('not_required' if outcome == 'outcome_gap' else 'missing_required')
    committed = _runs(runtime)
    rc, denied = _run_cli(registry, runtime, 'checkpoint-context', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
        '--turn-instance-id', context['settlement_identity']['turn_instance_id'], '--purpose', 'delivery_result', cwd=project)
    assert rc == 1 and denied['error_code'] == 'delivery_result_already_committed', denied
    rc, replay = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and replay['idempotent_replay'], replay
    assert _runs(runtime) == committed
    assert _spend_run_count(runtime) == 0
    if outcome == 'outcome_progress':
        # Work protection must coexist with a later direction decision on the
        # same Turn; these two indexed effects are not conflicting writes.
        rc, direction = _run_cli(registry, runtime, 'checkpoint-context', '--goal-id', GOAL_ID,
            '--agent-id', AGENT_ID, '--todo-id', TODO_ID,
            '--turn-instance-id', context['settlement_identity']['turn_instance_id'], cwd=project)
        assert rc == 0, direction
        supplement = (*args, '--checkpoint-read-context', direction['read_context_id'],
            '--vision-summary', 'Deliver the revised owner contract.',
            '--vision-acceptance', 'The retained result matches the revised contract.')
        rc, settled = _run_cli(registry, runtime, *supplement, cwd=project)
        assert rc == 0 and settled['vision_checkpoint']['satisfied'], settled
        rc, replay = _run_cli(registry, runtime, *supplement, cwd=project)
        assert rc == 0 and replay['idempotent_replay'], replay


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_each_new_turn_retains_its_own_work_basis(provider, tmp_path, monkeypatch):
    project, runtime, registry, state, context, args = _admit(provider, tmp_path, monkeypatch)
    first_turn = context['settlement_identity']['turn_instance_id']
    rc, first = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0, first
    rc, spent = _run_cli(registry, runtime, 'quota', 'spend-slot', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--todo-id', TODO_ID, '--turn-instance-id', first_turn,
        '--slots', '1', '--source', 'heartbeat', '--execute', cwd=project)
    assert rc == 0, spent
    rc, guard = _run_cli(registry, runtime, 'quota', 'should-run', '--codex-app', '--goal-id', GOAL_ID,
        '--agent-id', AGENT_ID, '--todo-id', TODO_ID, '--turn-instance-id', 'next-protected-turn', cwd=project)
    assert rc == 0, guard
    second = guard['delivery_result_context']
    assert second['read_context_id'] != context['read_context_id']
    state.write_text(state.read_text(encoding='utf-8') + '\n## Requirement B\nUse the revised contract for Turn two.\n', encoding='utf-8')
    before = _runs(runtime)
    second_args = tuple('next-protected-turn' if arg == first_turn else arg for arg in args)
    rc, rejected = _run_cli(registry, runtime, *second_args, cwd=project)
    assert rc == 1 and rejected.get('error_code') == 'checkpoint_read_context_stale', json.dumps(rejected, ensure_ascii=False)
    assert _runs(runtime) == before
    # A historical success is still replayable, but never satisfies Turn two.
    rc, replay = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and replay['idempotent_replay'], replay
    assert _runs(runtime) == before
