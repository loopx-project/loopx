"""A phase deadline stops dispatch without certifying the interrupted work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import socket
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from benchmark.runtime import worker
from benchmark.tests.test_shared_codex_runtime import worker_env
from loopx.global_registry import sync_project_registry_to_global
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane import test_quota_settlement_cli as fixture


def _phase_worker(tmp_path, monkeypatch, *, work_seconds=1, independent=None,
                  output="identity"):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    project, runtime, source = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, runtime, provider="sqlite")
    assert sync_project_registry_to_global(
        registry_path=source, runtime_root_override=str(runtime), goal_id=fixture.GOAL_ID)["ok"]
    env = worker_env(project)
    skills = tmp_path / "skills"
    skills.mkdir()
    env.update(LOOPX_ITERATION_CONTEXT="resume", LOOPX_TASK_ENTRY="seeded-todo",
        LOOPX_TASK_STAGE="execute", LOOPX_SHARED_SKILLS=str(skills),
        LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_REGISTRY=str(runtime / "registry.global.json"),
        LOOPX_GOAL_ID=fixture.GOAL_ID, LOOPX_AGENT_ID=fixture.AGENT_ID)
    env["LOOPX_CLI"] = str(write_python_host_fixture(tmp_path / "loopx-cli",
        f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n"))
    state = project / json.loads(source.read_text())["goals"][0]["state_file"]
    clock = [1000.0]
    monkeypatch.setattr(worker, "time", SimpleNamespace(
        time=lambda: clock[0], time_ns=time.time_ns, monotonic=time.monotonic))
    monkeypatch.setattr(worker, "heartbeat_body", lambda *a, **kw: "synthetic work")
    env.update(LOOPX_PHASE_DEADLINE_EPOCH=str(1160 + work_seconds), LOOPX_EXECUTION_MODE="heartbeat")
    env.pop("LOOPX_CODEX_TURN_TIMEOUT_SEC", None)
    if independent is not None:
        env["LOOPX_CODEX_TURN_TIMEOUT_SEC"] = str(independent)
    Path(env["CODEX_BIN"]).write_text(f"#!{sys.executable}\n" + f"""
import json,os,pathlib,sys,time
pathlib.Path('pid').write_text(str(os.getpid()))
with pathlib.Path('native-starts').open('a') as marker: marker.write('native\\n')
if {output!r} != 'missing_identity':
    print(json.dumps({{'type':'thread.started','thread_id':'original-native'}}),flush=True)
sys.stdin.read()
if {output!r} in ('auth_failure','capacity_failure'):
    code = 'invalid_api_key' if {output!r} == 'auth_failure' else 'model_at_capacity'
    print(json.dumps({{'type':'turn.failed','error':{{'code':code}}}}),flush=True)
    sys.exit(1)
if {output!r} == 'success':
    print(json.dumps({{'type':'turn.completed'}}),flush=True)
    sys.exit(0)
print('{{"type":',end='',flush=True)
if {output!r} == 'truncated_failure': sys.exit(1)
time.sleep(60)
""")
    return env, project, runtime, source, state, clock


def _receipt(env):
    paths = list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def _assert_reaped(project):
    with pytest.raises(ProcessLookupError):
        os.kill(int((project / "pid").read_text()), 0)


def _listen_without_capture(monkeypatch):
    from benchmark.runtime.checkpoint_wake import CheckpointWake
    listeners = []
    def listen(self):
        # Exercise the real polling transport without requesting a capture or
        # weakening its checkpoint declaration/participant qualification.
        self.listener = self.stack.enter_context(socket.socket())
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        listeners.append(self.listener)
        return self
    monkeypatch.setattr(CheckpointWake, "__enter__", listen)
    return listeners


@pytest.mark.parametrize("listener", [False, True])
def test_started_phase_timeout_is_budget_stop_and_preserves_partial_output(tmp_path, monkeypatch, listener):
    env, project, _runtime, _source, state, _clock = _phase_worker(tmp_path, monkeypatch)
    if listener:
        _listen_without_capture(monkeypatch)
    before = state.read_bytes()
    receipt = worker.run_once(env)
    assert receipt.get("budget_exhausted") is True
    assert receipt["timed_out"] is True and receipt["ok"] is False
    assert receipt["host_invoked"] is True and "host_failure" not in receipt
    assert receipt["session"]["session_id"] == "original-native"
    assert state.read_bytes() == before  # No fabricated work completion or spend.
    stdout = next(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/stdout.jsonl"))
    assert stdout.read_text().endswith('{"type":')
    _assert_reaped(project)


def test_preparation_and_admission_consume_original_phase_budget(tmp_path, monkeypatch):
    env, project, *_rest, clock = _phase_worker(tmp_path, monkeypatch)
    original = worker.child_process
    timeouts = []
    def prepare_body(*a, **kw):
        clock[0] += .2
        return "synthetic work"
    @contextmanager
    def admitted(*a, **kw):
        with original(*a, **kw) as process:
            clock[0] += .3
            communicate = process.communicate
            def wait(**kwargs):
                timeouts.append(kwargs["timeout"])
                return communicate(**kwargs)
            process.communicate = wait
            yield process
    monkeypatch.setattr(worker, "heartbeat_body", prepare_body)
    monkeypatch.setattr(worker, "child_process", admitted)
    original_deadline = env["LOOPX_PHASE_DEADLINE_EPOCH"]
    assert worker.run_once(env).get("budget_exhausted") is True
    assert timeouts == [pytest.approx(.5)]
    # Re-entry keeps the same absolute clock, without adding a fresh allowance.
    clock[0] = 1002
    assert worker.run_once(env)["host_invoked"] is False
    assert env["LOOPX_PHASE_DEADLINE_EPOCH"] == original_deadline
    assert (project / "native-starts").read_text().splitlines() == ["native"]
    _assert_reaped(project)


def test_independent_earlier_timeout_stays_unknown(tmp_path, monkeypatch):
    env, project, *_ = _phase_worker(tmp_path, monkeypatch, work_seconds=300, independent=.4)
    receipt = worker.run_once(env)
    assert receipt["timed_out"] and not receipt["ok"]
    assert not receipt.get("budget_exhausted")
    assert receipt["host_failure"]["kind"] == "unknown"
    assert receipt["host_failure"]["retryable"] is False
    _assert_reaped(project)


@pytest.mark.parametrize("after_preparation", [False, True])
def test_exhausted_phase_never_starts_host(tmp_path, monkeypatch, after_preparation):
    env, project, *_rest, clock = _phase_worker(
        tmp_path, monkeypatch, work_seconds=1 if after_preparation else 0)
    if after_preparation:
        def prepare_body(*a, **kw):
            clock[0] += 2
            return "synthetic work"
        monkeypatch.setattr(worker, "heartbeat_body", prepare_body)
    receipt = worker.run_once(env)
    assert receipt.get("budget_exhausted") is True and receipt["ok"]
    assert receipt["host_invoked"] is False
    assert not (project / "native-starts").exists()


@pytest.mark.parametrize(("output", "failure", "retryable"), [
    ("auth_failure", "auth_failed", False),
    ("capacity_failure", "provider_capacity", True),
    ("truncated_failure", "unknown", False),
])
def test_phase_cap_does_not_hide_completed_failure(tmp_path, monkeypatch, output, failure, retryable):
    env, _project, *_ = _phase_worker(tmp_path, monkeypatch, work_seconds=30, output=output)
    receipt = worker.run_once(env)
    assert not receipt["ok"] and receipt["return_code"] == 1
    assert not receipt.get("budget_exhausted")
    assert receipt["host_failure"]["kind"] == failure
    assert receipt["host_failure"]["retryable"] is retryable


def test_host_success_before_phase_cap_is_ordinary_success(tmp_path, monkeypatch):
    env, _project, *_ = _phase_worker(tmp_path, monkeypatch, work_seconds=30, output="success")
    receipt = worker.run_once(env)
    assert receipt["ok"] and receipt["return_code"] == 0
    assert not receipt.get("budget_exhausted") and "host_failure" not in receipt


def test_budget_expiry_cannot_repair_missing_session_identity(tmp_path, monkeypatch):
    env, project, *_ = _phase_worker(tmp_path, monkeypatch, output="missing_identity")
    with pytest.raises(RuntimeError, match="session identity"):
        worker.run_once(env)
    assert not _receipt(env)["ok"] and "session identity" in _receipt(env)["error"]
    _assert_reaped(project)


def test_setup_timeout_is_not_a_phase_stop(tmp_path, monkeypatch):
    import subprocess
    env, project, *_ = _phase_worker(tmp_path, monkeypatch, work_seconds=1)
    def setup_failure(*a, **kw):
        raise subprocess.TimeoutExpired("renderer", .01)
    monkeypatch.setattr(worker, "heartbeat_body", setup_failure)
    receipt = worker.run_once(env)
    assert receipt["timed_out"] and not receipt.get("budget_exhausted")
    assert receipt["host_failure"]["kind"] == "unknown"
    assert not (project / "native-starts").exists()


def test_checkpoint_internal_timeout_is_not_reclassified_after_deadline(tmp_path, monkeypatch):
    import subprocess
    from benchmark.runtime import checkpoint_wake
    env, project, *_ = _phase_worker(tmp_path, monkeypatch, work_seconds=.5)
    listeners = _listen_without_capture(monkeypatch)
    original_select = checkpoint_wake.select.select
    injected = []
    clock = [0.0]
    monkeypatch.setattr(checkpoint_wake, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=time.time))
    def unrelated_timeout(read, write, errors, timeout=None):
        if listeners and read == [listeners[0]]:
            injected.append(True)
            clock[0] = 1  # Past this transport's .5s phase cap.
            raise subprocess.TimeoutExpired("independent transport", .01)
        return original_select(read, write, errors, timeout)
    monkeypatch.setattr(checkpoint_wake.select, "select", unrelated_timeout)
    try:
        receipt = worker.run_once(env)
    except RuntimeError as error:
        # A child interrupted before its first event also fails identity
        # confirmation. That stronger failure must not become a budget stop.
        assert "session identity" in str(error)
        receipt = _receipt(env)
    assert injected == [True]
    assert receipt["timed_out"] and not receipt.get("budget_exhausted")
    assert receipt["host_failure"]["kind"] == "unknown"
    if (project / "pid").exists():
        _assert_reaped(project)


def test_phase_stop_cli_replays_scheduler_stop_without_new_host(tmp_path, monkeypatch, capsys):
    from tests import test_external_scheduler_worker as scheduler_fixture
    scheduler = scheduler_fixture.worker
    env, project, *_ = _phase_worker(tmp_path, monkeypatch)
    # Real worker CLI/child and real scheduler transport. Only the renderer and
    # worker clock are synthetic; session binding/current admission remain real.
    entry = tmp_path / "phase_worker.py"
    entry.write_text("from types import SimpleNamespace\nimport time\n"
        "from benchmark.runtime import worker\n"
        "worker.time=SimpleNamespace(time=lambda:1000.0,time_ns=time.time_ns,monotonic=time.monotonic)\n"
        "worker.heartbeat_body=lambda *a,**kw:'synthetic work'\n"
        "raise SystemExit(worker.main())\n")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    args, _ = scheduler_fixture._guarded_host(tmp_path / "scheduler")
    args.wake_cmd = shlex.join([sys.executable, str(entry)])
    args.wake_stop_exit_code = 75
    args.wake_timeout_seconds = None
    monkeypatch.setattr(scheduler, "run_quota_should_run",
        lambda *a, **kw: scheduler_fixture._hint_payload(should_run=True))
    assert scheduler.run_worker(args) == 0
    assert scheduler.run_worker(args) == 0
    assert (project / "native-starts").read_text().splitlines() == ["native"]
    receipt = _receipt(env)
    assert receipt.get("budget_exhausted") is True and receipt["ok"] is False
    assert "goal_complete=false" in capsys.readouterr().out
    _assert_reaped(project)
