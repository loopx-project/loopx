"""Synthetic real-process/CLI evidence for stage, candidate and result continuity."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmark.runtime.research_probe import run_probe
from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture


def probe(tmp_path, monkeypatch, *, source=None, **options):
    monkeypatch.chdir(tmp_path)
    candidate = tmp_path / "candidate.py"
    candidate.write_text(source or "import json,os; from pathlib import Path; "
        "Path(os.environ['LOOPX_RESEARCH_DIAGNOSTICS']).write_text(json.dumps("
        "{'search_iterations': 2, 'stop_reason': 'search_cap'})); print(sum(x*x for x in (1,2)))")
    return run_probe(command=[sys.executable, str(candidate)], candidate_files=[candidate],
        output=tmp_path / "observation.json", input_revision="synthetic-input-v1",
        planned_method_id="scheduler", executed_method_id="mask-selection",
        change_reason="The measured control motivated a different first intervention.",
        evidence_ref="probe:local-1", limit_seconds=options.pop("limit_seconds", 30),
        diagnostics=tmp_path / "diagnostics.json", **options)


def test_real_probe_reports_early_exit_not_budget_exhaustion(tmp_path, monkeypatch):
    observation = probe(tmp_path, monkeypatch)
    assert observation["search_iterations"] == 2
    assert 0 < observation["elapsed_seconds"] < observation["limit_seconds"]
    assert observation["stop_reason"] == "process_exit:0; solver_report:search_cap"
    assert len(observation["candidate_sha256"]) == 64
    assert (tmp_path / "observation.json.stdout").read_text().strip() == "5"


def test_missing_solver_telemetry_stays_unknown_and_timeout_is_host_observed(tmp_path, monkeypatch):
    observation = probe(tmp_path, monkeypatch, source="import time; time.sleep(10)", limit_seconds=0.05)
    assert observation["search_iterations"] is None
    assert observation["stop_reason"] == "host_timeout"
    assert observation["elapsed_seconds"] >= 0.05


def test_probe_refuses_changed_candidate_and_never_reuses_existing_evidence(tmp_path, monkeypatch):
    feedback_root = tmp_path / "feedback"
    monkeypatch.setenv("LOOPX_RESEARCH_FEEDBACK_ROOT", str(feedback_root))
    with pytest.raises(ValueError, match="candidate changed"):
        probe(tmp_path, monkeypatch, source="from pathlib import Path; Path(__file__).write_text('print(0)')")
    before = (tmp_path / "observation.json").read_bytes()
    with pytest.raises(FileExistsError):
        probe(tmp_path, monkeypatch)
    assert (tmp_path / "observation.json").read_bytes() == before
    assert not feedback_root.exists(), "a changed/unexecuted candidate cannot register as a completed probe"


@pytest.mark.parametrize("reported", [
    "{",
    '{"search_iterations":"unknown","stop_reason":"search_cap"}',
    '{"search_iterations":2,"stop_reason":{"unexpected":"value"}}',
    '{"unexpected":"do-not-retain-raw-diagnostic-body"}',
    "x" * 4097,
])
def test_invalid_optional_diagnostics_preserve_host_measurements_and_bounded_error_evidence(tmp_path, monkeypatch, reported):
    observed = probe(tmp_path, monkeypatch, source=(
        "import os; from pathlib import Path; "
        f"Path(os.environ['LOOPX_RESEARCH_DIAGNOSTICS']).write_text({reported!r}); "
        "print('work completed'); raise SystemExit(3)"))
    assert observed["elapsed_seconds"] > 0
    assert observed["exit_code"] == 3 and observed["stop_reason"] == "process_exit:3"
    assert observed["search_iterations"] is None
    assert (tmp_path / "observation.json.stdout").read_text().strip() == "work completed"
    assert json.loads((tmp_path / "observation.json").read_text()) == observed
    raw_error = (tmp_path / "observation.json.diagnostics-error.json").read_bytes()
    assert len(raw_error) < 1024 and b"do-not-retain-raw-diagnostic-body" not in raw_error
    detail = json.loads(raw_error)
    assert detail["source"] == "solver_diagnostics"
    assert detail["candidate_sha256"] == observed["candidate_sha256"]
    assert "diagnostics-error:" + hashlib.sha256(raw_error).hexdigest() in observed["evidence_refs"]


def test_invalid_solver_report_cannot_hide_a_host_timeout(tmp_path, monkeypatch):
    observed = probe(tmp_path, monkeypatch, source=(
        "import os,time; from pathlib import Path; "
        "Path(os.environ['LOOPX_RESEARCH_DIAGNOSTICS']).write_text('{'); time.sleep(10)"), limit_seconds=1)
    assert observed["stop_reason"] == "host_timeout" and observed["exit_code"] < 0
    assert observed["search_iterations"] is None and observed["elapsed_seconds"] >= 1
    assert (tmp_path / "observation.json.diagnostics-error.json").is_file()


def test_probe_cli_preserves_a_failed_candidate_exit_and_its_observation(tmp_path):
    candidate = tmp_path / "candidate.py"
    candidate.write_text("raise SystemExit(3)")
    output = tmp_path / "observed.json"
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.research_probe",
        "--candidate-file", str(candidate), "--output", str(output), "--input-revision", "fixture-v1",
        "--planned-method-id", "baseline", "--executed-method-id", "baseline",
        "--evidence-ref", "probe:failed", "--limit-seconds", "5", "--", sys.executable, str(candidate)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(fixture.REPO_ROOT)}, capture_output=True, text=True)
    assert result.returncode == 3, result.stderr
    observed = json.loads(output.read_text())
    assert observed["exit_code"] == 3 and observed["search_iterations"] is None
    assert json.loads(output.with_suffix(".json.candidate.json").read_text())["candidate_sha256"] == observed["candidate_sha256"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_requested_followup_uses_ordinary_todos_and_execution_reaches_next_replan(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["quota"]["allowed_slots"] = 12
    registry.write_text(json.dumps(configuration))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    monkeypatch.chdir(project)

    def call(*args, expected=0):
        code, result = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == expected, json.dumps({key: result.get(key) for key in (
            "ok", "error", "error_code", "effective_action", "reason", "blocker", "autonomous_replan_obligation")})
        return result

    call("configure-goal", "--goal-id", fixture.GOAL_ID, "--execution-replan-after-turns", "2",
        "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
    # The actor records requested work through ordinary Todo operations. The
    # observation producer neither creates these stages nor imposes a strategy.
    root = call("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent",
        "--claimed-by", fixture.AGENT_ID, "--task-class", "advancement_task",
        "--action-kind", "validate", "--status", "deferred",
        "--resume-when", "todo_done:" + fixture.TODO_ID,
        "--text", "Compare the requested local mechanism after validating the supplied candidate.")
    call("todo", "update", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
        "--todo-id", fixture.TODO_ID, "--successor-todo-id", root["todo_id"], "--execute")
    call("todo", "complete", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
         "--todo-id", fixture.TODO_ID)
    binding = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    current = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--turn-instance-id", "research-after-candidate", "--scan-path", str(project))
    assert current["should_run"]
    assert current["selected_todo"]["todo_id"] == root["todo_id"]
    assert current.get("goal_terminal_state") is None
    assert current["effective_action"] == "successor_replan_required"
    # Follow the existing resume action. Readiness is not itself a durable
    # open-state transition and must not be treated as completed research.
    call("todo", "update", *binding, "--todo-id", root["todo_id"], "--status", "open",
        "--clear-resume-when", "--reason", "The initial candidate stage completed; perform the retained research work.")
    scope = project / "research-scope.json"
    scope.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {"advancement_policy": "as_needed", "vision_summary": "The retained research work is now runnable.",
            "acceptance_summary": "Compare actual mechanisms and resources against the original objective."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "Research work awaits the initial candidate stage.",
            "observed_reality": "The canonical candidate stage is done and its retained research successor is open.",
            "retained": ["Original objective, resource limits and best candidate."],
            "changed": ["Run the retained research work."], "evidence_refs": ["todo:" + fixture.TODO_ID]}}))
    preparation = call("refresh-state", *binding, "--todo-id", root["todo_id"], "--turn-instance-id", "research-after-candidate",
        "--classification", "successor_replan_recorded", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(scope),
        "--no-global-sync", "--suppress-external-sinks")
    assert json.loads(Path(preparation["json_path"]).read_text())["vision_checkpoint"]["satisfied"]
    call("quota", "spend-slot", *binding, "--todo-id", root["todo_id"], "--turn-instance-id", "research-after-candidate",
        "--slots", "1", "--source", "heartbeat", "--execute")
    work_turn = "research-local-probe"
    work_guard = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", root["todo_id"], "--turn-instance-id", work_turn, "--scan-path", str(project))
    assert work_guard["should_run"] and work_guard["effective_action"] == "normal_run"
    call("configure-goal", "--goal-id", fixture.GOAL_ID, "--explore-graph-enabled", "--execute")
    observed = probe(project, monkeypatch)
    observed.update(result_ref="result:released-1", result_candidate_sha256=observed["candidate_sha256"])
    observed["evidence_refs"].append("result:released-1")
    observed["evidence_refs"].sort()
    attachment = {"schema_version": "explore_result_attachment_v0", "question": "Which mechanism improves the bounded case?",
        "applicability": "Synthetic public local inputs only", "input_revision": observed["input_revision"],
        "observation": "The process stopped after two search steps with allowance remaining.",
        "interpretation": "Retain the incumbent; change the search cap before claiming scheduler benefit.",
        "status": "tentative", "evidence_refs": observed["evidence_refs"], "execution_observation": observed}
    path = project / "result.json"
    path.write_text(json.dumps(attachment))
    result = call("refresh-state", *binding, "--todo-id", root["todo_id"],
        "--turn-instance-id", work_turn, "--classification", "validated_probe",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--vision-unchanged-reason", "Original research remains open; the changed intervention needs validation.",
        "--explore-result-json", str(path), "--no-global-sync", "--suppress-external-sinks")
    assert result["explore_result_delivery"]["ok"]
    context = call("explore", "turn-context", *binding)
    [finding] = context["graph"]["writeback_results"]
    assert finding["execution_observation"] == observed
    from loopx.capabilities.explore.result_log import explore_result_log_path, load_explore_result_events_strict, explore_history_rows
    rows = explore_history_rows(load_explore_result_events_strict(explore_result_log_path(runtime, fixture.GOAL_ID),
                                goal_id=fixture.GOAL_ID), goal_id=fixture.GOAL_ID)
    assert next(row for row in rows if row["event_kind"] == "finding")["execution_observation"] == observed
    # Late feedback for another snapshot must not be attached to this execution.
    attachment["execution_observation"]["result_candidate_sha256"] = "b" * 64
    path.write_text(json.dumps(attachment))
    bad = call("refresh-state", *binding, "--todo-id", root["todo_id"],
        "--turn-instance-id", work_turn, "--classification", "validated_probe",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(path), "--no-global-sync", "--suppress-external-sinks", expected=1)
    assert not bad.get("appended")
    call("quota", "spend-slot", *binding, "--todo-id", root["todo_id"],
        "--turn-instance-id", work_turn, "--slots", "1", "--source", "heartbeat", "--execute")
    review = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--turn-instance-id", "review-resource-evidence", "--scan-path", str(project))
    assert "replan_action_packet" in review, {key: review.get(key) for key in (
        "should_run", "effective_action", "reason", "autonomous_replan_obligation", "effective_turn_replan")}
    control = review["replan_action_packet"]["stride_control"]
    delivered = next(item for item in control["explore_basis"]["finding_refs"] if item["finding_id"] == finding["finding_id"])
    assert delivered["execution_observation"]["candidate_sha256"] == observed["candidate_sha256"]
    assert delivered["execution_observation"]["search_iterations"] == 2
    # The actor uses delivered measurements, then runs a changed implementation;
    # a new finding/identifier alone is not the observable outcome.
    assert delivered["execution_observation"]["elapsed_seconds"] < delivered["execution_observation"]["limit_seconds"]
    next_work = call("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent",
        "--claimed-by", fixture.AGENT_ID, "--task-class", "advancement_task", "--action-kind", "validate",
        "--target-key", "search-cap-control", "--replan-obligation-id", review["autonomous_replan_obligation"]["obligation_id"],
        "--text", "Vary the measured search cap while retaining the incumbent and the original method claim.")
    assert next_work["todo_id"] != root["todo_id"]
    review = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", next_work["todo_id"], "--turn-instance-id", "review-resource-evidence", "--scan-path", str(project))
    control = review["replan_action_packet"]["stride_control"]
    decision = control["minimal_example"]
    decision["explore_review"] = {"revision": control["explore_basis"]["revision"],
        "disposition": "used", "considered_refs": [finding["finding_id"]],
        "reason": "The executed mask candidate stopped at its search cap, not at the resource limit."}
    path.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Validate a changed search cap against the retained reference."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "A new scheduler was expected to explain the gain.",
            "observed_reality": "The executed artifact uses mask selection and stops after two search steps.",
            "changed": ["Vary the search cap before attributing gain to a scheduler."],
            "evidence_refs": [finding["finding_id"]]},
        "stride_decision": decision, "todo_delta": ["create:" + next_work["todo_id"]]}))
    replan_binding = (*binding, "--replan-obligation-id", review["autonomous_replan_obligation"]["obligation_id"],
                      "--turn-instance-id", "review-resource-evidence")
    accepted = call("refresh-state", *replan_binding, "--classification", "resource_probe_replan",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--agent-vision-json", str(path), "--no-global-sync", "--suppress-external-sinks")
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["accepted"]
    call("quota", "spend-slot", *replan_binding, "--slots", "1", "--source", "heartbeat", "--execute")
    admitted = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", next_work["todo_id"], "--turn-instance-id", "execute-search-control", "--scan-path", str(project))
    assert admitted["should_run"] and admitted["selected_todo"]["todo_id"] == next_work["todo_id"]
    candidate = project / "candidate.py"
    candidate.write_text("import json,os; from pathlib import Path; "
        "Path(os.environ['LOOPX_RESEARCH_DIAGNOSTICS']).write_text(json.dumps("
        "{'search_iterations': 5, 'stop_reason': 'local_cases_complete'})); print(1)")
    revised = run_probe(command=[sys.executable, str(candidate)], candidate_files=[candidate],
        output=project / "next-observation.json", input_revision="synthetic-input-v1",
        planned_method_id="mask-selection", executed_method_id="mask-selection", evidence_ref="probe:local-2",
        limit_seconds=30, diagnostics=project / "next-diagnostics.json")
    assert revised["candidate_sha256"] != observed["candidate_sha256"]
    assert revised["search_iterations"] == 5
    assert (project / "next-observation.json.stdout").read_text().strip() == "1"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_ordinary_entry_observes_an_existing_candidate_with_stride_off_and_preserves_stops(tmp_path, monkeypatch, provider):
    """Cross the adapter, real authority/CLI and process boundaries without a research stage."""
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor
    from benchmark.runtime.worker import heartbeat_body
    from benchmark.runtime.research_probe import execution_observation_guidance
    from loopx.capabilities.explore.result_log import (
        explore_result_log_path, load_explore_result_events_strict, explore_history_rows,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    task = "Validate the supplied candidate once against the original correctness and resource requirements."
    state = project / f".codex/goals/{fixture.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("Settle one standard Codex App delivery.", task))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    monkeypatch.chdir(project)
    candidate = project / "candidate.py"
    candidate.write_text("print(sum(x*x for x in (1, 2)))\n")
    original_candidate = candidate.read_bytes()
    control = tmp_path / "control"
    control.mkdir()
    cli_path = tmp_path / "loopx"
    cli_path.write_text(f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n")
    cli_path.chmod(0o755)
    for name, value in {"_CONTROL": control, "_TASK_DOC": control / "task.md",
        "_REGISTRY": registry, "_LOOPX_RUNTIME": runtime, "_CLI": cli_path,
        "_SRC": fixture.REPO_ROOT}.items():
        monkeypatch.setattr(harbor, name, str(value))

    def call(*args):
        code, result = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, result
        return result

    class Environment:
        async def exec(self, command, *, env=None, cwd=None, **kwargs):
            result = subprocess.run(["/bin/bash", "-c", command], env=env,
                cwd=cwd, text=True, capture_output=True, timeout=90)
            return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)

        async def upload_file(self, source, target):
            Path(target).write_bytes(Path(source).read_bytes())

    agent = harbor.BenchmarkCodex(logs_dir=tmp_path / "logs", model_name="fixture",
        goal_id=fixture.GOAL_ID, agent_id=fixture.AGENT_ID,
        execution_observation="local_probe", stride_strategy="off")
    env = dict(os.environ)
    monkeypatch.setattr(agent, "_profile_env", lambda: env)
    environment = Environment()
    asyncio.run(agent._prepare_phase(environment, task, cwd=str(project)))
    worker_env = agent._worker_env(cwd=str(project))
    assert worker_env["LOOPX_EXECUTION_OBSERVATION"] == "local_probe"
    assert "LOOPX_RESEARCH_LOOP" not in worker_env
    binding = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    listed = call("todo", "list", "--goal-id", fixture.GOAL_ID, "--role", "agent")
    assert [todo["todo_id"] for todo in listed["todos"]] == [fixture.TODO_ID]
    assert all(todo.get("target_key") != "research-continuation" for todo in listed["todos"])
    configured = call("configure-goal", "--goal-id", fixture.GOAL_ID)
    assert configured["after"]["execution_profile"]["stride_strategy"] == "off"
    assert candidate.read_bytes() == original_candidate
    call("configure-goal", "--goal-id", fixture.GOAL_ID, "--explore-graph-enabled", "--execute")
    work_turn = "ordinary-local-observation"
    guard = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", fixture.TODO_ID, "--turn-instance-id", work_turn)
    assert guard["should_run"] and guard["selected_todo"]["todo_id"] == fixture.TODO_ID
    prompt = heartbeat_body(worker_env, work_turn, native_goal=False)
    assert prompt.count(execution_observation_guidance()) == 1
    assert "Research-loop execution evidence:" not in prompt
    output = project / "ordinary-observation.json"
    observed = run_probe(command=[sys.executable, str(candidate)], candidate_files=[candidate],
        output=output, input_revision="synthetic-input-v1", planned_method_id="supplied-candidate",
        executed_method_id="supplied-candidate", evidence_ref="probe:ordinary/1", limit_seconds=30)
    assert observed["search_iterations"] is None and observed["exit_code"] == 0
    assert output.with_suffix(".json.stdout").read_text().strip() == "5"
    attachment = {"schema_version": "explore_result_attachment_v0", "question": "Does the supplied candidate satisfy this bounded check?",
        "applicability": "Synthetic public inputs only", "input_revision": observed["input_revision"],
        "observation": "The existing candidate returned five; unreported search counts remain unknown.",
        "interpretation": "Retain this scoped check without inventing further optimization work.",
        "status": "tentative", "evidence_refs": observed["evidence_refs"], "execution_observation": observed}
    attachment_path = project / "ordinary-result.json"
    attachment_path.write_text(json.dumps(attachment))
    result = call("refresh-state", *binding, "--todo-id", fixture.TODO_ID,
        "--turn-instance-id", work_turn, "--classification", "validated_probe",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--vision-state", "active", "--vision-summary", task,
        "--vision-acceptance", "Retain measured evidence against the original requested requirements.",
        "--explore-result-json", str(attachment_path), "--no-global-sync", "--suppress-external-sinks")
    assert result["explore_result_delivery"]["ok"]
    context = call("explore", "turn-context", *binding)
    [finding] = context["graph"]["writeback_results"]
    assert finding["execution_observation"] == observed
    history = explore_result_log_path(runtime, fixture.GOAL_ID)
    rows = explore_history_rows(load_explore_result_events_strict(history, goal_id=fixture.GOAL_ID), goal_id=fixture.GOAL_ID)
    assert next(row for row in rows if row["event_kind"] == "finding")["execution_observation"] == observed
    evidence_bytes, history_bytes = output.read_bytes(), history.read_bytes()
    call("quota", "spend-slot", *binding, "--todo-id", fixture.TODO_ID,
        "--turn-instance-id", work_turn, "--slots", "1", "--source", "heartbeat", "--execute")

    # A new input phase is not evidence that a real wait or an explicit Stop ended.
    resume_when = "resume_at:2099-01-01T00:00:00Z"
    call("todo", "update", *binding, "--todo-id", fixture.TODO_ID, "--status", "deferred",
        "--resume-when", resume_when, "--reason", "Wait for the scheduled external observation.", "--execute")
    call("configure-goal", "--goal-id", fixture.GOAL_ID, "--waiting-on", "external_evidence", "--execute")
    asyncio.run(agent._prepare_phase(environment, "Additional context for the same task.", cwd=str(project)))
    assert json.loads(registry.read_text())["goals"][0]["waiting_on"] == "external_evidence"
    waiting_todo = call("todo", "list", *binding, "--todo-id", fixture.TODO_ID)["todo"]
    assert waiting_todo["status"] == "deferred" and waiting_todo["resume_when"] == resume_when
    call("todo", "update", *binding, "--todo-id", fixture.TODO_ID, "--status", "open",
        "--clear-resume-when", "--reason", "The fixture now supplies the requested observation.", "--execute")
    call("configure-goal", "--goal-id", fixture.GOAL_ID, "--clear-waiting-on", "--quota-compute", "0", "--execute")
    asyncio.run(agent._prepare_phase(environment, "Context received while stopped.", cwd=str(project)))
    stopped = call("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--turn-instance-id", "ordinary-still-stopped")
    assert stopped["should_run"] is False and stopped["normal_delivery_allowed"] is False
    retained = call("todo", "list", *binding, "--todo-id", fixture.TODO_ID)
    assert retained["authority_read"]["source_authority"] == f"{provider}_v0"
    assert retained["todo"]["status"] == "open" and not retained["todo"].get("successor_todo_ids")
    assert [todo["todo_id"] for todo in call("todo", "list", "--goal-id", fixture.GOAL_ID,
        "--role", "agent")["todos"]] == [fixture.TODO_ID]
    assert candidate.read_bytes() == original_candidate
    assert output.read_bytes() == evidence_bytes and history.read_bytes() == history_bytes
