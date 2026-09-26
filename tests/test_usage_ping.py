"""Real Python entrypoint -> detached TS owner -> HTTP, using isolated machine state."""
from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loopx import usage_ping
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
    monkeypatch.setattr(usage_ping, 'DEFAULT_RUNTIME_ROOT', tmp_path)
    for key in ('CI', 'DO_NOT_TRACK', 'LOOPX_USAGE_PING', 'LOOPX_USAGE_POLICY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', 'http://127.0.0.1:1/v1/ping')
    return tmp_path


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


def test_unattended_fresh_install_never_creates_state(isolated, monkeypatch):
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: False)
    assert usage_ping.begin('status') is None
    assert not usage_ping.state_path().exists()


def test_first_interactive_command_discloses_but_does_not_measure(isolated, monkeypatch, capsys):
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: True)
    assert usage_ping.begin('status') is None
    assert 'random installation ID' in capsys.readouterr().err
    state = json.loads(usage_ping.state_path().read_text())
    assert 'last_attempt_day' not in state and 'counters' not in state
    assert usage_ping.begin('status') is not None


def test_real_cli_returns_while_http_response_is_held_and_disable_survives(isolated, collector, monkeypatch):
    endpoint, received, accepted, release = collector
    monkeypatch.setenv('LOOPX_USAGE_PING_ENDPOINT', endpoint)
    usage_ping.control('enable')
    setup = 'import sys; from pathlib import Path; from loopx import usage_ping; usage_ping.DEFAULT_RUNTIME_ROOT=Path(sys.argv[1]); from loopx.cli_runtime import main; '
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


def test_business_failure_and_usage_failure_do_not_replace_original_result(isolated, monkeypatch):
    usage_ping.control('enable')
    import loopx.cli_runtime as cli
    monkeypatch.setattr(cli, '_run_command', lambda *_: 7)
    monkeypatch.setattr(usage_ping, '_command', lambda: (_ for _ in ()).throw(OSError('no node')))
    assert main(['version']) == 7


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


def test_real_chat_settings_share_cli_choice_and_reject_cross_origin(isolated):
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
        assert response.status == 200 and json.loads(response.read())['consent'] == 'default'
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
        connection.request('GET', path)
        response = connection.getresponse()
        assert json.loads(response.read())['consent'] == 'disabled'
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
