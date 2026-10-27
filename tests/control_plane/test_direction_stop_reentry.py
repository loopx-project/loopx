"""Explicit recovery of a retained result must finish only its direction."""
from __future__ import annotations

import json
import copy
from contextlib import nullcontext
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.control_plane.goals.checkpoint_context_io import read_checkpoint_context, _receipt_path
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.configure_goal import configure_goal
from tests.control_plane.test_default_turn_recovery import (
    _managed_recovery_call, _work_counts, _business_rows,
)
from tests.test_goal_mode_mcp_settlement import GOAL_ID


def _stopped(provider, kind, tmp_path, monkeypatch, *, stop_reason=None):
    state, project, args, _ = _managed_recovery_call(provider, 'none', tmp_path, monkeypatch)
    host = project / 'host.py'
    stop = {'result_kind': kind, **({'stop_reason': stop_reason} if stop_reason else {})}
    host.write_text(host.read_text().replace("    count('direction.txt')", "    count('direction.txt')\n"
        "    if not pathlib.Path('resume-ready').exists():\n"
        f"        json.dump({stop!r},sys.stdout); raise SystemExit(0)"), encoding='utf-8')
    command = [sys.executable, '-m', 'loopx.cli', *args]
    stopped = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert stopped.returncode == 1, stopped.stdout + stopped.stderr
    runtime = tmp_path / 'runtime'
    journal_path = next(p for p in (runtime / 'goals' / GOAL_ID / 'turns').glob('*.json')
        if not p.name.endswith('.holder.json'))
    journal = json.loads(journal_path.read_text())
    return state, project, args, command, runtime, journal_path, journal


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('kind,reread', [
    ('wait', False), ('user_action_required', False), ('iteration_failed', False), ('wait', True),
])
def test_explicit_direction_reentry_retains_original_result(provider, kind, reread, tmp_path, monkeypatch):
    _, project, args, command, runtime, journal_path, journal = _stopped(provider, kind, tmp_path, monkeypatch)
    original_result = journal['host_result']
    resume = [*command, '--resume-turn-key', journal['turn_key']]
    assert _work_counts(project) == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 1}
    assert _business_rows(runtime)['quota_slot_spent'] == 0
    (project / 'resume-ready').touch()
    implicit = subprocess.run(resume, capture_output=True, text=True, timeout=90)
    assert implicit.returncode == 1, implicit.stdout + implicit.stderr
    assert _work_counts(project)['direction.txt'] == 1
    raw_identity = journal['plan']['transaction']['settlement_plan']['identity']
    identity = SettlementIdentity(**{key: raw_identity.get(key) for key in (
        'goal_id', 'agent_id', 'todo_id', 'turn_instance_id', 'replan_obligation_id')})
    if reread:
        context = read_checkpoint_context(registry_path=Path(args[args.index('--registry') + 1]),
            runtime_root_override=str(runtime), goal_id=identity.goal_id, agent_id=identity.agent_id,
            todo_id=identity.todo_id, replan_obligation_id=identity.replan_obligation_id,
            turn_instance_id=identity.turn_instance_id, purpose='first_delivery')
        assert context['read_context_id']
        assert json.loads(_receipt_path(runtime, identity).read_text())['direction_stopped'] is True
        assert _work_counts(project)['direction.txt'] == 1
    recovered = subprocess.run([*resume, '--retry-failed-turn'], capture_output=True, text=True, timeout=90)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert json.loads(recovered.stdout)['status'] == 'committed'
    assert _work_counts(project) == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 2}
    assert _business_rows(runtime) == {'candidate_verified': 1, 'quota_slot_spent': 1}
    assert json.loads(journal_path.read_text())['host_result'] == original_result
    replay = subprocess.run(resume, capture_output=True, text=True, timeout=90)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert _work_counts(project)['direction.txt'] == 2
    assert _business_rows(runtime) == {'candidate_verified': 1, 'quota_slot_spent': 1}


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('gate', ['pause', 'stop', 'permission'])
def test_stopped_direction_rechecks_current_gate_before_reentry(provider, gate, tmp_path, monkeypatch):
    _, project, args, command, runtime, journal_path, journal = _stopped(provider, 'wait', tmp_path, monkeypatch,
        stop_reason='permission_required' if gate == 'permission' else None)
    registry = Path(args[args.index('--registry') + 1])
    before = registry.read_bytes()
    if gate == 'pause':
        assert configure_goal(registry_path=registry, goal_id=GOAL_ID, quota_compute=0, execute=True)['written']
    elif gate == 'stop':
        doc = json.loads(before)
        doc['goals'][0]['activation'] = {'schema_version': 'loopx_goal_activation_v1', 'state': 'stopped',
            'updated_at': '2026-10-11T00:00:00Z', 'reason': 'Fixture owner stopped execution.'}
        registry.write_text(json.dumps(doc), encoding='utf-8')
    (project / 'resume-ready').touch()
    retry = [*command, '--resume-turn-key', journal['turn_key'], '--retry-failed-turn']
    denied = subprocess.run(retry, capture_output=True, text=True, timeout=90)
    assert denied.returncode == 1, denied.stdout + denied.stderr
    assert _work_counts(project) == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 1}
    assert _business_rows(runtime)['quota_slot_spent'] == 0
    assert json.loads(journal_path.read_text())['host_result'] == journal['host_result']
    if gate != 'permission':
        if gate == 'pause':
            assert configure_goal(registry_path=registry, goal_id=GOAL_ID, quota_compute=1, execute=True)['written']
        else:
            registry.write_bytes(before)
        recovered = subprocess.run(retry, capture_output=True, text=True, timeout=90)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        assert _work_counts(project) == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 2}
        assert _business_rows(runtime) == {'candidate_verified': 1, 'quota_slot_spent': 1}


@pytest.mark.parametrize('kind', ['wait', 'user_action_required', 'iteration_failed'])
def test_valid_stop_supersedes_transport_failure_without_resetting_attempt_budget(kind, tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver import checkpoint_continuation as cp
    from loopx.control_plane.turn_driver.host_failure import BuiltInHostError

    identity = SimpleNamespace(goal_id='reentry-goal', as_dict=lambda: {})
    receipt = {'purpose': 'first_delivery', 'read_context_id': 'original-read',
        'decision_basis': {}, 'vision_authoring': {}}
    for name, value in {
        '_receipt_path': lambda *_: SimpleNamespace(exists=lambda: True),
        '_read_context_receipt': lambda *_: copy.deepcopy(receipt),
        '_inspect_attempt': lambda *_: {'status': 'not_started'},
        '_resolve': lambda *a, **kw: {'ok': True},
        '_local_source_facts': lambda *_: {},
        '_source_guard': lambda *_: nullcontext(),
        'exclusive_file_lock': lambda *_: nullcontext(),
        'exclusive_cross_runtime_file_lock': lambda *a, **kw: nullcontext(),
    }.items():
        monkeypatch.setattr(cp, name, value)
    monkeypatch.setattr(cp.CurrentAgentCheckpoint, 'enabled', lambda _: True)

    def save(self, path, value):
        receipt.clear()
        receipt.update(copy.deepcopy(value))
    monkeypatch.setattr(cp.CurrentAgentCheckpoint, '_save', save)
    calls = []
    def host(context):
        calls.append(context['read_context_id'])
        if len(calls) == 2:
            return {'result_kind': kind}
        raise BuiltInHostError('Synthetic capacity refusal', failure_kind='provider_capacity')

    driver = cp.CurrentAgentCheckpoint(tmp_path/'registry', tmp_path, tmp_path/'state', identity, host)
    with pytest.raises(ValueError, match='capacity refusal'):
        driver.continue_direction({})
    with pytest.raises(ValueError, match='stopped direction'):
        driver.continue_direction({})
    assert receipt['direction_host_attempt_count'] == 2
    assert 'direction_host_failure' not in receipt
    assert receipt['direction_stops'] == [{'attempt': 2, 'result_kind': kind}]
    driver.explicit_resume = True
    with pytest.raises(ValueError, match='capacity refusal'):
        driver.continue_direction({})
    assert receipt['direction_host_attempt_count'] == receipt['direction_host_failure']['attempt'] == 3
    with pytest.raises(cp.CheckpointReadContextRejected) as rejected:
        driver.continue_direction({})
    assert rejected.value.code == 'host_retry_budget_exhausted'
    assert calls == ['original-read'] * 3
