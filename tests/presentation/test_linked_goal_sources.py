"""Normal Chat origin -> native Goal source -> local, explicit analysis.

The source CLI and assessment subprocess are disposable fixtures. No active
solver, model account or installed App is changed by this regression suite.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from test_research_analysis_api import workspace as workspace
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import register_pack
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_edge_event, build_explore_node_event, build_explore_finding_event,
    explore_history_rows, explore_result_log_path,
)
from loopx.control_plane.status.goal_source import GoalSource


def native_source(w):
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority

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
    rows, _, _ = parse_todo_source(state.read_text())
    for todo in rows['agent']:
        todo['schema_version'] = 'todo_item_v0'
    initialize_canonical_authority(runtime, 'solver-goal', build_todo_runtime_shadow_projection(
        goal_id='solver-goal', todos=rows['agent'], handoff_mode='soft_claim'), state_path=state, provider='sqlite')
    for goal in ('research-owner', 'other-owner'):
        initialize_canonical_authority(w['tmp_path'] / 'runtime', goal,
            build_todo_runtime_shadow_projection(goal_id=goal, todos=[], handoff_mode='soft_claim'),
            state_path=w['tmp_path'] / 'active.md', provider='sqlite')
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
                         ({'parent_goal_id': 'absent', 'source': raw}, None),
                         ({'parent_goal_id': 'research-owner', 'source': {
                             **raw, 'scope': {'goal_id': 'research-owner'}}}, None)]:
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


def test_source_failure_preserves_links_peers_and_native_recovery(workspace):
    w = workspace
    raw, request, before = native_source(w)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    bound = w['server'].registry_path.read_bytes()
    source_registry = Path(raw['registry'])
    request('/status.json?goal_id=solver-goal')
    # A real native CLI failure, including private stderr, must not become a
    # generic workspace failure or borrow a local Goal's snapshot.
    source_registry.unlink()
    reader = w['server'].goal_source_catalog.resolve('solver-goal')[1]
    reader._read_at = 0
    with pytest.raises(HTTPError) as unavailable:
        request('/status.json?goal_id=solver-goal')
    assert unavailable.value.code == 503
    error = json.load(unavailable.value)
    assert error['error_code'] == 'goal_source_unavailable'
    assert str(w['tmp_path']) not in json.dumps(error)
    assert w['server'].registry_path.read_bytes() == bound
    assert len(request('/api/chat/goal-sources')['goals']) == 1
    assert request('/status.json?goal_id=other-owner')['run_history']['goals'][0]['id'] == 'other-owner'
    missing = json.loads(before[source_registry])
    missing['goals'] = []
    source_registry.write_text(json.dumps(missing))
    with pytest.raises(HTTPError) as removed:
        request('/status.json?goal_id=solver-goal')
    assert removed.value.code == 409
    source_registry.write_bytes(before[source_registry])
    assert request('/status.json?goal_id=solver-goal')['run_history']['goals'][0]['registry_member']
    assert not w['count'].exists()


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
    from loopx.todos import update_goal_todo
    update_goal_todo(registry_path=Path(raw['registry']), runtime_root_arg=raw['runtime_root'],
        goal_id='solver-goal', todo_id='todo_original',
        text='Check the replacement question against its own evidence')
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': replacement})
    with pytest.raises(HTTPError) as replaced:
        request(old_url)
    assert replaced.value.code == 409
    namespace = GoalSource.parse(replacement).namespace
    detail = request(endpoint + urlencode({**query, 'source_namespace': namespace}))
    assert detail['source_access']['namespace'] == namespace
    assert detail['text'] == 'Check the replacement question against its own evidence'


def test_analysis_rejects_a_namespace_on_an_unlinked_local_goal(workspace):
    _, request, _ = native_source(workspace)
    with pytest.raises(HTTPError):
        request('/api/chat/research-analysis?source_namespace=' + 'f' * 64, workspace['operation'])
    assert not workspace['count'].exists()


def test_app_can_cancel_its_local_analysis_while_original_source_is_offline(workspace):
    w = workspace
    raw, request, before = native_source(w)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    namespace = GoalSource.parse(raw).namespace
    pack = register_pack(runtime_root=w['server'].runtime_root / 'goal-sources' / namespace,
        owner_goal_id='solver-goal', input_path=w['tmp_path'] / 'input.json', source_root=w['sources'],
        title='Fixed original source')
    operation = dict(goal_id='solver-goal', pack_id=pack['pack_id'], analysis_key=pack['analysis_key'])
    endpoint = '/api/chat/research-analysis?source_namespace=' + namespace
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(request, endpoint, operation)
        until = time.monotonic() + 12
        while not w['count'].exists() and time.monotonic() < until:
            time.sleep(.05)
        assert w['count'].exists()
        source_registry = Path(raw['registry'])
        source_registry.unlink()
        try:
            cancelled = request('/api/chat/research-analysis/cancel?source_namespace=' + namespace, operation)
            assert cancelled['job']['cancel_requested']
            assert running.result(timeout=8)['job']['status'] == 'cancelled'
        finally:
            source_registry.write_bytes(before[source_registry])
    assert len(w['count'].read_text().splitlines()) == 1


def test_removed_parent_does_not_poison_other_local_or_source_goals(workspace):
    from loopx.control_plane.projects.registry_codec import mutate_project_registry

    w = workspace
    raw, request, _ = native_source(w)
    source_registry = Path(raw['registry'])
    registry = json.loads(source_registry.read_text())
    (source_registry.parent / 'other.md').write_bytes((source_registry.parent / 'active.md').read_bytes())
    registry['goals'].append({**registry['goals'][0], 'id': 'other-solver', 'state_file': 'other.md'})
    source_registry.write_text(json.dumps(registry))
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    initialize_canonical_authority(Path(raw['runtime_root']), 'other-solver',
        build_todo_runtime_shadow_projection(goal_id='other-solver', todos=[], handoff_mode='soft_claim'),
        state_path=source_registry.parent / 'other.md', provider='sqlite')
    other = {**raw, 'id': 'other-source', 'scope': {'goal_id': 'other-solver'}}
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    request('/api/chat/goal-sources', {'parent_goal_id': 'other-owner', 'source': other})
    before = source_registry.read_bytes()
    # Exercise the registry owner's committed removal, never a source solver write.
    mutate_project_registry(w['server'].registry_path, operation='fixture_parent_removal',
        reducer=lambda data: data.update(goals=[{**g, 'domain': 'source-read'}
            for g in data['goals'] if g['id'] != 'research-owner']))
    directory = request('/status.json?view=workspace-directory')
    assert {goal['id'] for goal in directory['goals']} == {'other-owner', 'other-solver'}
    local = request('/status.json?goal_id=other-owner')
    assert [goal['id'] for goal in local['run_history']['goals']] == ['other-owner']
    assert request('/status.json?goal_id=other-solver')['run_history']['goals'][0]['id'] == 'other-solver'
    assert len(request('/api/chat/goal-sources')['goals']) == 1
    retained = json.loads(w['server'].registry_path.read_text())
    assert len(retained['goal_source_links']) == 2
    with pytest.raises(HTTPError):
        request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    assert json.loads(w['server'].registry_path.read_text()) == retained
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'goal_id': 'solver-goal',
        'source_namespace': GoalSource.parse(raw).namespace})
    assert len(json.loads(w['server'].registry_path.read_text())['goal_source_links']) == 1
    assert source_registry.read_bytes() == before and not w['count'].exists()


def test_original_saved_findings_keep_full_text_associations_and_source_fences(workspace):
    """Actual source CLI + normal App HTTP; no model or source mutations."""
    from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer
    from threading import Thread

    w = workspace
    raw, request, _ = native_source(w)
    state = Path(raw['registry']).parent / 'active.md'
    log = explore_result_log_path(Path(raw['runtime_root']), 'solver-goal')
    summary = "Counterexample. " + "Bounded observation. " * 70 + "Retain the original scope."
    for index in range(43):
        append_explore_result_event(log, build_explore_finding_event(goal_id='solver-goal',
            node_id='question', finding_id=f'finding{index}', title=f'Observation {index}',
            summary=summary, status='refuted', evidence_refs=['artifact:counterexample']))
    before = {path: path.read_bytes() for path in (state, log, Path(raw['registry']))}
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    namespace = GoalSource.parse(raw).namespace
    query = urlencode({'goal_id': 'solver-goal', 'source_namespace': namespace})
    endpoint = '/api/chat/explore-results?'
    first = request(endpoint + query + '&view=findings')
    assert first['total'] == 43 and len(first['items']) == 40
    item = first['items'][0]
    assert item['finding_id'] and 'event_kind' not in item
    assert item['summary'] == summary and item['status'] == 'refuted'
    assert item['question'] == 'question' and item['scope'] == 'Original native record'
    assert item['evidence_refs'] == ['artifact:counterexample']
    assert item['linked_todos'] == [] and item['linked_todos_available'] is False
    assert request(endpoint + query)['items'] == first['items']
    cursor_query = urlencode({'cursor': first['next_cursor']})
    assert len(request(endpoint + query + '&view=findings&' + cursor_query)['items']) == 3
    assert request(endpoint + query + '&view=history')['total'] == 46
    for suffix, status in (('&view=history&' + cursor_query, 409),
                           ('&view=invalid', 400), ('&view=findings&view=history', 400)):
        with pytest.raises(HTTPError) as rejected:
            request(endpoint + query + suffix)
        assert rejected.value.code == status
    with pytest.raises(HTTPError) as rejected:
        request(endpoint + query, origin='https://other.example')
    assert rejected.value.code == 403

    # Malformed/wrong-Goal evidence fails closed and invalidates previous pages.
    events = log.read_text().splitlines()
    wrong = json.loads(events[-1]); wrong['goal_id'] = 'unrelated'
    log.write_text('\n'.join([*events[:-1], json.dumps(wrong)]))
    with pytest.raises(HTTPError) as corrupt:
        request(endpoint + query)
    assert corrupt.value.code == 409
    assert str(w['tmp_path']) not in corrupt.value.read().decode()
    log.write_bytes(before[log])
    with pytest.raises(HTTPError):
        request(endpoint + query + '&' + cursor_query)
    assert request(endpoint + query)['total'] == 43

    # The prepared source adapter serves the same finding shape as the real App.
    source_server = RuntimeWorkspaceServer(GoalSource.parse(raw), w['tmp_path'])
    worker = Thread(target=source_server.serve_forever, daemon=True); worker.start()
    try:
        with urlopen(f'http://127.0.0.1:{source_server.server_port}' + endpoint + query + '&view=findings', timeout=30) as response:
            assert json.load(response)['items'] == first['items']
    finally:
        source_server.shutdown(); worker.join(timeout=5); source_server.server_close()

    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner',
        'goal_id': 'solver-goal', 'source_namespace': namespace})
    with pytest.raises(HTTPError) as removed:
        request(endpoint + query)
    assert removed.value.code == 409
    # The source binding cannot change while a read is in flight.
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    reader = w['server'].goal_source_catalog.resolve('solver-goal')[1]
    original = reader.findings
    def unlink_during_read(*args, **kwargs):
        page = original(*args, **kwargs)
        request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner',
            'goal_id': 'solver-goal', 'source_namespace': namespace})
        return page
    reader.findings = unlink_during_read
    with pytest.raises(HTTPError) as replaced:
        request(endpoint + query)
    assert replaced.value.code == 409
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not w['count'].exists()
