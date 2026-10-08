"""Online admission and coalescing invariants, independent of task scores."""
import hashlib
import json
import logging
from types import SimpleNamespace

import pytest
pytest.importorskip("sforge")
from benchmark.edgebench.online_sampling import CaptureStatus, OnlineSampler
from benchmark.edgebench.online_judge import resource_preflight


def sampler(tmp_path):
    result = OnlineSampler(trial=tmp_path, task=None, interval=3600,
        judge_url="http://localhost", secret="synthetic", logger=logging.getLogger("test"))
    result.epoch, result.token = "epoch", "token"
    return result


class Judge:
    def __init__(self):
        self.requests = {}
        self.lose_response = False

    def post(self, url, *, data, files, **kwargs):
        content = files["archive"][1].read()
        key = data["capture_id"]
        receipt = self.requests.setdefault(key, dict(epoch="epoch", capture_id=key,
            source_sha256=hashlib.sha256(content).hexdigest(),
            submission_id=f"id{len(self.requests)+1}", round_id=f"auto-{len(self.requests)+1}"))
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("Accepted; response lost")
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: receipt)

    def close(self):
        pass


def test_slow_evaluation_keeps_all_archives_but_only_latest_pending(tmp_path):
    queue = sampler(tmp_path)
    queue.session = Judge()
    queue.capture(b"first")
    queue.tick({"entries": []})
    for n in range(20):
        queue.capture(str(n).encode())
        queue.tick({"entries": []})
    assert len(queue.session.requests) == 1
    assert queue.pending["capture_id"] == "capture-21"
    assert queue.inflight["capture_id"] == "capture-1"
    assert len(list(queue.directory.glob("*.tar.gz"))) == 21
    assert sum(r["status"] == CaptureStatus.SUPERSEDED for r in queue.records) == 19
    queue.tick({"entries": [{"submission_id": "id1", "status": "completed"}]})
    assert set(queue.session.requests) == {"capture-1", "capture-21"}
    assert queue.pending is None
    assert set(queue.admitted()) == {"id1", "id2"}
    assert json.loads((queue.directory / "index.json").read_text())["captures"][0]["result_observed_at"]


def test_ambiguous_submission_retries_same_identity_then_failure_releases_lane(tmp_path):
    queue = sampler(tmp_path)
    queue.session = Judge()
    queue.session.lose_response = True
    queue.capture(b"first")
    with pytest.raises(TimeoutError):
        queue.tick({"entries": []})
    queue.capture(b"new")
    queue.tick({"entries": []})
    assert queue.inflight["capture_id"] == "capture-1"
    assert len(queue.session.requests) == 1
    queue.tick({"entries": [{"submission_id": "id1", "status": "error"}]})
    assert queue.inflight["capture_id"] == "capture-2"
    assert queue.records[0]["status"] == CaptureStatus.ERROR


def test_epoch_failure_preserves_archives_and_final_never_enters_online_lane(tmp_path):
    queue = sampler(tmp_path)
    queue.session = SimpleNamespace(post=lambda *a, **k: SimpleNamespace(status_code=409), close=lambda: None)
    queue.capture(b"first")
    with pytest.raises(RuntimeError, match="reconcile"):
        queue.tick({"entries": []})
    (tmp_path / "final_archive.tar.gz").write_bytes(b"final")
    queue.close()
    queue.close()
    assert queue.stop.is_set()
    assert len(queue.records) == 2
    assert queue.records[-1]["status"] == CaptureStatus.FINAL
    assert (queue.directory / "capture-2.tar.gz").read_bytes() == b"final"


def test_capacity_accounts_for_both_solver_and_judge_and_headroom():
    client = SimpleNamespace(info=lambda: dict(NCPU=18, MemTotal=52 << 30),
                             containers=SimpleNamespace(list=lambda: []))
    assert resource_preflight(client, 2)["reserved_cpu"] == 16
    with pytest.raises(ValueError, match="Insufficient"):
        resource_preflight(client, 3)
    client.containers.list = lambda: [SimpleNamespace(attrs={"HostConfig": {}})]
    with pytest.raises(ValueError, match="unbounded"):
        resource_preflight(client, 1)


def test_explicit_shared_pool_retains_memory_and_bounded_container_gates(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "read_text", lambda self: "MemAvailable: 33554432 kB\n")
    container = SimpleNamespace(attrs={"HostConfig": {
        "NanoCpus": 4000000000, "Memory": 16 << 30}})
    client = SimpleNamespace(info=lambda: dict(NCPU=14, MemTotal=45 << 30),
                             containers=SimpleNamespace(list=lambda: [container] * 4))
    with pytest.raises(ValueError, match="Insufficient"):
        resource_preflight(client, 2)
    result = resource_preflight(client, 2, allow_resource_overcommit=True)
    assert not result["exclusive_resources"]
    assert result["reserved_cpu"] == result["reserved_memory"] == 0
    assert result["operator_resource_monitor_required"]
    assert result["startup_memory_floor"] == 28 << 30
    monkeypatch.setattr(Path, "read_text", lambda self: "MemAvailable: 8388608 kB\n")
    with pytest.raises(ValueError, match="available memory"):
        resource_preflight(client, 2, allow_resource_overcommit=True)
    client.containers.list = lambda: [SimpleNamespace(attrs={"HostConfig": {}})]
    with pytest.raises(ValueError, match="unbounded"):
        resource_preflight(client, 1, allow_resource_overcommit=True)


def test_offline_high_score_and_foreign_submission_do_not_enter_incumbent(tmp_path):
    from benchmark.edgebench.feedback import BestOnlyFeedback
    queue = sampler(tmp_path)
    queue.session = Judge()
    queue.capture(b"source")
    queue.tick({"entries": []})
    publisher = BestOnlyFeedback(trial=tmp_path, run_id="run", task_id="fixture", direction="maximize",
        judge_url="http://localhost", admin_secret="synthetic", logger=logging.getLogger("test"), sampler=queue)
    def row(identifier, score, round_id):
        return dict(type="submission", status="completed", valid=True, task_id="fixture",
                    submission_id=identifier, score=score, round=round_id)
    assert publisher._candidate(dict(run_id="run", entries=[row("offline", 999, "auto-9")])) is None
    assert publisher.score is None
    assert publisher._candidate(dict(run_id="run", entries=[row("id1", 1, "auto-1"), row("offline", 999, "auto-9")])) is None
    assert publisher.score == 1
    assert publisher._candidate(dict(run_id="run", entries=[row("id1", 999, "auto-9")])) is None
    assert publisher.score == 1


def test_offline_scoring_retains_failed_points_and_is_restartable(tmp_path, monkeypatch):
    from benchmark.edgebench import offline_scoring
    queue = sampler(tmp_path)
    queue.capture(b"one")
    queue.capture(b"bad")
    queue.capture(b"final", final=True)
    (tmp_path / "runtime-receipt.json").write_text(json.dumps(dict(
        status="terminal", feedback="best-only", task="fixture", run_id="run", task_sha256="source-pin")))
    task = SimpleNamespace(task_id="fixture", judge=SimpleNamespace(score_direction="maximize", selection="score_first"))
    calls = []
    def grade(**kwargs):
        calls.append(kwargs["archive"])
        if kwargs["archive"] == b"bad":
            raise RuntimeError("Synthetic grader failure")
        return SimpleNamespace(to_dict=lambda: {"score": len(kwargs["archive"]), "valid": True})
    monkeypatch.setattr(offline_scoring, "judge_submission", grade)
    result = offline_scoring.score_captures(tmp_path, task, None, None)
    assert result["best_score"] == 5 and result["failed_captures"] == 1
    assert result["offline_scoring_complete"] is False
    assert len(result["entries"]) == 3
    assert offline_scoring.score_captures(tmp_path, task, None, None) == result
    assert len(calls) == 3  # Repeat cannot silently retry/rewrite a failure.
    (queue.directory / "capture-1.tar.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        offline_scoring.score_captures(tmp_path, task, None, None)


def test_offline_needs_terminal_solver_final_capture_and_exclusive_capacity(tmp_path, monkeypatch):
    from benchmark.edgebench.offline_scoring import score_captures
    from benchmark.edgebench.online_judge import cohort_lock
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    with cohort_lock():
        with pytest.raises(BlockingIOError):
            with cohort_lock():
                pytest.fail("Two cohorts acquired one pool")
    queue = sampler(tmp_path)
    queue.capture(b"one")
    task = SimpleNamespace(task_id="fixture")
    path = tmp_path / "runtime-receipt.json"
    value = dict(status="starting", feedback="best-only", task="fixture")
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="terminal"):
        score_captures(tmp_path, task, None, None)
    value["status"] = "terminal"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Final capture"):
        score_captures(tmp_path, task, None, None)


def test_solver_completion_pauses_feedback_without_breaking_official_resume(monkeypatch):
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    calls = []
    backend = object.__new__(RecordingDockerBackend)
    backend.blind_api_endpoint = None
    backend.feedback_command = "solver invocation"
    backend.feedback = SimpleNamespace(pause=lambda: calls.append("pause"))
    monkeypatch.setattr(DockerBackend, "exec_run_with_timeout", lambda *a, **k: "result")
    assert backend.exec_run_with_timeout(None, ["/bin/bash", "-c", "setup"]) == "result"
    assert calls == []
    assert backend.exec_run_with_timeout(None, ["/bin/bash", "-c", "solver invocation"]) == "result"
    assert calls == ["pause"]
