"""Exercise the EdgeBench entry and its controller handoff without model calls."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from benchmark.edgebench import managed_run
from benchmark.edgebench.managed_run import BINDING_FILENAME, RUNTIME_FILENAME, TERMINAL_FILENAME
from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    build_benchmark_concurrency_config,
    configure_benchmark_concurrency_envelope,
    default_benchmark_concurrency_envelope_path,
    default_benchmark_experiment_board_path,
    read_benchmark_concurrency_envelope,
    read_benchmark_experiment_board_rows,
    upsert_benchmark_experiment_board_row,
)
from tests.source_cli import run_source_cli


GOAL = "fixture-goal"
IDENTITY = {
    "benchmark_id": "edgebench-fixture", "study_id": "study", "case_id": "task",
    "run_id": "attempt", "arm_id": "official", "arm_role": "baseline", "attempt": 2,
}


def _binding_file(tmp_path: Path, admission: dict, *, identity: dict | None = None) -> Path:
    path = tmp_path / "private-binding.json"
    path.write_text(json.dumps({
        "goal_id": GOAL, "project": str(tmp_path / "controller-project"),
        "admission_receipt": admission, "identity": IDENTITY if identity is None else identity,
    }), encoding="utf-8")
    path.chmod(0o600)
    return path


def _admission() -> dict:
    return {
        "ok": True, "admitted": True, "dry_run": False, "goal_id": GOAL,
        "idempotent": False, "write_performed": True,
        "run": {key: IDENTITY[key] for key in ("run_id", "case_id", "arm_role")}
        | {"admitted_at": datetime.now(UTC).isoformat()},
    }


@pytest.fixture
def entry(tmp_path, monkeypatch):
    native = pytest.importorskip("sforge.harness.run_agent")
    pytest.importorskip("harbor")
    if not all(hasattr(native, name) for name in ("_write_grading", "_write_solver_execution", "_wait_for_grading")):
        pytest.skip("Selected EdgeBench SDK predates native terminal evidence")
    from benchmark.edgebench import run

    for name, value in {"LOOPX_SRC_DIR": str(tmp_path), "LOOPX_EXPECTED_COMMIT": "fixture",
                        "CODEX_AUTH_JSON_PATH": str(tmp_path / "unused-auth.json")}.items():
        monkeypatch.setenv(name, value)
    for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run, "source_pins", lambda *args: ("fixture", "fixture"))
    monkeypatch.setattr(run, "active_source_receipt", lambda: {"commit": "7" * 40})
    monkeypatch.setattr(run, "load_benchmark", lambda *args: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *args: SimpleNamespace(
        task_id="task", cwd="/task", work_image_key="work", judge_image_key="judge",
        internet=False, game_mode=False, submit_paths=["solver.py"],
        work=SimpleNamespace(agent_query="Synthetic task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    backend = SimpleNamespace(observe_local_images=lambda **keys: {},
                              registration_options=lambda provider: {}, native_goal_receipt=None,
                              execution_receipt={"exit_code": 0, "execution_started": True})
    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **kwargs: backend)
    trial = tmp_path / "runs" / "attempt" / "task"
    argv = ["--task", "task", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "attempt", "--worker", "official", "--m", "fixture",
            "--effort", "high", "--judge-url", "http://localhost:9999"]

    def invoke(binding: Path | None = None, *, evidence: str = "drained", segment_count: int = 1) -> int:
        def finish(**kwargs):
            assert kwargs["run_id"] == "attempt" and kwargs["task_spec"].task_id == "task"
            assert "managed_run" not in kwargs and "admission_receipt" not in kwargs
            if evidence == "exception":
                raise RuntimeError("native provider failed")
            (trial / "started_at").write_text("2026-01-01T00:00:00Z\n", encoding="utf-8")
            segments = []
            for index in range(1, segment_count + 1):
                now = time.time()
                segments.append({
                    "index": index, "status": "normal_exit", "finished_at": time.time(),
                    "started_at": now - 100 if evidence == "predates_admission" else now,
                    "terminal_observed": evidence != "unconfirmed_solver", "native_exit_code": 0,
                })
            execution = {"schema_version": 1, "status": "normal_exit", "segments": segments}
            native._write_solver_execution(trial, execution)
            history = {"run_id": "attempt", "entries": []}
            if evidence == "pending":
                history["entries"].append({"type": "submission", "status": "running"})
                grading = native._GradingResult(history, "pending", "deadline", 1)
            elif evidence == "unknown_grading":
                grading = native._GradingResult(None, "unknown", "history_unavailable")
            else:
                grading = native._GradingResult(history, "complete", "drained", 0)
            if evidence != "missing_grading":
                native._write_grading(trial, "run_history.json", grading, "attempt")
            return native.RunResult(runtime_seconds=2, best_score=4, total_rounds=1,
                                    grading_status=grading.status, grading_reason=grading.reason,
                                    solver_execution=execution)

        monkeypatch.setattr(run, "run_agent", finish)
        return run.main(argv + (["--managed-run-json", str(binding)] if binding is not None else []))

    def drain(run_id: str = "attempt", *, contradictory_status: str | None = None) -> None:
        history = {"run_id": run_id, "entries": [{"type": "submission", "status": contradictory_status or "completed"}]}
        if contradictory_status is None:
            grading = native._wait_for_grading(
                "http://unused.invalid", "fixture-session", "fixture-secret", eval_timeout=1,
                shutdown_event=None, logger=logging.getLogger("fixture"), max_wait_seconds=1,
                history_reader=lambda *args, **kwargs: (history, 0),
            )
        else:
            grading = native._GradingResult(history, "complete", "drained", 0)
        assert grading.status == "complete" and grading.reason == "drained"
        native._write_grading(trial, "run_history.json", grading, "attempt")

    return trial, invoke, drain


def _replay(trial: Path, *, execute: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "benchmark.edgebench.managed_run", "--trial", str(trial)]
    return subprocess.run(command + (["--execute"] if execute else []),
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("stage", ["drained", "large_solver_history", "pending", "write_failure",
    "receipt_before_replace", "receipt_after_replace", "receipt_persistent"])
def test_managed_terminal_flows_from_runner_to_controller(tmp_path, entry, capsys, monkeypatch, stage):
    trial, invoke, drain = entry
    project = tmp_path / "controller-project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": GOAL, "objective": "Run a benchmark fixture", "repo": str(project)}],
    }), encoding="utf-8")
    board = default_benchmark_experiment_board_path(project=project, goal_id=GOAL)
    envelope = default_benchmark_concurrency_envelope_path(project=project, goal_id=GOAL)
    config = build_benchmark_concurrency_config(max_active_cases=1, max_baseline_cases=1, max_test_cases=0)
    configure_benchmark_concurrency_envelope(envelope, config, execute=True)
    env = {**os.environ, "LOOPX_PYTHON": sys.executable}

    def controller(command: str, *args: str) -> dict:
        result = run_source_cli([
            "--registry", str(registry), "benchmark", command, "--goal-id", GOAL,
            "--project", str(project), "--format", "json", "--execute", *args,
        ], env=env, timeout=30)
        return json.loads(result.stdout)

    admission = controller("concurrency-admit", "--run-id", "attempt", "--case-id", "task", "--arm-role", "baseline")
    running = upsert_benchmark_experiment_board_row(board, {
        **IDENTITY, "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
        "status": "running", "observed_at": admission["run"]["admitted_at"], "model_id": "fixture",
        "protocol_id": "native", "comparison_protocol_id": "native", "claim_scope": "inventory_only",
        "primary_metric": "score", "treatment_fidelity": "not_applicable",
        "countability": {"integrity_qualified": False, "official_result_present": False, "score_countable": False},
        "insight": {"status": "pending"},
    })["row"]

    terminal_receipt_attempts = []
    with monkeypatch.context() as patch:
        if stage == "write_failure":
            def fail_write(*args, **kwargs):
                raise OSError("fixture proof storage failure")

            patch.setattr(managed_run, "atomic_write_state_text", fail_write)
        if stage.startswith("receipt_"):
            write_json_atomic = managed_run.write_json_atomic

            def fail_terminal_receipt_once(path, payload, **kwargs):
                terminal_receipt = path.name == RUNTIME_FILENAME and "solver_execution_sha256" in payload.get("managed_run", {})
                if terminal_receipt:
                    terminal_receipt_attempts.append(payload)
                    if len(terminal_receipt_attempts) == 1 or stage == "receipt_persistent":
                        if stage == "receipt_after_replace":
                            write_json_atomic(path, payload, **kwargs)
                        raise OSError("fixture terminal receipt storage failure")
                return write_json_atomic(path, payload, **kwargs)

            patch.setattr(managed_run, "write_json_atomic", fail_terminal_receipt_once)
        segment_count = 5500 if stage in {"large_solver_history", "receipt_persistent"} else 1
        assert invoke(_binding_file(tmp_path, admission), evidence=stage, segment_count=segment_count) == 0
    if segment_count > 1:
        assert all((trial / name).stat().st_size > 1 << 20 for name in ("solver_execution.json", "final_result.json"))
    runtime = json.loads(capsys.readouterr().out)
    assert runtime["status"] == "terminal" and runtime["best_score"] == 4
    assert GOAL not in json.dumps(runtime) and str(project) not in json.dumps(runtime)
    proof = trial / TERMINAL_FILENAME
    if stage == "receipt_persistent":
        runtime_path = trial / RUNTIME_FILENAME
        original_runtime = runtime_path.read_bytes()
        retained = json.loads(original_runtime)
        assert retained["status"] == "starting" and retained["run_id"] == admission["run"]["run_id"]
        assert "solver_execution_sha256" not in retained["managed_run"]
        assert len(terminal_receipt_attempts) == 2 and not proof.exists()
        final_path = trial / "final_result.json"
        original_final = final_path.read_bytes()
        final = json.loads(original_final)
        assert final["solver_execution"] == json.loads((trial / "solver_execution.json").read_bytes())
        final_path.unlink()
        assert _replay(trial, execute=True).returncode == 2 and not proof.exists()
        for change in (
            {"task": "other-task"},
            {"runtime_status": "cancelled"},
            {"solver_execution": final["solver_execution"] | {"status": "nonzero_exit"}},
        ):
            final_path.write_text(json.dumps(final | change))
            assert _replay(trial, execute=True).returncode == 2 and not proof.exists()
        final_path.write_bytes(original_final)
        history = trial / "run_history.json"
        original_history = history.read_bytes()
        history.write_text(json.dumps(json.loads(original_history) | {"run_id": "different-run"}))
        assert _replay(trial, execute=True).returncode == 2 and not proof.exists()
        history.write_bytes(original_history)
        runtime_path.write_text(json.dumps(retained | {"runner_commit": "different-runner"}))
        assert _replay(trial, execute=True).returncode == 2 and not proof.exists()
        runtime_path.write_bytes(original_runtime)
        preview = _replay(trial)
        assert preview.returncode == 0 and json.loads(preview.stdout) == {"ready": True, "dry_run": True}
        assert runtime_path.read_bytes() == original_runtime and not proof.exists()
        assert _replay(trial, execute=True).returncode == 0
        assert runtime_path.read_bytes() == original_runtime
    elif stage.startswith("receipt_"):
        retained = json.loads((trial / RUNTIME_FILENAME).read_bytes())
        assert retained["managed_run"]["solver_execution_sha256"]
        assert retained["run_id"] == admission["run"]["run_id"] and retained["status"] == "terminal"
        assert len(terminal_receipt_attempts) == (2 if stage == "receipt_before_replace" else 1)
        assert all(attempt == retained for attempt in terminal_receipt_attempts)
    if stage in {"pending", "write_failure"}:
        assert not proof.exists()
        if stage == "pending":
            pending = _replay(trial, execute=True)
            assert pending.returncode == 2 and json.loads(pending.stdout)["ready"] is False
            drain(run_id="different-run")
            wrong_run = _replay(trial, execute=True)
            assert wrong_run.returncode == 2 and not proof.exists()
            drain(contradictory_status="running")
            contradictory = _replay(trial, execute=True)
            assert contradictory.returncode == 2 and not proof.exists()
            drain()
        runtime_file = trial / "runtime-receipt.json"
        original = runtime_file.read_bytes()
        changed = json.loads(original) | {"runner_commit": "another-runner"}
        runtime_file.write_text(json.dumps(changed), encoding="utf-8")
        assert _replay(trial, execute=True).returncode == 2 and not proof.exists()
        runtime_file.write_bytes(original)
        preview = _replay(trial)
        assert preview.returncode == 0 and json.loads(preview.stdout) == {"ready": True, "dry_run": True}
        assert not proof.exists()
        assert _replay(trial, execute=True).returncode == 0
    for path in (proof, trial / BINDING_FILENAME):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    original_proof = proof.read_bytes()
    assert _replay(trial, execute=True).returncode == 0 and proof.read_bytes() == original_proof
    # The runner publishes proof only; the reserved slot survives until host readback.
    assert read_benchmark_experiment_board_rows(board) == [running]
    assert read_benchmark_concurrency_envelope(envelope)["active_runs"] == [admission["run"]]

    effect = controller("terminal-reconcile", "--request-json", str(proof))
    assert effect["board_readback_verified"] and effect["slot_readback_verified"] and effect["released"]
    completed, = read_benchmark_experiment_board_rows(board)
    assert completed["status"] == "completed" and completed["attempt"] == 2
    assert completed["metrics"] == running["metrics"] and completed["countability"] == running["countability"]
    assert read_benchmark_concurrency_envelope(envelope)["active_runs"] == []
    replay = controller("terminal-reconcile", "--request-json", str(proof))
    assert replay["already_released"] and not replay["write_performed"]


@pytest.mark.parametrize("evidence", ["unknown_grading", "missing_grading", "unconfirmed_solver", "predates_admission"])
def test_unconfirmed_managed_proof_preserves_provider_result(tmp_path, entry, evidence):
    trial, invoke, _ = entry
    assert invoke(_binding_file(tmp_path, _admission()), evidence=evidence) == 0
    assert (trial / BINDING_FILENAME).is_file() and not (trial / TERMINAL_FILENAME).exists()
    runtime = json.loads((trial / "runtime-receipt.json").read_bytes())
    assert runtime["status"] == "terminal" and runtime["best_score"] == 4


@pytest.mark.parametrize("change", ["goal", "case", "run", "preview", "idempotent", "attempt", "mode"])
def test_wrong_managed_binding_rejected_before_launch(tmp_path, entry, change):
    trial, invoke, _ = entry
    admission, identity = _admission(), dict(IDENTITY)
    if change == "goal":
        admission["goal_id"] = "another-goal"
    elif change == "case":
        identity["case_id"] = "another-task"
    elif change == "run":
        identity["run_id"] = "another-run"
    elif change == "preview":
        admission["dry_run"] = True
    elif change == "idempotent":
        admission.update(idempotent=True, write_performed=False)
    elif change == "attempt":
        identity["attempt"] = True
    binding = _binding_file(tmp_path, admission, identity=identity)
    if change == "mode":
        binding.chmod(0o644)
    with pytest.raises(SystemExit) as stopped:
        invoke(binding)
    assert stopped.value.code == 2 and not trial.exists()


def test_unmanaged_runner_retains_ordinary_result(entry):
    trial, invoke, _ = entry
    assert invoke() == 0
    assert (trial / "final_result.json").is_file()
    assert not (trial / BINDING_FILENAME).exists() and not (trial / TERMINAL_FILENAME).exists()


def test_managed_runner_exception_retains_original_failure(tmp_path, entry):
    trial, invoke, _ = entry
    with pytest.raises(RuntimeError, match="native provider failed"):
        invoke(_binding_file(tmp_path, _admission()), evidence="exception")
    runtime = json.loads((trial / "runtime-receipt.json").read_bytes())
    assert runtime["status"] == "runner_failed"
    assert (trial / BINDING_FILENAME).is_file() and not (trial / TERMINAL_FILENAME).exists()
