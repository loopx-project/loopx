import io
import json
import os
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx import chat_agent
from loopx.capabilities.native_chat import codex_auth
from loopx.capabilities.native_chat.project_context import ChatProjectContexts


def provider_fixture(home, provider, thread_id="original-thread"):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "sessions" / "synthetic.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": thread_id, "model_provider": provider}}) + "\n")
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, archived INTEGER)")
        db.execute("INSERT INTO threads VALUES (?, ?, 0)", (thread_id, str(path)))
    return path


def write_auth(home, access="synthetic-access", account="synthetic-account"):
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt",
        "tokens": {"access_token": access, "account_id": account,
                   "refresh_token": "synthetic-private-refresh", "id_token": "synthetic-private-id"},
        "unrelated_private_data": "never-forward"}))


def test_only_short_lived_model_credentials_cross_the_host_boundary(tmp_path):
    home, isolated = tmp_path / "account", tmp_path / "project-store"
    write_auth(home)
    broker = codex_auth.for_isolated_process(home, isolated, "synthetic-codex")
    assert broker.read() == {"accessToken": "synthetic-access", "chatgptAccountId": "synthetic-account"}
    assert "synthetic-access" not in repr(broker)
    assert not isolated.exists()
    assert codex_auth.for_isolated_process(tmp_path / "missing", isolated, "synthetic-codex") is None
    write_auth(isolated, account="separate-project-account")
    assert codex_auth.for_isolated_process(home, isolated, "synthetic-codex") is None


def test_concurrent_refresh_uses_native_account_owner_once(tmp_path, monkeypatch):
    write_auth(tmp_path)
    brokers = [codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex") for _ in range(6)]
    for broker in brokers:
        broker.read()
    calls = []
    def refresh(binary, home, **kwargs):
        calls.append((binary, home))
        write_auth(home, "synthetic-rotated")
    monkeypatch.setattr(codex_auth, "_native_refresh", refresh)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda b: b.read(refresh=True, previous_account_id="synthetic-account"), brokers))
    assert len(calls) == 1
    assert all(r["accessToken"] == "synthetic-rotated" for r in results)


def test_refresh_failure_is_redacted_and_can_retry_without_rebinding(tmp_path, monkeypatch):
    write_auth(tmp_path)
    broker = codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex")
    broker.read()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-refresh")
    monkeypatch.setattr(codex_auth, "_native_refresh", fail)
    with pytest.raises(codex_auth.CodexHostAuthUnavailable) as error:
        broker.read(refresh=True, previous_account_id="synthetic-account")
    assert "synthetic-private" not in str(error.value)
    assert error.value.__suppress_context__
    monkeypatch.setattr(codex_auth, "_native_refresh", lambda binary, home, **kw: write_auth(home, "synthetic-recovered"))
    assert broker.read(refresh=True, previous_account_id="synthetic-account")["accessToken"] == "synthetic-recovered"
    write_auth(tmp_path, "different-token", "different-account")
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        broker.read(refresh=True, previous_account_id="synthetic-account")


@pytest.mark.parametrize("value", [None, {}, {"auth_mode": "apikey"},
    {"auth_mode": "chatgpt", "tokens": {}},
    {"auth_mode": "chatgpt", "tokens": {"access_token": False, "account_id": "account"}}])
def test_invalid_host_auth_never_uses_wider_or_fallback_credentials(tmp_path, value):
    (tmp_path / "auth.json").write_text(json.dumps(value))
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex").read()


def test_host_auth_symlink_is_rejected(tmp_path):
    other = tmp_path / "other"
    write_auth(other)
    (tmp_path / "auth.json").symlink_to(other / "auth.json")
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex").read()


def test_broken_project_auth_symlink_cannot_switch_to_host_account(tmp_path):
    home, isolated = tmp_path / "host", tmp_path / "project"
    write_auth(home)
    isolated.mkdir()
    (isolated / "auth.json").symlink_to(tmp_path / "missing-private-file")
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.for_isolated_process(home, isolated, "synthetic-codex")


def test_native_callback_refresh_recovers_but_preserves_account_and_request_gates(tmp_path, monkeypatch):
    from tests.test_chat_agent import _FakeAppServerProcess
    import queue

    write_auth(tmp_path)
    broker = codex_auth.CodexHostModelAuth(tmp_path, "synthetic-codex")
    broker.read()
    monkeypatch.setattr(codex_auth, "_native_refresh", lambda binary, home, **kw: write_auth(home, "synthetic-rotated"))
    process = _FakeAppServerProcess()
    session = chat_agent.CodexChatAgentSession(process=process, messages=queue.Queue(),
        thread_id="same-thread", work_dir=tmp_path, _host_model_auth=broker)
    request = {"id": 9, "method": "account/chatgptAuthTokens/refresh",
               "params": {"reason": "unauthorized", "previousAccountId": "synthetic-account"}}
    assert session._check_server_gate(request)
    assert json.loads(process.stdin.getvalue().splitlines()[-1])["result"] == {
        "accessToken": "synthetic-rotated", "chatgptAccountId": "synthetic-account"}
    assert session.thread_id == "same-thread"
    for params in ([], {"reason": "other", "previousAccountId": "synthetic-account"},
                   {"reason": "unauthorized"}):
        assert session._check_server_gate({**request, "params": params})
        assert "error" in json.loads(process.stdin.getvalue().splitlines()[-1])
    with pytest.raises(chat_agent.CodexChatAgentError):
        session._check_server_gate({"id": 10, "method": "item/commandExecution/requestApproval", "params": {}})


def test_native_refresh_never_opens_a_thread_or_passes_credentials_in_argv(tmp_path, monkeypatch):
    from tests.test_chat_agent import _FakeAppServerProcess
    process = _FakeAppServerProcess(thread_response={"account": {"type": "chatgpt"}})
    launches = []
    monkeypatch.setattr(codex_auth.subprocess, "Popen", lambda command, **kw: launches.append((command, kw)) or process)
    codex_auth._native_refresh("synthetic-codex", tmp_path)
    packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [p["method"] for p in packets] == ["initialize", "initialized", "account/read"]
    assert packets[-1]["params"] == {"refreshToken": True}
    assert launches[0][1]["env"]["CODEX_HOME"] == str(tmp_path)
    assert "synthetic-access" not in str(launches[0][0])
    assert process.poll() == 0


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("native_provider", ["loopx_host_chatgpt_http", None, "unexpected-provider", "collision"])
def test_isolated_start_and_resume_supply_auth_only_over_private_rpc(tmp_path, monkeypatch, resume, native_provider):
    from tests.test_chat_agent import _FakeAppServerProcess
    home = tmp_path / "host"
    write_auth(home)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ChatProjectContexts([workspace], filesystem_scope="workspace_only").available()[0]
    profile = "loopx_workspace_only_write"
    config = {"model_providers": {"loopx_host_chatgpt_http": {
        "base_url": "https://example.invalid", "env_key": "PRIVATE_KEY"}}} if native_provider == "collision" else {}
    process = _FakeAppServerProcess(config_response={"config": config}, thread_response={
        "thread": {"id": "thread-loopx-chat"}, "activePermissionProfile": {"id": profile},
        "runtimeWorkspaceRoots": [str(workspace)], "modelProvider": native_provider})
    process.stdout = io.StringIO(json.dumps({"id": 1, "result": {}}) + "\n" +
        json.dumps({"id": 4, "result": {"type": "chatgptAuthTokens"}}) + "\n" + process.stdout.getvalue().split("\n", 1)[1])
    real_which = chat_agent.shutil.which
    monkeypatch.setattr(chat_agent.shutil, "which", lambda name: str(tmp_path / "native-codex") if name == "synthetic-codex" else real_which(name))
    real_popen = chat_agent.subprocess.Popen
    launches = []
    def launch(command, **kwargs):
        if command[0] != str(tmp_path / "native-codex"):
            return real_popen(command, **kwargs)
        launches.append((command, kwargs))
        return process
    monkeypatch.setattr(chat_agent.subprocess, "Popen", launch)
    def start():
        return chat_agent.CodexChatAgentSession.start(codex_bin="synthetic-codex", work_dir=workspace,
            goal_id=None, objective="project", project_context=context, codex_home=home,
            resume_thread_id="thread-loopx-chat" if resume else None, model="synthetic-model")
    if native_provider != "loopx_host_chatgpt_http":
        collision = native_provider == "collision"
        with pytest.raises(chat_agent.CodexChatAgentError, match=(
                "conflicts with project configuration" if collision else "requested conversation provider")):
            start()
        assert process.poll() is not None
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert not any(p.get("method") == "turn/start" for p in packets)
        assert sum(p.get("method") in {"thread/start", "thread/resume"} for p in packets) == (0 if collision else 1)
        return
    session = start()
    try:
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        login = next(p for p in packets if p["method"] == "account/login/start")
        assert login["params"] == {"type": "chatgptAuthTokens", "accessToken": "synthetic-access", "chatgptAccountId": "synthetic-account"}
        thread = next(p for p in packets if p["method"] in {"thread/start", "thread/resume"})
        assert thread["params"]["permissions"] == profile
        assert thread["params"]["modelProvider"] == "loopx_host_chatgpt_http"
        provider = thread["params"]["config"]["model_providers"]["loopx_host_chatgpt_http"]
        assert provider == {"name": "LoopX host ChatGPT HTTP", "wire_api": "responses",
                            "requires_openai_auth": True, "supports_websockets": False}
        assert thread["params"]["config"]["permissions"][profile]["network"]["enabled"] is False
        assert thread["params"]["config"]["skills"]["include_instructions"] is False
        assert "LOOPX_CHAT_CODEX_MODEL_PROVIDER" not in launches[0][1]["env"]
        assert "synthetic-access" not in str(thread)
        assert "synthetic-access" not in str(launches)
        isolated = Path(launches[0][1]["env"]["CODEX_HOME"])
        assert isolated != home and not (isolated / "auth.json").exists()
        assert session.next_request_id == 5
        session._check_server_gate({"id": 9, "method": "account/chatgptAuthTokens/refresh", "params": {"reason": "unauthorized", "previousAccountId": "wrong-account"}})
        assert json.loads(process.stdin.getvalue().splitlines()[-1])["error"]["message"] == "Trusted-host model authentication unavailable."
    finally:
        session.close()


@pytest.mark.parametrize('provider', sorted(codex_auth.TRANSPORT_PROVIDERS))
@pytest.mark.parametrize('api', [False, True])
def test_transport_switch_keeps_process_and_resume_on_original_provider(tmp_path, monkeypatch, provider, api):
    from tests.test_chat_agent import _FakeAppServerProcess, _stub_app_server_process
    home = tmp_path / 'native'
    original = provider_fixture(home, provider).read_bytes()
    transport = codex_auth.CodexApiTransport('http://127.0.0.1:19/v1', 'synthetic-scoped-key') if api else None
    monkeypatch.setattr(codex_auth, 'native_api_transport', lambda *a, **kw: transport)
    process = _FakeAppServerProcess(model_provider=provider, thread_id='original-thread',
        config_response={'config': {}, 'layers': [{'name': {'type': 'sessionFlags'},
            'config': {'model_providers': {provider: {}}}}]})
    if api and provider == 'openai':
        first, rest = process.stdout.getvalue().split('\n', 1)
        process.stdout = io.StringIO(first + '\n' + json.dumps({'id': 4, 'result': {'type': 'apiKey'}}) + '\n' + rest)
    _stub_app_server_process(monkeypatch, process)
    command = []
    expected = 'OPENAI_API_KEY' if provider == 'openai' else 'LOOPX_MODEL_RELAY_TOKEN'
    popen = chat_agent.subprocess.Popen
    def launch(args, **kw):
        command.extend(args)
        assert 'model_provider=' + json.dumps(provider) in args
        assert 'synthetic-scoped-key' not in str(args)
        if api:
            if provider == 'openai':
                assert 'synthetic-scoped-key' not in str(kw['env'])
                assert 'cli_auth_credentials_store="ephemeral"' in args
            else:
                assert kw['env'][expected] == 'synthetic-scoped-key'
        return popen(args, **kw)
    monkeypatch.setattr(chat_agent.subprocess, 'Popen', launch)
    class StrictResumeInput(io.StringIO):
        def write(self, text):
            request = json.loads(text)
            if request.get('method') == 'thread/resume':
                assert request['params']['modelProvider'] == provider
                assert request['params']['config']['model_provider'] == provider
            return super().write(text)
    process.stdin = StrictResumeInput()
    session = chat_agent.CodexChatAgentSession.start(codex_bin='codex', work_dir=tmp_path,
        codex_home=home, goal_id=None, objective='synthetic', resume_thread_id='original-thread',
        account_runtime_root=tmp_path / 'runtime')
    try:
        assert session.thread_id == 'original-thread' and session.native_model_provider == provider
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert [r['method'] for r in requests] == ['initialize', 'initialized', *(
            ['account/login/start'] if api and provider == 'openai' else []), 'config/read', 'thread/resume']
        config = requests[-1]['params']['config']
        assert 'synthetic-scoped-key' not in str([r for r in requests if r.get('method') != 'account/login/start'])
        if api:
            assert expected in config['shell_environment_policy']['exclude']
            if provider == 'openai':
                assert 'model_providers' not in config and config['openai_base_url'] == transport.endpoint
        assert (home / 'sessions/synthetic.jsonl').read_bytes() == original
        assert not (home / 'auth.json').exists()
    finally:
        session.close()


def test_recorded_provider_does_not_follow_another_store_or_corrupt_identity(tmp_path):
    path = provider_fixture(tmp_path / 'bound', 'openai')
    assert codex_auth.recorded_provider(tmp_path / 'other', 'original-thread') is None
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {
        'id': 'different-thread', 'model_provider': 'openai'}}) + '\n')
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        codex_auth.recorded_provider(tmp_path / 'bound', 'original-thread')


@pytest.mark.parametrize('layer', ['user', 'project', 'enterpriseManaged'])
def test_process_overlay_does_not_hide_lower_provider_collision(layer):
    result = {'config': {'model_providers': {'loopx_account_api': {}}}, 'layers': [
        {'name': {'type': 'sessionFlags'}, 'config': {'model_providers': {'loopx_account_api': {}}}},
        {'name': {'type': layer}, 'config': {'model_providers': {
            'loopx_account_api': {'http_headers': {'Authorization': 'synthetic-private'}}}}}]}
    transport = codex_auth.CodexApiTransport('http://127.0.0.1:19/v1', 'synthetic-scoped-key')
    with pytest.raises(codex_auth.CodexHostAuthUnavailable):
        transport.configuration({}, codex_auth.lower_transport_configuration(result))


@pytest.mark.skipif(not os.environ.get('LOOPX_TEST_NATIVE_CODEX_BINARY'), reason='explicit installed native SDK test')
@pytest.mark.parametrize('provider', sorted(codex_auth.TRANSPORT_PROVIDERS))
@pytest.mark.parametrize('consumer', ['worker', 'steward'])
def test_installed_native_resume_preserves_original_provider_across_transport_switches(tmp_path, monkeypatch, provider, consumer):
    """Real SDK outcomes; only the prior synthetic input is a fixture, never resume."""
    import queue
    import threading
    from datetime import datetime, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    binary = os.environ['LOOPX_TEST_NATIVE_CODEX_BINARY']
    home, work = tmp_path / 'native', tmp_path / 'work'
    home.mkdir(); work.mkdir()
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-no-model-request')
    monkeypatch.delenv('LOOPX_CHAT_CODEX_MODEL_PROVIDER', raising=False)
    definition = {'name': 'Synthetic local fixture', 'base_url': 'http://127.0.0.1:19/v1',
        'env_key': 'OPENAI_API_KEY', 'wire_api': 'responses', 'requires_openai_auth': False,
        'supports_websockets': False}
    config = {'model_provider': provider}
    if provider != 'openai':
        config['model_providers'] = {provider: definition}
    def start_fixture():
        process = subprocess.Popen([binary, 'app-server', *codex_auth.transport_arguments(config), '--listen', 'stdio://'],
            cwd=work, env={**os.environ, 'CODEX_HOME': str(home)}, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        messages = queue.Queue()
        native = chat_agent.CodexChatAgentSession(process=process, messages=messages,
            thread_id='', work_dir=work, response_timeout_sec=15)
        threading.Thread(target=chat_agent._reader, args=(process.stdout, messages), daemon=True).start()
        native._request('initialize', {'clientInfo': {'name': 'loopx_protocol_fixture', 'version': '1.0.0'},
            'capabilities': {'experimentalApi': True}}, request_id=1)
        native._notify('initialized', {})
        return native
    def stop_fixture(native):
        native.process.stdin.close()
        try:
            native.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            native.close()
    native = start_fixture()
    try:
        started = native._request('thread/start', {'cwd': str(work), 'modelProvider': provider,
            'approvalPolicy': 'never', 'sandbox': 'read-only', 'persistExtendedHistory': True}, request_id=2)
        thread = started['thread']; thread_id = thread['id']
        # Native allocates its path before persisting the first input. Supply
        # minimal prior input with the real native-returned ID/path/metadata.
        stamp = datetime.fromtimestamp(thread['createdAt'], timezone.utc).isoformat()
        path = Path(thread['path']); path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{'timestamp': stamp, 'type': 'session_meta', 'payload': {
            'id': thread_id, 'timestamp': stamp, 'cwd': str(work), 'originator': thread['originator'],
            'cli_version': thread['cliVersion'], 'source': thread['source'], 'model_provider': provider}},
            {'timestamp': stamp, 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'Synthetic previous input; no business or model execution.'}]}}]
        original = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        path.write_bytes(original)
    finally:
        stop_fixture(native)
    # Let the native owner load/index this synthetic persisted prestate once;
    # thread/start alone allocates no persisted input or indexed conversation.
    native = start_fixture()
    try:
        baseline = native._request('thread/resume', {'threadId': thread_id, 'excludeTurns': True,
            'cwd': str(work), 'modelProvider': provider, 'approvalPolicy': 'never', 'sandbox': 'read-only'}, request_id=2)
        assert baseline['thread']['id'] == thread_id
    finally:
        stop_fixture(native)
    # Confirm our metadata read uses native's actual index, not a monkeypatched
    # identity. Each resume must retain that exact thread and original input.
    assert codex_auth.recorded_provider(home, thread_id) == provider
    incoming = queue.Queue()
    class ResponseFixture(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            incoming.put((self.path, self.headers.get('Authorization')))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"type":"response.completed","response":{"id":"synthetic-response",'
                b'"status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":0,"total_tokens":1}}}\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), ResponseFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}' + ('/steward/v1' if consumer == 'steward' else '/v1')
    token = 'synthetic-scoped-' + consumer
    write_auth(home)  # A native subscription credential must not override the scoped API token.
    native_auth = (home / 'auth.json').read_bytes()
    try:
        for attempt, api in enumerate((True, False, True)):
            transport = codex_auth.CodexApiTransport(endpoint, token) if api else None
            def prepare(*a, **kw):
                assert kw['consumer'] == consumer
                return transport
            monkeypatch.setattr(codex_auth, 'native_api_transport', prepare)
            session = chat_agent.CodexChatAgentSession.start(codex_bin=binary, codex_home=home, work_dir=work,
                goal_id=None, objective='synthetic', resume_thread_id=thread_id, account_runtime_root=tmp_path / 'accounts',
                account_consumer=consumer, response_timeout_sec=15)
            try:
                assert session.thread_id == thread_id and session.native_model_provider == provider
                assert path.read_bytes().startswith(original)
                assert token.encode() not in path.read_bytes()
                assert (home / 'auth.json').read_bytes() == native_auth
                if attempt == 0:
                    session._request('turn/start', {'threadId': thread_id, 'input': [
                        {'type': 'text', 'text': 'Synthetic local HTTP transport probe.'}]})
                    observed_path, authorization = incoming.get(timeout=10)
                    assert observed_path == ('/steward/v1/responses' if consumer == 'steward' else '/v1/responses')
                    assert authorization == 'Bearer ' + token
            finally:
                session.close()
    finally:
        server.shutdown(); server.server_close()


def test_ephemeral_api_login_failure_redacts_private_rpc_details():
    import queue
    from tests.test_chat_agent import _FakeAppServerProcess
    session = chat_agent.CodexChatAgentSession(process=_FakeAppServerProcess(), messages=queue.Queue(),
        thread_id='', work_dir=Path('/synthetic-workspace'))
    def failed(*args, **kwargs):
        raise ValueError('synthetic-scoped-private-token')
    session._request = failed
    with pytest.raises(chat_agent.CodexChatAgentError) as error:
        codex_auth.authenticate_api_transport(session,
            codex_auth.CodexApiTransport('http://127.0.0.1:19/v1', 'synthetic-scoped-private-token').for_provider('openai'))
    assert 'synthetic-scoped' not in str(error.value) and error.value.__suppress_context__


def test_explicit_native_provider_keeps_its_existing_authentication(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Explicit native configuration must not select an account transport or read history')
    monkeypatch.setattr(codex_auth, 'recorded_provider', unexpected)
    monkeypatch.setattr(codex_auth, 'native_api_transport', unexpected)
    current = {'model_provider': 'operator-native'}
    config, provider, api, retain = codex_auth.prepare_conversation_transport(tmp_path, tmp_path,
        tmp_path, 'original', current=current, provider='', eligible=True,
        consumer='worker', model=None, environment={})
    assert config == current and provider == '' and api is None and not retain
