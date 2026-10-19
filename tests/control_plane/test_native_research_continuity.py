"""A real probe, native HTTP acceptance, canonical wait and next experiment."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from benchmark.runtime.research_probe import run_probe
from benchmark.runtime.research_feedback_sync import synchronize_native_feedback
from benchmark.runtime.research_submission import (
    mark_dispatched, prepare_submission, record_response, register_probe, synchronize_intents,
)
from benchmark.tests.test_native_research_feedback import (
    LocalCopyBackend, archive_bytes, native_http, probe_record, submit_fixture,
)
from test_research_feedback_wait import runtime as runtime  # real File/SQLite fixture
import test_quota_settlement_cli as fixture


def test_observer_replays_after_result_and_todo_link_commit_but_response_is_lost(runtime, monkeypatch):
    from benchmark.runtime.research_feedback_wait import synchronize_feedback_waits
    from test_research_feedback_wait import packet, waiting, todos
    from loopx.capabilities.explore import result_writeback

    dependency = waiting(runtime)["dependency_todo_id"]
    released = packet(status="completed", attribution="verified")
    released["records"][0]["result_observed_at"] = "2026-01-01T00:01:00Z"
    original = result_writeback.update_goal_todo
    interrupted = []

    def lose_committed_response(**args):
        result = original(**args)
        if args.get("dry_run") is False and not interrupted:
            interrupted.append(True)
            raise RuntimeError("synthetic lost Todo-link response")
        return result

    monkeypatch.setattr(result_writeback, "update_goal_todo", lose_committed_response)
    with pytest.raises(RuntimeError, match="lost Todo-link response"):
        synchronize_feedback_waits(runtime.env, released)
    assert next(row for row in todos(runtime)["todos"] if row["todo_id"] == dependency)["status"] == "open"
    assert not runtime.quota()["should_run"]
    observed = synchronize_feedback_waits(runtime.env, released)
    assert len(observed["released"]) == 1
    context = runtime.call("explore", "turn-context", *runtime.binding)
    assert len(context["graph"]["writeback_results"]) == 1
    assert runtime.quota()["should_run"]
    assert synchronize_feedback_waits(runtime.env, released) == {"released": []}


def test_released_independent_work_gets_a_wake_even_when_a_later_result_needs_retry(runtime, monkeypatch):
    from benchmark.runtime import research_feedback_sync
    from test_research_feedback_wait import packet, waiting, todos

    dependency = waiting(runtime)["dependency_todo_id"]
    released = packet(status="completed", attribution="verified")
    released["records"][0]["result_observed_at"] = "2026-01-01T00:01:00Z"
    released["records"].append({**released["records"][0], "submission_id": "submission-2",
        "round_id": "agent-2", "result_ref": "native-result:submission-2"})
    original = research_feedback_sync.write_feedback_finding
    signal = Path(runtime.env["LOOPX_RESEARCH_FEEDBACK_ROOT"]) / "ingestion" / "wake.json"
    published = []

    def unavailable(env, packet, record, todo_id):
        if record["submission_id"] == "submission-2":
            # The first release must be visible while this consumer still
            # owns the lock, before a later result has returned or failed.
            published.append(json.loads(signal.read_text())["revision"])
            assert runtime.quota()["should_run"]
            raise RuntimeError("synthetic independent result needs retry")
        return original(env, packet, record, todo_id)

    monkeypatch.setattr(research_feedback_sync, "write_feedback_finding", unavailable)
    with pytest.raises(RuntimeError, match="independent result needs retry"):
        synchronize_native_feedback(runtime.env, packet=released)
    assert len(published) == 1 and len(published[0]) == 64
    assert json.loads(signal.read_text())["revision"] == published[0]
    assert next(row for row in todos(runtime)["todos"] if row["todo_id"] == dependency)["status"] == "done"
    assert runtime.quota()["should_run"], "one failing result cannot suppress already released work"


def test_error_archive_and_independent_probe_result_reach_real_writeback(runtime):
    from benchmark.runtime.research_feedback_wait import wait_for_feedback

    native = runtime.temp / "native"
    native.mkdir()
    with native_http(native) as (_state, release, observer, root):
        env = dict(runtime.env, LOOPX_RESEARCH_FEEDBACK_ROOT=str(root))
        register_probe(root, *probe_record())
        invalid = submit_fixture(observer, b"not a gzip archive")
        valid = submit_fixture(observer, archive_bytes({"candidate.py": b"original"}))
        pending = observer.update(LocalCopyBackend(), None)
        wait_for_feedback(env, todo_id=fixture.TODO_ID,
            submission_id=valid["submission_id"], packet=pending)
        assert not runtime.quota()["should_run"]
        observer.after_publish = lambda _backend, _handle, packet: synchronize_native_feedback(env, packet=packet)
        release(invalid["submission_id"], status="error")
        release(valid["submission_id"], score=0)
        completed = observer.update(LocalCopyBackend(), None)
        records = {row["submission_id"]: row for row in completed["records"]}
        error_ref = records[invalid["submission_id"]]["result_ref"]
        valid_ref = records[valid["submission_id"]]["result_ref"]
        assert "candidate_sha256" not in records[invalid["submission_id"]]
        context = runtime.call("explore", "turn-context", *runtime.binding)
        findings = context["graph"]["writeback_results"]
        [error] = [row for row in findings if error_ref in row["evidence_refs"]]
        assert '"status": "error"' in error["summary"] and not error.get("execution_observation")
        valid_findings = [row for row in findings if valid_ref in row["evidence_refs"]]
        assert len(valid_findings) == 2
        [bound] = [row for row in valid_findings if row.get("execution_observation")]
        assert bound["execution_observation"]["candidate_sha256"] == probe_record()[0]["candidate_sha256"]
        assert runtime.quota()["should_run"], "an unrelated invalid archive must not strand released work"
        assert synchronize_native_feedback(env, packet=completed)["released"] == []
        assert runtime.call("explore", "turn-context", *runtime.binding)["graph"]["writeback_results"] == findings


def test_native_result_reaches_replan_and_a_changed_real_experiment(runtime, monkeypatch):
    monkeypatch.chdir(runtime.project)
    native_root = runtime.temp / "native"
    native_root.mkdir()
    runtime.call("configure-goal", "--goal-id", fixture.GOAL_ID,
        "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
    original = runtime.quota("--turn-instance-id", runtime.env["LOOPX_TURN"])
    original_budget = original["quota"]["spent_slots"]
    with native_http(native_root) as (state, release, observer, root):
        env = dict(runtime.env, LOOPX_RESEARCH_FEEDBACK_ROOT=str(root))
        monkeypatch.setenv("LOOPX_RESEARCH_FEEDBACK_ROOT", str(root))
        observer.after_publish = lambda _backend, _handle, packet: synchronize_native_feedback(env, packet=packet)
        candidate = runtime.project / "candidate.py"
        candidate.write_text("import json,os; from pathlib import Path; "
            "Path(os.environ['LOOPX_RESEARCH_DIAGNOSTICS']).write_text(json.dumps("
            "{'search_iterations':2,'stop_reason':'search_cap'})); print(2)")
        measured = run_probe(command=[sys.executable, str(candidate)], candidate_files=[candidate],
            output=runtime.project / "first-probe.json", input_revision="public-local-v1",
            planned_method_id="scheduler", executed_method_id="mask-selection",
            change_reason="A local control isolated mask selection before changing the scheduler.",
            evidence_ref="probe:first", limit_seconds=10, diagnostics=runtime.project / "first-diagnostics.json")
        archive = runtime.project / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": candidate.read_bytes()}))
        intent = prepare_submission(root, archive, wait_todo_id=fixture.TODO_ID,
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        mark_dispatched(root, intent["operation_id"])
        accepted = submit_fixture(observer, Path(intent["archive_path"]).read_bytes())
        record_response(root, intent["operation_id"], 200, accepted)
        # The accepted response can declare the exact dependency before the
        # runner's next history poll. It does not assert result attribution.
        synchronized = synchronize_native_feedback(env)
        assert synchronized["waits"][0]["waiting"]
        pending = observer.update(LocalCopyBackend(), None)
        from loopx.cli_commands.turn_decision import current_heartbeat_host_launch_decision
        quiet = current_heartbeat_host_launch_decision(registry_path=runtime.registry,
            runtime_root=runtime.root, project=runtime.project, goal_id=fixture.GOAL_ID,
            agent_id=fixture.AGENT_ID, turn_instance_id=env["LOOPX_TURN"], original_quota=original)["current"]
        assert not quiet["should_run"]
        assert quiet["quota"]["spent_slots"] == original_budget
        assert not runtime.quota("--turn-instance-id", "another-wake-while-pending")["should_run"]
        assert state["posts"] == 1 and state["result_reads"] == 0

        # Interrupted native operation, newer workspace, then late negative
        # feedback: the journal and accepted archive remain the sole binding.
        candidate.write_text("print('unsubmitted newer version')")
        observer.after_publish = None
        release(accepted["submission_id"], score=0)
        completed = observer.update(LocalCopyBackend(), None)
        assert pending["records"][0]["archive_sha256"] == completed["records"][0]["archive_sha256"]
        first_write = synchronize_native_feedback(env, packet=completed)
        assert first_write["released"] and not first_write["waits"]
        assert json.loads((root / "ingestion" / "wake.json").read_text())["revision"] == first_write["revision"]
        assert candidate.read_text() == "print('unsubmitted newer version')"
        recovered = prepare_submission(root, archive)
        assert recovered == {"action": "recover", "operation_id": intent["operation_id"],
                             "submission_id": accepted["submission_id"]}
        assert state["posts"] == 1
        [current_intent] = synchronize_intents(root, completed)
        assert not current_intent.get("result_observed"), "observer must not forge native operation completion"

        # Observe readiness without minting another work Turn. The original
        # interrupted Turn still needs the current owner's normal recovery.
        ready = runtime.quota()
        assert ready["should_run"] and ready["effective_action"] == "successor_replan_required", {
            key: ready.get(key) for key in ("should_run", "effective_action", "reason", "blocker")}
        assert ready["quota"]["spent_slots"] == original_budget
        context = runtime.call("explore", "turn-context", *runtime.binding)
        finding = next(row for row in context["graph"]["writeback_results"] if row.get("execution_observation"))
        bound = finding["execution_observation"]
        assert bound["candidate_sha256"] == bound["result_candidate_sha256"] == measured["candidate_sha256"]
        assert bound["search_iterations"] == 2
        assert '"score": 0' in finding["summary"]
        assert bound["result_ref"] == completed["records"][0]["result_ref"]

        runtime.call("todo", "update", *runtime.binding, "--todo-id", fixture.TODO_ID,
            "--status", "open", "--clear-resume-when", "--reason", "Use the released source-bound result in the resumed experiment.")
        decision = runtime.project / "resume-decision.json"
        decision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "active",
            "vision_patch": {"advancement_policy": "as_needed", "vision_summary": "Vary the measured search cap while retaining the incumbent.",
                "acceptance_summary": "Execute the declared local control before attributing any scheduler benefit."},
            "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
                "prior_assumption": "The scheduler proposal was expected to explain improvement.",
                "observed_reality": "The archived mask candidate stopped at two search steps and its native result was zero.",
                "retained": ["Original objective, best validated candidate and original budget."],
                "changed": ["Test five search steps before changing the scheduler."],
                "evidence_refs": [finding["finding_id"], bound["result_ref"]]}}))
        next_turn = env["LOOPX_TURN"]
        admitted = runtime.quota("--turn-instance-id", next_turn)
        assert admitted["should_run"] and admitted["effective_action"] == "normal_run", {
            key: admitted.get(key) for key in ("should_run", "effective_action", "unsettled_host_turn_recovery")}
        assert admitted["heartbeat_receipt"]["settlement_identity"] == original["heartbeat_receipt"]["settlement_identity"]
        candidate.write_text("import json,os; from pathlib import Path; "
            "Path(os.environ['LOOPX_RESEARCH_DIAGNOSTICS']).write_text(json.dumps("
            "{'search_iterations':5,'stop_reason':'local_cases_complete'})); print(5)")
        next_probe = run_probe(command=[sys.executable, str(candidate)], candidate_files=[candidate],
            output=runtime.project / "next-probe.json", input_revision="public-local-v1",
            planned_method_id="mask-selection", executed_method_id="mask-selection",
            evidence_ref="probe:next", limit_seconds=10, diagnostics=runtime.project / "next-diagnostics.json")
        assert next_probe["search_iterations"] == 5 and next_probe["candidate_sha256"] != measured["candidate_sha256"]
        assert (runtime.project / "next-probe.json.stdout").read_text().strip() == "5"
        followthrough = runtime.project / "followthrough.json"
        followthrough.write_text(json.dumps({"schema_version": "explore_result_attachment_v0",
            "question": "Did the declared search-cap control actually execute?",
            "applicability": "This synthetic local case and the declared selected candidate files.",
            "input_revision": next_probe["input_revision"], "observation": "The modified process executed five search steps and exited successfully.",
            "interpretation": "The feedback-led next action ran as declared; improvement on native evaluation remains unknown.",
            "status": "tentative", "evidence_refs": next_probe["evidence_refs"], "execution_observation": next_probe}))
        runtime.call("refresh-state", *runtime.binding, "--todo-id", fixture.TODO_ID,
            "--turn-instance-id", next_turn, "--classification", "native_feedback_replan_executed",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--agent-vision-json", str(decision), "--explore-result-json", str(followthrough),
            "--no-global-sync", "--suppress-external-sinks")
        runtime.call("quota", "spend-slot", *runtime.binding, "--todo-id", fixture.TODO_ID,
            "--turn-instance-id", next_turn, "--slots", "1", "--source", "heartbeat", "--execute")
        assert synchronize_native_feedback(env, packet=completed)["released"] == []
        after = runtime.quota()
        assert after["quota"]["spent_slots"] == original_budget + 1
        assert after["effective_action"] != "unsettled_host_turn_recovery"
        assert state["posts"] == 1, "a new local experiment does not consume a native evaluation"
