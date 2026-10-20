"""Report portability must not invent qualification or silently swap settings."""

import copy
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from benchmark.edgebench.export_report import digest, export, verify, write


ROOT = Path(__file__).resolve().parents[2]
PUBLISHED_DATA = Path("benchmark/edgebench/studies/portfolio-ten-arm-20261006/data")


@pytest.mark.parametrize("strategy", [None, "off", "evidence_conditioned_v1"])
def test_report_preserves_observed_stride_version_and_absence(trial, strategy):
    runs, selection, output, task = trial
    task = runs / "run" / "case"
    for name in ("runtime-receipt", "worker-profile"):
        path = task / (name + ".json")
        value = json.loads(path.read_text())
        if strategy is not None:
            value["stride_strategy"] = strategy
            value["worker" if name == "runtime-receipt" else "profile"] = "heartbeat-resume"
        write(path, value)
    export(runs, selection, output, observed_at="2026-01-02T00:00:00+00:00")
    config = json.loads((output / "settings/run.json").read_text())
    for field in ("runner", "worker_profile"):
        assert config[field].get("stride_strategy") == strategy
        assert ("stride_strategy" in config[field]) is (strategy is not None)


@pytest.mark.parametrize("runner,profile", [
    ("evidence_conditioned_v1", "off"),
    ("evidence_conditioned_v1", None),
    ("future_policy", "future_policy"),
])
def test_report_rejects_unbound_or_unknown_stride_version(trial, runner, profile):
    runs, selection, output, task = trial
    task = runs / "run" / "case"
    for name, strategy in (("runtime-receipt", runner), ("worker-profile", profile)):
        path = task / (name + ".json")
        value = json.loads(path.read_text())
        if strategy is not None:
            value["stride_strategy"] = strategy
        write(path, value)
    with pytest.raises(ValueError, match="stride_strategy"):
        export(runs, selection, output, observed_at="2026-01-02T00:00:00+00:00")


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


def native_trial(trial, policy="score_first", direction="minimize"):
    """Synthetic native receipts: selected round has score 0, another has 2."""
    trial[1][0]["settings"]["native_ranking"] = {
        "selection": policy, "score_direction": direction,
    }
    for name in ("runtime-receipt", "final_result", "run_history"):
        path = trial[3] / (name + ".json")
        data = json.loads(path.read_text())
        data["best_score"] = 0
        if name != "runtime-receipt":
            data.update(best_round="auto-2", best_pass_rate=1.0)
        if name == "run_history":
            data["entries"][0].update(
                pass_rate=0.5, valid=policy != "valid_then_score",
                private_future_field="must-not-be-exported",
            )
            data["entries"][1].update(pass_rate=1.0, valid=True)
        write(path, data)


@pytest.mark.parametrize("policy,direction", [
    ("score_first", "minimize"),
    ("valid_then_score", "maximize"),
    ("pass_rate_first", "maximize"),
])
def test_native_ranking_preserves_selected_lower_score(trial, policy, direction):
    native_trial(trial, policy, direction)
    out = build(trial)
    row = json.loads((out / "run-rows.json").read_text())[0]
    assert row["metrics"]["best_score"] == {
        "value": 0, "unit": "raw_score", "higher_is_better": direction == "maximize",
    }
    assert row["metrics"]["best_pass_rate"]["value"] == 1.0
    assert row["countability"]["score_countable"] is False
    with (out / "scores.csv").open() as stream:
        assert next(csv.DictReader(stream))["best_round"] == "auto-2"
    assert all("must-not-be-exported" not in p.read_text()
               for p in out.rglob("*") if p.is_file())
    assert verify(out)["verified"] is True


@pytest.mark.parametrize("metadata", [
    None, {}, {"selection": "score_first"},
    {"selection": "other", "score_direction": "minimize"},
    {"selection": "score_first", "score_direction": "down"},
    {"selection": "score_first", "score_direction": False},
    {"selection": "score_first", "score_direction": "minimize", "private": "extra"},
])
def test_invalid_native_ranking_metadata_fails_before_output(trial, metadata):
    trial[1][0]["settings"]["native_ranking"] = metadata
    with pytest.raises(ValueError, match="native ranking"):
        build(trial)
    assert not trial[2].exists()


@pytest.mark.parametrize("field,value", [
    ("best_round", "absent"), ("best_round", "auto-1"),
    ("best_score", 2), ("best_pass_rate", 0.5),
])
def test_native_history_winner_must_bind_final_result(trial, field, value):
    native_trial(trial)
    path = trial[3] / "run_history.json"
    data = json.loads(path.read_text())
    data[field] = value
    write(path, data)
    with pytest.raises(ValueError, match="Native history"):
        build(trial)
    assert not trial[2].exists()


def test_supplied_metric_direction_cannot_be_silently_overwritten(trial):
    native_trial(trial)
    trial[1][0]["row"]["metrics"]["best_score"] = {
        "value": 0, "higher_is_better": True,
    }
    with pytest.raises(ValueError, match="direction"):
        build(trial)
    assert not trial[2].exists()


@pytest.mark.parametrize("fault", ["direction", "missing_direction", "pass_rate", "winner"])
def test_rehashed_native_report_contradictions_fail(trial, fault):
    native_trial(trial, "pass_rate_first", "maximize")
    out = build(trial)
    if fault == "winner":
        replace_csv(out, "scores.csv", "best_round", "auto-1")
    else:
        path = out / "run-rows.json"
        rows = json.loads(path.read_text())
        if fault == "direction":
            rows[0]["metrics"]["best_score"]["higher_is_better"] = False
        elif fault == "missing_direction":
            del rows[0]["metrics"]["best_score"]["higher_is_better"]
        else:
            rows[0]["metrics"]["best_pass_rate"]["value"] = 0.5
        write(path, rows)
        reseal(out, "run-rows.json")
    with pytest.raises(ValueError, match="direction|pass rate|Best round"):
        verify(out)


@pytest.mark.parametrize("reverse", [False, True])
def test_cli_exports_mixed_legacy_and_native_without_sforge(trial, reverse):
    runs, selection, out, source = trial
    other = runs / "other" / "case"
    shutil.copytree(source, other)
    for name in ("runtime-receipt", "final_result", "run_history"):
        path = other / (name + ".json")
        data = json.loads(path.read_text())
        data["run_id"] = "other"
        write(path, data)
    legacy = copy.deepcopy(selection[0])
    legacy["row"]["run_id"] = "other"
    native_trial(trial)
    selection.append(legacy)
    if reverse:
        selection.reverse()
    selection_file = runs.parent / "selection.json"
    write(selection_file, selection)
    result = subprocess.run([
        sys.executable, "-c",
        "import sys, runpy; sys.modules['sforge'] = None; "
        "runpy.run_module('benchmark.edgebench.export_report', run_name='__main__')",
        "--runs-root", str(runs), "--selection", str(selection_file),
        "--output", str(out), "--observed-at", "2026-01-02T00:00:00+00:00",
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runs"] == 2
    with (out / "samples.csv").open() as stream:
        samples = list(csv.DictReader(stream))
    assert [p["pass_rate"] for p in samples if p["run_id"] == "run"] == ["0.5", "1.0"]
    assert all(p["pass_rate"] == "" for p in samples if p["run_id"] == "other")
    assert verify(out)["verified"] is True


def test_archived_report_cli_verifies_unchanged_without_sforge():
    data = ROOT / PUBLISHED_DATA
    before = {p: digest(p) for p in data.rglob("*") if p.is_file()}
    result = subprocess.run([
        sys.executable, "-c",
        "import sys, runpy; sys.modules['sforge'] = None; "
        "runpy.run_module('benchmark.edgebench.export_report', run_name='__main__')",
        "--verify", str(data),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"runs": 10, "files": 13, "verified": True}
    assert {p: digest(p) for p in before} == before


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


def _windows_text_writes(monkeypatch):
    """Mirror the os.linesep translation CPython applies to Windows text writes."""
    real_open = Path.open

    def windows_text_write(self, mode="r", *args, **kwargs):
        if "b" not in mode and any(flag in mode for flag in "wax"):
            if kwargs.get("newline") is None:
                kwargs["newline"] = "\r\n"
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", windows_text_write)


def _publish_and_recheck(tmp_path, build_data):
    """Publish files through Git with text conversion forced on, then verify."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    shutil.copyfile(ROOT / ".gitattributes", repository / ".gitattributes")
    data = build_data(repository)
    (repository / "control.txt").write_bytes(b"control\n")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repository), "-c", "core.autocrlf=true", *args],
            check=True, capture_output=True, text=True,
        )

    relative = data.relative_to(repository).as_posix()
    pin = git("check-attr", "eol", f"{relative}/index.json").stdout
    assert pin.strip().endswith("eol: lf"), "the LF pin must apply to this path"
    git("add", "--", ".gitattributes", relative, "control.txt")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git("checkout-index", "--all", f"--prefix={checkout.as_posix()}/")
    assert (checkout / "control.txt").read_bytes() == b"control\r\n", "conversion must be active"
    restored = checkout / relative
    assert verify(restored)["verified"] is True
    for path in data.rglob("*"):
        if path.is_file():
            assert (restored / path.relative_to(data)).read_bytes() == path.read_bytes()
    return restored


def test_json_writer_ignores_host_text_newline_translation(tmp_path, monkeypatch):
    _windows_text_writes(monkeypatch)
    value = {"best_score": 2, "note": "first line\nsecond line"}
    path = tmp_path / "value.json"
    write(path, value)
    expected = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    assert path.read_bytes() == expected.encode("utf-8")


def test_published_archive_materializes_byte_exact_under_autocrlf(tmp_path):
    def build(repository):
        data = repository / PUBLISHED_DATA
        shutil.copytree(ROOT / PUBLISHED_DATA, data)
        return data

    _publish_and_recheck(tmp_path, build)


def test_exported_report_survives_autocrlf_publication(tmp_path, trial, monkeypatch):
    """A Windows host writes CRLF for default text writes, but this export binds
    its own bytes, so Git must store and restore exactly what was written."""
    _windows_text_writes(monkeypatch)

    def build(repository):
        data = repository / "benchmark/edgebench/studies/review-roundtrip/data"
        runs, selection, _unused, _task = trial
        export(runs, selection, data, observed_at="2026-01-02T00:00:00+00:00")
        return data

    _publish_and_recheck(tmp_path, build)


@pytest.mark.parametrize("enabled", [None, False, True])
def test_report_preserves_horizon_opt_in_and_historical_absence(trial, enabled):
    runs, selection, output, _ = trial
    for name in ("runtime-receipt", "worker-profile"):
        path = runs / "run" / "case" / (name + ".json")
        value = json.loads(path.read_text())
        if enabled is not None:
            value["planning_horizon_goal"] = enabled
        write(path, value)
    export(runs, selection, output, observed_at="2026-01-02T00:00:00+00:00")
    config = json.loads((output / "settings/run.json").read_text())
    for field in ("runner", "worker_profile"):
        assert config[field].get("planning_horizon_goal") is enabled
        assert ("planning_horizon_goal" in config[field]) is (enabled is not None)


@pytest.mark.parametrize("receipt,profile", [(True, False), (True, None), ("true", "true"), (True, 1)])
def test_report_rejects_unbound_horizon_setting(trial, receipt, profile):
    runs, selection, output, _ = trial
    for name, enabled in (("runtime-receipt", receipt), ("worker-profile", profile)):
        path = runs / "run" / "case" / (name + ".json")
        value = json.loads(path.read_text())
        if enabled is not None:
            value["planning_horizon_goal"] = enabled
        write(path, value)
    with pytest.raises(ValueError, match="planning_horizon_goal"):
        export(runs, selection, output, observed_at="2026-01-02T00:00:00+00:00")


@pytest.mark.parametrize("enabled", [None, False, True])
def test_report_preserves_research_treatment_and_historical_absence(trial, enabled):
    for name in ("runtime-receipt", "worker-profile"):
        path = trial[3] / (name + ".json")
        value = json.loads(path.read_text())
        if enabled is not None:
            value["research_loop"] = enabled
        if enabled:
            value["worker" if name == "runtime-receipt" else "profile"] = "heartbeat-explore"
            value["feedback"] = "native"
        write(path, value)
    out = build(trial)
    config = json.loads((out / "settings/run.json").read_text())
    for owner in ("runner", "worker_profile"):
        assert config[owner].get("research_loop") is enabled
        assert ("research_loop" in config[owner]) is (enabled is not None)
    assert verify(out)["verified"] is True


@pytest.mark.parametrize("receipt,profile", [
    ({}, {"research_loop": True}),
    ({"research_loop": True}, {}),
    ({}, {"research_loop": False}),
    ({"research_loop": True}, {"research_loop": False}),
    ({"research_loop": True}, {"research_loop": 1}),
    ({"research_loop": "true"}, {"research_loop": "true"}),
    ({"research_loop": None}, {"research_loop": None}),
])
def test_report_rejects_one_sided_or_invalid_raw_research_treatment_before_projection(trial, receipt, profile):
    for name, fields in (("runtime-receipt", receipt), ("worker-profile", profile)):
        path = trial[3] / (name + ".json")
        value = json.loads(path.read_text())
        value.update(fields)
        write(path, value)
    with pytest.raises(ValueError, match="research_loop"):
        build(trial)
    assert not trial[2].exists(), "a discarded source flag must not become a verified portable report"


@pytest.mark.parametrize("runner,profile", [
    ({}, {"research_loop": True}),
    ({"research_loop": True}, {}),
    ({"research_loop": True}, {"research_loop": 1}),
    ({"research_loop": None}, {"research_loop": None}),
])
def test_rehashed_portable_research_treatment_still_requires_matching_booleans(trial, runner, profile):
    out = build(trial)
    path = out / "settings/run.json"
    config = json.loads(path.read_text())
    config["runner"].update(runner)
    config["worker_profile"].update(profile)
    write(path, config)
    reseal(out, "settings/run.json")
    with pytest.raises(ValueError, match="research_loop"):
        verify(out)
