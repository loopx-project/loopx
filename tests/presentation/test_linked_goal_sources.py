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
from threading import Event
import time
from hashlib import sha256
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from test_research_analysis_api import workspace as workspace
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import register_pack
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_edge_event, build_explore_node_event, build_explore_finding_event,
    explore_result_log_path,
)
from loopx.control_plane.status.goal_source import GoalSource


def test_original_document_alias_binding_and_recovery(workspace):
    raw, request, protected = native_source(workspace)
    registry = Path(raw['registry'])
    registered = json.loads(registry.read_text())
    declared = '/source/retained/primary-task.md'
    registered['goals'][0]['authority_sources'] = [
        {'kind': 'goal_doc', 'role': 'primary_goal_document', 'path': declared}]
    registry.write_text(json.dumps(registered))
    protected[registry] = registry.read_bytes()
    document = registry.parent / 'captured-task.md'
    original = b'# Original objective\r\n\nKeep the full original task and its constraints.\n'
    document.write_bytes(original)
    raw['goal_document_snapshot'] = {'declared_path': declared, 'path': str(document),
        'sha256': sha256(original).hexdigest()}
    namespace = GoalSource.parse(raw).namespace
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw,
        'workspace_goal_id': 'case-alias'})
    path = '/api/chat/goal-source-document?' + urlencode({'goal_id': 'case-alias', 'source_namespace': namespace})
    result = request(path)
    assert result['goal_id'] == 'solver-goal'
    assert result['source']['namespace'] == namespace
    assert result['document'] == {'text': original.decode(), 'sha256': sha256(original).hexdigest(), 'captured': True}
    assert 'path' not in result['document']
    document.write_bytes(b'changed after capture')
    with pytest.raises(HTTPError) as failed:
        request(path)
    assert failed.value.code == 503
    document.write_bytes(original)
    assert request(path)['document']['text'] == original.decode()
    with pytest.raises(HTTPError) as wrong_scope:
        request(path.replace(namespace, '0' * 64))
    assert wrong_scope.value.code == 409
    # Updating the attachment keeps physical identity but replaces the cached
    # reader, so the original binding cannot silently survive a new capture.
    replacement = b'# Another captured original\n'
    document.write_bytes(replacement)
    raw['goal_document_snapshot']['sha256'] = sha256(replacement).hexdigest()
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw,
        'workspace_goal_id': 'case-alias'})
    assert GoalSource.parse(raw).namespace == namespace
    assert request(path)['document']['text'] == replacement.decode()
    for file, content in protected.items():
        assert file.read_bytes() == content


def native_source(w, name='original', task_text='Check the original question against its evidence', done=False,
                  task_evidence=None, task_note=None):
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority

    source_root = w['tmp_path'] / name
    source_root.mkdir()
    registry, runtime = source_root / 'registry.json', source_root / 'runtime'
    state = source_root / 'active.md'
    state.write_text('---\nstatus: active\n---\n\n# Original solving Goal\n\n## Agent Todo\n\n'
        f'- [{"x" if done else " "}] {task_text}\n'
        f'  <!-- loopx:todo todo_id=todo_original role=agent status={"done" if done else "open"} -->\n')
    registry.write_text(json.dumps({'schema_version': 1, 'common_runtime_root': str(runtime), 'goals': [
        {'id': 'solver-goal', 'repo': str(source_root), 'state_file': 'active.md',
         'status': 'active', 'domain': 'source-read', 'authority_sources': [],
         'adapter': {'kind': 'test_v0', 'status': 'connected-read-only'}}]}))
    rows, _, _ = parse_todo_source(state.read_text())
    for todo in rows['agent']:
        todo['schema_version'] = 'todo_item_v0'
        if task_evidence is not None:
            todo['evidence'] = task_evidence
        if task_note is not None:
            todo['note'] = task_note
    initialize_canonical_authority(runtime, 'solver-goal', build_todo_runtime_shadow_projection(
        goal_id='solver-goal', todos=rows['agent'], handoff_mode='soft_claim'), state_path=state, provider='sqlite')
    for goal in (('research-owner', 'other-owner') if name == 'original' else ()):
        initialize_canonical_authority(w['tmp_path'] / 'runtime', goal,
            build_todo_runtime_shadow_projection(goal_id=goal, todos=[], handoff_mode='soft_claim'),
            state_path=w['tmp_path'] / 'active.md', provider='sqlite')
    log = explore_result_log_path(runtime, 'solver-goal')
    clock = '2026-01-03T00:15:00Z' if done else '2026-01-02T00:15:00Z'
    for name in ('question', 'attempt'):
        append_explore_result_event(log, build_explore_node_event(goal_id='solver-goal',
            node_id=name, node_kind='question', title=name, summary='Original native record', recorded_at=clock))
    append_explore_result_event(log, build_explore_edge_event(goal_id='solver-goal',
        from_node='attempt', to_node='question', edge_type='depends_on', recorded_at=clock))
    raw = {'id': name, 'label': 'Original experiment',
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


def strategy_runs(raw):
    """Independent native index fixture: old strategies survive the status window."""
    from datetime import datetime, timedelta, timezone

    runs = Path(raw['runtime_root']) / 'goals' / 'solver-goal' / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(23):
        vision = {'schema_version': 'goal_vision_replan_contract_v0', 'agent_id': 'source-worker',
            'vision_patch': {'vision_summary': f'Original objective version {index}',
                             'acceptance_summary': 'Compare independently checked outcomes.'},
            'path_delta': {'observed_reality': f'Observation {index}',
                           'stopped': ['Rejected candidate: negative result retained.'],
                           'unresolved_questions': ['Actual subsequent adoption remains unknown.']}}
        rows.append({'goal_id': 'solver-goal', 'agent_id': 'source-worker',
            'generated_at': (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=41 * index)).isoformat(),
            'classification': 'state_recorded', 'json_path': str(runs / f'record-{index}.json'),
            'agent_vision': vision})
    # Accounting records are not fabricated strategy versions.
    rows.append({'goal_id': 'solver-goal', 'agent_id': 'source-worker',
                 'generated_at': '2026-01-02T00:00:00Z', 'classification': 'accounting',
                 'json_path': str(runs / 'accounting.json')})
    index_file = runs / 'index.jsonl'
    index_file.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return index_file, rows


def test_strategy_history_reads_every_original_version_without_writing(workspace):
    raw, request, before = native_source(workspace)
    index_file, rows = strategy_runs(raw)
    before[index_file] = index_file.read_bytes()
    namespace = GoalSource.parse(raw).namespace
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    query = {'goal_id': 'solver-goal', 'source_namespace': namespace}
    endpoint = '/api/chat/goal-source-strategies?'
    value = request(endpoint + urlencode(query))
    assert value['total'] == 23 and value['native_run_count'] == 24
    assert value['goal_id'] == 'solver-goal' and value['source']['namespace'] == namespace
    assert [item['agent_vision'] for item in value['items']] == [row['agent_vision'] for row in rows[-2::-1]]
    assert value['items'] == request(endpoint + urlencode(query))['items']
    for changed, status in (({'goal_id': 'other-owner'}, 404), ({'source_namespace': 'f' * 64}, 409),
                            ({'cursor': 'invented'}, 400), ({'goal_id': ''}, 404)):
        with pytest.raises(HTTPError) as error:
            request(endpoint + urlencode({**query, **changed}))
        assert error.value.code == status
    with pytest.raises(HTTPError) as error:
        request(endpoint + urlencode(query), origin='https://untrusted.invalid')
    assert error.value.code == 403
    assert {path: path.read_bytes() for path in before} == before
    assert not list(Path(raw['runtime_root']).rglob('research-analysis'))


def test_strategy_history_rechecks_source_after_unlink_without_blocking_status(workspace, monkeypatch):
    from threading import Event

    raw, request, _ = native_source(workspace)
    strategy_runs(raw)
    namespace = GoalSource.parse(raw).namespace
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    started, release = Event(), Event()
    original = GoalSource.read_run_history

    def delayed(source, goal_id):
        started.set()
        assert release.wait(15)
        return original(source, goal_id)

    monkeypatch.setattr(GoalSource, 'read_run_history', delayed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(request, '/api/chat/goal-source-strategies?' + urlencode({
            'goal_id': 'solver-goal', 'source_namespace': namespace}))
        try:
            assert started.wait(15)
            status = executor.submit(request, '/status.json?goal_id=solver-goal')
            assert status.result(timeout=10)['ok']
            request('/api/chat/goal-sources', {'goal_id': 'solver-goal',
                'source_namespace': namespace, 'parent_goal_id': 'research-owner'})
        finally:
            release.set()
        with pytest.raises(HTTPError) as error:
            pending.result(timeout=20)
        assert error.value.code == 409


def test_strategy_history_failure_is_explicit_and_recovers(workspace, monkeypatch):
    raw, request, _ = native_source(workspace)
    strategy_runs(raw)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    url = '/api/chat/goal-source-strategies?' + urlencode({
        'goal_id': 'solver-goal', 'source_namespace': GoalSource.parse(raw).namespace})
    original = GoalSource.read_run_history
    monkeypatch.setattr(GoalSource, 'read_run_history', lambda *_: (_ for _ in ()).throw(OSError('private transport detail')))
    with pytest.raises(HTTPError) as error:
        request(url)
    assert error.value.code == 503
    assert b'private transport detail' not in error.value.read()
    monkeypatch.setattr(GoalSource, 'read_run_history', original)
    assert request(url)['total'] == 23


def test_prepared_workspace_and_cli_read_the_same_native_strategies(workspace):
    import subprocess
    from test_runtime_workspace import serving, get
    from loopx.control_plane.status.goal_source import read_endpoint

    raw, _, _ = native_source(workspace)
    index_file, _ = strategy_runs(raw)
    before = index_file.read_bytes()
    source = GoalSource.parse(raw)
    with serving(source, workspace['tmp_path']) as server:
        code, status = get(server, '/status.json?goal_id=solver-goal')
        assert code == 200
        goal = status['run_history']['goals'][0]
        assert goal['id'] == 'solver-goal' and 'source_access' not in goal
        path = '/api/chat/goal-source-strategies?' + urlencode({
            'goal_id': 'solver-goal', 'source_namespace': source.namespace})
        code, browser = get(server, path, timeout=30)
        assert code == 200 and browser['total'] == 23
        client = read_endpoint(server.status_url, 'strategies', goal_id='solver-goal')
        assert browser == client
        cli = subprocess.run([sys.executable, '-m', 'loopx.control_plane.status.goal_source',
            'strategies', '--endpoint', server.status_url, '--goal-id', 'solver-goal'],
            capture_output=True, text=True, check=True, timeout=30)
        assert json.loads(cli.stdout) == browser
        assert get(server, path + '&cursor=unrelated')[0] == 409
    assert index_file.read_bytes() == before


@pytest.mark.parametrize('done', [False, True])
def test_linked_task_details_keep_complete_evidence_and_notes(workspace, done):
    evidence = 'Independent verification. ' + '证据🙂 ' * 180 + ' Retained baseline: reference-v2.'
    note = 'Conditions and limitations. ' + '完整备注 ' * 180 + ' Attribution remains unknown.'
    raw, request, before = native_source(workspace, done=done, task_evidence=evidence, task_note=note)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    namespace = GoalSource.parse(raw).namespace
    query = {'goal_id': 'solver-goal', 'todo_id': 'todo_original', 'source_namespace': namespace}
    endpoint = '/api/chat/todo/detail?'
    detail = request(endpoint + urlencode(query))
    assert detail['evidence'] == evidence and detail['note'] == note
    assert detail['status'] == ('done' if done else 'open')
    assert detail['source_access']['namespace'] == namespace
    assert 'review_basis' not in detail

    for changed in ({'source_namespace': 'f' * 64}, {'todo_id': 'todo_missing'}):
        with pytest.raises(HTTPError) as refused:
            request(endpoint + urlencode({**query, **changed}))
        error = json.load(refused.value)
        assert not error['ok'] and not {'text', 'evidence', 'note'} & error.keys()

    source_registry = Path(raw['registry'])
    source_registry.unlink()
    with pytest.raises(HTTPError) as unavailable:
        request(endpoint + urlencode(query))
    assert unavailable.value.code == 503
    assert 'Retained baseline' not in unavailable.value.read().decode()
    source_registry.write_bytes(before[source_registry])
    assert request(endpoint + urlencode(query))['evidence'] == evidence
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not workspace['count'].exists()


def test_linked_task_details_preserve_absent_metadata(workspace):
    raw, request, _ = native_source(workspace)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    detail = request('/api/chat/todo/detail?' + urlencode({
        'goal_id': 'solver-goal', 'todo_id': 'todo_original', 'source_namespace': GoalSource.parse(raw).namespace}))
    assert detail['evidence'] is None and detail['note'] is None


@pytest.mark.parametrize("separate_chat_store", [False, True])
def test_app_parent_directory_reads_original_tasks_history_and_explicit_analysis(workspace, separate_chat_store):
    w = workspace
    raw, request, before = native_source(w)
    if separate_chat_store:
        w['server'].runtime_root = w['tmp_path'] / 'private-chat'
        w['server'].runtime_root_override = str(w['server'].runtime_root)
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

    analysis_runtime = w['tmp_path'] / 'runtime' / 'goal-sources' / namespace
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
    del w['server'].goal_source_catalog
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


@pytest.mark.parametrize(("view", "source_fails"), [
    ("history", False), ("findings", False), ("history", True),
])
def test_linked_analysis_does_not_wait_for_history_page_io(workspace, monkeypatch, view, source_fails):
    raw, request, before = native_source(workspace)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    namespace = GoalSource.parse(raw).namespace
    analysis_runtime = workspace['tmp_path'] / 'runtime' / 'goal-sources' / namespace
    pack = register_pack(runtime_root=analysis_runtime, owner_goal_id='solver-goal',
        input_path=workspace['tmp_path'] / 'input.json', source_root=workspace['sources'],
        title='Original source evidence')
    query = {'goal_id': 'solver-goal', 'source_namespace': namespace}
    catalog = '/api/chat/research-analysis?' + urlencode(query)
    history = '/api/chat/explore-results?' + urlencode({**query, 'view': view})
    fresh = '/api/chat/explore-results?' + urlencode({**query, 'view': 'history-timing'})
    entered, resume = Event(), Event()
    read_events = GoalSource.read_events

    def slow_events(self, goal_id):
        if self.namespace == namespace:
            entered.set()
            assert resume.wait(25)
        return read_events(self, goal_id)

    monkeypatch.setattr(GoalSource, 'read_events', slow_events)
    source_registry = Path(raw['registry'])
    try:
        with ThreadPoolExecutor(max_workers=2) as clients:
            pending_page = clients.submit(request, history)
            try:
                assert entered.wait(12)
                if source_fails:
                    source_registry.unlink()
                    with pytest.raises(HTTPError) as unavailable:
                        clients.submit(request, fresh).result(timeout=7)
                    assert unavailable.value.code == 503
                else:
                    analysis = clients.submit(request, catalog).result(timeout=7)
                    assert analysis['pack']['pack_id'] == pack['pack_id']
                assert not pending_page.done()
            finally:
                resume.set()
            if source_fails:
                with pytest.raises(HTTPError) as invalidated:
                    pending_page.result(timeout=12)
                assert invalidated.value.code == 409
            else:
                page = pending_page.result(timeout=12)
                assert page['source']['namespace'] == namespace
                assert page['total'] == (3 if view == 'history' else 0)
    finally:
        if source_fails:
            source_registry.write_bytes(before[source_registry])
    assert request(catalog)['pack']['pack_id'] == pack['pack_id']
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not workspace['count'].exists()


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
    timing_url = '/api/chat/explore-results?' + urlencode({
        'goal_id': 'solver-goal', 'source_namespace': GoalSource.parse(raw).namespace, 'view': 'history-timing',
    })
    assert request(timing_url)['first_recorded_at'] == '2026-01-02T00:15:00Z'
    # A real native CLI failure, including private stderr, must not become a
    # generic workspace failure or borrow a local Goal's snapshot.
    source_registry.unlink()
    # Even the warm native status snapshot cannot authorize the timing read.
    with pytest.raises(HTTPError) as unavailable_timing:
        request(timing_url)
    assert unavailable_timing.value.code == 503
    assert str(w['tmp_path']).encode() not in unavailable_timing.value.read()
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
    assert request(timing_url)['first_recorded_at'] == '2026-01-02T00:15:00Z'
    assert not w['count'].exists()


def test_research_reads_pin_code_revision_without_retaining_source_authority(workspace, monkeypatch):
    from loopx.control_plane import effect_runtime

    raw, request, before = native_source(workspace)
    request('/api/chat/goal-sources', {'parent_goal_id': 'research-owner', 'source': raw})
    query = urlencode({'goal_id': 'solver-goal', 'source_namespace': GoalSource.parse(raw).namespace})
    fingerprint = effect_runtime._runtime_fingerprint
    qualifications = []

    def qualify():
        revision = fingerprint()
        qualifications.append(revision)
        return revision

    monkeypatch.setattr(effect_runtime, '_runtime_fingerprint', qualify)

    def read(suffix):
        qualifications.clear()
        value = request('/api/chat/explore-results?' + query + suffix)
        assert len(qualifications) == 1
        return value

    assert read('&view=history')['total'] == 3
    log = explore_result_log_path(Path(raw['runtime_root']), 'solver-goal')
    append_explore_result_event(log, build_explore_node_event(goal_id='solver-goal',
        node_id='new-evidence', title='New source observation', recorded_at='2026-01-04T00:15:00Z'))
    assert any(row.get('title') == 'New source observation' for row in read('&view=history')['items'])
    assert read('&view=history-timing')['last_recorded_at'] == '2026-01-04T00:15:00Z'
    source_registry = Path(raw['registry'])
    source_registry.unlink()
    with pytest.raises(HTTPError) as unavailable:
        read('&view=history-timing')
    assert unavailable.value.code == 503
    source_registry.write_bytes(before[source_registry])
    assert read('&view=history-timing')['last_recorded_at'] == '2026-01-04T00:15:00Z'
    assert not workspace['count'].exists()


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
    wrong = json.loads(events[-1])
    wrong['goal_id'] = 'unrelated'
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
    worker = Thread(target=source_server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(f'http://127.0.0.1:{source_server.server_port}' + endpoint + query + '&view=findings', timeout=30) as response:
            assert json.load(response)['items'] == first['items']
    finally:
        source_server.shutdown()
        worker.join(timeout=5)
        source_server.server_close()

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


def test_same_named_native_goals_have_isolated_workspace_views(workspace):
    w = workspace
    first, request, before = native_source(w)
    second, _, other_before = native_source(w, 'second', 'Measure the second experiment', done=True)
    for view, raw in [('case-a', first), ('case-b', second)]:
        assert request('/api/chat/goal-sources', {
            'parent_goal_id': 'research-owner', 'source': raw, 'workspace_goal_id': view})['ok']
    namespaces = [GoalSource.parse(raw).namespace for raw in (first, second)]
    directory = request('/status.json?view=workspace-directory')
    children = [goal for goal in directory['goals'] if goal.get('source_access')]
    assert [goal['id'] for goal in children] == ['case-a', 'case-b']
    assert all(goal['source_access']['original_goal_id'] == 'solver-goal' for goal in children)
    for view, namespace, expected in [('case-a', namespaces[0], 'Check the original question against its evidence'),
                                      ('case-b', namespaces[1], 'Measure the second experiment')]:
        status = request('/status.json?goal_id=' + view)
        assert status['run_history']['goals'][0]['id'] == view
        todo = next(item for item in status['todo_index']['items'] if item['todo_id'] == 'todo_original')
        assert todo['goal_id'] == view and todo['text'] == expected
        query = urlencode({'goal_id': view, 'source_namespace': namespace})
        assert request('/api/chat/todo/detail?' + query + '&todo_id=todo_original')['text'] == expected
        history = request('/api/chat/explore-results?' + query + '&view=history')
        assert history['goal_id'] == 'solver-goal' and history['total'] == 3
        assert all(item['goal_id'] == 'solver-goal' for item in history['items'])
        timing = request('/api/chat/explore-results?' + query + '&view=history-timing')
        expected_start = '2026-01-03T00:15:00Z' if view == 'case-b' else '2026-01-02T00:15:00Z'
        assert timing['first_recorded_at'] == expected_start
        assert timing['goal_id'] == 'solver-goal' and timing['source_namespace'] == namespace
        assert request('/api/chat/research-analysis?' + query)['catalog']['packs'] == []
        completed = request('/api/chat/completed-todos?' + query)
        assert completed['total'] == (1 if view == 'case-b' else 0)
        if completed['items']:
            assert completed['items'][0]['text'] == expected
            assert request('/api/chat/completed-todos?' + query + '&q=absent')['total'] == 0
    with pytest.raises(HTTPError):
        request('/api/chat/explore-results?' + urlencode({
            'goal_id': 'case-a', 'source_namespace': namespaces[1], 'view': 'history'}))
    with pytest.raises(HTTPError) as wrong_timing_source:
        request('/api/chat/explore-results?' + urlencode({
            'goal_id': 'case-a', 'source_namespace': namespaces[1], 'view': 'history-timing'}))
    assert wrong_timing_source.value.code == 409
    with pytest.raises(HTTPError):
        request('/api/chat/completed-todos?' + urlencode({
            'goal_id': 'case-a', 'source_namespace': namespaces[1]}))
    # Packs retain their original Goal identity inside the physical source store.
    pack = register_pack(runtime_root=w['tmp_path'] / 'runtime' / 'goal-sources' / namespaces[0],
        owner_goal_id='solver-goal', input_path=w['tmp_path'] / 'input.json',
        source_root=w['sources'], title='First experiment')
    base = '/api/chat/research-analysis?'
    first_query = urlencode({'goal_id': 'case-a', 'source_namespace': namespaces[0]})
    assert request(base + first_query)['pack']['pack_id'] == pack['pack_id']
    second_query = urlencode({'goal_id': 'case-b', 'source_namespace': namespaces[1]})
    assert request(base + second_query)['pack'] is None
    w['mode'].write_text('ok')
    result = request(base + 'source_namespace=' + namespaces[0], dict(
        goal_id='case-a', pack_id=pack['pack_id'], analysis_key=pack['analysis_key']))
    assert result['job']['status'] == 'succeeded'
    assert request(base + first_query)['pack']['scope']['goal_id'] == 'solver-goal'
    request('/api/chat/goal-sources', dict(parent_goal_id='research-owner', goal_id='case-a',
                                         source_namespace=namespaces[0]))
    assert [goal['id'] for goal in request('/api/chat/goal-sources')['goals']] == ['case-b']
    assert request('/status.json?goal_id=case-b')['run_history']['goals'][0]['id'] == 'case-b'
    assert all(path.read_bytes() == data for path, data in {**before, **other_before}.items())
