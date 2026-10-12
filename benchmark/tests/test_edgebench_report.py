"""Report portability must not invent qualification or silently swap settings."""

import copy
import csv
import json
import subprocess
import sys

import pytest

from benchmark.edgebench.export_report import digest, export, verify, write


@pytest.fixture
def trial(tmp_path):
    row = dict(
        schema_version="benchmark_experiment_board_row_v0",
        benchmark_id="example",
        study_id="study",
        case_id="case",
        run_id="run",
        arm_id="baseline",
        arm_role="baseline",
        attempt=1,
        status="running",
        observed_at="2026-01-01T00:00:00+00:00",
        model_id="model",
        protocol_id="protocol",
        comparison_protocol_id="comparison",
        claim_scope="diagnostic_only",
        primary_metric="best_score",
        guardrail_metrics=[],
        metrics={},
        countability=dict(
            integrity_qualified=False,
            official_result_present=False,
            score_countable=False,
        ),
        treatment_fidelity="not_applicable",
        effort={},
        insight=dict(status="pending"),
        runner_revision="abc",
    )
    p = tmp_path / "runs" / "run" / "case"
    p.mkdir(parents=True)
    receipt = dict(
        run_id="run",
        task="case",
        model="model",
        effort="xhigh",
        timeout_seconds=1000,
        worker="single",
        feedback="blind",
        internet=False,
        eval_interval=300,
        submission_cooldown=120,
        loopx_commit="abc",
        runner_commit="abc",
        task_sha256="a" * 64,
        status="terminal",
        best_score=2,
    )
    profile = dict(
        profile="single",
        model="model",
        reasoning_effort="xhigh",
        timeout_seconds=1000,
        stop_hook=False,
        outer_resume=False,
        explore_graph=False,
        explore_harness=False,
        feedback="blind",
        private_future_field="must-not-be-exported",
    )
    final = dict(
        run_id="run",
        task="case",
        model="model",
        best_score=2,
        best_round="auto-1",
        runtime_seconds=800,
        total_rounds=2,
        agent_submissions=0,
        auto_submissions=2,
        timed_out=False,
    )
    history = dict(
        run_id="run",
        entries=[
            dict(type="submission", status="completed", round="auto-1", score=2),
            dict(type="submission", status="completed", round="auto-2", score=0),
        ],
    )
    for name, data in [
        ("runtime-receipt", receipt),
        ("worker-profile", profile),
        ("final_result", final),
        ("run_history", history),
    ]:
        write(p / (name + ".json"), data)
    (p / "started_at").write_text("2026-01-01\n1000\n")
    return (
        tmp_path / "runs",
        [dict(row=row, settings={"unknown": ["random_seed"]})],
        tmp_path / "out",
        p,
    )


def build(trial):
    runs, selection, out, _ = trial
    export(runs, selection, out, observed_at="2026-01-02T00:00:00+00:00")
    return out


def test_terminal_report_retains_unqualified_status_and_unknowns(trial):
    out = build(trial)
    row = json.loads((out / "run-rows.json").read_text())[0]
    assert row["status"] == "completed"
    assert row["metrics"]["best_score"]["value"] == 2
    assert row["countability"] == dict(
        integrity_qualified=False, official_result_present=True, score_countable=False
    )
    assert row["insight"]["status"] == "pending"
    assert "must-not-be-exported" not in (out / "settings/run.json").read_text()
    assert json.loads((out / "settings/run.json").read_text())["unknown"] == [
        "random_seed"
    ]
    assert verify(out)["runs"] == 1
    with pytest.raises(ValueError, match="immutable"):
        build(trial)


@pytest.mark.parametrize("fault", [None, "profile", "mode", "unknown"])
def test_official_feedback_payload_is_preserved_without_relabeling_history(trial, fault):
    for name in ("runtime-receipt", "worker-profile"):
        path = trial[3] / (name + ".json")
        value = json.loads(path.read_text())
        value["feedback"] = "best-only"
        value["feedback_payload"] = "official-result"
        if fault == "profile" and name == "worker-profile":
            value.pop("feedback_payload")
        elif fault == "mode":
            value["feedback"] = "blind"
        elif fault == "unknown":
            value["feedback_payload"] = "future-unknown"
        write(path, value)
    if fault:
        with pytest.raises(ValueError, match="feedback payload"):
            build(trial)
        assert not trial[2].exists()
    else:
        out = build(trial)
        settings = json.loads((out / "settings/run.json").read_text())
        assert settings["runner"]["feedback_payload"] == "official-result"
        assert settings["worker_profile"]["feedback_payload"] == "official-result"
        assert verify(out)["runs"] == 1


@pytest.mark.parametrize(
    "file,key,value",
    [
        ("runtime-receipt", "status", "starting"),
        ("worker-profile", "model", "other"),
        ("runtime-receipt", "runner_commit", "other"),
        ("final_result", "best_score", 3),
        ("run_history", "run_id", "other"),
    ],
)
def test_inconsistent_private_sources_fail_before_output(trial, file, key, value):
    p = trial[3] / (file + ".json")
    data = json.loads(p.read_text())
    data[key] = value
    write(p, data)
    with pytest.raises(ValueError):
        build(trial)
    assert not trial[2].exists()


def test_missing_and_tampered_settings_fail(trial):
    out = build(trial)
    p = out / "settings/run.json"
    original = p.read_bytes()
    p.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        verify(out)
    p.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="binding"):
        verify(out)


def test_rehashed_wrong_arm_is_not_accepted(trial):
    out = build(trial)
    p = out / "settings/run.json"
    data = json.loads(p.read_text())
    data["arm_id"] = "wrong"
    write(p, data)
    index = json.loads((out / "index.json").read_text())
    sha = digest(p)
    index["runs"][0]["settings_sha256"] = sha
    index["files_sha256"]["settings/run.json"] = sha
    write(out / "index.json", index)
    with pytest.raises(ValueError, match="identity mismatch"):
        verify(out)


def test_duplicate_selection_fails(trial):
    trial[1].append(copy.deepcopy(trial[1][0]))
    with pytest.raises(ValueError, match="Unique"):
        build(trial)


def test_rehashed_sample_loss_is_not_accepted(trial):
    out = build(trial)
    p = out / "samples.csv"
    p.write_text("\n".join(p.read_text().splitlines()[:2]) + "\n")
    index = json.loads((out / "index.json").read_text())
    index["files_sha256"]["samples.csv"] = digest(p)
    write(out / "index.json", index)
    with pytest.raises(ValueError, match="Sample count"):
        verify(out)


def test_required_file_must_be_in_inventory(trial):
    out = build(trial)
    index = json.loads((out / "index.json").read_text())
    del index["files_sha256"]["run-rows.json"]
    write(out / "index.json", index)
    with pytest.raises(ValueError, match="required"):
        verify(out)


def reseal(out, name):
    """Model a coherently rehashed file, without changing its semantic authority."""
    index = json.loads((out / "index.json").read_text())
    sha = digest(out / name)
    index["files_sha256"][name] = sha
    if name == "settings/run.json":
        index["runs"][0]["settings_sha256"] = sha
    write(out / "index.json", index)


def replace_csv(out, name, key, value, position=0):
    path = out / name
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    rows[position][key] = value
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    reseal(out, name)


@pytest.mark.parametrize("best_round", ["absent", "auto-2"])
def test_export_requires_native_best_round_score_witness(trial, best_round):
    path = trial[3] / "final_result.json"
    final = json.loads(path.read_text())
    final["best_round"] = best_round
    write(path, final)
    with pytest.raises(ValueError, match="Best round"):
        build(trial)
    assert not trial[2].exists()


def test_native_tie_choice_and_missing_sample_times_are_preserved(trial):
    path = trial[3] / "run_history.json"
    history = json.loads(path.read_text())
    history["entries"][1]["score"] = 2
    write(path, history)
    path = trial[3] / "final_result.json"
    final = json.loads(path.read_text())
    final["best_round"] = "auto-2"
    write(path, final)
    out = build(trial)
    with (out / "scores.csv").open() as stream:
        assert next(csv.DictReader(stream))["best_round"] == "auto-2"
    with (out / "samples.csv").open() as stream:
        assert all(row["elapsed_seconds"] == "" for row in csv.DictReader(stream))
    assert verify(out)["verified"] is True


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("arm_id", "other", "identity mismatch"),
        ("benchmark_id", "other", "identity mismatch"),
        ("score_countable", "True", "countability"),
        ("score_countable", "maybe", "boolean"),
        ("budget_seconds", "1", "budget"),
        ("runtime_seconds", "-500", "duration"),
        ("runtime_seconds", "799", "duration"),
        ("agent_submissions", "1", "Submission counts"),
        ("resume_count", "-1", "summary count"),
        ("best_round", "absent", "Best round"),
        ("best_round", "auto-2", "Best round"),
    ],
)
def test_rehashed_score_contradictions_fail(trial, key, value, message):
    out = build(trial)
    replace_csv(out, "scores.csv", key, value)
    with pytest.raises(ValueError, match=message):
        verify(out)


@pytest.mark.parametrize("key", ["source_study_id", "source_benchmark_id"])
def test_rehashed_settings_source_identity_fails(trial, key):
    out = build(trial)
    path = out / "settings/run.json"
    config = json.loads(path.read_text())
    config[key] = "other"
    write(path, config)
    reseal(out, "settings/run.json")
    with pytest.raises(ValueError, match="Settings identity mismatch"):
        verify(out)


@pytest.mark.parametrize("elapsed", ["-1", "nan", "inf"])
def test_rehashed_invalid_sample_time_fails(trial, elapsed):
    out = build(trial)
    replace_csv(out, "samples.csv", "elapsed_seconds", elapsed)
    with pytest.raises(ValueError):
        verify(out)


@pytest.mark.parametrize("operation", ["export", "verify"])
def test_cli_rejects_inconsistent_reports(trial, operation):
    if operation == "verify":
        out = build(trial)
        replace_csv(out, "scores.csv", "benchmark_id", "other")
        args = ["--verify", str(out)]
        message = "Score summary identity mismatch"
    else:
        path = trial[3] / "final_result.json"
        final = json.loads(path.read_text())
        final["best_round"] = "absent"
        write(path, final)
        selection = trial[0].parent / "selection.json"
        write(selection, trial[1])
        args = [
            "--runs-root",
            str(trial[0]),
            "--selection",
            str(selection),
            "--output",
            str(trial[2]),
            "--observed-at",
            "2026-01-02T00:00:00+00:00",
        ]
        message = "Best round does not substantiate best score"
    result = subprocess.run(
        [sys.executable, "-m", "benchmark.edgebench.export_report", *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert '"verified": true' not in result.stdout
    if operation == "export":
        assert not trial[2].exists()
