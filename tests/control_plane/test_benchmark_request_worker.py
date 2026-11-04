"""Real worker/canonical admission with a scripted host and local HTTP.

These tests do not qualify a Codex binary or invoke a model.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from benchmark.runtime import worker
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from tests.capabilities.test_provider_gateway import _recording_upstream
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane.test_benchmark_current_admission import _environment


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("enabled", [False, True])
def test_worker_request_route_is_invocation_only_and_default_off(tmp_path, monkeypatch, provider, enabled):
    env, project, _runtime, _source, _state = _environment(tmp_path, monkeypatch, provider)
    host = r'''
import json, os, sys, urllib.request
from pathlib import Path
sys.stdin.read()
print(json.dumps({"type":"thread.started", "thread_id":"benchmark-original-session"}), flush=True)
overrides = [arg for arg in sys.argv if arg.startswith("model_providers.harbor.base_url=")]
endpoint = json.loads(overrides[0].split("=",1)[1]) if overrides else os.environ["OPENAI_BASE_URL"]
with urllib.request.urlopen(urllib.request.Request(endpoint + "/responses",data=b'{"input":"fixture"}'), timeout=30) as response:
    assert response.status == 200
Path("request-route.json").write_text(json.dumps({"endpoint":endpoint,"argv":sys.argv[1:]}))
'''
    env["CODEX_BIN"] = str(write_python_host_fixture(project / "provider-host", f"#!{sys.executable}\n" + host))
    with _recording_upstream() as (upstream, observed):
        env["OPENAI_BASE_URL"] = upstream
        if enabled:
            env["LOOPX_PROVIDER_REQUEST_ADMISSION"] = "on"
        receipt = worker.run_once(env)
    assert receipt["ok"] and receipt["host_invoked"]
    assert observed["path"] == "/v1/responses" and observed["body"] == b'{"input":"fixture"}'
    route = json.loads((project / "request-route.json").read_text())
    home = (Path(env["LOOPX_CODEX_HOME"]) / "config.toml").read_text()
    assert upstream in home
    if enabled:
        assert route["endpoint"] != upstream and route["endpoint"] not in home
        assert receipt["provider_request_admission"] == "on"
        [invocation] = receipt["provider_request_invocations"]
        assert invocation["turn_id"] == receipt["turn_id"]
        assert invocation["requests"] == invocation["sent"] == 1
        assert invocation["refused"] == invocation["unknown"] == 0
    else:
        assert route["endpoint"] == upstream
        assert "provider_request_admission" not in receipt
        assert "provider_request_invocations" not in receipt


def test_worker_does_not_certify_native_success_after_request_refusal(tmp_path, monkeypatch):
    from benchmark.runtime import provider_request_admission

    env, project, _runtime, _source, _state = _environment(tmp_path, monkeypatch, "sqlite")
    # The original worker/launch check is real. Only the transport deadline is
    # exhausted after native attachment, so Popen has already succeeded.
    original = provider_request_admission.ProviderRequestAdmission.started
    clock = [time.monotonic()]
    monkeypatch.setattr(provider_request_admission, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: clock[0]))

    def exhausted(self, process):
        original(self, process)
        clock[0] += 3600

    monkeypatch.setattr(provider_request_admission.ProviderRequestAdmission, "started", exhausted)
    host = r'''
import json, signal, sys, urllib.error, urllib.request
signal.signal(signal.SIGINT, signal.SIG_IGN)
sys.stdin.read()
print(json.dumps({"type":"thread.started","thread_id":"benchmark-original-session"}),flush=True)
endpoint = json.loads(next(arg for arg in sys.argv if arg.startswith("model_providers.harbor.base_url=")).split("=",1)[1])
try:
    urllib.request.urlopen(urllib.request.Request(endpoint+"/responses",data=b"{}"),timeout=30)
except urllib.error.HTTPError as error:
    assert error.code == 409
sys.exit(0)
'''
    env["CODEX_BIN"] = str(write_python_host_fixture(project / "provider-host", f"#!{sys.executable}\n" + host))
    with _recording_upstream() as (upstream, observed):
        env.update(OPENAI_BASE_URL=upstream, LOOPX_PROVIDER_REQUEST_ADMISSION="on")
        with pytest.raises(FirstPartyHostRuntimeRejected, match="provider_request_budget_exhausted"):
            worker.run_once(env)
    [receipt_path] = Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json")
    receipt = json.loads(receipt_path.read_text())
    assert not receipt["ok"] and receipt["return_code"] == 0
    assert receipt["host_failure"]["kind"] == "unknown" and not receipt["host_failure"]["retryable"]
    assert not observed
