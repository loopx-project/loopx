"""Accepted native feedback reaches canonical waits and real request admission."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from benchmark.runtime import research_feedback_wait as feedback
from benchmark.runtime.research_request_guard import ResearchRequestGuard
from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.cli import main as cli_main
import test_quota_settlement_cli as fixture
from tests.capabilities.test_provider_gateway import _recording_upstream


@pytest.fixture(params=["file", "sqlite"])
def runtime(request, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, root, registry = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, root, provider=request.param)

    def call(*args):
        output = io.StringIO()
        with monkeypatch.context() as scope, redirect_stdout(output):
            scope.chdir(project)
            code = cli_main(["--registry", str(registry), "--runtime-root", str(root), "--format", "json", *args])
        payload = json.loads(output.getvalue())
        assert code == 0, {key: payload.get(key) for key in (
            "ok", "error", "errors", "error_code", "reason", "blocker", "diagnostics")}
        return payload

    call("configure-goal", "--goal-id", fixture.GOAL_ID, "--registered-agent", fixture.AGENT_ID,
        "--registered-agent", feedback.FEEDBACK_OBSERVER_AGENT, "--explore-graph-enabled", "--execute")
    env = dict(os.environ, LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(root),
        LOOPX_PROJECT=str(project), LOOPX_GOAL_ID=fixture.GOAL_ID, LOOPX_AGENT_ID=fixture.AGENT_ID,
        LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 300), LOOPX_TURN="original-feedback-turn",
        LOOPX_RESEARCH_FEEDBACK_ROOT=str(tmp_path / "native-feedback"))
    # Keep product state, CLI validation/CAS, provider transactions and readback
    # real; only share the fixture's public command construction.
    monkeypatch.setattr(feedback, "_loopx", lambda _env, *args: call(*args))
    binding = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)

    def quota(*args):
        return call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
            "--include-detail", "scheduler", "--scan-path", str(project), *args)

    return SimpleNamespace(env=env, call=call, quota=quota, project=project, root=root, registry=registry,
                           binding=binding, temp=tmp_path)


def packet(*, status="running", attribution="unknown", submission="submission-1", run="native-attempt-1"):
    record = dict(accepted_record=True, submission_id=submission, round_id="agent-1",
                  status=status, attribution_status=attribution)
    if attribution == "verified":
        record.update(archive_sha256="a" * 64, result_sha256="b" * 64,
                      result_ref="native-result:" + submission,
                      result_observed_at="2026-10-19T00:00:00Z")
    return dict(schema_version="edgebench_native_feedback_v1", run_id=run,
                task_id="synthetic-task", records=[record])


def waiting(runtime, value=None):
    return feedback.wait_for_feedback(runtime.env, todo_id=fixture.TODO_ID,
        submission_id="submission-1", packet=value or packet())


def todos(runtime):
    return runtime.call("todo", "list", "--goal-id", fixture.GOAL_ID)


def test_pending_native_submission_requires_explicit_dependency_and_preserves_independent_work(runtime):
    pending = packet()
    feedback.synchronize_feedback_waits(runtime.env, pending)
    initial = runtime.quota()
    assert initial["should_run"] and initial["selected_todo"]["todo_id"] == fixture.TODO_ID
    result = waiting(runtime, pending)
    source = next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == result["dependency_todo_id"])
    assert source["claimed_by"] == feedback.FEEDBACK_OBSERVER_AGENT
    assert fixture.AGENT_ID in source["excluded_agents"]
    code, denied = fixture._run_cli(runtime.registry, runtime.root, "quota", "should-run", *runtime.binding,
        "--runtime-profile", "generic_cli", "--todo-id", source["todo_id"], cwd=runtime.project)
    assert code == 1 and denied.get("should_run") is not True
    quiet = runtime.quota()
    assert quiet["should_run"] is False
    assert quiet["effective_action"] == "agent_scope_wait"
    current_work = next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == fixture.TODO_ID)
    assert current_work["status"] == "deferred" and current_work["resume_when"] == result["resume_when"]
    before = todos(runtime)["authority_read"]["provider_revision"]
    assert waiting(runtime, pending) == result
    assert feedback.synchronize_feedback_waits(runtime.env, pending) == {"released": []}
    assert todos(runtime)["authority_read"]["provider_revision"] == before

    independent = runtime.call("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent",
        "--claimed-by", fixture.AGENT_ID, "--task-class", "advancement_task", "--action-kind", "validate",
        "--text", "Validate an independent local mechanism without external feedback.", "--execute")
    ready = runtime.quota()
    assert ready["should_run"] and ready["selected_todo"]["todo_id"] == independent["todo_id"]
    assert not ready.get("vision_wait_state") or ready["vision_wait_state"].get("applied") is not True
    assert next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == fixture.TODO_ID)["resume_when"] == result["resume_when"]


def test_native_wait_stops_actual_upstream_send_under_original_turn(runtime):
    original = runtime.quota("--todo-id", fixture.TODO_ID, "--turn-instance-id", runtime.env["LOOPX_TURN"])
    assert original["should_run"]
    session = SimpleNamespace(session_id="native-feedback-session", goal_ref=None,
        admission=FirstPartyHostGoalAdmission.for_plan(registry_path=runtime.registry,
            goal_id=fixture.GOAL_ID, planned_goal_ref=None))
    wake = runtime.temp / "wake"
    wake.mkdir()
    receipt = {"turn_id": "native-feedback-wake"}
    with _recording_upstream() as (url, upstream):
        env = dict(runtime.env, OPENAI_BASE_URL=url, OPENAI_API_KEY="synthetic-credential", NO_PROXY="127.0.0.1")
        guard = ResearchRequestGuard(env, receipt, wake)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with guard.serve():
                guard.bind(process, env, session, original_quota=original)

                def send():
                    return urllib.request.urlopen(urllib.request.Request(guard.base_url + "/responses",
                        data=b'{"input":"synthetic input"}', headers={"Content-Type": "application/json"}), timeout=45)

                with send() as response:
                    assert json.load(response) == {"ok": True}
                upstream.clear()
                waiting(runtime)
                with pytest.raises(urllib.error.HTTPError) as refused:
                    send()
                assert refused.value.code == 409 and upstream == {}
                process.wait(timeout=10)
                assert guard.refused
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    rows = [json.loads(line) for line in (wake / "provider-requests.jsonl").read_text().splitlines()]
    finished = [row for row in rows if row["event"] == "finished"]
    assert [row["forwarded"] for row in finished] == [True, False]
    assert all(row["loopx_turn_id"] == runtime.env["LOOPX_TURN"] for row in finished)
    assert runtime.env["LOOPX_PHASE_DEADLINE_EPOCH"] == env["LOOPX_PHASE_DEADLINE_EPOCH"]


def test_ambiguous_or_other_attempt_feedback_never_releases_original_wait(runtime, monkeypatch):
    result = waiting(runtime)
    ambiguous = packet()
    ambiguous["records"].append(copy.deepcopy(ambiguous["records"][0]))
    with pytest.raises(ValueError, match="ambiguous"):
        waiting(runtime, ambiguous)
    with pytest.raises(ValueError, match="accepted submission"):
        waiting(runtime, packet(submission="submission-2"))
    current = next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == result["dependency_todo_id"])
    assert current["status"] == "open"
    before = todos(runtime)["authority_read"]["provider_revision"]
    assert feedback.synchronize_feedback_waits(runtime.env, packet(status="completed")) == {"released": []}
    assert todos(runtime)["authority_read"]["provider_revision"] == before
    from benchmark.runtime import research_feedback_sync
    written = []
    monkeypatch.setattr(research_feedback_sync, "write_feedback_finding", lambda *args: written.append(args[2]["submission_id"]))
    other = packet(status="completed", attribution="verified", run="native-attempt-2")
    feedback.synchronize_feedback_waits(runtime.env, other)
    assert written == ["submission-1"]
    assert next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == current["todo_id"])["status"] == "open"
    assert runtime.quota()["should_run"] is False


@pytest.mark.parametrize("native_status", ["completed", "error"])
def test_result_writeback_precedes_wait_release_and_negative_feedback_is_not_blocked(runtime, monkeypatch, native_status):
    result = waiting(runtime)
    released = packet(status=native_status, attribution="verified")
    from benchmark.runtime import research_feedback_sync
    real_writer = research_feedback_sync.write_feedback_finding

    def unavailable(*_args):
        raise RuntimeError("synthetic finding write failure")

    monkeypatch.setattr(research_feedback_sync, "write_feedback_finding", unavailable)
    with pytest.raises(RuntimeError, match="finding write failure"):
        feedback.synchronize_feedback_waits(runtime.env, released)
    assert next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == result["dependency_todo_id"])["status"] == "open"
    assert runtime.quota()["should_run"] is False

    def persisted(*args):
        assert args[3] == result["dependency_todo_id"]
        assert next(todo for todo in todos(runtime)["todos"] if todo["todo_id"] == args[3])["status"] == "open"
        return real_writer(*args)

    monkeypatch.setattr(research_feedback_sync, "write_feedback_finding", persisted)
    observed = feedback.synchronize_feedback_waits(runtime.env, released)
    assert observed["released"][0]["dependency_todo_id"] == result["dependency_todo_id"]
    after = todos(runtime)
    assert next(todo for todo in after["todos"] if todo["todo_id"] == result["dependency_todo_id"])["status"] == "done"
    resume = runtime.quota()
    assert resume["should_run"] and resume["selected_todo"]["todo_id"] == fixture.TODO_ID
    assert resume["effective_action"] == "successor_replan_required"
    context = runtime.call("explore", "turn-context", *runtime.binding)
    [finding] = context["graph"]["writeback_results"]
    assert released["records"][0]["result_ref"] in finding["evidence_refs"]
    assert f'"status": "{native_status}"' in finding["summary"]
    runs = runtime.root / f"goals/{fixture.GOAL_ID}/runs/index.jsonl"
    run_events = map(json.loads, runs.read_text().splitlines()) if runs.exists() else []
    assert not any(row.get("classification") == "quota_slot_spent" for row in run_events)
    assert feedback.synchronize_feedback_waits(runtime.env, released) == {"released": []}
    assert todos(runtime)["authority_read"]["provider_revision"] == after["authority_read"]["provider_revision"]
