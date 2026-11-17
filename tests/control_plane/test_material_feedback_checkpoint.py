"""Released feedback closes a segment without closing its continuing Todo.

The actor is deterministic. Real CLI, File/SQLite authority, settlement,
Explore, Stride and local evaluations run; model adoption is a separate check.
"""
from __future__ import annotations

import hashlib
from itertools import product
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from benchmark.runtime.research_probe import run_probe
from canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.codex_checkpoint_fixture import selected_context_values
import test_quota_settlement_cli as fixture


@pytest.mark.parametrize(("provider", "strategy", "handoff_mode", "preexisting_feedback"), [
    (*case, False) for case in product(["file", "sqlite"], ["off", "evidence_conditioned_v1"], ["soft_claim", "hard_lease"])
] + [pytest.param("sqlite", "evidence_conditioned_v1", "hard_lease", True, id="historical-feedback-is-context")])
def test_feedback_checkpoint_reaches_next_actual_experiment(
    tmp_path, monkeypatch, provider, strategy, handoff_mode, preexisting_feedback,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["quota"]["allowed_slots"] = 8
    registry.write_text(json.dumps(configuration))
    state = project / f".codex/goals/{fixture.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("Settle one standard Codex App delivery.",
        "Minimize route cost on the supplied graph while retaining a valid route. "
        "A feasible candidate is a checkpoint; stop when no useful authorized comparison remains."))
    fixture._configure_completion_validation_todo(project)
    fixture._initialize_fixture_authority(project, runtime, provider=provider, handoff_mode=handoff_mode)
    monkeypatch.chdir(project)
    binding = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)

    def cli(*args):
        code, packet = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, json.dumps(packet)
        return packet

    def execute(command, *, expected_code=0, **values):
        replacements = {f"<{name}>": str(value) for name, value in values.items()}
        argv = [replacements.get(word, word) for word in shlex.split(command)]
        assert not any(word.startswith("<") for word in argv), argv
        code, packet = fixture._run_generated_cli(shlex.join(argv), registry_path=registry, cwd=project)
        assert code == expected_code, json.dumps(packet)
        return packet

    def read_stage(command):
        argv = shlex.split(command)
        assert argv[:3] == [sys.executable, "-m", "loopx.entrypoint"]
        assert argv[3:8] == ["--format", "json", "quota", "replan-view", "--decision-file"]
        result = subprocess.run(argv, cwd=project, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    cli("configure-goal", "--goal-id", fixture.GOAL_ID, "--execution-replan-after-turns", "1",
        "--execution-stride-strategy", strategy, "--explore-graph-enabled", "--execute")
    if handoff_mode == "hard_lease":
        claimed = cli("todo", "claim", *binding, "--todo-id", fixture.TODO_ID,
            "--claimed-by", fixture.AGENT_ID, "--claim-operation-id", "route-original-execution")
        assert claimed["execution_managed"] and claimed["claim_operation_id"] == "route-original-execution"
        lease_before = cli("task-lease", "inspect", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID)
        assert lease_before["active"]
    route = project / "route.json"
    graph = {"start": {"detour": 1, "middle": 1, "finish": 1},
             "detour": {"middle": 1}, "middle": {"finish": 1}, "finish": {}}
    (project / "graph.json").write_text(json.dumps(graph))
    validator = project / "evaluate.py"
    validator.write_text(
        "import json\nfrom pathlib import Path\n"
        "g=json.loads(Path('graph.json').read_text()); r=json.loads(Path('route.json').read_text())\n"
        "assert r[0]=='start' and r[-1]=='finish' and len(r)==len(set(r))\n"
        "cost=sum(g[a][b] for a,b in zip(r,r[1:]))\n"
        "print(json.dumps({'valid':True,'cost':cost}))\n")
    route.write_text(json.dumps(["start", "detour", "middle", "finish"] if preexisting_feedback
                               else ["start", "middle", "finish"]))
    if preexisting_feedback:
        previous = subprocess.run([sys.executable, str(validator)], capture_output=True, text=True, check=True)
        prior = project / "released-before-admission.json"
        prior.write_text(previous.stdout)
        assert json.loads(prior.read_text()) == {"valid": True, "cost": 3}
    guard = cli("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", fixture.TODO_ID, "--turn-instance-id", "route-feedback-1")
    assert guard["should_run"] and guard["selected_todo"]["todo_id"] == fixture.TODO_ID
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    spend_precondition = plan["ordered_steps"][2]["precondition"]
    assert "exact original-identity durable writeback receipt" in spend_precondition
    assert "fresh open-Todo semantic checkpoint" in spend_precondition
    writeback = plan["ordered_steps"][1]
    checkpoint = writeback["semantic_closeout_command_template"]
    assert "--delivery-boundary semantic_closeout" in checkpoint and "<boundary>" not in checkpoint
    assert writeback["vision_authoring"]["continuation_review"]
    # Merely obtaining the new executable projection performs no work or writeback.
    assert not (runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl").exists()
    if preexisting_feedback:
        guidance = writeback["precondition"]
        assert "not this Turn's progress; use them to advance the task first" in guidance
        repeated = run_probe(command=[sys.executable, str(validator)], candidate_files=[route],
            output=project / "prior-recheck.json", input_revision="graph-v1", planned_method_id="retained-route",
            executed_method_id="retained-route", evidence_ref="probe:prior-recheck", limit_seconds=10)
        assert json.loads((project / "prior-recheck.json.stdout").read_text()) == json.loads(prior.read_text())
        # Reading and deterministically reproducing the known result adds no
        # decision evidence here. Advance the actual route before checkpointing.
        assert not (runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl").exists()
        assert fixture._spend_run_count(runtime) == 0
        route.write_text(json.dumps(["start", "middle", "finish"]))
    observed = run_probe(command=[sys.executable, str(validator)], candidate_files=[route],
        output=project / "probe-1.json", input_revision="graph-v1", planned_method_id="retained-route",
        executed_method_id="retained-route", evidence_ref="probe:route-1", limit_seconds=10)
    first = json.loads((project / "probe-1.json.stdout").read_text())
    assert first == {"valid": True, "cost": 2}
    if preexisting_feedback:
        assert observed["candidate_sha256"] != repeated["candidate_sha256"]
        assert json.loads(prior.read_text())["cost"] > first["cost"]
    evidence = "result:" + hashlib.sha256((project / "probe-1.json.stdout").read_bytes()).hexdigest()
    observed.update(result_ref=evidence, result_candidate_sha256=observed["candidate_sha256"])
    observed["evidence_refs"].append(evidence)
    result = project / "released-result.json"
    result.write_text(json.dumps({"schema_version": "explore_result_attachment_v0",
        "question": "Can an allowed route reduce the original cost?", "input_revision": "graph-v1",
        "applicability": "Only the supplied public synthetic graph.",
        "observation": "The independently evaluated feasible route costs two.",
        "interpretation": "The allowed direct edge is an informative next comparison; feasibility alone is not closeout.",
        "status": "tentative", "evidence_refs": observed["evidence_refs"], "execution_observation": observed}))
    writeback_words = shlex.split(checkpoint)
    result_command = shlex.join([
        *writeback_words[:writeback_words.index("--agent-vision-json")],
        "--explore-result-json", str(result),
        "--delivery-read-context", guard["delivery_result_context"]["read_context_id"],
        "--no-global-sync", "--suppress-external-sinks",
    ])
    rejected_command = result_command
    for key, value in dict(validated_progress="bookkeeping", scale="single_surface",
                           outcome="surface_only").items():
        rejected_command = rejected_command.replace("<" + key + ">", shlex.quote(str(value)))
    rejected_code, rejected = fixture._run_generated_cli(rejected_command, registry_path=registry, cwd=project)
    assert rejected_code == 1 and not rejected.get("appended")
    assert fixture._spend_run_count(runtime) == 0
    fields = dict(validated_progress="route_feedback_verified", scale="implementation", outcome="outcome_progress")
    retained = execute(result_command, **fields)
    assert retained["settlement_identity"] == plan["identity"]
    assert retained["vision_checkpoint"]["satisfied"] is False
    spend_command = plan["ordered_steps"][2]["command_template"]
    code, premature_spend = fixture._run_generated_cli(spend_command, registry_path=registry, cwd=project)
    assert code == 1 and premature_spend["appended"] is False, json.dumps(premature_spend)
    assert fixture._spend_run_count(runtime) == 0
    direction = cli("checkpoint-context", *binding, "--purpose", "supplement_checkpoint",
        "--todo-id", fixture.TODO_ID, "--turn-instance-id", plan["identity"]["turn_instance_id"])
    assert direction["settlement_identity"] == plan["identity"]
    assert direction["basis"]["todo"]["status"] == "open"
    patch = {"vision_summary": "Minimize route cost under the original graph and authority.",
        "acceptance_summary": "Retain a valid route and compare useful allowed alternatives before closeout.",
        "advancement_policy": "repeat_until_closed"}
    vision = project / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": patch, "todo_delta": ["retain:" + fixture.TODO_ID],
        "path_delta": {"outcome": "continue", "prior_assumption": "The retained route is feasible.",
            "observed_reality": "Released validation confirms cost two; the permitted direct edge is untested.",
            "retained": ["Original optimization objective and current feasible route."], "evidence_refs": [evidence]}}))
    direction_command = checkpoint + " " + shlex.join([
        "--checkpoint-read-context", direction["read_context_id"],
        "--no-global-sync", "--suppress-external-sinks",
    ])
    refreshed = execute(direction_command, **fields, file=vision)
    execute(direction_command, **fields, file=vision)
    assert fixture._classification_count(runtime, "route_feedback_verified") == 1
    persisted = json.loads(Path(refreshed["json_path"]).read_text())
    assert persisted["vision_checkpoint"]["delivery_boundary"] == "semantic_closeout"
    assert persisted["agent_vision"]["path_delta"]["evidence_refs"] == [evidence]
    execute(spend_command)
    execute(spend_command)
    assert fixture._spend_run_count(runtime) == 1
    retained_todo = cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID)["todo"]
    assert retained_todo["status"] == "open" and retained_todo["completion_validation_required"]
    if handoff_mode == "hard_lease":
        lease_after = cli("task-lease", "inspect", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID)
        assert lease_after["lease"]["idempotency_key"] == lease_before["lease"]["idempotency_key"]
        assert lease_after["lease"]["lease_epoch"] == lease_before["lease"]["lease_epoch"]
    # A new Host wake has a new Turn and the same Goal/Agent/Todo lane. The
    # ordinary cadence reads the material result; raw experiment counts do not.
    review = cli("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--turn-instance-id", "route-review")
    assert any(t["kind"] == "periodic_review_due" for t in review["autonomous_replan_obligation"]["triggers"])
    selection = review["interaction_contract"]["cli_channel"]["selection_command"]
    projected_review = execute(selection["route_prefix"] + " "
        + selection["command_args_template"].format(todo_id=fixture.TODO_ID))
    # The ordinary selection route delivers staged readers. Follow their
    # captured review/evidence/writer rather than expecting the raw guard shape.
    reviewed = read_stage(projected_review["replan_action_packet"]["review"])
    writer = read_stage(reviewed["next"]["writeback"])
    assert writer["context_id"] == reviewed["context_id"]
    review_plan = writer["writeback"]["settlement_plan"]
    assert fixture._spend_run_count(runtime) == 1  # Reading does not settle.
    control = writer.get("stride_control")
    if control is not None:
        explore = read_stage(reviewed["capabilities"]["explore"]["read_action"])["explore_basis"]
        control = {**control, "explore_basis": explore}
    review = cli("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", fixture.TODO_ID, "--turn-instance-id", "route-review")
    assert review_plan["identity"] == review["heartbeat_receipt"]["settlement_identity"]
    review_result, review_read, review_decision, review_spend = review["interaction_contract"]["cli_channel"]["next_cli_actions"]
    retained_review = execute(review_result + " " + shlex.join([
        "--delivery-read-context", review["delivery_result_context"]["read_context_id"],
        "--no-global-sync", "--suppress-external-sinks",
    ]))
    review_context = execute(review_read)
    assert retained_review["settlement_identity"] == review_context["settlement_identity"]
    assert review_context["basis"]["todo"]["status"] == "open"
    packet = {"schema_version": "goal_vision_replan_contract_v0", "state": "active", "vision_patch": patch,
        "todo_delta": ["retain:" + fixture.TODO_ID], "path_delta": {"outcome": "continue",
            "prior_assumption": "A feasible candidate may still leave useful optimization work.",
            "observed_reality": "The scoped validation retains a cost-two route; one allowed direct route remains untested.",
            "retained": ["Original objective and feasible incumbent."],
            "changed": ["Compare the allowed direct route on the same graph."], "evidence_refs": [evidence]}}
    def stride_decision(basis):
        finding = next(row for row in basis["explore_basis"]["finding_refs"]
            if row.get("execution_observation", {}).get("candidate_sha256") == observed["candidate_sha256"])
        decision = dict(basis["minimal_example"])
        decision.update(action="switch", next_probe={"scope": "Compare the permitted direct route.",
            "prediction": "The independently checked route cost falls from two to one.",
            "adoption_criterion": "Same graph, valid endpoints and a lower actual route cost.",
            "cost_bound": "One local process and existing settlement; no new evaluation authority.",
            "minimum_evidence": "Exact route bytes and evaluator output."})
        decision["explore_review"] = {"revision": basis["explore_basis"]["revision"], "disposition": "used",
            "considered_refs": [finding["finding_id"]], "reason": "The measured feasible candidate leaves the direct edge untested."}
        return decision

    decision_command = review_decision + " --no-global-sync --suppress-external-sinks"
    decision_inputs = {
        "path-to-evidence-linked-goal-vision-replan-contract-v0.json": vision,
        "read-context-id": review_context["read_context_id"],
    }
    if strategy == "off":
        assert control is None
    else:
        # Retaining this Turn's result revises the Stride review basis.
        # The rejection exposes the exact facts for a fresh judgment.
        packet["stride_decision"] = stride_decision(control)
        vision.write_text(json.dumps(packet))
        stale = execute(decision_command, expected_code=1, **decision_inputs)
        assert stale["appended"] is False and fixture._spend_run_count(runtime) == 1
        assert stale["settlement_recovery"]["settlement_identity"] == retained_review["settlement_identity"]
        transition = stale["replan_transition"]
        assert transition["reason_code"] == "stride_review_basis_mismatch"
        reference = transition["context_recovery"]
        if "path" in reference:
            pointers = ("/replan_context", "/stride_control")
            page = cli("quota", "context-read", "--capture-file", reference["path"],
                "--capture-sha256", reference["sha256"], "--page-bytes", "64000",
                "--max-page-output-bytes", "64000",
                *(arg for pointer in pointers for arg in ("--select", pointer)))
            assert page["eof"], page
            values = selected_context_values(page["content"])
            current_context, current_control = (values[pointer] for pointer in pointers)
        else:
            current_context, current_control = reference["replan_context"], reference["stride_control"]
        assert current_context["obligation_id"] == transition["obligation_id"]
        assert "Minimize route cost" in current_context["core_goal"]["objective"]
        assert current_context["context_id"] == current_control["reference_context_id"]
        assert current_context["context_id"] != control["reference_context_id"]
        packet["stride_decision"] = stride_decision(current_control)
    vision.write_text(json.dumps(packet))
    acknowledged = execute(decision_command, **decision_inputs)
    assert acknowledged["autonomous_replan_ack"]["semantic_delta"]["accepted"]
    execute(review_spend)
    assert fixture._spend_run_count(runtime) == 2
    next_guard = cli("quota", "should-run", *binding, "--runtime-profile", "generic_cli",
        "--todo-id", fixture.TODO_ID, "--turn-instance-id", "route-feedback-2")
    assert next_guard["should_run"] and next_guard["selected_todo"]["todo_id"] == fixture.TODO_ID
    route.write_text(json.dumps(["start", "finish"]))
    changed = run_probe(command=[sys.executable, str(validator)], candidate_files=[route],
        output=project / "probe-2.json", input_revision="graph-v1", planned_method_id="direct-route",
        executed_method_id="direct-route", evidence_ref="probe:route-2", limit_seconds=10)
    assert changed["candidate_sha256"] != observed["candidate_sha256"]
    assert json.loads((project / "probe-2.json.stdout").read_text()) == {"valid": True, "cost": 1}
    assert first["cost"] == 2  # Retain the original result; no hindsight rewrite.
    result_read_id = next_guard["delivery_result_context"]["read_context_id"]
    if preexisting_feedback:
        code, refused_completion = fixture._run_cli(registry, runtime, "todo", "complete", *binding,
            "--todo-id", fixture.TODO_ID, "--turn-instance-id", "route-feedback-2",
            "--claim-operation-id", "route-original-execution", "--evidence", "probe:route-2",
            "--delivery-read-context", result_read_id, cwd=project)
        assert code == 1 and refused_completion.get("validation_blocked_completion"), json.dumps(refused_completion)
    terminal = dict(packet, state="no_followup", path_delta={"outcome": "stop",
        "prior_assumption": "The retained route could still be improved.",
        "observed_reality": "The direct route now has cost one.", "stopped": ["This comparison is complete."],
        "evidence_refs": ["probe:route-2"]})
    terminal.pop("stride_decision", None)
    vision.write_text(json.dumps(terminal))
    refused_command = next_guard["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1][
        "semantic_closeout_command_template"] + " " + shlex.join([
        "--delivery-read-context", result_read_id, "--no-global-sync", "--suppress-external-sinks",
    ])
    for key, value in dict(validated_progress="unvalidated_completion", scale="implementation",
                           outcome="primary_goal_outcome", file=vision).items():
        refused_command = refused_command.replace("<" + key + ">", shlex.quote(str(value)))
    code, refused = fixture._run_generated_cli(refused_command, registry_path=registry, cwd=project)
    assert code == 1 and "completion validation" in refused["error"], refused
    assert cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID)["todo"]["status"] == "open"
    assert fixture._classification_count(runtime, "unvalidated_completion") == 0
