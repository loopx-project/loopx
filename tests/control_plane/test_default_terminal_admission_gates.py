"""Native terminal admission gates; all mutations target disposable fixture state."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest


from loopx.configure_goal import configure_goal
from tests.control_plane.test_default_turn_recovery import (
    _managed_recovery_call, _business_rows, _work_counts, _original_checkpoint_receipt,
)
from tests.test_goal_mode_mcp_settlement import GOAL_ID
import tests.control_plane.test_declared_terminal_settlement_cli as declared


def _assert_preserved(before, after):
    assert after == before, 'A denied operation repeated work or successful business effects'


def test_negative_counter_fixture_rejected():
    before = {'work': {'implementation.txt': 1}, 'effects': {'quota_slot_spent': 1}}
    for key in ('work', 'effects'):
        broken = deepcopy(before)
        broken[key][next(iter(broken[key]))] += 1
        with pytest.raises(AssertionError):
            _assert_preserved(before, broken)


def _original(provider, tmp_path, monkeypatch):
    state, project, args, injection = _managed_recovery_call(provider, 'result', tmp_path, monkeypatch)
    lost = subprocess.run([sys.executable, '-c', injection, *args], capture_output=True, text=True, timeout=90)
    assert lost.returncode == 97, lost.stdout+lost.stderr
    runtime = tmp_path/'runtime'
    journal_path = next(p for p in (runtime/'goals'/GOAL_ID/'turns').glob('*.json') if not p.name.endswith('.holder.json'))
    journal = json.loads(journal_path.read_text())
    assert journal['host_result']['result_kind'] == 'validated_completion'
    resume = [*args, '--resume-turn-key', journal['turn_key'], '--retry-failed-turn']
    registry = Path(args[args.index('--registry')+1])
    return state, project, runtime, registry, journal_path, journal, resume


def _snapshot(project, runtime):
    return {'work': _work_counts(project), 'effects': _business_rows(runtime),
            'candidate': (project/'candidate.txt').read_bytes()}


def _resume(args):
    return subprocess.run([sys.executable, '-m', 'loopx.cli', *args], capture_output=True, text=True, timeout=90)


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize('gate', ['pause', 'stop'])
def test_pending_result_respects_explicit_gate_and_original_resume(provider, gate, tmp_path, monkeypatch):
    state, project, runtime, registry, journal_path, journal, resume = _original(provider, tmp_path, monkeypatch)
    before = _snapshot(project, runtime)
    assert before['work'] == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 0}
    source_before = state.read_bytes()
    registry_before = registry.read_bytes()
    if gate == 'pause':
        changed = configure_goal(registry_path=registry, goal_id=GOAL_ID, quota_compute=0, execute=True)
        assert changed['written']
    else:
        doc = json.loads(registry.read_text())
        doc['goals'][0]['activation'] = {'schema_version': 'loopx_goal_activation_v1', 'state': 'stopped',
                                      'updated_at': '2026-10-11T00:00:00Z', 'reason': 'Fixture owner stops pending execution.'}
        registry.write_text(json.dumps(doc), encoding='utf-8')
    denied = _resume(resume)
    assert denied.returncode != 0, denied.stdout+denied.stderr
    _assert_preserved(before, _snapshot(project, runtime))
    retained = json.loads(journal_path.read_text())
    assert retained['turn_key'] == journal['turn_key']
    assert retained['host_result'] == journal['host_result']
    assert state.read_bytes() == source_before
    if gate == 'pause':
        assert configure_goal(registry_path=registry, goal_id=GOAL_ID, quota_compute=1, execute=True)['written']
    else:
        registry.write_bytes(registry_before)
    recovered = _resume(resume)
    assert recovered.returncode == 0, recovered.stdout+recovered.stderr
    value = json.loads(recovered.stdout)
    assert value['status'] == 'committed' and value['resume_turn_key'] == journal['turn_key']
    assert _work_counts(project) == {'implementation.txt': 1, 'validation.txt': 1, 'direction.txt': 1}
    assert _business_rows(runtime) == Counter({'candidate_verified': 1, 'quota_slot_spent': 1})
    after = _snapshot(project, runtime)
    replay = _resume(resume)
    assert replay.returncode == 0, replay.stdout+replay.stderr
    _assert_preserved(after, _snapshot(project, runtime))


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_real_direction_permission_signal_retains_result_and_blocks_retry(provider, tmp_path, monkeypatch):
    _, project, runtime, _, journal_path, journal, resume = _original(provider, tmp_path, monkeypatch)
    host = project/'host.py'
    source = host.read_text()
    needle = "    c=r['checkpoint_context']; assert c['basis']['todo']['status']=='done'"
    assert needle in source
    source = source.replace(needle, "    count('permission_requests.txt')\n    json.dump({'result_kind':'user_action_required'},sys.stdout); sys.exit(0)\n"+needle)
    host.write_text(source, encoding='utf-8')
    before = _snapshot(project, runtime)
    first = _resume(resume)
    assert first.returncode != 0, first.stdout+first.stderr
    _assert_preserved(before, _snapshot(project, runtime))
    receipt = _original_checkpoint_receipt(runtime)
    assert receipt['direction_stopped'] is True
    assert 'current_agent_decision' not in receipt
    assert receipt['decision_basis']['todo']['status'] == 'done'
    assert (project/'permission_requests.txt').read_text() == '1\n'
    # Ordinary replay must not reprompt. Explicit retry is a separate owner
    # decision, exercised after resolution in test_direction_stop_reentry.
    second = _resume([arg for arg in resume if arg != '--retry-failed-turn'])
    assert second.returncode != 0, second.stdout+second.stderr
    _assert_preserved(before, _snapshot(project, runtime))
    assert (project/'permission_requests.txt').read_text() == '1\n'
    assert json.loads(journal_path.read_text())['host_result'] == journal['host_result']


# The shipped fixture now explicitly covers all three actual providers.
test_native_hard_lease_and_failed_validator_preserve_original_declaration = declared.test_original_lease_and_failed_validator_remain_required_before_accounting
