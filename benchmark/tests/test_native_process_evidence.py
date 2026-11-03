"""Process observations must distinguish native exits from our own cleanup."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from benchmark.tests.test_native_goal_outcomes import native_worker
from benchmark.tests.test_shared_codex_runtime import worker_env


def failed_worker(tmp_path, action, *, during_turn=False):
    env = worker_env(tmp_path)
    env.update(LOOPX_EXECUTION_MODE="native-goal", LOOPX_CODEX_TURN_TIMEOUT_SEC="5")
    script = f'''#!{sys.executable}
import json, os, signal, sys, time
from pathlib import Path
objective = ""
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    with Path("requests.jsonl").open("a") as log:
        log.write(json.dumps(method) + "\\n")
    if method == "initialized":
        continue
    if method == "initialize":
        if not {during_turn!r}:
            {action}
        result = {{}}
    elif method == "thread/start":
        result = {{"thread": {{"id": "thread"}}}}
    elif method == "thread/goal/set":
        objective = request["params"]["objective"]
        result = {{}}
    elif method == "thread/goal/get":
        result = {{"goal": {{"threadId": "thread", "objective": objective, "status": "active"}}}}
    elif method == "turn/start":
        result = {{"turn": {{"id": "turn", "status": "inProgress"}}}}
    else:
        raise AssertionError(method)
    print(json.dumps({{"id": request["id"], "result": result}}), flush=True)
    if method == "turn/start":
        print(json.dumps({{"method": "turn/started", "params": {{"threadId": "thread",
            "turn": {{"id": "turn", "status": "inProgress"}}}}}}), flush=True)
        print(json.dumps({{"method": "item/started", "params": {{"threadId": "thread",
            "turnId": "turn", "item": {{"type": "commandExecution", "id": "tool"}}}}}}), flush=True)
        {action}
'''
    Path(env["CODEX_BIN"]).write_text(script)
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.worker"],
                            env=env, capture_output=True, text=True, timeout=20)
    receipt_path, = (tmp_path / "logs/wakes").glob("*/receipt.json")
    receipt = json.loads(receipt_path.read_text())
    assert not receipt["ok"]
    assert result.returncode != 0
    observation = json.loads((receipt_path.parent / "native-process.json").read_text())
    assert observation == receipt["native_process"]
    assert observation["pid"] > 0
    assert observation["started_at_unix"] > 0
    if sys.platform.startswith("linux"):
        assert observation["linux_start_ticks"] > 0
    calls = [json.loads(line) for line in (Path(env["LOOPX_PROJECT"]) / "requests.jsonl").read_text().splitlines()]
    assert calls.count("initialize") == 1  # The observer never replays a transaction.
    return observation, receipt, calls


@pytest.mark.parametrize("code", [0, 23])
def test_request_eof_retains_actual_exit_code(tmp_path, code):
    observation, receipt, calls = failed_worker(tmp_path, f"os._exit({code})")
    assert observation["first_failure"]["operation"] == "request"
    assert observation["first_failure"]["error_type"] == "NativeGoalProtocolError"
    assert observation["after_cleanup"]["return_code"] == code
    assert observation["after_cleanup"]["signal"] is None
    assert calls == ["initialize"]
    # A zero process exit did not finish the outstanding native transaction.
    assert receipt["error_kind"] == "NativeGoalProtocolError"


@pytest.mark.skipif(os.name != "posix", reason="POSIX child signal semantics")
def test_signal_death_retains_signal_without_inferring_oom(tmp_path):
    observation, _, _ = failed_worker(tmp_path, "os.kill(os.getpid(), signal.SIGKILL)")
    assert observation["after_cleanup"]["return_code"] == -signal.SIGKILL
    assert observation["after_cleanup"]["signal"] == signal.SIGKILL
    assert "oom" not in json.dumps(observation).lower()


@pytest.mark.skipif(os.name != "posix", reason="POSIX pipe and signal semantics")
def test_live_child_eof_does_not_attribute_cleanup_signal_to_native_failure(tmp_path):
    observation, _, calls = failed_worker(tmp_path, "os.close(1); time.sleep(30)")
    assert observation["first_failure"]["process"]["return_code"] is None
    assert observation["before_cleanup"]["return_code"] is None
    assert observation["cleanup_started_at_unix"] >= observation["first_failure"]["observed_at_unix"]
    assert observation["after_cleanup"]["signal"] in {signal.SIGINT, signal.SIGKILL}
    assert calls == ["initialize"]


def test_eof_with_outstanding_tool_keeps_event_failure_and_partial_turn(tmp_path):
    observation, receipt, calls = failed_worker(tmp_path, "os._exit(17)", during_turn=True)
    assert observation["first_failure"]["operation"] == "next_event"
    assert observation["after_cleanup"]["return_code"] == 17
    assert not receipt["native_goal"]["terminal_event_observed"]
    assert calls.count("turn/start") == 1


def test_normal_goal_completion_records_intentional_cleanup_separately(tmp_path):
    result, receipt = native_worker(tmp_path, "complete", "completed")
    assert result.returncode == 0
    observation = receipt["native_process"]
    assert observation["first_failure"] is None
    assert observation["cleanup_trigger"] == "scope_completed"
    assert observation["before_cleanup"]["return_code"] is None
    assert receipt["native_goal_outcome"] == "complete"
    path, = (tmp_path / "logs/wakes").glob("*/native-process.json")
    assert json.loads(path.read_text()) == observation
    assert path.stat().st_mode & 0o777 == 0o600


def test_first_failure_is_durable_before_cleanup_and_never_replaced(tmp_path, monkeypatch):
    from benchmark.runtime import worker
    from loopx.capabilities.benchmark_toolkit.native_codex_process import (
        NativeCodexProcessObservation, write_process_observation,
    )

    path = tmp_path / "process.json"
    observation = NativeCodexProcessObservation(lambda value: write_process_observation(path, value))
    original_reap = worker._reap_child_process
    checked = []

    def reap(process, **kwargs):
        retained = json.loads(path.read_text())
        assert retained["first_failure"]["operation"] == "request"
        assert retained["before_cleanup"]["return_code"] == 0
        assert retained["after_cleanup"] is None
        checked.append(True)
        return original_reap(process, **kwargs)

    monkeypatch.setattr(worker, "_reap_child_process", reap)
    with worker.child_process([sys.executable, "-c", "pass"],
                              env={**os.environ, "LOOPX_PROJECT": str(tmp_path)},
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              process_observation=observation) as process:
        startup = json.loads(path.read_text())
        assert startup["pid"] == process.pid
        assert startup["before_cleanup"] is None
        process.wait(timeout=5)
        observation.failure(process, "request", RuntimeError("PRIVATE-SENTINEL"))
        first = observation.snapshot()["first_failure"]
        observation.failure(process, "next_event", ValueError("different private text"))
        assert observation.snapshot()["first_failure"] == first
    assert checked == [True]
    assert "PRIVATE-SENTINEL" not in path.read_text()
    assert "different private text" not in path.read_text()


def test_observation_write_failure_keeps_original_exception_and_reaps_child(tmp_path):
    from benchmark.runtime.worker import child_process
    from loopx.capabilities.benchmark_toolkit.native_codex_process import NativeCodexProcessObservation

    retained = []

    def unavailable(snapshot):
        retained.append(snapshot)
        raise OSError("private destination is unavailable")

    observation = NativeCodexProcessObservation(unavailable)
    original = RuntimeError("original failure")
    with pytest.raises(RuntimeError) as caught:
        with child_process([sys.executable, "-c", "import time; time.sleep(30)"],
                           env={**os.environ, "LOOPX_PROJECT": str(tmp_path)},
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           process_observation=observation) as process:
            raise original
    assert caught.value is original
    assert process.poll() is not None
    assert observation.snapshot()["persistence_error_type"] == "OSError"
    assert retained[-1]["persistence_error_type"] == "OSError"
    assert "private destination" not in json.dumps(observation.snapshot())
