"""The native provider and report share one duration classification owner."""

import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
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
