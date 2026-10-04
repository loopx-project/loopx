"""Real Python entrypoint -> detached TS owner -> HTTP, using isolated machine state."""
from __future__ import annotations

import json
import io
import os
import runpy
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from loopx import paths, usage_ping
from loopx.cli_runtime import main


@pytest.fixture
def collector():
    received, accepted, release = [], threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            # A tiny real tunnel lets Node's proxy transport reach this fixture
            # without relying on the machine's proxy or external DNS.
            with socket.create_connection(self.server.server_address) as upstream:
                self.send_response(200)
                self.end_headers()
                sockets = (self.connection, upstream)
                while True:
                    ready, _, _ = select.select(sockets, [], [], 5)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            accepted.set()
            release.wait(6)
            try:
                self.send_response(204)
                self.end_headers()
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/v1/ping', received, accepted, release
    release.set()
    server.shutdown()
    server.server_close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, 'select_default_runtime_root', lambda: tmp_path)
    for key in ('CI', 'DO_NOT_TRACK', 'LOOPX_USAGE_PING', 'LOOPX_USAGE_POLICY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', 'http://127.0.0.1:1/v1/ping')
    return tmp_path


def test_machine_usage_state_keeps_legacy_choice_and_rejects_ambiguous_route(
    tmp_path, monkeypatch, capsys,
):
    current = tmp_path / ".loopx"
    legacy = tmp_path / ".codex" / "loopx"
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    for key in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING", "LOOPX_USAGE_POLICY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOOPX_USAGE_PING_ENDPOINT", "http://127.0.0.1:1/v1/ping")
    assert usage_ping.state_path() == current / "usage-ping.json"

    legacy.mkdir(parents=True)
    (legacy / "registry.global.json").write_text('{"goals": []}', encoding="utf-8")
    assert usage_ping.state_path() == legacy / "usage-ping.json"
    usage_ping.control("disable")
    before = usage_ping.state_path().read_bytes()
    assert usage_ping.begin("version") is None
    assert not current.exists()

    current.mkdir()
    (current / "registry.global.json").write_text('{"goals": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="Both default LoopX registries exist"):
        usage_ping.state_path()
    monkeypatch.setattr(usage_ping, "_detach", lambda *_: pytest.fail("ambiguous route must not send"))
    assert main(["version", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert (legacy / "usage-ping.json").read_bytes() == before
    assert not (current / "usage-ping.json").exists()
    assert usage_ping.state_path(tmp_path / "explicit") == tmp_path / "explicit" / "usage-ping.json"


def test_settings_commands_and_corrupt_state_repair(isolated, capsys):
    assert main(['usage-ping', 'status', '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)['blocked_by'] == 'notice_required'
    assert not usage_ping.state_path().exists()
    assert main(['usage-ping', 'enable', '--format', 'json']) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert enabled['sending'] is True
    assert set(enabled['next_payload']) == {'schema', 'install_id', 'version', 'os', 'arch', 'python', 'channel'}
    usage_ping.state_path().write_text('invalid')
    assert main(['usage-ping', 'disable', '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)['next_payload'] is None


@pytest.mark.parametrize('interactive', [False, True])
def test_first_cli_command_discloses_before_automatic_enable(isolated, monkeypatch, capsys, interactive):
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: interactive)
    monkeypatch.setattr(usage_ping, '_detach', lambda *_: pytest.fail('first command must not send'))
    assert usage_ping.begin('status') is None
    assert 'random installation ID' in capsys.readouterr().err
    state = json.loads(usage_ping.state_path().read_text())
    assert 'last_attempt_day' not in state and 'counters' not in state
    assert usage_ping.control('status')['sending'] is True


@pytest.mark.parametrize('command', ['chat', 'serve-status'])
def test_background_app_service_defers_first_disclosure_to_frontend(isolated, monkeypatch, command):
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: False)
    assert usage_ping.begin(command) is None
    assert not usage_ping.state_path().exists()


def test_discarded_or_broken_stderr_cannot_acknowledge(isolated, monkeypatch):
    with open(os.devnull, 'w') as discarded:
        monkeypatch.setattr(sys, 'stderr', discarded)
        assert usage_ping.begin('status') is None
        assert not usage_ping.state_path().exists()
    class BrokenStream:
        def isatty(self):
            return False
        def write(self, _text):
            raise BrokenPipeError()
    monkeypatch.setattr(sys, 'stderr', BrokenStream())
    assert usage_ping.begin('status') is None
    assert not usage_ping.state_path().exists()


def test_absent_stderr_cannot_acknowledge_or_reach_stdout(isolated, monkeypatch, capsys):
    monkeypatch.setattr(usage_ping, '_detach', lambda *_: pytest.fail('an undisclosed command must not measure'))
    monkeypatch.setattr(sys, 'stderr', None)
    assert usage_ping.begin('status') is None
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == ''
    assert not usage_ping.state_path().exists()


def test_absent_stderr_keeps_real_cli_json_pure_until_a_stream_discloses(isolated, monkeypatch, capsys):
    monkeypatch.setattr(usage_ping, '_detach', lambda *_: pytest.fail('a command without disclosure must not measure'))
    monkeypatch.setattr(sys, 'stderr', None)
    assert main(['version', '--format', 'json']) == 0
    captured = capsys.readouterr()
    assert captured.out.lstrip().startswith('{')
    assert json.loads(captured.out)['ok'] is True
    assert captured.err == ''
    assert not usage_ping.state_path().exists()
    assert usage_ping.control('status')['sending'] is False
    # Only the missing stream is rejected: an in-memory host stream still discloses and ACKs.
    stderr = io.StringIO()
    monkeypatch.setattr(sys, 'stderr', stderr)
    assert main(['version', '--format', 'json']) == 0
    assert 'random installation ID' in stderr.getvalue()
    assert json.loads(capsys.readouterr().out)['ok'] is True
    assert json.loads(usage_ping.state_path().read_text())['notice']['version'] == 6


@pytest.mark.parametrize('setting,value', [
    ('LOOPX_USAGE_PING', 'off'), ('DO_NOT_TRACK', '1'), ('CI', 'true'),
    ('LOOPX_USAGE_POLICY', 'consent_required'), ('LOOPX_USAGE_POLICY', 'unknown'),
])
def test_first_disclosure_respects_policy_overrides(isolated, monkeypatch, capsys, setting, value):
    monkeypatch.setenv(setting, value)
    assert usage_ping.begin('status') is None
    assert not usage_ping.state_path().exists()
    assert capsys.readouterr().err == ''


def test_real_agent_cli_keeps_json_clean_and_sends_only_after_disclosure(isolated, collector, monkeypatch):
    endpoint, received, accepted, release = collector
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    setup = 'import sys; from pathlib import Path; from loopx import usage_ping; usage_ping.select_default_runtime_root=lambda: Path(sys.argv[1]); from loopx.cli_runtime import main; '
    command = [sys.executable, '-c', setup + 'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)]
    first = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0 and isinstance(json.loads(first.stdout), dict)
    assert 'random installation ID' in first.stderr
    assert received == []
    assert 'counters' not in json.loads(usage_ping.state_path().read_text())
    second = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0 and json.loads(second.stdout) == json.loads(first.stdout)
    assert 'random installation ID' not in second.stderr
    assert accepted.wait(4), 'subsequent Agent call should reach the isolated collector'
    usage_ping.control('disable')
    release.set()
    third = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert third.returncode == 0 and 'random installation ID' not in third.stderr
    assert usage_ping.control('status')['consent'] == 'disabled'


@pytest.mark.parametrize('parent_opt_out', [None, '0', '1'])
def test_claude_smoke_environment_disables_real_cli_and_sender(
    isolated, collector, monkeypatch, parent_opt_out,
):
    endpoint, received, accepted, release = collector
    release.set()
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    # An already acknowledged installation must still obey synthetic isolation.
    usage_ping.control('enable')
    path = usage_ping.state_path()
    before = path.read_bytes()
    generation = json.loads(before)['generation']
    if parent_opt_out is None:
        monkeypatch.delenv('LOOPX_USAGE_PING', raising=False)
    else:
        monkeypatch.setenv('LOOPX_USAGE_PING', parent_opt_out)
    smoke = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'examples/claude-install-optin-smoke.py'))
    env = smoke['_isolated_host_env'](isolated / 'home')
    # Even a regression may only reach this disposable collector.
    env['LOOPX_USAGE_PING_ENDPOINT'] = endpoint
    setup = (
        'import json,sys; from pathlib import Path; from loopx import usage_ping; '
        'usage_ping.select_default_runtime_root=lambda: Path(sys.argv[1]); '
    )
    cli = subprocess.run(
        [sys.executable, '-c', setup + 'from loopx.cli_runtime import main; '
         'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert cli.returncode == 0 and json.loads(cli.stdout)['ok'], cli.stderr
    assert cli.stderr == ''
    sender = subprocess.run(
        [sys.executable, '-c', setup +
         'print(json.dumps(usage_ping.control("start", generation=sys.argv[2])))',
         str(isolated), generation],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert sender.returncode == 0, sender.stderr
    assert json.loads(sender.stdout) == {'sent': False, 'reason': 'blocked'}
    assert not accepted.wait(0.3) and received == []
    assert path.read_bytes() == before


def test_real_cli_returns_while_http_response_is_held_and_disable_survives(isolated, collector, monkeypatch):
    endpoint, received, accepted, release = collector
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    usage_ping.control('enable')
    setup = 'import sys; from pathlib import Path; from loopx import usage_ping; usage_ping.select_default_runtime_root=lambda: Path(sys.argv[1]); from loopx.cli_runtime import main; '
    command = [sys.executable, '-c', setup + 'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)]
    started = time.monotonic()
    baseline = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              env={**os.environ, "LOOPX_USAGE_PING": "0"})
    baseline_elapsed = time.monotonic() - started
    assert baseline.returncode == 0
    started = time.monotonic()
    result = subprocess.run([sys.executable, '-c', setup + 'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)],
                            capture_output=True, text=True, timeout=30, env=os.environ)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert elapsed < baseline_elapsed + 1, f'foreground {elapsed}s, disabled baseline {baseline_elapsed}s'
    assert accepted.wait(4), 'actual detached TS sender did not reach HTTP'
    assert not release.is_set()
    usage_ping.control('disable')
    release.set()
    time.sleep(0.3)
    state = json.loads(usage_ping.state_path().read_text())
    assert state['consent'] == 'disabled' and 'install_id' not in state and 'last_sent_day' not in state
    assert received[0]['schema'] == 'loopx_usage_ping_v1'
    assert str(isolated) not in json.dumps(received)


def test_real_cli_first_result_reaches_http_without_next_day_return(isolated, collector, monkeypatch):
    endpoint, received, accepted, release = collector
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    release.set()
    setup = 'import sys; from pathlib import Path; from loopx import usage_ping; usage_ping.select_default_runtime_root=lambda: Path(sys.argv[1]); from loopx.cli_runtime import main; '
    command = [sys.executable, '-c', setup + 'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)]
    first = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0 and 'random installation ID' in first.stderr
    assert received == []
    second = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0 and json.loads(second.stdout) == json.loads(first.stdout)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(p['schema'] == 'loopx_usage_diagnostics_v1' for p in received):
        time.sleep(0.02)
    aggregates = [p for p in received if p['schema'] == 'loopx_usage_diagnostics_v1']
    assert len(aggregates) == 1, 'one completed command must not depend on a next-day invocation'
    assert set(aggregates[0]) == {'schema', 'counters'}
    counters = aggregates[0]['counters']
    assert len(counters) == 1 and counters[0]['feature'] == 'version'
    assert counters[0]['outcome'] == 'ok' and counters[0]['count'] == 1
    assert counters[0]['version'] and counters[0]['context'] == 'unknown'
    assert usage_ping.control('status')['aggregate_preview'] is None
    usage_ping.control('disable')


def test_business_failure_and_usage_failure_do_not_replace_original_result(isolated, monkeypatch):
    usage_ping.control('enable')
    import loopx.cli_runtime as cli
    monkeypatch.setattr(cli, '_run_command', lambda *_: 7)
    monkeypatch.setattr(usage_ping, '_command', lambda: (_ for _ in ()).throw(OSError('no node')))
    assert main(['version']) == 7


@pytest.mark.parametrize('switch,value', [
    ('CI', 'true'), ('CI', '1'), ('LOOPX_USAGE_PING', '0'),
    ('LOOPX_USAGE_PING', 'off'), ('DO_NOT_TRACK', '1'),
])
def test_disabled_synthetic_real_cli_never_contacts_collector(isolated, collector, switch, value):
    endpoint, received, accepted, release = collector
    release.set()
    env = {**os.environ, 'LOOPX_USAGE_PING_ENDPOINT': endpoint, switch: value}
    setup = ('import sys; from pathlib import Path; from loopx import usage_ping; '
             'usage_ping.select_default_runtime_root=lambda: Path(sys.argv[1]); from loopx.cli_runtime import main; ')
    command = [sys.executable, '-c', setup + 'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)]
    # A matching acknowledged state cannot override an environment suppressor.
    usage_ping.control('enable')
    for _ in range(2):
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0 and json.loads(result.stdout)['ok'] is True
    assert not accepted.wait(0.5)
    assert received == []


@pytest.mark.parametrize('bypass_proxy', [False, True])
def test_detached_sender_honors_proxy_and_no_proxy(isolated, collector, monkeypatch, bypass_proxy):
    endpoint, received, accepted, release = collector
    for key in ('http_proxy', 'https_proxy', 'no_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.delenv(key, raising=False)
    proxy = endpoint.removesuffix('/v1/ping')
    # Without proxy forwarding the first target is unreachable; with NO_PROXY
    # the second target must succeed even though its configured proxy is dead.
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:59999' if bypass_proxy else proxy)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:59999' if bypass_proxy else proxy)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1' if bypass_proxy else '')
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint if bypass_proxy else 'http://127.0.0.1:59999/v1/ping')
    usage_ping.control('enable')
    assert main(['version', '--format', 'json']) == 0
    assert accepted.wait(4), 'detached sender ignored HTTP_PROXY or NO_PROXY'
    assert received[0]['schema'] == 'loopx_usage_ping_v1'
    assert not release.is_set(), 'CLI must finish before network response'
    usage_ping.control('disable')
    release.set()


@pytest.mark.parametrize('upgrade', [False, True])
def test_real_chat_settings_share_cli_choice_and_reject_cross_origin(isolated, upgrade):
    if upgrade:
        usage_ping.control('enable')
        prior = json.loads(usage_ping.state_path().read_text())
        prior['consent'] = 'default'
        prior['notice']['version'] = 3
        usage_ping.state_path().write_text(json.dumps(prior))
        before = usage_ping.state_path().read_bytes()
    import http.client
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    server = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
    server.verbose = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=8)
    path = '/api/chat/usage-statistics'
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        initial = json.loads(response.read())
        assert response.status == 200 and initial['automatic_notice_required']
        if upgrade:
            assert usage_ping.state_path().read_bytes() == before
        else:
            assert not usage_ping.state_path().exists()
        assert initial['notice']['version'] == 6
        connection.request('POST', path, json.dumps({'notice': initial['notice']}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        acknowledged = json.loads(response.read())
        assert response.status == 200 and acknowledged['sending']
        assert acknowledged['consent'] == 'default'
        assert 'last_attempt_day' not in json.loads(usage_ping.state_path().read_text())
        connection.request('POST', path, json.dumps({'notice': {**initial['notice'], 'version': 0}}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 503
        response.read()
        connection.request('POST', path, json.dumps({'enabled': True}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())['consent'] == 'enabled'
        assert usage_ping.control('status')['sending'] is True
        connection.request('POST', path, json.dumps({'enabled': False}), {'Content-Type': 'application/json', 'Origin': 'https://evil.example'})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        assert usage_ping.control('status')['consent'] == 'enabled'
        connection.request('POST', path, json.dumps({'enabled': 'false'}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        usage_ping.control('disable')
        connection.request('POST', path, json.dumps({'notice': initial['notice']}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 200 and not json.loads(response.read())['sending']
        connection.request('GET', path)
        response = connection.getresponse()
        assert json.loads(response.read())['consent'] == 'disabled'
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_context_setting_real_cli_and_http_share_state_without_enabling(isolated, capsys):
    import http.client
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    assert main(['usage-ping', 'context', '--context', 'maintainer', '--format', 'json']) == 0
    initial = json.loads(capsys.readouterr().out)
    assert initial['stored_context'] == 'maintainer' and not initial['sending']
    server = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
    server.verbose = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=8)
    path = '/api/chat/usage-statistics'
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        assert json.loads(response.read())['effective_context'] == 'maintainer'
        connection.request('POST', path, json.dumps({'context': 'personal'}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        updated = json.loads(response.read())
        assert response.status == 200 and updated['effective_context'] == 'personal' and not updated['sending']
        assert usage_ping.control('status')['stored_context'] == 'personal'
        connection.request('POST', path, json.dumps({'context': 'my-company'}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.request('POST', path, json.dumps({'context': 'maintainer'}), {'Content-Type': 'application/json', 'Origin': 'https://evil.example'})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        assert usage_ping.control('status')['stored_context'] == 'personal'
        before = usage_ping.state_path().read_bytes()
        assert main(['usage-ping', 'context', '--context', 'my-company', '--format', 'json']) == 2
        assert 'Invalid device deployment context' in capsys.readouterr().err
        assert usage_ping.state_path().read_bytes() == before
        usage_ping.state_path().write_text('invalid')
        connection.request('POST', path, json.dumps({'context': 'personal'}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 503, 'a damaged store is not a caller input rejection'
        response.read()
        assert usage_ping.state_path().read_text() == 'invalid'
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_real_cli_installation_profile_tags_context_and_contains_no_work_identity(isolated, collector, monkeypatch, capsys):
    endpoint, received, accepted, release = collector
    release.set()
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    assert main(['usage-ping', 'context', '--context', 'maintainer', '--format', 'json']) == 0
    capsys.readouterr()
    usage_ping.control('enable')
    assert main(['version', '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)['ok']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not any(row['schema'] == 'loopx_installation_usage_v1' for row in received):
        time.sleep(0.05)
    payload = next(row for row in received if row['schema'] == 'loopx_installation_usage_v1')
    assert payload['profiles'][0]['context'] == 'maintainer'
    assert payload['profiles'][0]['cli'] == [{'feature': 'version', 'count': 1}]
    assert payload['profiles'][0]['runtime'] == []
    assert set(payload) == {'schema', 'install_id', 'profiles'}
    assert not any(word in json.dumps(payload) for word in ('goal_id', 'agent_id', 'prompt', str(isolated)))


def test_goal_observer_does_not_wait_for_unresponsive_collector(isolated, collector, monkeypatch):
    from loopx.usage_goal import observe_goal_execution
    endpoint, received, accepted, release = collector
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    usage_ping.control('enable')
    started = time.monotonic()
    with observe_goal_execution(isolated / 'runtime', 'synthetic-goal'):
        time.sleep(0.01)
    assert time.monotonic() - started < 0.8
    assert accepted.wait(4)
    assert not release.is_set(), 'host returned before collector released HTTP response'
    usage_ping.control('disable')
    release.set()


def test_v3_cli_upgrade_requires_visible_renewal_before_real_http(isolated, collector, monkeypatch):
    endpoint, received, accepted, release = collector
    release.set()
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    usage_ping.control('enable')
    path = usage_ping.state_path()
    old = json.loads(path.read_text())
    old.update(consent='default', day='2026-09-28', counters=[{
        'feature': 'todo', 'outcome': 'ok', 'duration': 'lt_1s', 'error': 'none', 'count': 7,
    }])
    old['notice']['version'] = 3
    path.write_text(json.dumps(old))
    before = path.read_bytes()
    setup = 'import sys; from pathlib import Path; from loopx import usage_ping; usage_ping.select_default_runtime_root=lambda: Path(sys.argv[1]); from loopx.cli_runtime import main; '
    command = [sys.executable, '-c', setup + 'raise SystemExit(main(["version", "--format", "json"]))', str(isolated)]
    hidden = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30)
    assert hidden.returncode == 0 and json.loads(hidden.stdout)['ok']
    assert path.read_bytes() == before
    assert not accepted.wait(0.3) and received == []
    visible = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert visible.returncode == 0 and json.loads(visible.stdout)['ok']
    assert 'daily installation profiles' in visible.stderr and '15 minutes' in visible.stderr
    assert 'connection metadata' in visible.stderr
    current = json.loads(path.read_text())
    assert current['notice']['version'] == 6
    assert current['generation'] != old['generation']
    assert current['counters'] == []
    assert not accepted.wait(0.3) and received == []
    subsequent = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert subsequent.returncode == 0 and subsequent.stderr == ''
    assert accepted.wait(5)
    deadline = time.monotonic() + 5
    while not any(item.get('schema') == 'loopx_usage_diagnostics_v1' for item in received) and time.monotonic() < deadline:
        time.sleep(0.02)
    batches = [item for item in received if item.get('schema') == 'loopx_usage_diagnostics_v1']
    assert len(batches) == 1 and sum(row['count'] for row in batches[0]['counters']) == 1


def test_actual_pr_readiness_cli_keeps_exit_code_and_projects_only_typed_facts(isolated, monkeypatch, capsys):
    import runpy
    from pathlib import Path
    fixtures = runpy.run_path(str(Path(__file__).with_name('test_pr_review_github_scan.py')))
    pr = fixtures['_merge_ready_pr']()
    pr['statusCheckRollup'] = [{'name': 'test', 'status': 'IN_PROGRESS'}]
    pr['review_thread_summary'] = {'complete': True, 'total_count': 0, 'unresolved_count': 0}
    fixture = isolated / 'prs.json'
    fixture.write_text(json.dumps({'repository': 'owner/repo', 'pull_requests': [pr]}))
    registry = isolated / 'registry.json'
    registry.write_text(json.dumps({'goals': [{'id': 'review-goal', 'repo': str(isolated), 'status': 'active'}]}))
    usage_ping.control('enable')
    requests = []
    monkeypatch.setattr(usage_ping, '_detach', requests.append)
    code = main(['--registry', str(registry), 'pr-review', '--goal-id', 'review-goal', '--fixture', str(fixture), '--check-merge-readiness', '4110@' + 'a' * 40, '--format', 'json'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload['ok'] and not payload['ready'], payload
    observation = next(row for row in requests if row['action'] == 'observe')
    assert observation['operation'] == 'merge-readiness'
    assert observation['result_facts'] == {'ok': True, 'ready': False}
    assert 'owner/repo' not in json.dumps(observation) and str(fixture) not in json.dumps(observation)


def test_turn_capture_reuses_committed_current_effects_not_historical_receipt(isolated):
    usage_ping.begin('turn')
    committed = {'schema_version': 'loopx_turn_execution_v0', 'ok': True,
                 'status': 'committed', 'receipt': {'status': 'committed'},
                 'validation': {'status': 'passed'}, 'effects': {'state_written': True, 'quota_spent': True}}
    usage_ping.capture_result(committed)
    assert usage_ping._observation.get()['result_facts']['turn_committed'] is True
    usage_ping.capture_result({**committed, 'replayed': True, 'effects': {'state_written': False, 'quota_spent': False}})
    assert usage_ping._observation.get()['result_facts']['turn_committed'] is False
    usage_ping.finish(None, 'turn', 0)


def test_projection_failure_cannot_replace_business_output(isolated, monkeypatch, capsys):
    from loopx.cli_runtime import print_payload
    usage_ping.begin('turn')
    monkeypatch.setattr('loopx.control_plane.turn_driver.loopx_turn_execution_committed', lambda _: (_ for _ in ()).throw(ValueError('private')))
    payload = {'schema_version': 'loopx_turn_execution_v0', 'ok': True}
    print_payload(payload, 'json', lambda _: 'unused')
    assert json.loads(capsys.readouterr().out) == payload
    assert 'result_facts' not in usage_ping._observation.get()
    usage_ping.finish(None, 'turn', 0)
