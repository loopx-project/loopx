"""Consumer policy through the actual private owner, relay HTTP and Chat startup."""
import importlib
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from loopx.capabilities.native_chat.codex_auth import CodexApiTransport, CodexHostAuthUnavailable


@pytest.fixture
def account_owner(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'packages/loopx-codex-provider-routing/src'))
    native = importlib.import_module('loopx_codex_provider_routing.native')
    relay = importlib.import_module('loopx_codex_provider_routing.openai_relay')
    calls = []
    owner = native.NativeAccounts(tmp_path, transport=lambda *_a, **_k: calls.append('native') or {
        'slots': ['a'], 'active_slot': 'a', 'current_login_matches': True, 'recovery_required': False,
        'api_fallback_supported': True}, clock=lambda: 1000)
    owner.api_keys().add(expected_revision=0, label='Synthetic', api_key='sk-' + 'synthetic-only-key-' * 3)
    return owner, native, relay, calls


def test_api_primary_is_independent_and_preserved_by_worker_configuration(account_owner):
    owner, native, _, calls = account_owner
    before = owner.api_keys().path.read_bytes()
    result = owner.configure(expected_revision=0, enabled=False, slots=[], steward_api_role='primary')
    assert result['policy']['steward_api_role'] == 'primary' and not result['policy'].get('api_fallback')
    assert calls == [] and owner.api_keys().path.read_bytes() == before
    result = owner.configure(expected_revision=1, enabled=False, slots=[], api_fallback=False)
    assert result['policy']['steward_api_role'] == 'primary'
    assert owner.steward_route(codex_home=Path('/synthetic-home'), primary='antigravity',
        pool=['antigravity', 'codex'], current='antigravity', primary_capacity=None)['endpoint'] == 'codex'
    assert owner.model_transport(codex_home=Path('/synthetic-home'), consumer='worker') is None
    assert calls == []
    with pytest.raises(Exception, match='invalid_policy'):
        owner.configure(expected_revision=2, enabled=False, slots=[], steward_api_role='arbitrary')
    assert native.private_read(owner.path)['revision'] == 2


def test_api_primary_cannot_replace_a_different_bound_native_profile(account_owner):
    owner, native, _, calls = account_owner
    owner.configure(expected_revision=0, enabled=False, slots=[], steward_api_role='primary')
    state = native.private_read(owner.path)
    state['adapter'] = {'profile': {'home': '/synthetic-registered-home'}}
    native.atomic(owner.path, state)
    before = owner.path.read_bytes()
    other = Path('/synthetic-other-home')
    assert owner.model_transport(codex_home=other, consumer='steward') is None
    assert owner.conversation_epoch(codex_home=other, consumer='steward') is None
    assert owner.steward_route(codex_home=other, primary='antigravity', pool=['antigravity', 'codex'],
        current='antigravity', primary_capacity=None)['endpoint'] is None
    assert calls == [] and owner.path.read_bytes() == before


def test_disabled_account_automation_starts_no_policy_or_native_process(account_owner, monkeypatch):
    owner, native, _, calls = account_owner
    monkeypatch.setattr(native, 'policy', lambda *_: pytest.fail('Disabled observation must start no policy process'))
    home = Path('/synthetic-home')
    for consumer in ('worker', 'steward'):
        assert owner.model_transport(codex_home=home, consumer=consumer) is None
        assert owner.conversation_epoch(codex_home=home, consumer=consumer) is None
    assert owner.steward_route(codex_home=home, primary='antigravity', pool=['antigravity', 'codex'],
        current='antigravity', primary_capacity=None)['endpoint'] is None
    assert calls == [] and not owner.path.exists()


def test_steward_and_worker_tokens_cannot_cross_billing_routes(account_owner):
    owner, native, relay, _ = account_owner
    owner.configure(expected_revision=0, enabled=False, slots=[], steward_api_role='primary')
    upstream = []
    class Response(io.BytesIO):
        status = 200
        headers = {'Content-Type': 'text/event-stream'}
    class Upstream:
        def open(self, request, timeout):
            upstream.append(request)
            assert request.full_url == 'https://api.openai.com/v1/responses'
            return Response(b'data: {"type":"response.completed"}\n\n')
    token = 'synthetic-worker-loopback-token'
    value = {'port': 0, 'token': token, 'steward_token': 'independent-synthetic-steward-token'}
    server = relay.RelayServer(owner, value, opener=Upstream())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(path, bearer):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',
            data=json.dumps({'model': 'synthetic-model', 'input': 'fixture', 'stream': True}).encode(),
            headers={'Authorization': 'Bearer ' + bearer, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as error:
            error.read()
            return error.code
    try:
        steward_token = relay.consumer_token(value, 'steward')
        assert steward_token != token
        assert post('/steward/v1/responses', token) == 401
        assert post('/v1/responses', steward_token) == 401
        assert post('/v1/responses', token) == 503  # Worker's subscription policy remains off.
        assert not upstream
        assert post('/steward/v1/responses', steward_token) == 200 and len(upstream) == 1
        state = native.private_read(owner.path)
        state['policy']['steward_api_role'] = 'disabled'
        native.atomic(owner.path, state)
        assert post('/steward/v1/responses', steward_token) == 503 and len(upstream) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_model_transport_hides_token_from_config_and_rejects_native_alias_collision():
    transport = CodexApiTransport('http://127.0.0.1:32100/steward/v1', 'synthetic-secret')
    original = {'shell_environment_policy': {'inherit': 'all', 'exclude': ['OTHER_SECRET'], 'set': {'SAFE': 'yes'}}}
    config = transport.configuration(original)
    assert 'synthetic-secret' not in repr(transport) + json.dumps(config)
    assert config['shell_environment_policy']['set'] == {'SAFE': 'yes'}
    assert transport.env_key in config['shell_environment_policy']['exclude']
    assert config['model_providers'][transport.provider]['env_key'] == transport.env_key
    assert original['shell_environment_policy']['exclude'] == ['OTHER_SECRET']
    with pytest.raises(CodexHostAuthUnavailable):
        transport.configuration(original, {'model_providers': {transport.provider: {'base_url': 'https://untrusted.invalid'}}})


def test_known_api_rejection_returns_primary_to_existing_subscription_pool(account_owner):
    owner, native, _, _ = account_owner
    owner.configure(expected_revision=0, enabled=False, slots=[], steward_api_role='primary')
    key_id = owner.api_keys().status()['keys'][0]['id']
    owner.api_keys().record(key_id, status=429, code='insufficient_quota', model='synthetic-model', headers={})
    assert owner.steward_route(codex_home=Path('/synthetic-home'), primary='antigravity',
        pool=['antigravity', 'codex'], current='antigravity', primary_capacity=None)['endpoint'] is None
    # Native owner remains selected; no credential or native switch was attempted.
    assert owner.model_transport(codex_home=Path('/synthetic-home'), consumer='steward', model='synthetic-model') is None
    assert native.private_read(owner.path)['policy']['steward_api_role'] == 'primary'


@pytest.mark.parametrize('resume', [False, True])
def test_chat_start_uses_model_only_api_and_preserves_original_thread_and_project_store(tmp_path, monkeypatch, resume):
    from loopx import chat_agent
    from loopx.capabilities.native_chat import codex_auth
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from tests.test_chat_agent import _FakeAppServerProcess, _stub_app_server_process

    home, workspace = tmp_path / 'host', tmp_path / 'workspace'
    workspace.mkdir()
    context = ChatProjectContexts([workspace], filesystem_scope='workspace_only').available()[0]
    transport = CodexApiTransport('http://127.0.0.1:32100/steward/v1', 'synthetic-secret')
    observed = []
    monkeypatch.setattr(codex_auth, 'native_api_transport', lambda root, base, **kw:
        observed.append((root, base, kw)) or transport)
    process = _FakeAppServerProcess(thread_response={'thread': {'id': 'original-native-thread'},
        'modelProvider': transport.provider, 'activePermissionProfile': {'id': 'loopx_workspace_only_write'},
        'runtimeWorkspaceRoots': [str(workspace)]})
    launches = []
    _stub_app_server_process(monkeypatch, process, launched=launches)
    session = chat_agent.CodexChatAgentSession.start(codex_bin='codex', codex_home=home,
        work_dir=workspace, goal_id=None, objective='fixture', project_context=context,
        account_runtime_root=tmp_path / 'runtime', account_consumer='steward',
        resume_thread_id='original-native-thread' if resume else None)
    try:
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        thread = next(p for p in packets if p.get('method') in ('thread/start', 'thread/resume'))
        assert not any(p.get('method') == 'account/login/start' for p in packets)
        assert thread['params']['modelProvider'] == transport.provider
        assert transport.env_key in thread['params']['config']['shell_environment_policy']['exclude']
        assert observed[0][2]['consumer'] == 'steward'
        assert launches[0]['env'][transport.env_key] == 'synthetic-secret'
        assert 'synthetic-secret' not in process.stdin.getvalue()
        assert session.thread_id == 'original-native-thread'
        assert not list(workspace.rglob('auth.json')) and not (home / 'auth.json').exists()
    finally:
        session.close()
