"""Complete public-safe readback parity; retire crossing pins with the Python facade."""
from collections import Counter
from copy import deepcopy
from difflib import unified_diff
import json
from pathlib import Path

import pytest

from test_local_delegation import demo

from test_local_delegation import service as delegation_service
from test_progress_review_criterion_binding import bind_exact_goal
from tests.capabilities.test_progress_review import receipt

service = delegation_service
FIXTURE = Path(__file__).with_suffix('.json')


def fixture_json(value, depth=0):
    """Expand structural objects, keeping scalar records together for review."""
    compact = json.dumps(value, sort_keys=True, ensure_ascii=False)
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    if depth >= 5 or not any(isinstance(child, (dict, list)) and child for child in children):
        return compact
    pad = "  " * (depth + 1)
    if isinstance(value, dict):
        rows = [pad + json.dumps(key) + ": " + fixture_json(child, depth + 1)
                for key, child in sorted(value.items())]
        return "{\n" + ",\n".join(rows) + "\n" + "  " * depth + "}"
    return "[\n" + ",\n".join(pad + fixture_json(child, depth + 1) for child in value) + "\n" + "  " * depth + "]"


@pytest.fixture(autouse=True)
def stable_acceptance_setup(monkeypatch):
    """Pin the declared command before bootstrap hashes it; never normalize output.

    This read-only qualification test does not execute the acceptance command.
    A relative synthetic argv keeps contract and Todo digests independent of
    both the temporary home and the interpreter installation.
    """
    original_write = demo.write

    def write(path, value):
        if path.name == "bootstrap.json":
            for criterion in value["document"]["criteria"]:
                criterion["validation_argv"] = ["python3", "validation/acceptance.py", ".",
                                                *criterion["validation_argv"][3:]]
        original_write(path, value)

    monkeypatch.setattr(demo, "write", write)


def test_qualification_characterization(service, monkeypatch):
    from loopx.capabilities.progress_review import receipt as receipt_module
    from loopx.capabilities.progress_review.context import external_progress_review_context
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.work_items.project_asset import attach_active_state_project_asset_fields
    from loopx.presentation.renderers.status_markdown import render_status_markdown

    _, runner = service
    goal = bind_exact_goal(runner)
    goal['control_plane'] = {'progress_review': {'mode': 'shadow'}}
    original = effect_runtime.effect_runtime_result
    rows = []
    for todo, criterion in [('todo_analyst-initial', 'analyst-initial'), ('todo_reviewer-corrected', 'reviewer-corrected')]:
        requirements = original('goal.acceptance.inspect', {
            'goal_id': runner.goal_id, 'runtime_root': str(runner.root), 'todo_id': todo,
            'goal_ref': {key: goal[key] for key in ('goal_instance_id',)} | {'goal_id': runner.goal_id},
        })['completion_requirements']
        binding = original('progress_review.criterion_basis', {'requirements': requirements,
            'criterion_ids': [criterion], 'acceptance': [], 'goal_id': runner.goal_id, 'agent_id': todo.split('_')[1].split('-')[0]})['binding']
        row = receipt(goal_id=runner.goal_id, evidence_scope={'criterion_binding': binding,
            'coverage': 'declared_file_net_change', 'files': ['output.json']})
        row['run'].update(todo_id=todo, agent_id=binding['agent_id'])
        rows.append(row)
    manual = receipt(goal_id=runner.goal_id, evidence_scope={'criterion_binding': original(
        'progress_review.criterion_basis', {'requirements': None, 'acceptance': ['Synthetic criterion']})['binding'],
        'coverage': 'declared_file_net_change', 'files': []})
    removed = deepcopy(rows[0])
    removed['evidence_scope']['criterion_binding']['criterion_ids'] = ['removed']
    unavailable = deepcopy(rows[0])
    unavailable['evidence_scope']['criterion_binding']['todo_id'] = 'todo_missing'
    unavailable['run']['todo_id'] = 'todo_missing'
    newer = receipt(goal_id=runner.goal_id, contract_revision='b' * 64, sequence=0)
    newer['run']['generated_at'] = '2026-09-22T00:00:00Z'
    scenarios = {
        'off': ({'id': runner.goal_id}, [rows[0]]),
        'empty': (goal, []),
        'repeated': (goal, [rows[0], rows[0]]),
        'multiple': (goal, rows),
        'wrong_instance': ({**goal, 'goal_instance_id': 'ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'}, [rows[0]]),
        'removed': (goal, [removed]),
        'unavailable_inspection': (goal, [unavailable, rows[1]]),
        'manual_legacy': (goal, [manual, receipt(goal_id=runner.goal_id)]),
        'outside_pin': ({**goal, 'control_plane': {'progress_review': {'mode': 'assist',
            'contract_revision': rows[0]['contract_revision']}}}, [rows[0], newer]),
        'unavailable_store': (goal, None),
    }
    captures = {}
    counts = {}
    for name, (current_goal, observations) in scenarios.items():
        calls = Counter()
        def tracked(method, params, **kwargs):
            calls[method] += 1
            return original(method, params, **kwargs)
        def load(*args):
            if observations is None:
                raise OSError('synthetic unavailable store')
            normalized = [receipt_module.normalize_progress_review_receipt(deepcopy(row)) for row in observations]
            normalized.sort(key=receipt_module.progress_review_receipt_order_key, reverse=True)
            return normalized, 0
        with monkeypatch.context() as patch:
            patch.setattr(effect_runtime, 'effect_runtime_result', tracked)
            patch.setattr(receipt_module, 'load_progress_review_receipts', load)
            context = external_progress_review_context(current_goal, runner.root)
        item = {'id': runner.goal_id, 'project_asset': {}}
        attach_active_state_project_asset_fields(item, external_progress_review=context)
        payload = {'attention_queue': {'items': [item]}}
        # The two public JSON locations must carry the complete same summary.
        summary = context['summary'] if context else None
        assert item.get('external_progress_review') == summary
        assert item['project_asset'].get('external_progress_review') == summary
        captures[name] = {'context': context,
                          'status_review': item.get('external_progress_review'),
                          'markdown': render_status_markdown(payload)}
        counts[name] = dict(calls)
    actual = fixture_json(captures) + "\n"
    assert actual == FIXTURE.read_text(), ''.join(unified_diff(FIXTURE.read_text().splitlines(True), actual.splitlines(True), fromfile='frozen', tofile='unchanged-rerun'))
    baseline_counts = json.loads(FIXTURE.with_name(FIXTURE.stem + '_crossings.json').read_text())
    expected = {
        name: ({**({'progress_review.evidence_scope': row['progress_review.evidence_scope']}
                    if 'progress_review.evidence_scope' in row else {}),
                'progress_review.context.qualify': 1}
               if name not in ('off', 'empty', 'unavailable_store') else {})
        for name, row in baseline_counts.items()
    }
    assert counts == expected
    print('CROSSINGS', json.dumps(counts, sort_keys=True))


def test_qualification_retains_historical_numeric_io(service):
    from loopx.capabilities.progress_review.context import external_progress_review_context
    from loopx.capabilities.progress_review.receipt import (
        load_progress_review_receipts,
        write_progress_review_receipt,
    )

    _, runner = service
    goal = {'id': runner.goal_id, 'control_plane': {'progress_review': {'mode': 'shadow'}}}
    observed = receipt(goal_id=runner.goal_id, label_probability_threshold=1.0,
                       timing_ns={'assessment_total': 2**60 + 1}, usage={'input_tokens': 2**60 + 3},
                       judgments={'choice': {'relation': 'off_goal', 'increment': 'no_new_evidence'},
                                  'noul': {'behavior_change': 0.0, 'serves_acceptance': 0.0,
                                           'evidence_increment': 0.0}})
    write_progress_review_receipt(runner.root, runner.goal_id, observed)
    loaded, _ = load_progress_review_receipts(runner.root, runner.goal_id)
    result = external_progress_review_context(goal, runner.root)
    assert json.dumps(result['receipts'], sort_keys=True) == json.dumps(loaded, sort_keys=True)
    assert result['summary']['latest']['drift_signal'] == {'choice': True, 'noul': True}
