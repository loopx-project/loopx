"""Completion markers follow native grading; partial evidence remains native."""

import json
from types import SimpleNamespace

import pytest

from benchmark.edgebench.result_publication import write_native_result


@pytest.fixture
def native_result():
    payload = dict(
        best_score=0.0, best_pass_rate=1.0, best_round="auto-1", total_rounds=2,
        agent_submissions=1, auto_submissions=1, runtime_seconds=12.0,
        timed_out=True, resume_count=1, archive_size_bytes=17,
        grading_status="complete", grading_reason="all_submissions_terminal",
        solver_execution={"status": "timed_out"},
    )
    result = SimpleNamespace(runtime_seconds=12.0, to_dict=lambda: dict(payload))
    return result, payload


def publish(trial, result, **kwargs):
    write_native_result(trial, result, agent="codex", task="case", run_id="run",
                        model="model", effort="high", **kwargs)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("native_goal_outcome", [None, "completed"])
def test_complete_or_legacy_result_retains_final_shape(tmp_path, native_result, legacy,
                                                       native_goal_outcome):
    result, payload = native_result
    if legacy:
        payload.pop("grading_status")
        payload.pop("grading_reason")
    publish(tmp_path, result, status="terminal", native_goal_outcome=native_goal_outcome)
    expected = dict(agent="codex", task="case", run_id="run", model="model", effort="high",
                    **payload)
    if native_goal_outcome is not None:
        expected.update(runtime_status="terminal", native_goal_outcome=native_goal_outcome)
    assert json.loads((tmp_path / "final_result.json").read_text()) == expected
    assert not (tmp_path / "partial_result.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("grading_status", ["pending", "unknown", None, "future-status"])
def test_terminal_incomplete_grading_retains_partial_evidence(tmp_path, native_result,
                                                              grading_status):
    result, payload = native_result
    payload.update(grading_status=grading_status, grading_reason="synthetic_unfinished_grading")
    history = {"run_id": "run", "best_score": 0.0, "best_round": "auto-1", "entries": [
        {"type": "submission", "status": "completed", "round": "auto-1", "score": 0.0},
        {"type": "submission", "status": "pending", "round": "agent-1"},
    ]}
    retained = {
        "run_history.json": json.dumps(history).encode(),
        "final_archive.tar.gz": b"synthetic current workspace",
        "submissions/auto-1/submission.tar.gz": b"synthetic earlier incumbent",
    }
    for name, content in retained.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    publish(tmp_path, result, status="terminal")
    assert not (tmp_path / "final_result.json").exists()
    partial = json.loads((tmp_path / "partial_result.json").read_text())
    assert partial == dict(agent="codex", task="case", run_id="run", model="model",
                           effort="high", runtime_status="terminal", **payload)
    assert {name: (tmp_path / name).read_bytes() for name in retained} == retained
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("grading_status", ["pending", "unknown"])
def test_completed_native_goal_does_not_override_unfinished_grading(tmp_path, native_result,
                                                                   grading_status):
    result, payload = native_result
    payload.update(grading_status=grading_status, grading_reason="synthetic_unfinished_grading")
    publish(tmp_path, result, status="terminal", native_goal_outcome="completed")
    assert not (tmp_path / "final_result.json").exists()
    partial = json.loads((tmp_path / "partial_result.json").read_text())
    assert partial["runtime_status"] == "terminal"
    assert partial["native_goal_outcome"] == "completed"
    assert all(partial[key] == value for key, value in payload.items())


@pytest.mark.parametrize("status,outcome", [
    ("cancelled", "cancelled"), ("runner_failed", "failed"),
    ("runner_failed", "usage_limited"), ("runner_failed", "unknown"),
])
def test_completed_grading_preserves_partial_failed_native_goal(tmp_path, native_result,
                                                                status, outcome):
    result, payload = native_result
    publish(tmp_path, result, status=status, native_goal_outcome=outcome)
    assert not (tmp_path / "final_result.json").exists()
    partial = json.loads((tmp_path / "partial_result.json").read_text())
    assert partial["runtime_status"] == status
    assert partial["native_goal_outcome"] == outcome
    assert all(partial[key] == value for key, value in payload.items())


@pytest.mark.parametrize("status,outcome,runtime", [
    ("launch_failed", None, 0.0), ("runner_failed", None, 12.0),
    ("cancelled", None, 12.0), ("runner_failed", "failed", 0.0),
])
def test_unqualified_execution_keeps_existing_publication_behavior(tmp_path, native_result,
                                                                  status, outcome, runtime):
    result, payload = native_result
    result.runtime_seconds = runtime
    payload["runtime_seconds"] = runtime
    publish(tmp_path, result, status=status, native_goal_outcome=outcome)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("grading_status,destination", [
    ("complete", "final_result.json"), ("pending", "partial_result.json"),
])
def test_best_only_retains_coverage_without_claiming_offline_scoring(tmp_path, native_result,
                                                                   grading_status, destination):
    result, payload = native_result
    payload["grading_status"] = grading_status
    publish(tmp_path, result, status="terminal", feedback="best-only")
    observed = json.loads((tmp_path / destination).read_text())
    assert observed["evaluation_coverage"] == "online_only"
    assert observed["offline_scoring_complete"] is False
    assert all(observed[key] == value for key, value in payload.items())
