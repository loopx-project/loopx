"""Receiver startup checks never dispatch a model or rewrite failed work."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

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
import json, os, sys, time
from pathlib import Path
counter = Path(os.environ["PROBE_ATTEMPTS"])
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
mode = os.environ["PROBE_MODE"]
print("private native stderr must not escape", file=sys.stderr)
for line in sys.stdin:
    packet = json.loads(line)
    pathlib = __import__("pathlib")
    pathlib.Path(os.environ["CODEX_HOME"], "skills", ".system").mkdir(parents=True, exist_ok=True)
    with open(os.environ["PROBE_TRACE"], "a") as stream:
        stream.write(json.dumps(packet) + "\\n")
    if "id" not in packet:
        continue
    method = packet["method"]
    blocked = {"account_timeout": "account/read", "account_timeout_once": "account/read",
               "usage_timeout_once": "account/rateLimits/read", "initialize_timeout": "initialize"}
    if method == blocked.get(mode) and (not mode.endswith("_once") or attempt == 1):
        time.sleep(60)  # The real stdio owner must time out and terminate this observer.
    if mode in ("disconnect", "disconnect_once") and method == "initialize":
        if mode == "disconnect" or attempt == 1:
            sys.exit(0)
    if mode == "malformed":
        print("private malformed frame", flush=True)
        continue
    result = {}
    if method == "account/read":
        result = {"account": {"type": "chatgpt", "email": "private@example.invalid"}}
    if method == "account/rateLimits/read":
        result = {"rateLimits": {"primary": {"usedPercent": 97}}}
    if mode == "invalid_account" and method == "account/read":
        result = {"account": {"type": "apiKey"}}
    if mode == "empty_usage" and method == "account/rateLimits/read":
        result = {}
    response = {"id": packet["id"], "result": result}
    if mode == "forged_timeout" and method == "account/read":
        response = {"id": packet["id"], "error": {
            "code": -32603, "message": "app_server_response_timeout:account/read"}}
    if os.environ["PROBE_MODE"] == method:
        response = {"id": packet["id"], "error": {"code": -32603,
                    "message": "private login and internal route must not escape"}}
    print(json.dumps(response), flush=True)
''')
    binary.chmod(0o700)
    env = {**os.environ, "CODEX_BIN": str(binary), "LOOPX_CODEX_HOME": str(home),
           "CODEX_HOME": str(tmp_path / "wrong-home"), "LOOPX_PROJECT": str(tmp_path),
           "PROBE_TRACE": str(trace), "PROBE_MODE": mode,
           "PROBE_ATTEMPTS": str(tmp_path / "attempts")}
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
        assert receipt["attempts"][0]["stage"] == {
            "initialize": "initialize", "account/read": "account_read",
            "account/rateLimits/read": "rate_limits"}[failure]
        assert receipt["attempts"][0]["error_kind"] == "protocol_rejected"
        assert len(receipt["attempts"]) == 1
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
    now = [100.0]
    monkeypatch.setattr(codex_readiness.time, "monotonic", lambda: now[0])
    original_copy = codex_readiness.shutil.copyfile

    def copy(*args):
        now[0] += 4
        return original_copy(*args)

    monkeypatch.setattr(codex_readiness.shutil, "copyfile", copy)
    budgets = []

    class Reader:
        def request(self, method, params):
            budgets.append(self.response_timeout_sec)
            now[0] += 5
            return {"account": {"type": "chatgpt"}} if method == "account/read" else {"rateLimits": {}}

    @contextmanager
    def observer(native_env, *, timeout, deadline):
        observer_home = Path(native_env["CODEX_HOME"])
        assert observer_home != home
        assert (observer_home / "auth.json").read_bytes() == (home / "auth.json").read_bytes()
        assert timeout == 21  # Copy time is charged before initialization.
        now[0] += 5
        yield Reader()

    monkeypatch.setattr(codex_readiness, "native_checkpoint_reader", observer)
    result = check_native_receiver(env)
    assert result["ok"] is True
    assert budgets == [16, 11]
    assert result["elapsed_seconds"] == 19


@pytest.mark.parametrize("mode,ok,attempts,stage,kind", [
    ("disconnect_once", True, 2, "initialize", "disconnected"),
    ("disconnect", False, 3, "initialize", "disconnected"),
    ("forged_timeout", False, 1, "account_read", "protocol_rejected"),
    ("malformed", False, 1, "initialize", "protocol_rejected"),
    ("invalid_account", False, 1, "account_read", "invalid_account"),
    ("empty_usage", False, 1, "rate_limits", "unavailable_rate_limits"),
])
def test_real_native_cli_retry_and_failure_boundaries(tmp_path, mode, ok, attempts, stage, kind):
    env, trace, home, pending = native_fixture(tmp_path, mode)
    original = {p: p.read_bytes() for p in (home / "auth.json", pending)}
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.codex_readiness"],
                            env=env, text=True, capture_output=True, timeout=10)
    receipt = json.loads(result.stdout)
    assert result.returncode == (0 if ok else 1)
    assert receipt["ok"] is ok
    assert receipt["inference_invoked"] is False
    assert len(receipt["attempts"]) == attempts
    assert int(Path(env["PROBE_ATTEMPTS"]).read_text()) == attempts
    assert receipt["attempts"][0]["stage"] == stage
    assert receipt["attempts"][0]["error_kind"] == kind
    assert "private" not in result.stdout + result.stderr
    methods = [json.loads(line)["method"] for line in trace.read_text().splitlines()]
    assert set(methods) <= {"initialize", "initialized", "account/read", "account/rateLimits/read"}
    assert {p: p.read_bytes() for p in original} == original
    assert not (home / "skills").exists()
    assert not (tmp_path / "phase-deadline").exists()


@pytest.mark.parametrize("expire_at", ["copy", "initialize", "account_read", "rate_limits"])
def test_total_deadline_cannot_be_reset_by_retry(tmp_path, monkeypatch, expire_at):
    from contextlib import contextmanager
    from benchmark.runtime import codex_readiness
    from loopx.control_plane.turn_driver.codex_stdio import NativeGoalProtocolError
    env, _, _, _ = native_fixture(tmp_path, "success")
    now = [100.0]
    monkeypatch.setattr(codex_readiness.time, "monotonic", lambda: now[0])
    instances = []
    original_copy = codex_readiness.shutil.copyfile

    def copy(*args):
        if expire_at == "copy":
            now[0] = 126
        return original_copy(*args)

    monkeypatch.setattr(codex_readiness.shutil, "copyfile", copy)

    class Reader:
        def request(self, method, params):
            if (expire_at, method) in (("account_read", "account/read"),
                                     ("rate_limits", "account/rateLimits/read")):
                now[0] = 126
                raise NativeGoalProtocolError("app_server_response_timeout:" + method)
            return {"account": {"type": "chatgpt"}} if method == "account/read" else {"rateLimits": {}}

    @contextmanager
    def observer(native_env, *, timeout, deadline):
        instances.append(native_env["CODEX_HOME"])
        if expire_at == "initialize":
            now[0] = 126
            raise NativeGoalProtocolError("app_server_response_timeout:initialize")
        yield Reader()

    monkeypatch.setattr(codex_readiness, "native_checkpoint_reader", observer)
    receipt = check_native_receiver(env)
    assert receipt["ok"] is False
    assert len(receipt["attempts"]) == 1
    assert receipt["attempts"][0]["error_kind"] == "timeout"
    assert receipt["attempts"][0]["stage"] == ("inspection_home" if expire_at == "copy" else expire_at)
    assert len(instances) == (0 if expire_at == "copy" else 1)


def test_retry_charges_cleanup_and_backoff_and_uses_a_fresh_home(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from benchmark.runtime import codex_readiness
    from loopx.control_plane.turn_driver.codex_stdio import NativeGoalProtocolError
    env, _, home, _ = native_fixture(tmp_path, "success")
    now = [100.0]
    monkeypatch.setattr(codex_readiness.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(codex_readiness.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    instances, budgets = [], []

    class Reader:
        def request(self, method, params):
            return {"account": {"type": "chatgpt"}} if method == "account/read" else {"rateLimits": {}}

    @contextmanager
    def observer(native_env, *, timeout, deadline):
        instance = Path(native_env["CODEX_HOME"])
        assert instance != home
        assert not (instance / "prior-observer-write").exists()
        assert (instance / "auth.json").read_bytes() == (home / "auth.json").read_bytes()
        instances.append(instance)
        budgets.append(timeout)
        if len(instances) == 1:
            (instance / "prior-observer-write").touch()
            now[0] += 5  # failed transport plus its cleanup
            raise NativeGoalProtocolError("app_server_stream_closed")
        yield Reader()

    monkeypatch.setattr(codex_readiness, "native_checkpoint_reader", observer)
    receipt = check_native_receiver(env)
    assert receipt["ok"] is True
    assert len(instances) == 2 and len(set(instances)) == 2
    assert all(not instance.exists() for instance in instances)
    assert budgets == pytest.approx([25, 19.8])
    assert receipt["elapsed_seconds"] == pytest.approx(5.2)


@pytest.mark.parametrize("expiry", ["spawn", "initialize"])
def test_native_reader_rechecks_deadline_after_spawn_and_before_notify(tmp_path, monkeypatch, expiry):
    from benchmark.runtime import codex_readiness
    from loopx.control_plane.turn_driver import codex_stdio
    env, _, _, _ = native_fixture(tmp_path, "success")
    now = [100.0]
    monkeypatch.setattr(codex_readiness.time, "monotonic", lambda: now[0])
    calls = []

    class Transport:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            calls.append("closed")
        def request(self, method, params):
            calls.append((method, now[0], self.response_timeout_sec))
            now[0] = 126
            return {}
        def notify(self, method, params):
            calls.append(method)

    def spawn(*args, **kwargs):
        calls.append("spawn")
        if expiry == "spawn":
            now[0] = 126
        return Transport()

    monkeypatch.setattr(codex_stdio.StdioNativeGoalTransport, "spawn", spawn)
    result = check_native_receiver(env)
    assert result["ok"] is False
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["stage"] == "initialize"
    assert result["attempts"][0]["error_kind"] == "timeout"
    assert calls == (["spawn", "closed"] if expiry == "spawn" else [
        "spawn", ("initialize", 100, 25), "closed"])


def test_unknown_exception_and_local_configuration_fail_closed(tmp_path, monkeypatch):
    from benchmark.runtime import codex_readiness
    env, _, _, _ = native_fixture(tmp_path, "success")
    def observer(*args, **kwargs):
        raise RuntimeError("private login details and app_server_stream_closed")
    monkeypatch.setattr(codex_readiness, "native_checkpoint_reader", observer)
    receipt = check_native_receiver(env)
    assert len(receipt["attempts"]) == 1
    assert receipt["attempts"][0]["error_kind"] == "unexpected"
    assert "private" not in json.dumps(receipt)
    receipt = check_native_receiver({})
    assert len(receipt["attempts"]) == 1
    assert receipt["attempts"][0]["stage"] == "inspection_home"
    assert receipt["attempts"][0]["error_kind"] == "configuration"


@pytest.mark.parametrize("profile", ["native-goal", "heartbeat-resume", "heartbeat-explore"])
@pytest.mark.parametrize("budget", [False, True])
def test_sforge_rejects_bad_receiver_before_clock_or_scheduler(tmp_path, monkeypatch, profile, budget):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex, _SRC, _PYTHON, _CODEX_HOME
    from pathlib import Path

    env, trace, home, pending = native_fixture(tmp_path, "account/read")
    env.update(SFORGE_AGENT_REMAINING_SECONDS="100", SFORGE_AGENT_SEGMENT_STARTED_AT=str(time.time()))
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(home / "auth.json"))

    async def installed(self, environment):
        pass

    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile=profile, cwd=str(tmp_path),
                          **({"native_readiness_total_seconds": 75} if budget else {}))
    worker.install_stop_hook(None, None, tmp_path, None)
    worker.prepared = True
    selected = json.loads((tmp_path / "worker-profile.json").read_text()).get("native_readiness_budget")
    assert selected == ({"total_seconds": 75, "attempt_seconds": 25} if budget else None)
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
