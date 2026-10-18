"""Ordinary Goal capture entry: real SQLite/artifact owners, synthetic Host IO."""
import json
import os
import sys
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.cli import main
from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
from loopx.control_plane.turn_driver.codex_sessions import _store_codex_cli_session
from tests.control_plane.test_research_context_adoption import (
    _ordinary_review_and_work, no_benchmark_dependency,  # noqa: F401 -- fixture is active in this module
)


@pytest.mark.parametrize('real_host', [False, True])
def test_explicit_ordinary_goal_capture_preserves_context_without_activation(tmp_path, monkeypatch, capsys, request, real_host):
    if real_host and not os.environ.get('LOOPX_NATIVE_CHECKPOINT_BIN'):
        pytest.skip('Requires an explicit real Codex binary; inference is a local deterministic provider.')
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    workspace, runtime, registry = tmp_path / 'workspace', tmp_path / 'runtime', tmp_path / 'registry.json'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    prefix = ['--format', 'json', '--registry', str(registry), '--runtime-root', str(runtime)]
    def cli(*args, ok=True):
        code = main([*prefix, *args])
        value = json.loads(capsys.readouterr().out)
        assert code == (0 if ok else 1), value
        assert value['ok'] is ok
        return value
    cli('bootstrap', '--project', str(workspace), '--goal-id', 'source', '--objective', 'Investigate candidates.',
        '--adapter-kind', 'read_only_project_map_v0', '--adapter-status', 'connected-read-only', '--write-scope', '**', '--no-global-sync')
    cli('configure-goal', '--goal-id', 'source', '--registered-agent', 'researcher', '--agent-work-mode', 'researcher=active',
        '--explore-mode', 'planning', '--execute')
    todo = cli('todo', 'add', '--goal-id', 'source', '--role', 'agent', '--claimed-by', 'researcher',
        '--task-class', 'advancement_task', '--text', 'Retain rejected candidate evidence.')['todo_id']
    actor = ['--goal-id', 'source', '--agent-id', 'researcher', '--todo-id', todo, '--turn-instance-id', 'captured-work']
    cli('quota', 'should-run', *actor, '--runtime-profile', 'generic_cli', '--available-capability', 'shell')
    cli('task-lease', 'acquire', '--goal-id', 'source', '--todo-id', todo, '--owner', 'researcher', '--idempotency-key', 'source-work')
    code = workspace / 'candidate.txt'
    code.write_text('7')
    from loopx.capabilities.explore.result_log import (
        append_explore_result_events, build_explore_node_event, build_explore_finding_event, explore_result_log_path,
    )
    log = explore_result_log_path(runtime, 'source')
    events = [build_explore_node_event(goal_id='source', node_id='route', title='Rejected route', status='dead_end'),
        build_explore_finding_event(goal_id='source', node_id='route', finding_id='negative-3', title='Rejected candidate 3',
            status='refuted', summary='Fresh samples refuted candidate 3.', evidence_refs=['validation:fresh-samples'])]
    append_explore_result_events(log, events, expected_goal_id='source')
    source_bytes = log.read_bytes()
    vision = tmp_path / 'vision.json'
    vision.write_text(json.dumps({'schema_version': 'goal_vision_replan_contract_v0', 'state': 'vision_patch_proposed',
        'vision_patch': {'acceptance_summary': 'Preserve rejected candidates and continue complete experiments.'}}))
    cli('refresh-state', *actor, '--classification', 'captured_research', '--delivery-outcome', 'outcome_progress',
        '--agent-vision-json', str(vision), '--no-global-sync', '--suppress-external-sinks')
    cli('quota', 'spend-slot', *actor, '--slots', '1', '--source', 'heartbeat', '--execute')
    pending = {'command': ''}
    def response(body, index, message):
        if index == 2:
            return {'type': 'function_call', 'id': 'fc_complete', 'call_id': 'call_complete',
                'name': 'exec_command', 'arguments': json.dumps({'cmd': pending['command'], 'max_output_tokens': 6000})}
        return message
    stack = ExitStack()
    request.addfinalizer(stack.close)
    # The isolated Docker qualification owns the outer sandbox. The native
    # profile is explicit and must remain identical across capture and fork.
    sandbox = 'danger-full-access' if real_host else 'read-only'
    if real_host:
        from tests.control_plane.codex_checkpoint_fixture import real_native_source
        native_env, source_session, _, model_calls = stack.enter_context(real_native_source(
            tmp_path / 'native', os.environ['LOOPX_NATIVE_CHECKPOINT_BIN'], workspace,
            close_source=True, response_builder=response, sandbox=sandbox))
        home = Path(native_env['CODEX_HOME'])
        for key, value in native_env.items():
            monkeypatch.setenv(key, value)
    else:
        home = tmp_path / 'codex-home'
        home.mkdir()
        source_session = 'source-native'
    history = home / 'history.jsonl'
    history.write_text('{"retained":"complete"}\n')
    secret = home / 'auth.json'
    secret.write_text('synthetic credential must not enter this point')
    values = {'CODEX_BIN': os.environ['LOOPX_NATIVE_CHECKPOINT_BIN'] if real_host else sys.executable,
        'CODEX_HOME': str(home), 'MODEL_NAME': 'fixture', 'REASONING_EFFORT': 'high',
        'LOOPX_PROJECT': str(workspace), 'LOOPX_RUNTIME_ROOT': str(runtime), 'LOOPX_REGISTRY': str(registry),
        'LOOPX_GOAL_ID': 'source', 'LOOPX_AGENT_ID': 'researcher', 'LOOPX_CODEX_SANDBOX': sandbox}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    session = CodexAgentSession(dict(os.environ), context='fresh', sandbox=sandbox, receipt={})
    session.admission.accept_result(lambda: _store_codex_cli_session(runtime, lineage=session.lineage,
        session_scope='agent', session_id=source_session, goal_ref=session.goal_ref, session_profile_digest=session.digest))
    observations = []
    from loopx.hosts.codex_checkpoint import native_session_checkpoint
    def native(env, session_id, **kwargs):
        observations.append(session_id)
        if real_host:
            return native_session_checkpoint(env, session_id, **kwargs)
        return {'components': [{'id': 'native_history', 'source_path': str(history)}],
            'metadata': {'session_id': session_id, 'turn_ids': ['completed-work'], 'completed_turn_id': 'completed-work',
                'store_members': [{'id': 'native_history', 'name': history.name}], 'execution_authority_granted': False}}
    monkeypatch.setattr('loopx.hosts.codex_checkpoint.native_session_checkpoint', native)
    if not real_host:
        from contextlib import nullcontext
        monkeypatch.setattr('loopx.hosts.codex_checkpoint.native_checkpoint_reader',
                            lambda *args, **kwargs: nullcontext(None))
    destination = tmp_path / 'points' / 'captured'
    capture_request = tmp_path / 'capture.json'
    capture_request.write_text(json.dumps({'destination': str(destination), 'components': [{'id': 'code', 'source_path': str(code)}],
        'point_metadata': {'purpose': 'ordinary research continuation'}}))
    capture = ['research-checkpoint', 'capture', '--goal-id', 'source', '--agent-id', 'researcher',
        '--project', str(workspace), '--request-file', str(capture_request), '--turn-instance-id', 'captured-work', '--sandbox', sandbox]
    assert cli(*capture)['dry_run'] and not destination.exists() and not observations
    bad = [*capture]
    bad[bad.index('captured-work')] = 'unsettled-work'
    assert 'settled' in cli(*bad, '--execute', ok=False)['error']
    assert not destination.exists() and not observations
    sealed = cli(*capture, '--execute')
    assert observations == [source_session, source_session]
    assert sealed['execution_authority_granted'] is False
    verified = cli('research-checkpoint', 'verify', '--root', str(destination), '--expected-sha256', sealed['sha256'])
    assert verified['sha256'] == sealed['sha256']
    manifest = json.loads((destination / 'checkpoint.json').read_bytes())
    metadata = manifest['point_metadata']
    context = metadata['research_context']
    raw = (destination / 'components' / context['component_id'] / context['member_path']).read_bytes()
    carrier = json.loads(raw)
    assert carrier['research_evidence']['explore_events'] == events and 'leases' not in carrier['snapshot']
    assert log.read_bytes() == source_bytes and code.read_text() == '7' and secret.read_text().startswith('synthetic credential')
    assert all('auth.json' not in item['path'] for entries in manifest['artifact_witness'].values() for item in entries)
    original = (destination / 'checkpoint.json').read_bytes()
    assert 'new physical absolute path' in cli(*capture, '--execute', ok=False)['error']
    assert (destination / 'checkpoint.json').read_bytes() == original
    assert len(observations) == 2
    if real_host:
        assert model_calls == ['/v1/responses']  # Capture and verify start no inference.
        config = tmp_path / 'source-config.toml'
        config.write_bytes((home / 'config.toml').read_bytes())
        # Isolated fixture only: retain the closed source home, reconstruct a
        # fresh home at the qualified provider's same fixed guest path.
        home.rename(home.with_name('preserved-source-home'))
        branch_registry, branch_root = tmp_path / 'branch-registry.json', tmp_path / 'branch-runtime'
        branch_prefix = ['--format', 'json', '--registry', str(branch_registry), '--runtime-root', str(branch_root)]
        def branch(*args):
            code = main([*branch_prefix, *args])
            result = json.loads(capsys.readouterr().out)
            assert code == 0, result
            return result
        branch('bootstrap', '--project', str(workspace), '--goal-id', 'branch', '--objective', 'Continue retained experiments.',
            '--adapter-kind', 'read_only_project_map_v0', '--adapter-status', 'connected-read-only', '--write-scope', '**', '--no-global-sync')
        branch('configure-goal', '--goal-id', 'branch', '--registered-agent', 'branch-actor', '--agent-work-mode',
            'branch-actor=active', '--explore-mode', 'planning', '--execute')
        current_todo = branch('todo', 'add', '--goal-id', 'branch', '--role', 'agent', '--claimed-by', 'branch-actor',
            '--task-class', 'advancement_task', '--text', 'Complete a retained two-candidate experiment.')['todo_id']
        common = ['--goal-id', 'branch', '--agent-id', 'branch-actor', '--project', str(workspace), '--sandbox', sandbox,
            '--root', str(destination), '--expected-sha256', sealed['sha256']]
        admitted = branch('research-checkpoint', 'admit-session', *common, '--config-path', str(config), '--execute')
        assert admitted['session_id'] != source_session and admitted['session_binding_admitted']
        lease = branch('task-lease', 'acquire', '--goal-id', 'branch', '--todo-id', current_todo,
            '--owner', 'branch-actor', '--idempotency-key', 'branch-lease')
        revision = branch('todo', 'list', '--goal-id', 'branch', '--todo-id', current_todo)['authority_read']['provider_revision']
        adopted = branch('research-checkpoint', 'adopt-context', *common, '--source-todo-id', todo,
            '--todo-id', current_todo, '--operation-id', 'ordinary-native-context', '--expected-revision', revision,
            '--lease-key', 'branch-lease', '--lease-version', str(lease['lease']['version']), '--execute')
        inherited = branch('explore', 'turn-context', '--goal-id', 'branch', '--agent-id', 'branch-actor')
        assert any(row['finding_id'] == 'negative-3' and row['status'] == 'refuted'
            for row in inherited['graph']['recent_findings'])
        import shlex
        script = ('import json; from pathlib import Path; '
            'c=json.loads(Path(' + repr(adopted['artifact']) + ').read_text()); '
            'assert any(e.get("event_kind")=="finding" and e.get("result_id")=="negative-3" and e.get("status")=="refuted" for e in c["research_evidence"]["explore_events"]); '
            'assert Path("candidate.txt").read_text()=="7"; '
            'Path("complete-experiment.json").write_text(json.dumps({"candidates":[8,9],"rejected":[3],"retained_start":7})); '
            'print("Retained counterexample used; candidates 8 and 9 completed together.")')
        pending['command'] = 'python3 -c ' + shlex.quote(script)
        def work(guard):
            from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport
            env = {**os.environ, 'LOOPX_REGISTRY': str(branch_registry), 'LOOPX_RUNTIME_ROOT': str(branch_root),
                'LOOPX_GOAL_ID': 'branch', 'LOOPX_AGENT_ID': 'branch-actor'}
            with StdioNativeGoalTransport.spawn([env['CODEX_BIN'], '-c', 'features.goals=false', 'app-server', '--listen', 'stdio://'],
                    cwd=str(workspace), env=env, stderr=subprocess.DEVNULL, response_timeout_sec=30) as native:
                native.request('initialize', {'clientInfo': {'name': 'ordinary_checkpoint_fixture', 'version': '0.1.0'},
                    'capabilities': {'experimentalApi': True}})
                native.notify('initialized', {})
                native.request('thread/resume', {'threadId': admitted['session_id'], 'cwd': str(workspace),
                    'sandbox': sandbox, 'approvalPolicy': 'never', 'persistExtendedHistory': True})
                turn = native.request('turn/start', {'threadId': admitted['session_id'], 'input': [{'type': 'text',
                    'text': json.dumps(guard['interaction_contract']) + '\nUse retained evidence in one complete experiment.'}]})['turn']['id']
                end = time.monotonic() + 30
                while time.monotonic() < end:
                    rows = native.request('thread/turns/list', {'threadId': admitted['session_id'],
                        'sortDirection': 'asc', 'itemsView': 'full', 'limit': 100})['data']
                    if rows and rows[-1]['id'] == turn and rows[-1]['status'] == 'completed':
                        assert len(rows) == len(metadata['native_session']['turn_ids']) + 1
                        break
                    time.sleep(.05)
                else:
                    raise AssertionError('Ordinary native continuation did not complete')
        _ordinary_review_and_work(branch, workspace, tmp_path, current_todo, sealed['sha256'], monkeypatch, work=work)
        assert len(model_calls) == 3
        assert code.read_text() == '7' and log.read_bytes() == source_bytes


def test_mutation_preview_requires_no_native_host_and_dispatches_no_operation(tmp_path, capsys):
    assert main(['--format', 'json', 'research-checkpoint', 'admit-session', '--root', str(tmp_path / 'absent'),
        '--expected-sha256', 'a' * 64, '--goal-id', 'destination', '--agent-id', 'researcher',
        '--config-path', str(tmp_path / 'unread'), '--sandbox', 'read-only']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['dry_run'] and result['execution_authority_granted'] is False
    assert list(tmp_path.iterdir()) == []
