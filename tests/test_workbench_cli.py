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
    for action in ("start", "status", "stop"):
        argv = ["workbench", action, "--state-dir", str(tmp_path), "--format", "json"]
        full = build_parser().parse_args(argv)
        selected = _build_selected_parser("workbench").parse_args(argv)
        assert (full.workbench_action, full.state_dir, full.subcommand_format) == (
            selected.workbench_action, selected.state_dir, selected.subcommand_format)


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
def test_packaged_cli_restart_preserves_conversation_and_external_core(tmp_path):
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
