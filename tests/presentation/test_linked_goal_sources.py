"""Normal Chat origin -> native Goal source -> local, explicit analysis.

The source CLI and assessment subprocess are disposable fixtures. No active
solver, model account or installed App is changed by this regression suite.
"""
import copy
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from test_research_analysis_api import workspace as workspace
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import register_pack
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_edge_event, build_explore_node_event,
    explore_history_rows, explore_result_log_path,
)
from loopx.control_plane.status.goal_source import GoalSource


def native_source(w):
    source_root = w['tmp_path'] / 'original'
    source_root.mkdir()
    registry, runtime = source_root / 'registry.json', source_root / 'runtime'
    state = source_root / 'active.md'
    state.write_text('---\nstatus: active\n---\n\n# Original solving Goal\n\n## Agent Todo\n\n'
        '- [ ] Check the original question against its evidence\n'
        '  <!-- loopx:todo todo_id=todo_original role=agent status=open -->\n')
    registry.write_text(json.dumps({'schema_version': 1, 'common_runtime_root': str(runtime), 'goals': [
        {'id': 'solver-goal', 'repo': str(source_root), 'state_file': 'active.md',
         'status': 'active', 'domain': 'source-read', 'authority_sources': [],
         'adapter': {'kind': 'test_v0', 'status': 'connected-read-only'}}]}))
    log = explore_result_log_path(runtime, 'solver-goal')
    for name in ('question', 'attempt'):
        append_explore_result_event(log, build_explore_node_event(goal_id='solver-goal',
            node_id=name, node_kind='question', title=name, summary='Original native record'))
    append_explore_result_event(log, build_explore_edge_event(goal_id='solver-goal',
        from_node='attempt', to_node='question', edge_type='depends_on'))
    raw = {'id': 'original', 'label': 'Original experiment',
        'connection': {'kind': 'local'}, 'environment': {'kind': 'host'},
        'scope': {'goal_id': 'solver-goal'}, 'registry': str(registry), 'runtime_root': str(runtime),
        'cli': str(Path(sys.executable).with_name('loopx')), 'python': sys.executable,
        'path': os.environ['PATH']}
    w['server'].runtime_root = w['tmp_path'] / 'runtime'
    w['server'].selected_goal_id = None
    w['server'].scan_roots, w['server'].limit = [], 80
    base = f'http://127.0.0.1:{w["server"].server_port}'

    def request(path, body=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        with urlopen(req, timeout=30) as response:
            return json.load(response)

    return raw, request, {path: path.read_bytes() for path in (registry, state, log)}


def test_app_parent_directory_reads_original_tasks_history_and_explicit_analysis(workspace):
    w = workspace
    raw, request, before = native_source(w)
    assert request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})['ok']
    namespace = GoalSource.parse(raw).namespace
    directory = request('/status.json?view=workspace-directory')
    child = next(goal for goal in directory['goals'] if goal['id'] == 'solver-goal')
    assert child['parent_goal_id'] == 'research-owner' and child['source_access']['read_only']
    assert str(w['tmp_path']) not in json.dumps(child)
    snapshot = request('/status.json?goal_id=solver-goal')
    assert snapshot['workspace_registry_revision'] == directory['registry_revision']
    actual = snapshot['run_history']['goals'][0]
    assert actual['id'] == 'solver-goal' and actual['parent_goal_id'] == 'research-owner'
    assert actual['native_parent_goal_id'] is None
    task = request('/api/chat/todo/detail?' + urlencode({
        'goal_id': 'solver-goal', 'todo_id': 'todo_original', 'source_namespace': namespace}))
    assert task['text'] == 'Check the original question against its evidence'
    assert 'review_basis' not in task
    history = request('/api/chat/explore-results?' + urlencode({
        'goal_id': 'solver-goal', 'source_namespace': namespace, 'view': 'history'}))
    assert history['total'] == 3 and any(row.get('edge_type') == 'depends_on' for row in history['items'])

    analysis_runtime = w['server'].runtime_root / 'goal-sources' / namespace
    pack = register_pack(runtime_root=analysis_runtime, owner_goal_id='solver-goal',
        input_path=w['tmp_path'] / 'input.json', source_root=w['sources'], title='Original source evidence')
    query = urlencode({'goal_id': 'solver-goal', 'source_namespace': namespace})
    endpoint = '/api/chat/research-analysis'
    initial = request(endpoint + '?' + query)
    assert initial['execution_enabled'] and initial['pack']['job'] is None
    assert not w['count'].exists()
    operation = dict(goal_id='solver-goal', pack_id=pack['pack_id'], analysis_key=pack['analysis_key'])
    w['mode'].write_text('ok')
    result = request(endpoint + '?source_namespace=' + namespace, operation)
    assert result['job']['status'] == 'succeeded'
    assert len(w['count'].read_text().splitlines()) == 1
    assert request(endpoint + '?' + query)['pack']['result']['assessments'][0]['verdict'] == 'no'
    for path, data in before.items():
        assert path.read_bytes() == data
    host_registry = json.loads(w['server'].registry_path.read_text())
    assert [goal['id'] for goal in host_registry['goals']] == ['research-owner', 'other-owner']
    # A new App service reads the persisted relationship, not a browser URL or in-memory mirror.
    from loopx.control_plane.status.goal_source import GoalSourceCatalog
    w['server'].goal_source_catalog = GoalSourceCatalog(w['server'].registry_path, w['server'].runtime_root,
                                                     history_projector=explore_history_rows)
    assert request(endpoint + '?' + query)['pack']['job']['status'] == 'succeeded'
    assert len(w['count'].read_text().splitlines()) == 1
    bound = w['server'].registry_path.read_bytes()
    unlink = dict(parent_goal_id='research-owner', goal_id='solver-goal', source_namespace='f' * 64)
    with pytest.raises(HTTPError):
        request('/api/chat/goal-sources', unlink)
    assert w['server'].registry_path.read_bytes() == bound
    request('/api/chat/goal-sources', {**unlink, 'source_namespace': namespace})
    assert not request('/api/chat/goal-sources')['goals']
    with pytest.raises(HTTPError):
        request(endpoint + '?source_namespace=' + namespace, operation)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    assert request(endpoint + '?' + query)['pack']['job']['status'] == 'succeeded'
    assert len(w['count'].read_text().splitlines()) == 1
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize('retry', [False, True])
@pytest.mark.parametrize('failure', ['removed', 'unavailable'])
def test_app_warm_read_cannot_admit_analysis_after_original_goal_disappears(workspace, retry, failure):
    w = workspace
    raw, request, before = native_source(w)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    namespace = GoalSource.parse(raw).namespace
    query = urlencode({'goal_id': 'solver-goal', 'source_namespace': namespace})
    request('/api/chat/research-analysis?' + query)
    source_registry = Path(raw['registry'])
    missing = json.loads(source_registry.read_text())
    missing['goals'] = []
    if failure == 'removed':
        source_registry.write_text(json.dumps(missing))
    else:
        source_registry.unlink()
    operation = dict(goal_id='solver-goal', pack_id='a' * 64, analysis_key='b' * 64, retry=retry)
    with pytest.raises(HTTPError):
        request('/api/chat/research-analysis?source_namespace=' + namespace, operation)
    assert not w['count'].exists()
    source_registry.write_bytes(before[source_registry])
    assert request('/api/chat/research-analysis?' + query)['catalog']['packs'] == []


def test_app_source_links_reject_other_origins_unknown_parents_and_identity_collisions(workspace):
    w = workspace
    raw, request, _ = native_source(w)
    host = w['server'].registry_path
    before = host.read_bytes()
    for body, origin in [({'parent_goal_id': 'research-owner', 'source': raw}, 'https://unrelated.example'),
                         ({'parent_goal_id': 'absent', 'source': raw}, None)]:
        with pytest.raises(HTTPError):
            request('/api/chat/goal-sources', body, origin)
        assert host.read_bytes() == before
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    bound = host.read_bytes()
    moved = copy.deepcopy(raw)
    moved['runtime_root'] += '-different'
    with pytest.raises(HTTPError):
        request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': moved})
    assert host.read_bytes() == bound and not w['count'].exists()
    with pytest.raises(HTTPError):
        request('/api/chat/research-analysis?goal_id=solver-goal&source_namespace=' + 'f' * 64)


def test_original_task_detail_cannot_follow_a_replaced_source(workspace):
    raw, request, _ = native_source(workspace)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    old_namespace = GoalSource.parse(raw).namespace
    endpoint = '/api/chat/todo/detail?'
    query = {'goal_id': 'solver-goal', 'todo_id': 'todo_original'}
    with pytest.raises(HTTPError):
        request(endpoint + urlencode(query))
    old_url = endpoint + urlencode({**query, 'source_namespace': old_namespace})
    assert request(old_url)['source_access']['namespace'] == old_namespace
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner',
        'goal_id': 'solver-goal', 'source_namespace': old_namespace})
    with pytest.raises(HTTPError) as removed:
        request(old_url)
    assert removed.value.code == 409
    replacement = copy.deepcopy(raw)
    replacement['id'] = 'replacement'
    state = Path(raw['registry']).parent / 'active.md'
    state.write_text(state.read_text().replace('Check the original question against its evidence',
        'Check the replacement question against its own evidence'))
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': replacement})
    with pytest.raises(HTTPError) as replaced:
        request(old_url)
    assert replaced.value.code == 409
    namespace = GoalSource.parse(replacement).namespace
    detail = request(endpoint + urlencode({**query, 'source_namespace': namespace}))
    assert detail['source_access']['namespace'] == namespace
    assert detail['text'] == 'Check the replacement question against its own evidence'
