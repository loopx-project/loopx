"""Public consumers use full relationship evidence without writing the provider."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.chat_manager_details import read_manager_goal_details
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import list_goal_todos


def fixture_projection():
    script = """import {productionScaleSuccessionFixture,PRODUCTION_SCALE_VALIDATION_DECLARATION} from './tests/control_plane_ts/production_scale_coordination_fixture.ts';
process.stdout.write(JSON.stringify({...productionScaleSuccessionFixture('goal-a','legacy'), validation:PRODUCTION_SCALE_VALIDATION_DECLARATION}));"""
    process = subprocess.run(['node', '--no-warnings', '--experimental-strip-types', '--input-type=module', '-e', script],
        capture_output=True, text=True, check=True, timeout=30)
    return json.loads(process.stdout)


def cli(registry: Path, *args: str):
    child = subprocess.run([sys.executable, '-m', 'loopx.cli', '--registry', str(registry), '--format', 'json',
        'todo', 'list', '--goal-id', 'goal-a', *args], capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout or child.stderr
    return json.loads(child.stdout)


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
def test_real_cli_historical_route_advisory_preserves_old_source_and_provider(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / 'runtime', tmp_path / 'state.md', tmp_path / 'registry.json'
    gate = {'schema_version': 'todo_item_v0', 'archive_state': 'active', 'source_section': 'Agent Todo',
            'role': 'agent', 'text': 'stale handoff closeout', 'status': 'open', 'done': False,
            'task_class': 'advancement_task', 'excluded_agents': ['agent-b'], 'unblocks_todo_id': 'todo_work'}
    records = [{**gate, 'todo_id': 'todo_legacy'},
               {**gate, 'todo_id': 'todo_ordinary', 'text': 'Review the route'},
               {**gate, 'todo_id': 'todo_closed', 'status': 'done', 'done': True, 'no_followup': True}]
    projection = build_todo_runtime_shadow_projection(goal_id='goal-a', todos=records, handoff_mode='soft_claim')
    state.write_text(render_canonical_todo_sections('# Goal\n\n## Agent Todo\n', projection['todos'],
        provider_revision='fixture-source').markdown)
    registry.write_text(json.dumps({'common_runtime_root': str(runtime), 'goals': [{
        'id': 'goal-a', 'repo': str(tmp_path), 'state_file': state.name, 'status': 'active',
        'coordination': {'registered_agents': ['agent-a', 'agent-b']},
    }]}))
    if provider != 'legacy':
        initialize_canonical_authority(runtime, 'goal-a', projection, state_path=state, provider=provider)
        state.unlink()
    source = state.read_bytes() if state.exists() else None
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id='goal-a')
    for todo_id, expected in [('todo_legacy', True), ('todo_ordinary', False), ('todo_closed', False)]:
        result = cli(registry, '--todo-id', todo_id)
        summary = result['agent_todos']
        handoff = summary['handoff_gates'][0]
        assert (handoff.get('route_continuation_replan_required') is True) is expected
        assert handoff['gate_state'] == ('cleared_no_followup' if todo_id == 'todo_closed' else 'blocking')
        assert 'terminal_closure_proof' not in summary or todo_id == 'todo_closed'
        assert result['todo']['excluded_agents'] == ['agent-b']
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id='goal-a') == before
    assert (state.read_bytes() if state.exists() else None) == source


@pytest.mark.parametrize('provider', ['legacy', 'file', 'sqlite'])
def test_real_cli_graph_selection_history_and_read_only_manager(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = fixture_projection()
    projection, cases = fixture['projection'], fixture['cases']
    runtime = tmp_path / 'runtime'
    state = tmp_path / 'state.md'
    state.write_text(render_canonical_todo_sections('# Goal\n\nIndependent narrative.\n\n## Agent Todo\n',
        projection['todos'], provider_revision='fixture-source',
        private_validation_declarations={row['todo_id']: fixture['validation'] for row in projection['todos']
            if row.get('completion_validation_required') is True}).markdown)
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'common_runtime_root': str(runtime), 'goals': [{
        'id': 'goal-a', 'repo': str(tmp_path), 'state_file': state.name, 'status': 'active',
        'coordination': {'registered_agents': ['agent-a', 'agent-b'], 'handoff_mode': 'soft_claim'},
    }]}))
    if provider != 'legacy':
        initialize_canonical_authority(runtime, 'goal-a', projection, state_path=state, provider=provider)
        state.unlink()  # Promoted readback must not rely on or regenerate Markdown.
    state_before = state.read_bytes() if state.exists() else None
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id='goal-a')
    for name, gap in [('inferred_source', 0), ('missing_source', 1), ('self_source', 1), ('handoff_source', 0)]:
        result = cli(registry, '--todo-id', cases[name], '--limit', '1')
        summary = result['agent_todos']
        assert summary.get('completed_without_successor_count', 0) == gap
        assert 'terminal_closure_proof' not in summary
        if name == 'handoff_source':
            assert summary['handoff_gates'][0]['gate_state'] == 'cleared_with_successor'
    details = read_manager_goal_details(registry, runtime, 'goal-a', owner_scope=True, limit=3)
    assert details['status'] == 'read'
    assert details['coverage']['active'] > details['coverage']['included']
    # A read-model evaluation must not become another persisted fact on capture.
    rows = list_goal_todos(registry_path=registry, goal_id='goal-a')['todos']
    assert all("succession_evaluation" not in row for row in rows)
    captured = build_todo_runtime_shadow_projection(goal_id='goal-a', todos=rows, handoff_mode='soft_claim')
    assert all('succession_evaluation' not in row for row in captured['todos'])
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id='goal-a') == before
    assert (state.read_bytes() if state.exists() else None) == state_before


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_large_goal_reuses_complete_succession_without_exceeding_rpc_budget(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / 'runtime', tmp_path / 'state.md', tmp_path / 'registry.json'
    state.write_text('# Goal\n\n## Agent Todo\n')
    records = [{
        'todo_id': f'todo_capacity_{index:05}', 'schema_version': 'todo_item_v0',
        'role': 'agent', 'text': 'Verify retained work', 'status': 'done', 'done': True,
        'archive_state': 'active', 'source_section': 'Agent Todo', 'index': index + 1,
        'task_class': 'advancement_task', 'claimed_by': 'agent-a', 'no_followup': True,
        'note': 'Retained metadata 完整🙂', 'evidence': 'fixture:verified',
    } for index in range(5000)]
    records[0].update(no_followup=False)
    records[-1].update(status='open', done=False, no_followup=False,
                       resume_when='todo_done:todo_capacity_00000')
    projection = build_todo_runtime_shadow_projection(goal_id='goal-a', todos=records, handoff_mode='soft_claim')
    registry.write_text(json.dumps({'common_runtime_root': str(runtime), 'goals': [{
        'id': 'goal-a', 'repo': str(tmp_path), 'state_file': state.name, 'status': 'active',
        'coordination': {'registered_agents': ['agent-a']},
    }]}))
    initialize_canonical_authority(runtime, 'goal-a', projection, state_path=state, provider=provider)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id='goal-a')
    result = cli(registry)
    summary = result['agent_todos']
    assert summary['total_count'] == 5000
    assert summary['done_count'] == 4999
    assert summary['open_count'] == 1
    assert summary.get('completed_without_successor_count', 0) == 0
    assert 'terminal_closure_proof' not in summary
    selected = cli(registry, '--todo-id', 'todo_capacity_00000', '--limit', '1')['agent_todos']
    assert selected.get('completed_without_successor_count', 0) == 0
    assert 'terminal_closure_proof' not in selected
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id='goal-a')
    assert after == before
    assert after is not None
    assert all(row['note'] == 'Retained metadata 完整🙂' and row['evidence'] == 'fixture:verified'
               for row in after['todos'])
