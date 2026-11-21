"""Public Workbench CLI: side-effect boundaries and the packaged application journey."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import shutil
import time
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/workbench"


def cli(state, *args, env=None):
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "workbench", *args, "--state-dir", str(state), "--format", "json"],
        cwd=ROOT, env={**os.environ, "LOOPX_USAGE_PING": "0", **(env or {})},
        text=True, encoding="utf-8", capture_output=True, timeout=70,
    )
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("action", ["status", "stop"])
def test_read_or_stop_absent_does_not_create_configuration(tmp_path, action):
    state = tmp_path / "absent"
    code, report = cli(state, action)
    assert (code, report["state"]) == (0, "stopped")
    assert not state.exists()


def test_complete_and_selected_parsers_accept_the_same_operations(tmp_path):
    from loopx.cli import build_parser
    from loopx.cli_runtime import _build_selected_parser
    for action, flags in (("start", ["--autostart"]), ("status", []), ("stop", ["--disable-autostart", "--force"])):
        argv = ["workbench", action, *flags, "--state-dir", str(tmp_path), "--format", "json"]
        full = build_parser().parse_args(argv)
        selected = _build_selected_parser("workbench").parse_args(argv)
        assert (full.workbench_action, full.state_dir, full.subcommand_format) == (
            selected.workbench_action, selected.state_dir, selected.subcommand_format)
        for name in ("autostart", "disable_autostart", "force"):
            assert getattr(full, name, None) == getattr(selected, name, None)


def test_missing_install_does_not_create_state(tmp_path):
    state = tmp_path / "absent"
    code, report = cli(state, "start", "--workbench-dir", str(tmp_path / "missing"))
    assert code == 1
    assert "installed separately" in report["error"]
    assert not state.exists()


def test_legacy_listener_is_never_replaced_or_stopped(tmp_path):
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(tmp_path / "daemon.sock"))
    sock.listen(8)
    try:
        for action in ("status", "start", "stop"):
            code, report = cli(tmp_path, action)
            assert code == 1
            assert "independently managed" in report["error"]
            connection, _ = sock.accept()
            connection.close()
        assert not (tmp_path / ".env").exists()
    finally:
        sock.close()


def test_stale_start_lock_reports_unknown_without_deleting_it(tmp_path):
    lock = tmp_path / "lifecycle.lock"
    lock.mkdir()
    for action in ("status", "stop"):
        code, report = cli(tmp_path, action)
        assert code == 1 and report["state"] == "unverified"
        assert lock.is_dir()


@pytest.fixture
def workbench_tmp_path():
    # Unix-domain socket names must fit even after data/coordination/calli.sock.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="wb-test-", dir="/tmp") as path:
        yield Path(path)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def api(url, path, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url + path, headers=headers, data=None if body is None else json.dumps(body).encode()), timeout=10) as response:
        return response.status, response.read()


@pytest.mark.skipif(not (APP / "packages/daemon/dist/workbench-lifecycle.js").exists(), reason="Build Workbench before the real CLI integration check")
def test_packaged_cli_restart_preserves_conversation_and_external_core(workbench_tmp_path):
    tmp_path = workbench_tmp_path
    class Core(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_):
            pass

    core = ThreadingHTTPServer(("127.0.0.1", 0), Core)
    thread = threading.Thread(target=core.serve_forever, daemon=True)
    thread.start()
    core_url = f"http://127.0.0.1:{core.server_port}"
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "example-project"
    project.mkdir()
    port = free_port()
    token = "test-only-workbench-secret"
    # A fixture agent and isolated HOME prohibit discovery or live model execution.
    agents = json.dumps({"agents": [{"id": "pi", "adapter": "pi", "command": [shutil.which("node"), str(APP / "packages/daemon/test/fixtures/fake-pi.mjs")]}]})
    (state / ".env").write_text(
        f"LOOCHAT_HOST=127.0.0.1\nCALLI_TOKEN={token}\nCALLI_AGENTS_JSON={agents}\nLOOCHAT_AUTO_JOIN=off\n"
        f"LOOCHAT_LOOPX_CORE_URL={core_url}\nLOOCHAT_WORKDIRS={tmp_path}\n"
        "LOOCHAT_CLAUDE_COMMAND=false\nLOOCHAT_CODEX_BIN=/nonexistent\n"
        "LOOCHAT_PI_BIN=/nonexistent\nLOOCHAT_AGY_BIN=/nonexistent\n"
        "LOOCHAT_TRAEX_BIN=/nonexistent\nLOOCHAT_DSH_BIN=/nonexistent\n",
        encoding="utf-8",
    )
    env = {"HOME": str(home)}
    try:
        # An unrelated listener is preserved; correcting the port recovers startup.
        code, conflict = cli(state, "start", "--port", str(core.server_port), env=env)
        assert code == 1 and "failed to start" in conflict["error"]
        assert api(core_url, "/healthz")[0] == 200
        assert not (state / "lifecycle.lock").exists()
        code, first = cli(state, "start", "--port", str(port), env=env)
        assert code == 0, first.get("error")
        assert first["core"]["reachable"] is True
        url = first["url"]
        assert url.endswith(f":{port}")
        assert api(url, "/")[0] == 200
        assert b'"name": "LoopX"' in api(url, "/manifest.webmanifest")[1]
        link = first["access_url"].split("#token=", 1)[1]
        assert json.loads(api(url, "/api/threads", link)[1]) == []
        code, again = cli(state, "start", env=env)
        assert code == 0, again.get("error")
        assert again["pid"] == first["pid"]
        assert again["instance_id"] == first["instance_id"]
        code, repeated = cli(state, "start", "--port", str(port), "--env-file", str(state / ".env"), "--workbench-dir", str(APP), env=env)
        assert code == 0, repeated.get("error")
        assert repeated["pid"] == first["pid"]
        # No lifecycle authority is reachable through the browser listener.
        with pytest.raises(HTTPError):
            api(url, "/api/lifecycle/stop", token, {})
        # Persist a real conversation through the daemon API; no model request.
        _, body = api(url, "/api/threads", token, {"agent": "pi", "cwd": str(project), "title": "Retained conversation"})
        conversation = json.loads(body)
        capture = os.environ.get("LOOPX_WORKBENCH_CAPTURE_DIR")
        if capture:
            capture_workbench(first["access_url"], Path(capture))
        code, status = cli(state, "status", env=env)
        assert code == 0 and "access_url" not in status
        assert token not in json.dumps(status) and link not in json.dumps(status)
        api(url, f"/api/threads/{conversation['id']}/turns", token, {"text": "slow"})
        for _ in range(30):
            current = json.loads(api(url, "/api/threads", token)[1])
            if any(row["status"] == "running" for row in current):
                break
            time.sleep(0.1)
        code, busy = cli(state, "stop", env=env)
        assert code == 1 and "running turn" in busy["error"]
        assert cli(state, "stop", "--force", env=env)[0] == 0
        assert api(core_url, "/healthz")[0] == 200
        core.shutdown()
        core.server_close()
        code, restarted = cli(state, "start", env=env)
        assert code == 0, restarted.get("error")
        assert restarted["url"] == url  # custom port retained without repeating flags
        assert restarted["instance_id"] != first["instance_id"]
        assert restarted["core"]["reachable"] is False
        rows = json.loads(api(url, "/api/threads", token)[1])
        assert any(row["id"] == conversation["id"] for row in rows)
        assert cli(state, "stop", env=env)[0] == 0
        assert cli(state, "stop", env=env)[0] == 0
        assert (state / "data/hub.db").exists()
    finally:
        cli(state, "stop", "--force", env=env)
        core.shutdown()
        core.server_close()


def capture_workbench(access_url: str, directory: Path):
    """Optional public-safe first viewport, using this test's real isolated backend."""
    directory.mkdir(parents=True, exist_ok=True)
    script = r"""
import {chromium} from 'playwright-core';
import {join} from 'node:path';
const [url, output] = process.argv.slice(1);
const browser = await chromium.launch({executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome', args: ['--no-sandbox']});
try {
  for (const [name, width, height, mode] of [['desktop', 1280, 900, 'light'], ['narrow', 390, 844, 'dark']]) {
    const context = await browser.newContext({viewport: {width, height}, colorScheme: mode});
    await context.addInitScript(mode => {
      localStorage.setItem('calli.appearance', JSON.stringify({mode, palette: 'codex'}));
    }, mode);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.waitForFunction(() => !location.hash.includes('token='));
    const row = page.getByText('Retained conversation', {exact: true}).first();
    await row.waitFor({state: 'attached'});
    if (!await row.isVisible()) await page.locator('.menu-btn').click();
    await row.click();
    await page.screenshot({path: join(output, `workbench-${name}.png`), animations: 'disabled'});
    if (errors.length) throw new Error(errors.join('; '));
    await context.close();
  }
} finally { await browser.close(); }
"""
    subprocess.run([shutil.which("node"), "--input-type=module", "-e", script, access_url, str(directory)],
                   cwd=APP, check=True, timeout=70, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(os.environ.get("LOOPX_TEST_USER_SERVICE") != "1", reason="Explicit opt-in: registers an isolated native user service")
def test_native_autostart_recovery_and_disable(workbench_tmp_path):
    """Real launchd/systemd journey; never uses the installed Workbench's state."""
    tmp_path = workbench_tmp_path
    import signal
    import pwd
    from functools import partial
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    native_cli = partial(cli, env={'HOME': real_home, 'XDG_CONFIG_HOME': str(Path(real_home) / '.config')})
    state = tmp_path / 'native-state'
    state.mkdir()
    home = tmp_path / 'agent-home'
    home.mkdir()
    # Keep the real user service manager, isolate every child Agent and its data.
    agents = json.dumps({"agents": [{"id": "pi", "adapter": "pi", "command": [shutil.which("node"), str(APP / "packages/daemon/test/fixtures/fake-pi.mjs")]}]})
    token = 'native-test-only-secret'
    (state / '.env').write_text(
        f'HOME={home}\nLOOCHAT_TOKEN={token}\nLOOCHAT_AGENTS_JSON={agents}\n'
        f'LOOCHAT_WORKDIRS={tmp_path}\nLOOCHAT_HOST=127.0.0.1\n'
        'LOOCHAT_LOOPX_CORE_URL=http://127.0.0.1:1\nLOOCHAT_AUTO_JOIN=off\n'
        'LOOCHAT_CLAUDE_COMMAND=false\nLOOCHAT_CODEX_BIN=/nonexistent\n'
        'LOOCHAT_PI_BIN=/nonexistent\nLOOCHAT_AGY_BIN=/nonexistent\n'
        'LOOCHAT_TRAEX_BIN=/nonexistent\nLOOCHAT_DSH_BIN=/nonexistent\n', encoding='utf-8')
    native_file = None
    try:
        # Idle detached start migrates to supervision and retains the conversation.
        code, detached = native_cli(state, 'start', '--port', str(free_port()))
        assert code == 0, detached
        url = detached['url']
        _, body = api(url, '/api/threads', token, {'agent': 'pi', 'cwd': str(tmp_path), 'title': 'Retained conversation'})
        conversation = json.loads(body)
        api(url, f"/api/threads/{conversation['id']}/turns", token, {'text': 'slow'})
        time.sleep(0.3)
        code, busy_migration = native_cli(state, 'start', '--autostart')
        assert code == 1 and 'running turn' in busy_migration['error']
        assert not (state / 'autostart.json').exists()
        assert native_cli(state, 'stop', '--force')[0] == 0
        assert native_cli(state, 'start')[0] == 0
        code, first = native_cli(state, 'start', '--autostart')
        assert code == 0, first
        auto = first['autostart']
        assert auto['enabled'] is True and auto['running'] is True
        assert auto['pid'] == first['parent_pid']
        native_file = Path(json.loads((state / 'autostart.json').read_text())['file'])
        assert token not in native_file.read_text()
        assert json.loads(api(url, '/api/threads', first['access_url'].split('#token=', 1)[1])[1])[0]['id'] == conversation['id']
        capture = os.environ.get('LOOPX_WORKBENCH_CAPTURE_DIR')
        if capture:
            capture_workbench(first['access_url'], Path(capture))
        code, again = native_cli(state, 'start', '--autostart')
        assert code == 0 and again['pid'] == first['pid'], again
        status = native_cli(state, 'status')[1]
        assert 'access_url' not in status and token not in json.dumps(status)
        # Only the isolated child returned above is crashed, never a saved PID.
        os.kill(first['pid'], signal.SIGKILL)
        recovered = None
        for _ in range(40):
            time.sleep(0.5)
            code, candidate = native_cli(state, 'status')
            if code == 0 and candidate.get('state') == 'running' and candidate['pid'] != first['pid']:
                recovered = candidate
                break
        assert recovered is not None, candidate
        assert recovered['autostart']['enabled'] is True
        assert any(row['id'] == conversation['id'] for row in json.loads(api(url, '/api/threads', token)[1]))
        api(url, f"/api/threads/{conversation['id']}/turns", token, {'text': 'slow'})
        time.sleep(0.3)
        code, busy = native_cli(state, 'stop', '--disable-autostart')
        assert code == 1 and 'running turn' in busy['error']
        assert native_file.exists()
        assert native_cli(state, 'status')[1]['autostart']['enabled'] is True
        assert native_cli(state, 'stop', '--force')[0] == 0
        time.sleep(6)  # longer than native restart delay: an intentional stop stays stopped
        code, stopped = native_cli(state, 'status')
        assert code == 0 and stopped['state'] == 'stopped', stopped
        assert stopped['autostart']['enabled'] is True
        assert stopped['autostart']['running'] is False
        code, restarted = native_cli(state, 'start')
        assert code == 0 and restarted['autostart']['enabled'] is True, restarted
        code, disabled = native_cli(state, 'stop', '--disable-autostart')
        assert code == 0 and disabled['autostart']['configured'] is False, disabled
        assert not native_file.exists()
        assert (state / 'data/hub.db').exists()
    finally:
        code, cleanup = native_cli(state, 'stop', '--force', '--disable-autostart')
        assert code == 0, cleanup
        if native_file:
            assert not native_file.exists()
