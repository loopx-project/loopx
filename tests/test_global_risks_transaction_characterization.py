"""Complete public read compatibility corpus, captured before owner cutover."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

import loopx.global_risks as risks
import loopx.control_plane.projection_envelope_facts as envelope
import loopx.control_plane.quota.host_poll_receipts as receipts

FIXTURE = Path(__file__).parent / 'fixtures/global_risks_transaction.json'
NOW = '2026-11-02T12:00:00+00:00'


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def cases():
    empty = {'ok': True, 'contract': {'error_diagnostics': []},
             'global_registry': {'available': True, 'findings': [], 'global_goal_count': 2},
             'attention_queue': {'items': []}}
    diagnostic = {'code': 'synthetic_check', 'scope': 'goal', 'goal_id': 'goal-a',
                  'message': 'Synthetic check failed.', 'severity': 'warning'}
    def row(name, **kw):
        return {'name': name, 'status': deepcopy(empty), **kw}
    result = [row('empty'), row('single', diagnostics=[diagnostic]),
              row('multiple', diagnostics=[{**diagnostic, 'scope': 'goals', 'goal_ids': ['goal-b', 'goal-a', 'goal-b']}]),
              row('duplicate_and_severity', diagnostics=[diagnostic, {**diagnostic, 'severity': 'error'}, diagnostic]),
              row('redaction', diagnostics=[{**diagnostic, 'message': 'Inspect /home/synthetic/private/file and /loop-global-risks. ' + 'x' * 300}]),
              row('truncation', diagnostics=[{**diagnostic, 'goal_id': f'goal-{i}'} for i in range(45)], limit=1),
              row('limit_zero', limit=0), row('limit_max', limit=999),
              row('malformed_rows', diagnostics=[None, {}, {'code': 'x', 'scope': 'goals', 'goal_ids': 'bad'}]),
              row('stale_warning', items=[{'goal_id': 'goal-a', 'stale_latest_run_warning': {'kind': 'stale_latest_run_projection', 'reason': 'Synthetic stale routing.', 'latest_run_generated_at': '2026-11-01', 'active_state_updated_at': 'invalid'}}]),
              row('malformed_items', items=[None, {'stale_latest_run_warning': []}, {'stale_latest_run_warning': {'kind': 'wrong'}}]),
              row('registry_findings', findings=[None, {'severity': 'high'}, {'kind': 'synthetic_registry', 'severity': 'action'}, {'kind': 'ignored', 'severity': 'info'}]),
              row('agent_filter', diagnostics=[diagnostic, {**diagnostic, 'goal_id': 'goal-b'}, {**diagnostic, 'scope': 'global', 'goal_id': None}], agent_id='agent-a', history=[{'id': 'goal-a', 'coordination': {'registered_agents': ['agent-a']}}, {'id': 'goal-b', 'coordination': {'registered_agents': ['agent-b']}}]),
              row('agent_failure', diagnostics=[diagnostic], agent_id='agent-a'),
              row('status_nonobject', status=[]), row('status_missing', status={}),
              row('status_bad_list', status={**empty, 'contract': {'error_diagnostics': {}}}),
              row('status_exception', status_error='Synthetic source unavailable.'),
              row('registry_unreadable', registry_error='Synthetic registry unavailable.'),
              row('registry_malformed', registry={'goals': {}}),
              row('error', error='Synthetic /home/synthetic/private/failure unavailable.'),
              row('error_code', error='Synthetic failure.', error_code='synthetic_unavailable')]
    for name, stamp, gate in [('fresh', '2026-11-02T11:00:00Z', True),
                              ('stale', '2026-11-01T12:00:00Z', True),
                              ('edge', '2026-11-02T08:00:00Z', True),
                              ('before_edge', '2026-11-02T08:00:00.000001Z', True),
                              ('timezone', '2026-11-02T16:00:00+08:00', True),
                              ('naive', '2026-11-02T08:00:00', True),
                              ('malformed', 'invalid', True),
                              ('stopped', '2026-11-01T12:00:00Z', False),
                              ('no_gate', '2026-11-01T12:00:00Z', None)]:
        receipt = {'schema_version': 'loopx_host_poll_receipt_v0', 'goal_id': 'goal-a',
                   'agent_id': 'agent-a', 'poll_count': 3, 'last_poll_at': stamp,
                   'last_decision_action': 'wait'}
        if gate is not None:
            receipt['expected_continuation'] = gate
        result.append(row('poll_' + name, receipt=receipt))
    for time_range in [' 2D ', '001h', '0h', 'bad', '', '48h']:
        result.append(row('range_' + repr(time_range), time_range=time_range))
    return result


def observe(case, monkeypatch, tmp_path):
    calls = []
    original = envelope.effect_runtime_result
    def invoke(method, params, **kw):
        calls.append(method)
        return original(method, params, **kw)
    monkeypatch.setattr(envelope, 'effect_runtime_result', invoke)
    monkeypatch.setattr(risks, 'effect_runtime_result', invoke, raising=False)
    monkeypatch.setattr(risks, 'now_utc_iso', lambda: NOW)
    monkeypatch.setattr(receipts, 'datetime', Clock)
    status = deepcopy(case['status'])
    for source, key, target in [('diagnostics', 'contract', 'error_diagnostics'),
                                ('findings', 'global_registry', 'findings'),
                                ('items', 'attention_queue', 'items')]:
        if source in case:
            status[key][target] = case[source]
    if 'history' in case:
        status['run_history'] = {'goals': case['history']}
    def collect(**kw):
        if 'status_error' in case:
            raise ValueError(case['status_error'])
        return status
    monkeypatch.setattr(risks, 'collect_status', collect)
    registry = case.get('registry', {'goals': []})
    if 'receipt' in case:
        registry = {'goals': [{'id': 'goal-a', 'state_file': 'goal-a.md', 'repo': str(tmp_path)}]}
        directory = tmp_path / '.loopx-host-poll-receipts'
        directory.mkdir(exist_ok=True)
        (directory / 'goal-a.json').write_text(json.dumps(case['receipt']))
    def load(path):
        if 'registry_error' in case:
            raise ValueError(case['registry_error'])
        return registry
    monkeypatch.setattr(risks, 'load_registry', load)
    if 'error' in case:
        payload = risks.build_global_risks_error(case['error'], error_code=case.get('error_code', 'global_risks_unavailable'))
    else:
        payload = risks.build_global_risks(registry_path=tmp_path / 'registry.json',
            runtime_root_override=None, agent_id=case.get('agent_id'),
            time_range=case.get('time_range', '24h'), limit=case.get('limit', 8))
    return {'json': json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            'markdown': risks.render_global_risks_markdown(payload), 'crossings': len(calls)}


@pytest.mark.parametrize('case', cases(), ids=lambda case: case['name'])
def test_complete_read_bytes(case, monkeypatch, tmp_path):
    expected = json.loads(FIXTURE.read_text())[case['name']]
    assert observe(case, monkeypatch, tmp_path) == expected
