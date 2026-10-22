"""Native terminal evidence must survive the worker and scoring handoff."""

import json
import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from benchmark.tests.test_shared_codex_runtime import worker_env


def native_worker(tmp_path, goal_status, turn_status, *, http_status=None):
    env = worker_env(tmp_path)
    env.update(LOOPX_EXECUTION_MODE="native-goal", LOOPX_TURN_TIMEOUT_SECONDS="10")
    error = ({"message": "PRIVATE-SENTINEL", "codexErrorInfo": {
        "httpConnectionFailed": {"httpStatusCode": http_status}}} if http_status else None)
    terminal = {"method": "turn/completed", "params": {"threadId": "t1",
                    "turn": {"id": "r1", "status": turn_status, "error": error}}}
    Path(env["CODEX_BIN"]).write_text(f"#!{sys.executable}\n" + f'''
import json, sys
objective = ""
started = False
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    params = request.get("params", {{}})
    if method == "initialized":
        continue
    if method == "initialize":
        result = {{}}
    elif method == "thread/start":
        result = {{"thread": {{"id": "t1"}}}}
    elif method == "thread/goal/set":
        objective = params["objective"]
        result = {{"goal": {{"threadId": "t1"}}}}
    elif method == "thread/goal/get":
        result = {{"goal": {{"threadId": "t1", "objective": objective,
                              "status": {goal_status!r} if started else "active"}}}}
    elif method == "turn/start":
        assert not started, "unexpected external continuation"
        started = True
        result = {{"turn": {{"id": "r1", "status": "inProgress"}}}}
    else:
        raise AssertionError(method)
    print(json.dumps({{"id": request["id"], "result": result}}), flush=True)
    if method == "turn/start":
        print(json.dumps({{"method": "turn/started", "params": {{"threadId": "t1",
            "turn": {{"id": "r1", "status": "inProgress"}}}}}}), flush=True)
        if {error!r}:
            print(json.dumps({{"method": "error", "params": {{"threadId": "t1",
                "turnId": "r1", "error": {error!r}}}}}), flush=True)
        print(json.dumps({terminal!r}), flush=True)
''')
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.worker"],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.stdout, result.stderr
    receipt = json.loads(result.stdout)
    persisted = list((tmp_path / "logs/wakes").glob("*/receipt.json"))
    assert len(persisted) == 1
    assert json.loads(persisted[0].read_text()) == receipt
    assert "PRIVATE-SENTINEL" not in json.dumps(receipt)
    return result, receipt


@pytest.mark.parametrize("goal,turn,outcome,exit_code", [
    ("complete", "completed", "complete", 0),
    ("blocked", "completed", "blocked", 0),
    ("blocked", "failed", "failed", 1),
    ("complete", "failed", "failed", 1),
    ("paused", "interrupted", "cancelled", 130),
    ("paused", "completed", "paused", 0),
    ("budgetLimited", "completed", "budget_exhausted", 0),
    ("usageLimited", "completed", "usage_limited", 1),
    ("future-status", "completed", "unknown", 1),
])
def test_native_terminal_outcome_survives_real_worker_process(tmp_path, goal, turn, outcome, exit_code):
    result, receipt = native_worker(tmp_path, goal, turn)
    assert receipt["native_goal_outcome"] == outcome
    assert result.returncode == exit_code
    assert receipt["ok"] is (exit_code == 0)
    assert receipt["native_goal"]["post_goal_status"] == goal


@pytest.mark.parametrize("http_status", [401, 429, 503])
def test_terminal_error_is_failed_without_counting_printed_error_twice(tmp_path, http_status):
    result, receipt = native_worker(tmp_path, "blocked", "failed", http_status=http_status)
    assert result.returncode == 1
    assert receipt["native_goal_outcome"] == "failed"
    native = receipt["native_goal"]
    assert native["turn_status"] == "failed"
    assert native["error_event_count"] == 1  # terminal error is evidence, not a second request
    assert native["terminal_error_http_status"] == http_status
    assert native["terminal_error_signature"]


def test_jsonl_terminal_error_and_later_recovery_do_not_leak_or_poison_completion():
    from benchmark.tests.test_native_codex_goal import FakeTransport, _config
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
        compact_native_goal_receipt, native_goal_outcome, observe_native_goal_event, start_native_goal_turn,
    )

    turn = start_native_goal_turn(FakeTransport(), _config())
    observe_native_goal_event(turn, {"type": "event_msg", "payload": {
        "type": "task_complete", "error": {"message": "PRIVATE-SENTINEL",
            "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}}}})
    turn.post_goal_status = "blocked"
    assert native_goal_outcome(turn) == "failed"
    assert turn.terminal_error_kind == "responseTooManyFailedAttempts"
    assert turn.terminal_error_http_status == 429
    assert turn.error_event_count == 0  # no error notification in this stream
    assert "PRIVATE-SENTINEL" not in json.dumps(compact_native_goal_receipt(turn))
    observe_native_goal_event(turn, {"method": "turn/started", "params": {
        "threadId": turn.thread_id, "turn": {"id": "next", "status": "inProgress"}}})
    assert turn.terminal_error_signature is None
    assert not turn.terminal_event_observed
    observe_native_goal_event(turn, {"method": "turn/completed", "params": {
        "threadId": turn.thread_id, "turn": {"id": "next", "status": "completed"}}})
    turn.post_goal_status = "complete"
    assert native_goal_outcome(turn) == "complete"


@pytest.mark.parametrize("error", ["PRIVATE-SENTINEL", {"codexErrorInfo": "PRIVATE-SENTINEL"},
                                  {"codexErrorInfo": {"private-field": {"httpStatusCode": 401}}}])
def test_unknown_terminal_error_stays_unknown(error):
    from benchmark.tests.test_native_codex_goal import FakeTransport, _config
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
        compact_native_goal_receipt, observe_native_goal_event, start_native_goal_turn,
    )
    turn = start_native_goal_turn(FakeTransport(), _config())
    observe_native_goal_event(turn, {"type": "event_msg", "payload": {"type": "task_complete", "error": error}})
    assert turn.turn_status == "failed"
    assert turn.terminal_error_kind == "unknown"
    assert turn.terminal_error_http_status is None
    assert "PRIVATE-SENTINEL" not in json.dumps(compact_native_goal_receipt(turn))


def test_edgebench_retains_failed_native_score_without_claiming_completion(tmp_path):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _result_status, _write_native_final_result
    from sforge.harness.run_agent import RunResult

    result, receipt = native_worker(tmp_path, "blocked", "failed", http_status=401)
    status = _result_status(interrupted=False, started=True, execution_started=True,
                            runtime_seconds=2, exit_code=result.returncode,
                            native_goal_outcome=receipt["native_goal_outcome"])
    assert status == "runner_failed"
    score = RunResult(runtime_seconds=2, best_score=12.5, total_rounds=2)
    _write_native_final_result(tmp_path, score, status=status, agent="fixture", task="fixture",
                              run_id="fixture", model="fixture", effort="high",
                              native_goal_outcome=receipt["native_goal_outcome"])
    partial = json.loads((tmp_path / "partial_result.json").read_text())
    assert all(partial[key] == value for key, value in score.to_dict().items())
    assert partial["runtime_status"] == "runner_failed"
    assert partial["native_goal_outcome"] == "failed"
    assert not (tmp_path / "final_result.json").exists()
    assert not (tmp_path / "partial_result.json.tmp").exists()


@pytest.mark.parametrize("outcome,expected_code", [
    ("failed", 1), ("usage_limited", 1), ("cancelled", 130), (None, 1),
    ("complete", 0), ("blocked", 0), ("paused", 0), ("budget_exhausted", 0),
])
def test_edgebench_cli_persists_native_outcome_even_when_transport_exits_zero(tmp_path, monkeypatch, outcome, expected_code):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run
    from sforge.harness.run_agent import RunResult

    for name, value in {"LOOPX_SRC_DIR": str(tmp_path), "LOOPX_EXPECTED_COMMIT": "fixture",
                        "OPENAI_BASE_URL": "http://localhost:8123/v1", "OPENAI_API_KEY": "fixture-key"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "active_source_receipt", lambda: {})
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        task_id="fixture", cwd="/task", work_image_key="work", judge_image_key="judge",
        internet=False, game_mode=False, submit_paths=["solver.py"],
        work=SimpleNamespace(agent_query="Synthetic task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    backend = SimpleNamespace(image_exists=lambda image: True,
                              execution_receipt={"exit_code": 0, "execution_started": True},
                              native_goal_receipt={"native_goal_outcome": outcome} if outcome else None)
    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **kwargs: backend)
    trial = tmp_path / "runs" / "attempt" / "fixture"
    def completed_scoring(**kwargs):
        assert kwargs["disable_auto_resume"] is True
        (trial / "started_at").write_text("2024-01-01T00:00:00Z\n1704067200\n")
        return RunResult(runtime_seconds=2, best_score=4, total_rounds=1)
    monkeypatch.setattr(run, "run_agent", completed_scoring)
    (tmp_path / "fixture.json").write_text("{}")
    code = run.main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
                     "--run-id", "attempt", "--worker", "native-goal", "--model", "fixture",
                     "--effort", "high", "--judge-url", "http://localhost:9999"])
    assert code == expected_code
    receipt = json.loads((trial / "runtime-receipt.json").read_text())
    assert receipt["native_goal_outcome"] == (outcome or "unknown")
    assert receipt["score_countable"] is False
    assert receipt["status"] == ("terminal" if expected_code == 0 else
                                  "cancelled" if expected_code == 130 else "runner_failed")
    terminal = expected_code == 0
    artifact = trial / ("final_result.json" if terminal else "partial_result.json")
    result = json.loads(artifact.read_text())
    assert result["runtime_status"] == receipt["status"]
    assert result["best_score"] == 4 and result["total_rounds"] == 1
    assert (trial / "final_result.json").exists() is terminal
    assert (trial / "partial_result.json").exists() is not terminal

    # Exercise the real consumer through normal directory discovery and HTTP
    # rendering: outcome labels alone cannot override its completion marker.
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from sforge.visualizer.scanner import RunsIndex
    from sforge.visualizer.server import create_app

    discovered, = RunsIndex(tmp_path / "runs").list_runs(include_submissions=False)
    assert discovered.has_final is terminal
    assert discovered.aborted is not terminal
    with TestClient(create_app(tmp_path / "runs")) as client:
        response = client.get("/")
    assert response.status_code == 200
    group = response.text.split('data-run-id="attempt"', 1)[1].split("</details>", 1)[0]
    done = re.search(r'font-medium">(\d+)<span[^>]*>/1</span> done', group)
    assert done and int(done[1]) == int(terminal)
    assert (">done</span>" in group) is terminal
    assert (">aborted</span>" in group) is not terminal


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires explicit isolated Docker qualification")
@pytest.mark.parametrize("goal,turn,expected_status", [
    ("complete", "completed", "terminal"),
    ("blocked", "completed", "terminal"),
    ("blocked", "failed", "runner_failed"),
])
def test_docker_collection_preserves_real_worker_outcome(tmp_path, goal, turn, expected_status):
    from benchmark.edgebench.run import _result_status, _write_native_final_result
    from benchmark.runtime.sforge_backend import RecordingDockerBackend
    from benchmark.runtime.harbor import NATIVE_GOAL_SOURCE_PATHS
    from sforge.harness.run_agent import RunResult

    # The production worker really spawns a stdio app-server; only inference is
    # replaced. No real task, credential, network service or leaderboard is used.
    native_worker(tmp_path, goal, turn)
    binary = tmp_path / "codex"
    binary.write_text(binary.read_text().replace(f"#!{sys.executable}", "#!/usr/local/bin/python3", 1))
    logger = logging.getLogger("native-outcome-smoke")
    backend = RecordingDockerBackend(log_dir=tmp_path / "collected", logger=logger, oauth_proxy=True)
    handle = backend.create_container("python:3.12-slim", "native-outcome-" + uuid.uuid4().hex[:10],
                                      cpu_limit=1, mem_limit="256m")
    backend.start_container(handle)
    cleaned = False
    try:
        root = Path(__file__).resolve().parents[2]
        sources = []
        for name in NATIVE_GOAL_SOURCE_PATHS:
            source = root / name
            if source.is_dir():
                sources.extend(str(path.relative_to(root)) for path in source.rglob("*.py"))
            else:
                sources.append(name)
        for name in sources:
            target = PurePosixPath("/tmp/source") / name
            backend.exec_run(handle, ["mkdir", "-p", str(target.parent)], user="root")
            backend.copy_to_container(handle, root / name, target)
        backend.copy_to_container(handle, binary, PurePosixPath("/tmp/codex"))
        backend.copy_to_container(handle, tmp_path / "task.md", PurePosixPath("/tmp/task.md"))
        env = {"LOOPX_EXECUTION_MODE": "native-goal", "LOOPX_PROJECT": "/tmp",
               "LOOPX_TASK_DOC": "/tmp/task.md", "LOOPX_CODEX_HOME": "/tmp/home",
               "LOOPX_WAKE_LOG_DIR": "/logs/agent/wakes", "CODEX_BIN": "/tmp/codex",
               "MODEL_NAME": "fixture", "REASONING_EFFORT": "high", "PYTHONPATH": "/tmp/source",
               "OPENAI_BASE_URL": "http://localhost:8123/v1", "OPENAI_API_KEY": "fixture-key"}
        backend.execution_command = "python3 -m benchmark.runtime.worker"
        observed = backend.exec_run_with_timeout(handle, ["/bin/bash", "-c", backend.execution_command],
                                                environment=env, timeout=30, user="root")
        assert '"native_goal_outcome"' in observed.output, observed.output
        backend.cleanup_container(handle)
        cleaned = True
        native = backend.native_goal_receipt
        assert native and native["native_goal"]["turn_status"] == turn
        status = _result_status(interrupted=False, started=True, runtime_seconds=observed.elapsed_seconds,
                                exit_code=backend.execution_receipt["exit_code"],
                                native_goal_outcome=native["native_goal_outcome"])
        assert status == expected_status
        _write_native_final_result(tmp_path, RunResult(runtime_seconds=observed.elapsed_seconds, best_score=7),
                                  status=status, agent="fixture", task="fixture", run_id="fixture",
                                  model="fixture", effort="high", native_goal_outcome=native["native_goal_outcome"])
        artifact = tmp_path / ("final_result.json" if expected_status == "terminal" else "partial_result.json")
        result = json.loads(artifact.read_text())
        assert result["best_score"] == 7 and result["runtime_status"] == expected_status
        assert (tmp_path / "final_result.json").exists() is (expected_status == "terminal")
    finally:
        if not cleaned:
            backend.cleanup_container(handle)
        backend.client.close()
