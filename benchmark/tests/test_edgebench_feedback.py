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


@pytest.fixture
def publisher(tmp_path):
    return BestOnlyFeedback(trial=tmp_path, run_id="run", task_id="fixture", direction="maximize",
                            judge_url="http://127.0.0.1:1", admin_secret="private-secret",
                            logger=logging.getLogger("fixture"), sampler=Admissions())


def archive(publisher, n, content=b"agent source only"):
    path = publisher.trial / "submissions" / f"auto-{n}" / "submission.tar.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)


def test_silent_baseline_then_positive_only_disclosure_and_source_identity(publisher):
    transport = Transport()
    publisher.update(history(row(1, 0)), transport, None)
    assert not transport.files
    entries = [row(1, 0), row(2, 2, summary="SECRET", metrics={"private": 7})]
    archive(publisher, 2)
    publisher.update(history(*entries), transport, None)
    packet = json.loads(transport.files[str(FEEDBACK_FILE)])
    assert set(packet) == {"schema_version", "latest"}
    assert set(packet["latest"]) == {"kind", "snapshot_id", "source_sha256", "source_archive", "message"}
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


def test_scalar_improvement_requires_native_winner_and_preserves_global_threshold(publisher):
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
    publisher.update(history(*entries), transport, None)
    assert not transport.files  # Native winner improved, scalar record did not.
    entries.append(row(4, .5, pass_rate=.96))
    archive(publisher, 4)
    publisher.update(history(*entries), transport, None)
    assert json.loads(transport.files[str(FEEDBACK_FILE)])["latest"]["snapshot_id"] == "auto-4"


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
@pytest.mark.parametrize("mode", [None, "blind", "native"])
def test_cli_default_and_explicit_controls_reach_native_registration(tmp_path, monkeypatch, profile, mode):
    pytest.importorskip("harbor")
    from benchmark.edgebench import run
    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    (tmp_path / "fixture.json").write_text("{}")
    monkeypatch.setattr(run.OnlineSampler, "qualify", lambda self: setattr(self, "epoch", "synthetic-epoch"))
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
        selected = mode or "best-only"
        assert kwargs["agent"].feedback == selected
        assert kwargs["max_submissions"] == (None if selected == "native" else 0)
        assert (constructed["feedback"] is not None) == (selected == "best-only")
        assert constructed["blind_api_endpoint"] == (None if selected == "native" else ("192.0.2.1", 9090))
        assert (kwargs["agent"].feedback_prompt is None) == (selected == "native")
        assert kwargs["eval_interval"] == 300
        assert kwargs["disable_auto_eval"] == (selected == "best-only")
        raise RuntimeError("synthetic launch boundary")
    monkeypatch.setattr(run, "run_agent", handoff)
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "run", "--worker", profile, "--model", "fixture", "--effort", "xhigh",
            "--judge-url", "http://192.0.2.1:8080", "--api-proxy-url", "http://192.0.2.1:9090"]
    if mode:
        args += ["--feedback", mode]
    with pytest.raises(RuntimeError, match="synthetic launch boundary"):
        run.main(args)
    receipt = json.loads((tmp_path / "runs/run/fixture/runtime-receipt.json").read_text())
    assert receipt["feedback"] == (mode or "best-only")
