"""The native provider and report share one duration classification owner."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from benchmark.edgebench.execution_commitment import (
    NativeExecutionEvidence,
    install_execution_commitment,
)


def execution(path, segments, status="normal_exit"):
    path.write_text(
        json.dumps(dict(schema_version=1, status=status, segments=segments))
    )


def segment(start, end=None):
    return dict(
        started_at=start,
        **({"finished_at": end} if end is not None else {}),
        status="normal_exit" if end is not None else "pending",
    )


def test_gaps_are_excluded_and_restore_reuses_pinned_native_intervals(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "benchmark.edgebench.execution_commitment.time.time", lambda: 210
    )
    old = tmp_path / "previous.json"
    execution(old, [segment(100, 104)])
    current = tmp_path / "solver_execution.json"
    execution(current, [segment(200, 204)])
    owner = NativeExecutionEvidence(
        run_id="run",
        task_id="task",
        trial=tmp_path,
        target_seconds=10,
        runner_active=lambda: False,
        previous=((old, hashlib.sha256(old.read_bytes()).hexdigest()),),
    )
    assert owner.fulfilled() is False  # The 96-second outage is not useful execution.
    execution(current, [segment(200, 206)])
    assert owner.fulfilled() is True
    execution(old, [segment(100, 110)])
    assert owner.fulfilled() is None  # Retained evidence cannot silently change.


def test_pending_native_interval_requires_exact_runner_liveness(tmp_path, monkeypatch):
    now = [109]
    monkeypatch.setattr(
        "benchmark.edgebench.execution_commitment.time.time", lambda: now[0]
    )
    execution(tmp_path / "solver_execution.json", [segment(100)], status="pending")
    live = [True]
    owner = NativeExecutionEvidence(
        run_id="run",
        task_id="task",
        trial=tmp_path,
        target_seconds=10,
        runner_active=lambda: live[0],
    )
    assert owner.fulfilled() is False
    now[0] = 110
    assert owner.fulfilled() is True
    live[0] = False
    assert owner.fulfilled() is None
    now[0] = 10000
    assert owner.fulfilled() is None


def test_unknown_missing_or_symlinked_solver_evidence_never_attests(tmp_path):
    owner = NativeExecutionEvidence(
        run_id="run",
        task_id="task",
        trial=tmp_path,
        target_seconds=10,
        runner_active=lambda: True,
    )
    assert owner.fulfilled() is None
    path = tmp_path / "solver_execution.json"
    execution(path, [segment(100, 200)], status="unknown")
    assert owner.fulfilled() is None
    path.unlink()
    other = tmp_path / "other.json"
    execution(other, [segment(100, 200)])
    path.symlink_to(other)
    assert owner.fulfilled() is None


@pytest.mark.parametrize("fraction", [0.0001, 0.0006, 0.0016, 0.9996])
def test_fractional_native_clock_does_not_create_a_future_tail(
    tmp_path, monkeypatch, fraction
):
    monkeypatch.setattr(
        "benchmark.edgebench.execution_commitment.time.time", lambda: 110 + fraction
    )
    execution(tmp_path / "solver_execution.json", [segment(100)], status="pending")
    owner = NativeExecutionEvidence(
        run_id="run",
        task_id="task",
        trial=tmp_path,
        target_seconds=10,
        runner_active=lambda: True,
    )
    assert owner.fulfilled() is True


def test_short_native_segment_is_known_zero_credit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "benchmark.edgebench.execution_commitment.time.time", lambda: 110
    )
    execution(tmp_path / "solver_execution.json", [segment(100.0001, 100.0002)])
    owner = NativeExecutionEvidence(
        run_id="run",
        task_id="task",
        trial=tmp_path,
        target_seconds=10,
        runner_active=lambda: False,
    )
    assert owner.fulfilled() is False


def test_read_only_route_is_absent_by_default_and_scoped_to_original_relay():
    FastAPI = pytest.importorskip("fastapi").FastAPI
    pytest.importorskip("httpx")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    app = FastAPI()
    with TestClient(app) as client:
        assert (
            client.get("/api/v1/feedback/own/execution-commitment").status_code == 404
        )
    calls = []

    def resolve(relay):
        if relay == "missing":
            raise KeyError(relay)
        return "private-token", dict(
            run_id="run" if relay == "own" else "other", task_id="task"
        )

    app.state.judge = SimpleNamespace(resolve_feedback_relay=resolve)
    install_execution_commitment(
        app, run_id="run", task_id="task", observe=lambda: calls.append(True) or False
    )
    with TestClient(app) as client:
        for relay in ["missing", "foreign"]:
            assert (
                client.get(f"/api/v1/feedback/{relay}/execution-commitment").status_code
                == 404
            )
        assert calls == []
        response = client.get("/api/v1/feedback/own/execution-commitment")
        assert response.status_code == 200 and response.json()["fulfilled"] is False
        assert "private-token" not in response.text
        assert (
            client.post("/api/v1/feedback/own/execution-commitment").status_code == 405
        )
        assert calls == [True]


@pytest.mark.parametrize("order", [("a", "b", "c"), ("c", "b", "a")])
def test_multiple_native_trials_keep_independent_routes_and_public_probes(order):
    """A later valid registration must not be captured by the first route."""
    import socket
    import subprocess
    import sys
    import threading
    import time
    from urllib.error import HTTPError
    from urllib.request import urlopen

    FastAPI = pytest.importorskip("fastapi").FastAPI
    uvicorn = pytest.importorskip("uvicorn")
    app = FastAPI()
    # A/B share a task; B/C share a run. Neither field alone identifies a trial.
    identities = {"a": ("run-a", "task-a"), "b": ("run-b", "task-a"),
                  "c": ("run-b", "task-c"), "uninstalled": ("run-x", "task-x")}
    states = {"a": False, "b": True, "c": None}
    calls = []

    def resolve(relay):
        run, task = identities[relay]
        return "private-token", dict(run_id=run, task_id=task)

    app.state.judge = SimpleNamespace(resolve_feedback_relay=resolve)
    observers = {name: (lambda name=name: calls.append(name) or states[name])
                 for name in order}
    for name in (*order, *order):
        run, task = identities[name]
        install_execution_commitment(app, run_id=run, task_id=task,
                                     observe=observers[name])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        root = f"http://127.0.0.1:{sock.getsockname()[1]}/api/v1/feedback"
        for name in order:
            response = urlopen(f"{root}/{name}/execution-commitment", timeout=5)
            packet = json.load(response)
            assert packet["task_id"] == identities[name][1]
            assert packet["fulfilled"] is states[name]
            assert "private-token" not in json.dumps(packet)
            for action in ("probe", "check"):
                result = subprocess.run(
                    [sys.executable, "-m", "benchmark.runtime.execution_commitment", action,
                     "--task-id", identities[name][1], "--feedback-url", f"{root}/{name}"],
                    capture_output=True, text=True, timeout=5)
                accepted = states[name] is not None if action == "probe" else states[name] is True
                assert result.returncode == (0 if accepted else 1), result.stdout
        previous_calls = list(calls)
        for name in ("missing", "uninstalled"):
            with pytest.raises(HTTPError) as error:
                urlopen(f"{root}/{name}/execution-commitment", timeout=5)
            assert error.value.code == 404
        assert calls == previous_calls
        assert sorted(calls) == sorted(list(order) * 3)
        with pytest.raises(ValueError, match="already installed"):
            install_execution_commitment(app, run_id="run-b", task_id="task-a",
                                         observe=lambda: False)
        routes = [r for r in app.routes if r.path.endswith("/execution-commitment")]
        assert len(routes) == 1
        # One app cannot inherit another app's registrations.
        isolated = FastAPI()
        isolated.state.judge = app.state.judge
        TestClient = pytest.importorskip("fastapi.testclient").TestClient
        with TestClient(isolated) as client:
            assert client.get("/api/v1/feedback/a/execution-commitment").status_code == 404
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
