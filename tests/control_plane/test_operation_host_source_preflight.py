"""Operation Hosts keep complete work preparation ahead of native initialization."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from loopx.control_plane.turn_driver import codex_operation_host, dsh_operation_host
from loopx.control_plane.turn_driver.host_candidate import complete_host_input
from tests.control_plane.test_turn_host_decision_capture import (
    _pending_goal_contract,
    complete_host_request as complete_host_request,
)


class PreflightObserved(Exception):
    """Stop at the native boundary, before this test starts any provider."""


@pytest.mark.parametrize('wire', [False, True], ids=['executor', 'complete-wire'])
def test_optional_session_metadata_preserves_prompt_and_standalone_scope_checks(complete_host_request, wire):
    from loopx.control_plane.turn_driver.codex_sessions import _lineage
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

    internal, _source = complete_host_request
    request = complete_host_input(internal) if wire else deepcopy(internal)
    original = extract_turn_authority(request)
    prepared = extract_turn_authority(request, include_session_lineage=True)
    assert render_prompt(prepared) == render_prompt(original)
    lineage = prepared.pop('_session_lineage')
    assert prepared == original
    for scope in ['todo', 'agent']:
        assert _lineage(request, session_scope=scope, checked_lineage=lineage) == _lineage(request, session_scope=scope)
    assert lineage == prepared['_work_identity']


def _options(tmp_path, source):
    registry = Path(source['registry'])
    goal = next(g for g in json.loads(registry.read_text())['goals']
                if g.get('id', g.get('goal_id')) == source['goal_id'])
    executable = tmp_path / 'operation-preflight-executable'
    executable.write_text('synthetic file pin; this executable is never started')
    executable.chmod(0o700)
    return dict(runtime_root=Path(source['runtime_root']), registry_path=registry,
                project=Path(goal['repo']), model='test-model', reasoning_effort='xhigh'), executable


@pytest.mark.parametrize('host', ['codex', 'dsh'])
@pytest.mark.parametrize('wire', [False, True], ids=['executor', 'complete-wire'])
@pytest.mark.parametrize('state', ['valid', 'pending_goal', 'damaged_source'])
def test_operation_preflight_uses_one_checked_source_before_native_boundary(
    complete_host_request, tmp_path, monkeypatch, host, wire, state,
):
    from loopx.control_plane.quota import turn_envelope

    original, source = complete_host_request
    request = deepcopy(original)
    if state == 'pending_goal':
        request['work_contract'], request['turn_envelope'] = _pending_goal_contract(source)
    if wire:
        request = complete_host_input(request) if state != 'pending_goal' else {
            **{k: v for k, v in request.items() if k != 'turn_envelope'},
            'schema_version': 'loopx_turn_host_request_v1',
        }
    if state == 'damaged_source':
        request['work_contract']['reason'] = 'changed source before operation preflight'
    before = deepcopy(request)
    options, executable = _options(tmp_path, source)
    calls, native = [], []
    projection = turn_envelope.effect_runtime_projection_result

    def observe(method, arguments, **kwargs):
        if arguments.get('operation') in {'host_dispatch_context', 'complete_host_lineage'}:
            calls.append((arguments['operation'], deepcopy(arguments)))
        return projection(method, arguments, **kwargs)

    def native_boundary(*_args, **_kwargs):
        native.append(True)
        raise PreflightObserved

    monkeypatch.setattr(turn_envelope, 'effect_runtime_projection_result', observe)
    if host == 'codex':
        monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, 'start', native_boundary)

        def run():
            return codex_operation_host.run_codex_operation_host(
                request, **options, codex_bin=str(executable), timeout_seconds=5)
    else:
        # Only make the preflight import available. No SDK class is invoked;
        # the real SDK/runtime qualification is separate from this boundary oracle.
        monkeypatch.setitem(sys.modules, 'deepseek_harness', SimpleNamespace(
            DeepSeekHarness=object, DeepSeekHarnessConfig=object))
        monkeypatch.setattr(dsh_operation_host, '_sdk_runtime_pin', native_boundary)

        def run():
            return dsh_operation_host._run_dsh_operation_host(
                request, **options, dsh_home=tmp_path / 'dsh-home',
                operation_module=executable, provider='fixture-provider', max_tokens=128,
                owner=SimpleNamespace(session_id=None))

    if state == 'valid':
        with pytest.raises(PreflightObserved):
            run()
        assert native == [True]
        assert [operation for operation, _ in calls] == ['host_dispatch_context']
        assert calls[0][1]['include_session_lineage'] is True
    else:
        with pytest.raises((RuntimeError, ValueError)) as rejected:
            run()
        if state == 'pending_goal':
            assert 'Goal' in str(rejected.value) + str(rejected.value.__cause__)
        else:
            assert any(word in str(rejected.value) for word in ['complete', 'integrity', 'source'])
        assert native == []
    assert request == before
    assert not list(Path(source['runtime_root']).glob('goals/*/turn-sessions/*.json'))


@pytest.mark.parametrize('wire', [False, True], ids=['executor', 'complete-wire'])
def test_codex_operation_rechecks_source_after_native_initialization(
    complete_host_request, tmp_path, monkeypatch, wire,
):
    from tests.test_codex_operation_host import FAKE_SERVER

    internal, source = complete_host_request
    request = complete_host_input(internal) if wire else deepcopy(internal)
    options, executable = _options(tmp_path, source)
    executable.write_text(FAKE_SERVER)
    prompt = tmp_path / 'sent-prompt.txt'
    monkeypatch.setenv('FAKE_OPERATION_PROMPT_FILE', str(prompt))
    start = codex_operation_host.CodexChatAgentSession.start
    sessions = []

    def changed_after_start(**kwargs):
        session = start(**kwargs)
        sessions.append(session.thread_id)
        request['work_contract']['reason'] = 'changed during native initialization'
        return session

    monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, 'start', changed_after_start)
    with pytest.raises((RuntimeError, ValueError), match='complete|integrity|source'):
        codex_operation_host.run_codex_operation_host(
            request, **options, codex_bin=str(executable), timeout_seconds=5)
    assert len(sessions) == 1
    assert not prompt.exists()  # The fresh post-start check precedes turn/start.


@pytest.mark.parametrize('wire', [False, True], ids=['executor', 'complete-wire'])
def test_complete_dsh_operation_preflight_reaches_real_sdk_context_and_result(
    complete_host_request, tmp_path, monkeypatch, wire,
):
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.turn_driver.codex_sessions import _lineage, load_codex_cli_session
    from tests.control_plane.test_dsh_operation_sdk import _sdk_response_stream, tool_results

    pytest.importorskip('deepseek_harness')
    module = os.environ.get('DSH_NATIVE_OPERATION_MODULE')
    if not module:
        pytest.skip('explicit compiled native operation module required')
    internal, source = complete_host_request
    request = complete_host_input(internal) if wire else deepcopy(internal)
    before = deepcopy(request)
    lineage = _lineage(request)
    messages, errors, notifications, projections = [], [], [], []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert body['model'] == 'fixture-model'
                assert self.path.endswith(('/messages', '/chat/completions'))
                index = len(messages)
                assert index < 2, 'bounded loopback provider request count exceeded'
                messages.append(body['messages'])
                delta = ({'role': 'assistant', 'reasoning_content': '', 'tool_calls': [{
                    'index': 0, 'id': 'preflight-context', 'type': 'function',
                    'function': {'name': 'loopx_operation', 'arguments': '{"action":"context"}'},
                }]} if index == 0 else {'role': 'assistant', 'content': json.dumps({
                    'result_kind': 'wait', 'classification': 'complete_source_context_observed',
                    'summary': 'Synthetic context read; no domain effect'})})
                data = _sdk_response_stream(index, delta, 'tool_calls' if index == 0 else 'stop',
                                            lambda _index: {'action': 'context'}, path=self.path)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                errors.append(repr(exc))
                self.send_error(500)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'loopback-fixture-placeholder')
    monkeypatch.setenv('DEEPSEEK_BASE_URL', f'http://127.0.0.1:{server.server_port}/anthropic')
    projection = turn_envelope.effect_runtime_projection_result

    def observe(method, arguments, **kwargs):
        if arguments.get('operation') in {'host_dispatch_context', 'complete_host_lineage'}:
            projections.append(arguments['operation'])
        return projection(method, arguments, **kwargs)

    monkeypatch.setattr(turn_envelope, 'effect_runtime_projection_result', observe)
    options, _executable = _options(tmp_path, source)
    options.update(model='fixture-model', reasoning_effort='high')
    try:
        result = dsh_operation_host.run_dsh_operation_host(
            request, **options, dsh_home=tmp_path / 'sdk-home', operation_module=Path(module),
            provider='deepseek-official', max_tokens=1024, timeout_seconds=60,
            on_notification=notifications.append)
        assert result['result_kind'] == 'wait' and result['turn_key'] == request['turn_key']
        assert len(messages) == 2 and not errors
        contexts = [row for row in tool_results(notifications) if isinstance(row.get('executor'), dict)]
        assert len(contexts) == 1
        assert all(contexts[0][field] == value for field, value in lineage.items())
        assert contexts[0]['executor']['todo_id'] == lineage['todo_id']
        binding = load_codex_cli_session(options['runtime_root'], lineage=lineage, host='dsh')
        assert binding['session_id'] == contexts[0]['executor']['session_id']
        assert projections == ['host_dispatch_context']
        assert request == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
