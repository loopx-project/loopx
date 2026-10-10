"""Feedback is a disclosure policy, not a replacement evaluator."""
import hashlib
import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sforge")
from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_FILE, validate_best_only


class Transport:
    def __init__(self):
        self.files = {}
        self.fail = False
        self.read_failure = False
        self.corrupt = False

    def copy_to_container(self, handle, source, target):
        if self.fail:
            raise OSError("synthetic copy failure")
        self.files[str(target)] = Path(source).read_bytes()

    def exec_run(self, handle, command, **kwargs):
        if command[0] == "sha256sum":
            assert kwargs.get("user") == "agent"
            content = self.files[command[1]]
            digest = hashlib.sha256(content if not self.corrupt else b"changed").hexdigest()
            return SimpleNamespace(exit_code=int(self.read_failure), output=digest)
        pending = str(FEEDBACK_FILE.parent / ".latest.pending")
        if command[0] == "/bin/sh" and "mv -f" in command[-1]:
            self.files[str(FEEDBACK_FILE)] = self.files.pop(pending)
        return SimpleNamespace(exit_code=0, output="")


def row(n, score, **kwargs):
    return dict(type="submission", status="completed", valid=True, task_id="fixture",
                round=f"auto-{n}", submission_id=f"s{n}", score=score, **kwargs)


def history(*entries):
    return {"run_id": "run", "entries": list(entries)}


class Admissions:
    epoch = "synthetic-epoch"
    evaluators = {"fixture": {"native_source_sha256": "b" * 64, "task_spec_sha256": "c" * 64,
                              "judge_image_key": "fixture", "selection": "score_first",
                              "score_direction": "maximize"}}

    def admitted(self):
        return {f"s{n}": dict(round_id=f"auto-{n}", source_sha256=hashlib.sha256(b"agent source only").hexdigest())
                for n in range(1, 10)}

    def start(self, *args):
        pass

    def pause(self):
        pass

    def close(self):
        pass

    def tick(self, history):
        pass


def official_result(entry):
    return dict(submission_id=entry["submission_id"], status="completed", error=None,
                report=dict(task_id="fixture", submission_id=entry["submission_id"], valid=True,
                            score=entry["score"], pass_rate=entry.get("pass_rate"),
                            summary="OFFICIAL_DIAGNOSTIC", metrics={"coverage": .75},
                            details=[{"name": "case", "message": "official detail"}]))


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    result = BestOnlyFeedback(trial=tmp_path, run_id="run", task_id="fixture", direction="maximize",
                            judge_url="http://127.0.0.1:1", admin_secret="private-secret",
                            logger=logging.getLogger("fixture"), sampler=Admissions())
    # Selection and source-delivery tests use a provider response fixture.
    # The unpatched HTTP/binding method is tested separately below.
    monkeypatch.setattr(result, "_official_result", official_result)
    return result


def archive(publisher, n, content=b"agent source only"):
    path = publisher.trial / "submissions" / f"auto-{n}" / "submission.tar.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)


def test_silent_baseline_then_complete_official_improvement_and_source_identity(publisher):
    transport = Transport()
    publisher.update(history(row(1, 0)), transport, None)
    assert not transport.files
    entries = [row(1, 0), row(2, 2, summary="SECRET", metrics={"private": 7})]
    archive(publisher, 2)
    publisher.update(history(*entries), transport, None)
    packet = json.loads(transport.files[str(FEEDBACK_FILE)])
    assert set(packet) == {"schema_version", "latest"}
    assert packet["schema_version"] == "edgebench_best_feedback_v2"
    assert packet["latest"]["official_result"] == official_result(row(2, 2))
    assert packet["latest"]["evaluator"] == Admissions.evaluators["fixture"]
    assert (packet["latest"]["run_id"], packet["latest"]["task_id"],
            packet["latest"]["online_epoch"]) == ("run", "fixture", "synthetic-epoch")
    assert packet["latest"]["kind"] == "new_best"
    assert packet["latest"]["snapshot_id"] == "auto-2"
    assert transport.files[packet["latest"]["source_archive"]] == b"agent source only"
    import hashlib
    assert packet["latest"]["source_sha256"] == hashlib.sha256(b"agent source only").hexdigest()
    assert "SECRET" not in json.dumps(packet)
    previous = dict(transport.files)
    for more in [[], [row(3, 2)], [row(4, -1)]]:
        publisher.update(history(*entries, *more), transport, None)
        assert transport.files == previous
    assert publisher.notifications == 1


@pytest.mark.parametrize("fault", [None, "submission", "task", "score", "pass_rate", "invalid",
                                    "pending", "error", "epoch", "evaluator", "network"])
def test_official_response_binding_failure_retries_without_advancing(publisher, monkeypatch, fault):
    baseline, candidate = row(1, 1, pass_rate=.5), row(2, 3, pass_rate=.75)
    archive(publisher, 2)
    expected = official_result(candidate)
    response = json.loads(json.dumps(expected))
    admission = {"epoch": "synthetic-epoch", "evaluators": Admissions.evaluators}
    if fault == "submission":
        response["submission_id"] = "another"
    elif fault == "task":
        response["report"]["task_id"] = "another"
    elif fault in {"score", "pass_rate"}:
        response["report"][fault] = 9
    elif fault == "invalid":
        response["report"]["valid"] = False
    elif fault == "pending":
        response["status"] = "running"
    elif fault == "error":
        response["error"] = "failed"
    elif fault == "epoch":
        admission = {**admission, "epoch": "restarted"}
    elif fault == "evaluator":
        admission = {**admission, "evaluators": {}}
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if fault == "network":
            raise TimeoutError("synthetic unavailable result")
        value = admission if url.endswith("/admission") else response
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: value)
    monkeypatch.setattr(publisher.session, "get", get)
    monkeypatch.setattr(publisher, "_official_result", BestOnlyFeedback._official_result.__get__(publisher))
    transport = Transport()
    values = history(baseline, candidate)
    if fault:
        with pytest.raises((ValueError, TimeoutError)):
            publisher.update(values, transport, None)
        assert not transport.files and publisher.score == 1 and publisher.notifications == 0
        response, admission, fault = expected, {"epoch": "synthetic-epoch", "evaluators": Admissions.evaluators}, None
    publisher.update(values, transport, None)
    packet = json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]
    assert packet["official_result"] == expected
    assert calls[-2][0].endswith("/api/v1/result/s2")
    assert "params" not in calls[-2][1]  # No credentials go into the disclosed API body.
    assert calls[-1][1]["params"] == {"admin_secret": "private-secret"}
    publisher.update(values, transport, None)
    assert publisher.notifications == 1


def test_native_ranking_alone_controls_improvement_notifications(publisher):
    from sforge.harness.selection import select_best
    transport = Transport()
    publisher.selection = "pass_rate_first"
    entries = [row(1, .2, pass_rate=.9), row(2, .4, pass_rate=.8)]
    publisher.update(history(entries[0]), transport, None)
    archive(publisher, 2)
    publisher.update(history(*entries), transport, None)
    assert not transport.files
    native = select_best(entries, "maximize", "pass_rate_first")
    assert native["best_round"] == "auto-1"
    assert native["best_score"] == .2
    assert entries[1]["pass_rate"] == .8
    entries.append(row(3, .3, pass_rate=.95))
    archive(publisher, 3)
    publisher.update(history(*entries), transport, None)
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]["snapshot_id"] == "auto-3"
    # A higher native pass rate is an improvement even with a lower score.
    entries.append(row(4, .1, pass_rate=.96))
    archive(publisher, 4)
    publisher.update(history(*entries), transport, None)
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]["snapshot_id"] == "auto-4"
    previous = dict(transport.files)
    entries.append(row(5, .1, pass_rate=.96))
    publisher.update(history(*entries), transport, None)
    assert transport.files == previous  # Equal native rank is silent.


@pytest.mark.parametrize("pass_rate", [0, None])
def test_native_empty_winner_stays_silent_then_recovers(publisher, pass_rate):
    publisher.selection = "pass_rate_first"
    transport = Transport()
    entries = [row(1, 9, pass_rate=pass_rate), row(2, 99, pass_rate=pass_rate)]
    # Zero/missing pass rates do not select a native winner, even with higher
    # scores. Repeated polls must not fail, touch source files, or notify.
    for _ in range(2):
        publisher.update(history(*entries), transport, None)
    assert not transport.files
    assert publisher.score == 9 and publisher.notifications == 0
    assert publisher.errors == 0
    entries.append(row(3, 1, pass_rate=.1))
    archive(publisher, 3)
    publisher.update(history(*entries), transport, None)
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]["snapshot_id"] == "auto-3"
    assert publisher.score == 1 and publisher.notifications == 1
    # Below full pass rate, equal pass rate is tied regardless of scalar score.
    previous = dict(transport.files)
    entries.append(row(4, 999, pass_rate=.1))
    publisher.update(history(*entries), transport, None)
    assert transport.files == previous and publisher.notifications == 1


@pytest.mark.parametrize("patch", [
    {"score": None}, {"score": math.nan}, {"score": math.inf}, {"score": True},
    {"score": "9"}, {"status": "running"}, {"status": "error"}, {"valid": False},
    {"type": "game"}, {"round": "agent-2"}, {"round": "../../private"},
])
def test_invalid_pending_nonautomatic_or_malformed_results_never_disclose(publisher, patch):
    transport = Transport()
    publisher.update(history(row(1, 1), {**row(2, 9), **patch}), transport, None)
    assert not transport.files
    assert publisher.notifications == 0


def test_minimize_and_batch_coalescing_preserve_strict_improvement(publisher):
    publisher.direction = "minimize"
    transport = Transport()
    archive(publisher, 3)
    publisher.update(history(row(1, 9), row(2, 7), row(3, 3)), transport, None)
    assert publisher.score == 3 and publisher.notifications == 1
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]["snapshot_id"] == "auto-3"
    publisher.update(history(row(4, 5), row(3, 3)), transport, None)
    assert publisher.notifications == 1


def test_late_results_do_not_misattribute_to_current_workspace(publisher):
    transport = Transport()
    archive(publisher, 1)
    publisher.update(history({**row(1, None), "status": "running"}, row(2, 2)), transport, None)
    assert publisher.score == 2 and not transport.files
    publisher.update(history(row(1, 4), row(2, 2)), transport, None)
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]["snapshot_id"] == "auto-1"
    publisher.update(history(row(2, 2), row(1, 4)), transport, None)
    assert publisher.notifications == 1


def test_delivery_failure_retries_without_advancing_incumbent(publisher):
    transport = Transport()
    archive(publisher, 2)
    transport.fail = True
    values = history(row(1, 1), row(2, 3))
    with pytest.raises(OSError):
        publisher.update(values, transport, None)
    assert publisher.score == 1 and publisher.notifications == 0
    transport.fail = False
    publisher.update(values, transport, None)
    assert publisher.score == 3 and publisher.notifications == 1
    publisher.update(values, transport, None)
    assert publisher.notifications == 1


@pytest.mark.parametrize("failure", ["read_failure", "corrupt"])
def test_unreadable_or_changed_worker_source_keeps_previous_notice_and_incumbent(publisher, failure):
    transport = Transport()
    archive(publisher, 2)
    archive(publisher, 3)
    values = history(row(1, 1), row(2, 2))
    publisher.update(values, transport, None)
    previous = transport.files[str(FEEDBACK_FILE)]
    setattr(transport, failure, True)
    values["entries"].append(row(3, 3))
    with pytest.raises(RuntimeError, match="source checkpoint"):
        publisher.update(values, transport, None)
    assert transport.files[str(FEEDBACK_FILE)] == previous
    assert publisher.score == 2 and publisher.notifications == 1
    setattr(transport, failure, False)
    publisher.update(values, transport, None)
    publisher.update(values, transport, None)
    assert publisher.score == 3 and publisher.notifications == 2


def test_missing_snapshot_or_foreign_history_fails_closed(publisher):
    transport = Transport()
    with pytest.raises(FileNotFoundError):
        publisher.update(history(row(1, 1), row(2, 3)), transport, None)
    assert not transport.files and publisher.score == 1
    with pytest.raises(ValueError):
        publisher.update({"run_id": "other", "entries": [row(1, 5)]}, transport, None)
    with pytest.raises(ValueError):
        publisher.update(history({**row(1, 5), "task_id": "other"}), transport, None)
    assert not transport.files


def test_close_prevents_late_feedback_and_native_resume_does_not_restart(publisher):
    transport = Transport()
    publisher.bind({"SFORGE_TOKEN": "private-token"})
    publisher.start(transport, None)
    thread = publisher.thread
    publisher.start(transport, None)
    assert publisher.thread is thread
    publisher.close()
    assert not thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        publisher.start(transport, None)
    archive(publisher, 2)
    publisher.update(history(row(1, 1), row(2, 9)), transport, None)
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"] is None
    assert "private-token" not in (publisher.directory / "state.json").read_text()


@pytest.mark.parametrize("policy,direction,interval", [
    ("unknown", "maximize", 300), ("score_first", "unknown", 300),
    ("score_first", "maximize", 0), ("score_first", "maximize", -1),
])
def test_unsupported_task_semantics_fail_before_launch(policy, direction, interval):
    task = SimpleNamespace(judge=SimpleNamespace(selection=policy, score_direction=direction))
    with pytest.raises(ValueError):
        validate_best_only(task, interval)


@pytest.mark.parametrize("policy", ["score_first", "valid_then_score", "pass_rate_first"])
def test_best_only_accepts_native_rankings_without_changing_task_policy(policy):
    task = SimpleNamespace(judge=SimpleNamespace(selection=policy, score_direction="maximize"))
    validate_best_only(task, 1800)
    assert task.judge.selection == policy


@pytest.mark.parametrize("profile", ["official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore"])
@pytest.mark.parametrize("mode", [None, "blind", "native", "best-only"])
@pytest.mark.parametrize("override", [False, True])
def test_cli_native_default_and_explicit_controls_reach_registration(tmp_path, monkeypatch, profile, mode, override):
    pytest.importorskip("harbor")
    from benchmark.edgebench import run
    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    (tmp_path / "fixture.json").write_text("{}")
    def qualify(self):
        self.epoch, self.evaluators = Admissions.epoch, Admissions.evaluators
    monkeypatch.setattr(run.OnlineSampler, "qualify", qualify)
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False, game_mode=False,
        submit_paths=["solver.py"], work=SimpleNamespace(agent_query="Complete the task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    constructed = {}
    def backend(**kwargs):
        constructed.update(kwargs)
        return SimpleNamespace(image_exists=lambda image: True)
    monkeypatch.setattr(run, "RecordingDockerBackend", backend)
    def handoff(**kwargs):
        selected = mode or "native"
        assert kwargs["agent"].feedback == selected
        assert kwargs["max_submissions"] == (None if selected == "native" else 0)
        assert (constructed["feedback"] is not None) == (selected == "best-only")
        assert constructed["blind_api_endpoint"] == (None if selected == "native" else ("192.0.2.1", 9090))
        prompt = kwargs["agent"].feedback_prompt
        assert prompt.endswith("---\n\nComplete the task\n")
        interval = 600 if override else 0 if selected == "native" else 300
        cooldown = 42 if override else 3600 if selected == "native" else 120
        assert kwargs["eval_interval"] == interval
        assert kwargs["submission_cooldown"] == cooldown
        if selected == "native":
            assert f"**{cooldown} seconds**" in prompt
            assert "current promising candidate" in prompt
            assert prompt.startswith(kwargs["agent"].workspace_instructions)
        else:
            assert kwargs["agent"].workspace_instructions is None
        assert kwargs["disable_auto_eval"] == (selected == "best-only")
        raise RuntimeError("synthetic launch boundary")
    monkeypatch.setattr(run, "run_agent", handoff)
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "run", "--worker", profile, "--model", "fixture", "--effort", "xhigh",
            "--judge-url", "http://192.0.2.1:8080", "--api-proxy-url", "http://192.0.2.1:9090"]
    if mode:
        args += ["--feedback", mode]
    if override:
        args += ["--eval-interval", "600", "--submission-cooldown", "42"]
    with pytest.raises(RuntimeError, match="synthetic launch boundary"):
        run.main(args)
    receipt = json.loads((tmp_path / "runs/run/fixture/runtime-receipt.json").read_text())
    selected = mode or "native"
    assert receipt["feedback"] == selected
    assert receipt["eval_interval"] == (600 if override else 0 if selected == "native" else 300)
    assert receipt["submission_cooldown"] == (42 if override else 3600 if selected == "native" else 120)


@pytest.mark.parametrize("option", ["--submission-cooldown", "--eval-interval"])
def test_negative_intervals_fail_before_attempt_creation(tmp_path, option):
    from benchmark.edgebench import run
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "run", "--worker", "official", "--model", "fixture", "--effort", "xhigh",
            "--judge-url", "http://192.0.2.1:8080", option, "-1"]
    with pytest.raises(SystemExit):
        run.main(args)
    assert not (tmp_path / "runs").exists()
