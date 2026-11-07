"""Online admission and coalescing invariants, independent of task scores."""
import hashlib
import json
import logging
import math
from types import SimpleNamespace

import pytest
pytest.importorskip("sforge")
from benchmark.edgebench.online_sampling import CaptureStatus, OnlineSampler
from benchmark.edgebench.online_judge import resource_preflight


@pytest.mark.parametrize("fault", [None, "missing", "task_spec_sha256", "judge_image_key",
                                  "selection", "score_direction", "native_source_sha256"])
def test_evaluator_configuration_is_qualified_before_solver_admission(tmp_path, fault):
    from benchmark.edgebench.online_judge import POLICY
    task = SimpleNamespace(task_id="fixture", judge_image_key="fixture-judge",
        judge=SimpleNamespace(selection="score_first", score_direction="maximize"))
    queue = OnlineSampler(trial=tmp_path, task=task, interval=300, judge_url="http://localhost",
        secret="synthetic", logger=logging.getLogger("test"), task_sha256="c" * 64)
    evaluator = dict(native_source_sha256="b" * 64, task_spec_sha256="c" * 64,
        judge_image_key="fixture-judge", selection="score_first", score_direction="maximize")
    if fault not in (None, "missing"):
        evaluator[fault] = "different"
    admission = dict(policy=POLICY, epoch="epoch", max_running_per_run=1,
                     evaluators={} if fault == "missing" else {"fixture": evaluator})
    queue.session.get = lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: admission)
    if fault:
        with pytest.raises(ValueError, match="task configuration"):
            queue.qualify()
        assert not (queue.directory / "admission.json").exists()
    else:
        queue.qualify()
        assert queue.evaluators == admission["evaluators"]
        assert json.loads((queue.directory / "admission.json").read_text()) == admission


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


def test_explicit_shared_floor_admits_large_cohort_without_claiming_reserved_compute(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "read_text", lambda self: "MemAvailable: 33554432 kB\n")
    client = SimpleNamespace(info=lambda: dict(NCPU=14, MemTotal=45 << 30),
                             containers=SimpleNamespace(list=lambda: []))
    with pytest.raises(ValueError, match="available memory"):
        resource_preflight(client, 12, allow_resource_overcommit=True)
    result = resource_preflight(client, 12, allow_resource_overcommit=True,
                                shared_startup_memory_gib=16)
    assert result["slots"] == 12
    assert result["startup_memory_floor"] == 16 << 30
    assert result["reserved_cpu"] == result["reserved_memory"] == 0
    assert result["operator_resource_monitor_required"]
    with pytest.raises(ValueError, match="explicit resource overcommit"):
        resource_preflight(client, 12, shared_startup_memory_gib=16)
    for invalid in (True, 0, 15, 16.5):
        with pytest.raises(ValueError, match="at least"):
            resource_preflight(client, 12, allow_resource_overcommit=True,
                               shared_startup_memory_gib=invalid)
    monkeypatch.setattr(Path, "read_text", lambda self: "MemAvailable: 15728640 kB\n")
    with pytest.raises(ValueError, match="available memory"):
        resource_preflight(client, 12, allow_resource_overcommit=True,
                           shared_startup_memory_gib=16)
    client.containers.list = lambda: [SimpleNamespace(attrs={"HostConfig": {}})]
    with pytest.raises(ValueError, match="unbounded"):
        resource_preflight(client, 12, allow_resource_overcommit=True,
                           shared_startup_memory_gib=16)


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


@pytest.mark.parametrize("score,max_round,min_round", [
    (-2.5, "capture-3", "capture-2"), (float("inf"), "capture-2", "capture-1"),
    (float("-inf"), "capture-3", "capture-2"), (float("nan"), "capture-3", "capture-1"),
])
@pytest.mark.parametrize("direction", ["maximize", "minimize"])
@pytest.mark.parametrize("policy", ["score_first", "pass_rate_first", "valid_then_score"])
def test_offline_nonfinite_reports_preserve_native_selection_on_restart(
        tmp_path, monkeypatch, score, max_round, min_round, direction, policy):
    from sforge.harness.grading import EvalReport
    from benchmark.edgebench import offline_scoring
    from benchmark.runtime import sforge_json

    queue = sampler(tmp_path)
    for archive in (b"first", b"nonfinite", b"final"):
        queue.capture(archive, final=archive == b"final")
    receipt = tmp_path / "runtime-receipt.json"
    receipt.write_text(json.dumps(dict(status="terminal", feedback="best-only", task="fixture",
                                       run_id="run", task_sha256="source-pin")))
    native_before = receipt.read_bytes(), (queue.directory / "index.json").read_bytes()
    task = SimpleNamespace(task_id="fixture", judge=SimpleNamespace(score_direction=direction, selection=policy))
    calls = []
    literal = {"sforge_nonfinite_json": 1, "value": {"score": None},
               "nonfinite_values": [{"path": ["score"], "kind": "nan"}]}

    def grade(**kwargs):
        calls.append(kwargs["archive"])
        return EvalReport(task_id="fixture", submission_id=kwargs["submission_id"], pass_rate=1,
            score={b"first": 1.0, b"nonfinite": score, b"final": 2.0}[kwargs["archive"]],
            metrics={"measured": score, "literal": literal, "missing": None})

    monkeypatch.setattr(offline_scoring, "judge_submission", grade)
    for _ in range(2):
        result = offline_scoring.score_captures(tmp_path, task, None, None)
        assert result["offline_scoring_complete"] and result["failed_captures"] == 0
        assert result["best_round"] == (max_round if direction == "maximize" else min_round)
        for entry in result["entries"]:
            measured = entry["metrics"]["measured"]
            assert math.isnan(measured) if math.isnan(score) else measured == score
            assert entry["metrics"]["literal"] == literal and entry["metrics"]["missing"] is None
        restored = sforge_json.loads((tmp_path / "offline-scoring/result.json").read_text())
        assert restored["best_round"] == result["best_round"]
        if math.isfinite(score):
            assert (tmp_path / "offline-scoring/result.json").read_text() == json.dumps(result, allow_nan=False)
    assert calls == [b"first", b"nonfinite", b"final"]  # Cached scores are decoded before native ranking.
    assert (receipt.read_bytes(), (queue.directory / "index.json").read_bytes()) == native_before


def test_offline_nan_incumbent_and_malformed_cached_transport_are_not_regraded(tmp_path, monkeypatch):
    from sforge.harness.grading import EvalReport
    from benchmark.edgebench import offline_scoring

    queue = sampler(tmp_path)
    queue.capture(b"first")
    queue.capture(b"final", final=True)
    (tmp_path / "runtime-receipt.json").write_text(json.dumps(dict(
        status="terminal", feedback="best-only", task="fixture", run_id="run", task_sha256="source-pin")))
    task = SimpleNamespace(task_id="fixture", judge=SimpleNamespace(score_direction="maximize", selection="score_first"))
    calls = []

    def grade(**kwargs):
        calls.append(kwargs["archive"])
        return EvalReport(task_id="fixture", submission_id=kwargs["submission_id"], pass_rate=1,
                          score=float("nan") if kwargs["archive"] == b"first" else 10.0)

    monkeypatch.setattr(offline_scoring, "judge_submission", grade)
    for _ in range(2):
        result = offline_scoring.score_captures(tmp_path, task, None, None)
        # Preserve native first-entry comparison semantics, including NaN.
        assert result["best_round"] == "capture-1" and math.isnan(result["best_score"])
    prior = (tmp_path / "offline-scoring/result.json").read_bytes()
    path = tmp_path / "offline-scoring/capture-1/score.json"
    wire = json.loads(path.read_text())
    wire["nonfinite_values"].append(wire["nonfinite_values"][0])
    path.write_text(json.dumps(wire))
    with pytest.raises(ValueError, match="Duplicate"):
        offline_scoring.score_captures(tmp_path, task, None, None)
    assert calls == [b"first", b"final"]
    assert (tmp_path / "offline-scoring/result.json").read_bytes() == prior


def test_offline_cli_summary_uses_the_same_lossless_transport(tmp_path, monkeypatch, capsys):
    from contextlib import nullcontext
    import sys
    import docker
    from sforge.harness.config import SForgeConfig
    from sforge.harness.grading import EvalReport
    from benchmark.edgebench import offline_scoring
    from benchmark.runtime import sforge_json

    sampler(tmp_path).capture(b"final", final=True)
    task_file = tmp_path / "fixture.json"
    task_file.write_text("{}")
    (tmp_path / "runtime-receipt.json").write_text(json.dumps(dict(status="terminal", feedback="best-only",
        task="fixture", run_id="run", task_sha256=hashlib.sha256(task_file.read_bytes()).hexdigest())))
    task = SimpleNamespace(task_id="fixture", judge=SimpleNamespace(score_direction="maximize", selection="score_first"))
    monkeypatch.setattr(sys, "argv", ["offline_scoring", "--trial", str(tmp_path), "--tasks-dir", str(tmp_path)])
    monkeypatch.setattr(offline_scoring, "load_benchmark", lambda path: None)
    monkeypatch.setattr(offline_scoring, "make_task_spec", lambda *args: task)
    monkeypatch.setattr(offline_scoring, "load_config", lambda: SForgeConfig())
    monkeypatch.setattr(offline_scoring, "cohort_lock", nullcontext)
    monkeypatch.setattr(docker, "from_env", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(offline_scoring, "resource_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(offline_scoring, "create_backend_from_config", lambda config: None)
    monkeypatch.setattr(offline_scoring, "judge_submission", lambda **kwargs: EvalReport(
        task_id="fixture", submission_id=kwargs["submission_id"], score=float("inf"), pass_rate=1))
    assert offline_scoring.main() == 0
    summary = sforge_json.loads(capsys.readouterr().out)
    assert summary["best_score"] == float("inf") and summary["best_round"] == "capture-1"
    assert summary["offline_scoring_complete"] and "entries" not in summary


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


def test_solver_completion_pauses_feedback_without_breaking_official_resume(tmp_path, monkeypatch):
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    calls = []
    backend = RecordingDockerBackend(log_dir=tmp_path / "collected", logger=None,
                                    oauth_proxy=True, client=SimpleNamespace(),
                                    feedback=SimpleNamespace(pause=lambda: calls.append("pause")))
    backend.execution_command = "solver invocation"
    result = SimpleNamespace(exit_code=0, timed_out=False, elapsed_seconds=10, output="result")
    monkeypatch.setattr(DockerBackend, "exec_run_with_timeout", lambda *a, **k: result)
    assert backend.exec_run_with_timeout(None, ["/bin/bash", "-c", "setup"]) is result
    assert calls == []
    assert backend.exec_run_with_timeout(None, ["/bin/bash", "-c", "solver invocation"]) is result
    assert calls == ["pause"]


@pytest.mark.parametrize("worker_present", [False, True])
def test_cleanup_returns_registration_only_after_verified_worker_removal(tmp_path, monkeypatch, worker_present):
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    calls = []
    feedback = SimpleNamespace(run_id="run", task_id="fixture",
        close=lambda: calls.append("capture-stopped"),
        release_registration=lambda: calls.append("registration-release"))
    backend = RecordingDockerBackend(log_dir=tmp_path / "collected", logger=None,
                                    oauth_proxy=True, client=SimpleNamespace(), feedback=feedback)
    monkeypatch.setattr(DockerBackend, "cleanup_container", lambda *a, **k: calls.append("native-cleanup"))
    def exists(name):
        assert name == "sforge.run.fixture.run"
        calls.append("absence-readback")
        return worker_present
    backend.container_exists = exists
    if worker_present:
        with pytest.raises(RuntimeError, match="not released"):
            backend.cleanup_container(None)
    else:
        backend.cleanup_container(None)  # Registration may precede container creation.
    assert calls == ["capture-stopped", "native-cleanup", "absence-readback"] + (
        [] if worker_present else ["registration-release"])


def test_release_lost_response_retries_exact_identity_without_credential_receipt(tmp_path, monkeypatch):
    queue = sampler(tmp_path)
    queue.close()
    calls = []
    class ReleaseSession:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, data, **kwargs):
            calls.append((url, dict(data)))
            if len(calls) == 1:
                raise TimeoutError("Release accepted but acknowledgement lost")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                "epoch": "epoch", "run_id": "run", "task_id": "fixture", "state": "released"})
    monkeypatch.setattr("benchmark.edgebench.online_sampling.requests.Session", ReleaseSession)
    with pytest.raises(TimeoutError):
        queue.release_registration(run_id="run", task_id="fixture")
    assert not (queue.directory / "release.json").exists()
    queue.release_registration(run_id="run", task_id="fixture")
    assert calls[0] == calls[1]
    receipt = json.loads((queue.directory / "release.json").read_text())
    assert set(receipt) == {"epoch", "run_id", "task_id", "state"}
    assert "synthetic" not in json.dumps(receipt)


@pytest.mark.parametrize("field,value", [("epoch", "new-epoch"), ("run_id", "another"),
    ("task_id", "another"), ("state", "active")])
def test_release_cannot_acknowledge_changed_epoch_or_registration(tmp_path, monkeypatch, field, value):
    queue = sampler(tmp_path)
    receipt = dict(epoch="epoch", run_id="run", task_id="fixture", state="released")
    receipt[field] = value
    class ReleaseSession:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: receipt)
    monkeypatch.setattr("benchmark.edgebench.online_sampling.requests.Session", ReleaseSession)
    with pytest.raises(RuntimeError, match="Stop online sampling"):
        queue.release_registration(run_id="run", task_id="fixture")
    queue.close()
    with pytest.raises(ValueError, match="original registration"):
        queue.release_registration(run_id="run", task_id="fixture")
    assert not (queue.directory / "release.json").exists()
