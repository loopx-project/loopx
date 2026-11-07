"""Receiver startup checks never dispatch a model or rewrite failed work."""
import json
import os
import subprocess
import sys

import pytest

from benchmark.runtime.codex_readiness import check_native_receiver


def native_fixture(tmp_path, mode):
    home = tmp_path / "native-home"
    home.mkdir()
    (home / "auth.json").write_text("private synthetic login")
    pending = tmp_path / "scheduler.json.host-attempt"
    pending.write_text('{"pending":true}')
    trace = tmp_path / "requests.jsonl"
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
for line in sys.stdin:
    packet = json.loads(line)
    pathlib = __import__("pathlib")
    pathlib.Path(os.environ["CODEX_HOME"], "skills", ".system").mkdir(parents=True, exist_ok=True)
    with open(os.environ["PROBE_TRACE"], "a") as stream:
        stream.write(json.dumps(packet) + "\\n")
    if "id" not in packet:
        continue
    method = packet["method"]
    result = {}
    if method == "account/read":
        result = {"account": {"type": "chatgpt", "email": "private@example.invalid"}}
    if method == "account/rateLimits/read":
        result = {"rateLimits": {"primary": {"usedPercent": 97}}}
    response = {"id": packet["id"], "result": result}
    if os.environ["PROBE_MODE"] == method:
        response = {"id": packet["id"], "error": {"code": -32603,
                    "message": "private login and internal route must not escape"}}
    print(json.dumps(response), flush=True)
''')
    binary.chmod(0o700)
    env = {**os.environ, "CODEX_BIN": str(binary), "LOOPX_CODEX_HOME": str(home),
           "CODEX_HOME": str(tmp_path / "wrong-home"), "LOOPX_PROJECT": str(tmp_path),
           "PROBE_TRACE": str(trace), "PROBE_MODE": mode}
    return env, trace, home, pending


@pytest.mark.parametrize("failure", [None, "initialize", "account/read", "account/rateLimits/read"])
def test_cli_checks_receiver_without_model_or_state_mutation(tmp_path, failure):
    env, trace, home, pending = native_fixture(tmp_path, failure or "success")
    original = {p: p.read_bytes() for p in (home / "auth.json", pending)}
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.codex_readiness"],
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == (1 if failure else 0)
    receipt = json.loads(result.stdout)
    assert receipt["ok"] is (failure is None)
    assert receipt["inference_invoked"] is False
    assert "private" not in result.stdout + result.stderr
    if failure:
        assert receipt["code"] == "native_receiver_unavailable"
    requests = [json.loads(line) for line in trace.read_text().splitlines()]
    methods = [r["method"] for r in requests]
    expected = ["initialize", "initialized", "account/read", "account/rateLimits/read"]
    if failure:
        expected = expected[:expected.index(failure) + 1]
    assert methods == expected
    if "account/read" in methods:
        assert requests[methods.index("account/read")]["params"] == {"refreshToken": False}
    assert {p: p.read_bytes() for p in original} == original
    assert not (home / "skills").exists()
    assert not (tmp_path / "phase-deadline").exists()


def test_observer_uses_receiver_home_and_one_total_io_budget(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from benchmark.runtime import codex_readiness
    env, _, home, _ = native_fixture(tmp_path, "success")
    times = iter([100, 104, 109])
    monkeypatch.setattr(codex_readiness.time, "monotonic", lambda: next(times))
    budgets = []

    class Reader:
        def request(self, method, params):
            budgets.append(self.response_timeout_sec)
            return {"account": {"type": "chatgpt"}} if method == "account/read" else {"rateLimits": {}}

    @contextmanager
    def observer(native_env, *, timeout):
        observer_home = __import__("pathlib").Path(native_env["CODEX_HOME"])
        assert observer_home != home
        assert (observer_home / "auth.json").read_bytes() == (home / "auth.json").read_bytes()
        assert timeout == 25
        yield Reader()

    monkeypatch.setattr(codex_readiness, "native_checkpoint_reader", observer)
    check_native_receiver(env)
    assert budgets == [21, 16]


@pytest.mark.parametrize("profile", ["native-goal", "heartbeat-resume", "heartbeat-explore"])
def test_sforge_rejects_bad_receiver_before_clock_or_scheduler(tmp_path, monkeypatch, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex, _SRC, _PYTHON, _CODEX_HOME
    from pathlib import Path

    env, trace, home, pending = native_fixture(tmp_path, "account/read")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(home / "auth.json"))

    async def installed(self, environment):
        pass

    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile=profile, cwd=str(tmp_path))
    worker.install_stop_hook(None, None, tmp_path, None)
    worker.prepared = True
    command = worker.format_run_cmd("/task.md")
    checkout = str(Path(__file__).resolve().parents[2])
    runtime = tmp_path / "runtime"
    command = (command.replace(_SRC, checkout)
               .replace(f"{_PYTHON}/bin/python3", sys.executable)
               .replace(_CODEX_HOME, str(home))
               .replace("/usr/local/bin/codex", env["CODEX_BIN"])
               .replace("/opt/loopx-benchmark", str(runtime)))
    result = subprocess.run(["/bin/bash", "-c", command], env=env, text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "native_receiver_unavailable"
    assert not (runtime / "control/phase-deadline").exists()
    assert not (runtime / "control/execution-started").exists()
    assert pending.read_text() == '{"pending":true}'
    assert [json.loads(line)["method"] for line in trace.read_text().splitlines()] == [
        "initialize", "initialized", "account/read"]
