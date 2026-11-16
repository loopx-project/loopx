"""A disposable browser fixture must drain its own workers before deletion."""
import subprocess
import sys
import time
import threading
import http.client
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import tempfile

import pytest

import team_task_revalidation_fixture as fixture
from loopx.control_plane.runtime.file_paths import windows_extended_path


def test_fixture_removes_long_opaque_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    with fixture.fixture_workspace() as directory:
        root = Path(directory)
        leaf = root / ('a' * 64) / ('b' * 64) / (('c' * 64) + '.json.lock.incidents.jsonl')
        extended = windows_extended_path(leaf)
        extended.parent.mkdir(parents=True)
        extended.write_text('owned fixture data')
        assert extended.read_text() == 'owned fixture data'
        assert len(str(leaf)) > 260
    assert not root.exists(), 'long-path cleanup silently left fixture data behind'


def test_fixture_closes_inflight_http_before_directory_cleanup():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            assert release.wait(timeout=5)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_):
            pass

    server = fixture.fixture_http_server(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    responses = []
    def request():
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request('GET', '/')
            responses.append(connection.getresponse().status)
        finally:
            connection.close()
    client = threading.Thread(target=request)
    client.start()
    closer = None
    try:
        assert entered.wait(timeout=5)
        server.shutdown()
        def close():
            server.server_close()
            closed.set()
        closer = threading.Thread(target=close)
        closer.start()
        assert not closed.wait(timeout=.05), 'cleanup raced an active HTTP handler'
        release.set()
        assert closed.wait(timeout=5)
        client.join(timeout=5)
        assert responses == [200]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
        client.join(timeout=5)
        if closer is not None:
            closer.join(timeout=5)


def test_fixture_waits_for_real_owned_writer(tmp_path):
    completed = tmp_path / 'completed'
    worker = subprocess.Popen([sys.executable, '-c',
        "import sys,time; from pathlib import Path; time.sleep(.2); Path(sys.argv[1]).write_text('complete')",
        str(completed)])
    try:
        fixture.drain_fixture_workers([worker], timeout=5)
        assert worker.returncode == 0 and completed.read_text() == 'complete'
    finally:
        if worker.poll() is None:
            worker.terminate()
        worker.wait(timeout=5)


def test_fixture_does_not_accept_an_undrained_worker(tmp_path):
    ready = tmp_path / 'ready'
    worker = subprocess.Popen([sys.executable, '-c',
        "import sys,time; from pathlib import Path; Path(sys.argv[1]).touch(); time.sleep(60)", str(ready)])
    try:
        deadline = time.monotonic()+5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists() and worker.poll() is None
        with pytest.raises(subprocess.TimeoutExpired):
            fixture.drain_fixture_workers([worker], timeout=.02)
        assert worker.poll() is None
    finally:
        worker.terminate()
        worker.wait(timeout=5)


def test_fixture_does_not_accept_worker_failure():
    worker = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(7)'])
    try:
        with pytest.raises(RuntimeError, match='worker exited with status 7'):
            fixture.drain_fixture_workers([worker], timeout=5)
    finally:
        worker.wait(timeout=5)
