"""A native observation can attest execution; elapsed downtime cannot."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from benchmark.runtime.execution_commitment import observe


@pytest.fixture
def native(monkeypatch):
    state = {
        "schema_version": "sforge_execution_commitment_observation_v0",
        "task_id": "synthetic-task",
        "observed_at_epoch": time.time(),
        "fulfilled": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/v1/feedback/only-this-trial/execution-commitment"
            raw = state["raw"] if "raw" in state else json.dumps(state).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "SFORGE_FEEDBACK_URL",
        f"http://127.0.0.1:{server.server_port}/api/v1/feedback/only-this-trial",
    )
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def cli(action):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmark.runtime.execution_commitment",
            action,
            "--task-id",
            "synthetic-task",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_completion_requires_an_independent_native_true(native):
    assert observe("synthetic-task") is False
    assert cli("probe").returncode == 0
    assert cli("check").returncode == 1
    native["fulfilled"] = None
    assert observe("synthetic-task") is None
    assert cli("check").returncode == 1
    native["fulfilled"] = True
    accepted = cli("check")
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout) == {"commitment_fulfilled": True}


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "another-trial"},
        {"schema_version": "unknown"},
        {"observed_at_epoch": time.time() - 120},
        {"observed_at_epoch": True},
        {"observed_at_epoch": float("nan")},
        {"fulfilled": 1},
        {"fulfilled": "true"},
    ],
)
def test_wrong_stale_and_malformed_observations_never_accept(native, change):
    native.update(change)
    with pytest.raises(ValueError):
        observe("synthetic-task")
    result = cli("check")
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"available": False}


@pytest.mark.parametrize("raw", [b"[]", b"null", b"not-json", b"{}", b"x" * 8193])
def test_missing_or_invalid_provider_is_not_a_pass(native, raw):
    native["raw"] = raw
    assert cli("probe").returncode == 1
    assert cli("check").returncode == 1


def test_native_capability_bypasses_model_api_proxy(native, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    assert cli("probe").returncode == 0


def test_host_pinned_relay_survives_a_stale_inherited_service_environment(
    native, monkeypatch
):
    endpoint = os.environ["SFORGE_FEEDBACK_URL"]
    monkeypatch.setenv(
        "SFORGE_FEEDBACK_URL", "http://127.0.0.1:1/api/v1/feedback/stale"
    )
    assert observe("synthetic-task", endpoint) is False
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmark.runtime.execution_commitment",
            "probe",
            "--task-id",
            "synthetic-task",
            "--feedback-url",
            endpoint,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {"available": True}




@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "file:///tmp/state",
        "http://127.0.0.1/other",
        "http://127.0.0.1/api/v1/feedback/key?secret=other",
    ],
)
def test_unscoped_endpoints_are_rejected(monkeypatch, endpoint):
    monkeypatch.setenv("SFORGE_FEEDBACK_URL", endpoint)
    assert cli("probe").returncode == 1
