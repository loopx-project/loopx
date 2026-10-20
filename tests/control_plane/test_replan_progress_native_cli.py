"""Typed progress must settle only the admitted replan through the real CLI."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.canonical_snapshot import read_canonical_snapshot
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.work_items.progress_observation import normalize_progress_observation

GOAL = "typed-progress-cli-fixture"
AGENT = "typed-progress-agent"
TODO = "todo_original_validation"
TURN = "turn-typed-progress-review"


def make_progress_cli_fixture(root: Path, provider: str, scenario: str) -> dict:
    """Create synthetic source/history for source and installed-package replay."""
    project, runtime = root / "project", root / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\nstatus: active\n---\n\n# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Validate the original artifact.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=open task_class=advancement_task "
        f"claimed_by={AGENT} action_kind=validate validation_command=pytest -->\n"
    )
    baseline = {
        "schema_version": "typed_progress_observation_v0", "work_item_id": TODO,
        "surface_id": "surface-existing", "hypothesis_id": "hypothesis-existing",
        "probe_kind": "probe-existing", "result_class": "unchanged",
        "evidence_ids": ["evidence-existing"],
    }
    if scenario.startswith("terminal_"):
        baseline["coverage_scope_id"] = "scope-existing"
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    source_json, source_md = index.parent / "source.json", index.parent / "source.md"
    source_json.write_text('{"fixture":"synthetic-typed-progress"}\n')
    source_md.write_text("# Synthetic prior source audit\n")
    rows = [{
        "classification": "state_refreshed", "agent_id": AGENT,
        "generated_at": f"2026-08-13T00:0{i}:00Z", "turn_instance_id": f"turn-prior-{i}",
        "progress_observation": normalize_progress_observation(baseline),
        "json_path": str(source_json), "markdown_path": str(source_md),
    } for i in range(2)]
    if scenario == "known_blocker":
        rows.insert(0, {
            **rows[0], "generated_at": "2026-08-12T23:59:00Z", "turn_instance_id": "turn-old-blocker",
            "progress_observation": normalize_progress_observation({
                **baseline, "result_class": "blocked", "blocker_id": "blocker-permission",
            }),
        })
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    registry = root / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-typed-progress",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]},
    }]}))
    todos = parse_active_state_todos(state.read_text(), item_limit=None)["agent_todos"]["items"]
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)
    observation = {**baseline, "evidence_ids": ["evidence-current"]}
    if scenario in {"advanced", "advanced_no_evidence"}:
        observation.update(result_class="advanced", probe_kind="probe-independent")
        if scenario == "advanced_no_evidence":
            observation.pop("evidence_ids")
    elif scenario == "evidence_only":
        observation["result_class"] = "advanced"
    elif scenario == "known_blocker":
        observation.update(work_item_id="todo_different_work", result_class="blocked", blocker_id="blocker-permission")
    elif scenario.startswith("terminal_"):
        observation.update(result_class="no_followup", coverage_scope_id="scope-independent")
    refresh = [
        "refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
        "--turn-instance-id", TURN, "--classification", "bounded_replan_progress",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks",
    ]
    for field in ("result_class", "work_item_id", "surface_id", "hypothesis_id", "probe_kind", "blocker_id", "coverage_scope_id"):
        if field in observation:
            refresh.extend(["--progress-" + field.replace("_", "-"), observation[field]])
    if observation.get("evidence_ids"):
        refresh.extend(["--progress-evidence-id", "evidence-current"])
    if scenario == "terminal_stop":
        vision = root / "vision.json"
        vision.write_text(json.dumps({
            "goal_id": GOAL, "agent_id": AGENT, "state": "no_followup",
            "vision_patch": {"acceptance_summary": "The bounded source scope has no remaining follow-up."},
            "path_delta": {
                "outcome": "stop", "prior_assumption": "The source scope might contain follow-up work.",
                "observed_reality": "Independent coverage found no follow-up in this bounded scope.",
                "stopped": ["Repeat exploration of the covered source scope."],
                "evidence_refs": ["evidence-current"],
            },
        }))
        refresh.extend(["--agent-vision-json", str(vision)])
    else:
        refresh.extend(["--vision-unchanged-reason", "The original validation remains open."])
    return {"project": project, "runtime": runtime, "registry": registry, "state": state,
            "index": index, "observation": observation, "refresh": refresh}


def call_progress_cli(fixture: dict, *args: str, expected_code: int = 0) -> dict:
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(fixture["registry"]),
        "--runtime-root", str(fixture["runtime"]), "--format", "json", *args,
    ], cwd=fixture["project"], env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == expected_code, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def _authority(fixture: dict) -> dict:
    result = read_canonical_snapshot(rpc=effect_runtime_result, runtime_root=str(fixture["runtime"]),
        goal_id=GOAL, include_leases=True, projection_readback=None, timeout=20)
    assert result["status"] == "loaded", result
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scenario,expected_outcomes,reason_code", [
    ("advanced", ["new_probe_family"], None),
    ("evidence_only", [], None),
    ("known_blocker", [], "progress_observation_replayed"),
    ("advanced_no_evidence", [], None),
    ("terminal_inconsistent", [], "no_followup_vision_path_inconsistent"),
    ("terminal_stop", ["coverage_backed_no_followup"], None),
])
def test_typed_progress_cli_preserves_replan_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    scenario: str, expected_outcomes: list[str], reason_code: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, scenario)
    try:
        guard_args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                      "--agent-id", AGENT, "--turn-instance-id", TURN,
                      "--scan-path", str(fixture["project"]))
        initial = call_progress_cli(fixture, *guard_args)
        assert initial["decision"] == "autonomous_replan_required", initial
        bound = call_progress_cli(fixture, *guard_args, "--todo-id", TODO)
        obligation = bound["autonomous_replan_obligation"]
        assert [trigger["kind"] for trigger in obligation["triggers"]] == ["typed_progress_repeat"]
        assert bound["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO
        original = {key: fixture[key].read_bytes() for key in ("registry", "state", "index")}
        authority = _authority(fixture)
        result = call_progress_cli(fixture, *fixture["refresh"],
            expected_code=0 if expected_outcomes else 1)
        if expected_outcomes:
            assert result["ok"] is True and result["appended"] is True
            ack = result["autonomous_replan_ack"]
            assert ack["recorded"] is True
            delta = ack["semantic_delta"]
            fingerprint = hashlib.sha256(json.dumps(
                {key: value for key, value in fixture["observation"].items() if key != "schema_version"},
                sort_keys=True, ensure_ascii=True, separators=(",", ":"),
            ).encode()).hexdigest()[:16]
            assert delta == {
                "schema_version": "replan_semantic_delta_v0", "accepted": True,
                "outcomes": expected_outcomes, "satisfying_outcomes": expected_outcomes,
                "required_any_of": ["new_surface", "new_hypothesis", "new_probe_family",
                    "new_runnable_successor", "coverage_backed_exploration_exhausted",
                    "new_concrete_blocker", "coverage_backed_no_followup"],
                "observation_fingerprint": fingerprint,
                "reason": "writeback changes an outcome accepted by this obligation source",
                "trigger_kinds": ["typed_progress_repeat"], "trigger_checkpoints": [],
                "obligation_id": obligation["obligation_id"],
            }
            persisted = json.loads(Path(result["json_path"]).read_text())
            assert persisted["autonomous_replan_ack"] == ack
            assert persisted["progress_observation"]["fingerprint"] == fingerprint
            compact = json.loads(fixture["index"].read_text().splitlines()[-1])
            assert compact["autonomous_replan_ack"]["semantic_delta"] == delta
            restart_effect_runtime(wait_for_exit=True)
            replay = call_progress_cli(fixture, *fixture["refresh"])
            assert replay["appended"] is False and replay["idempotent_replay"] is True
        else:
            assert result["ok"] is False and result["appended"] is False
            rejection = result["replan_transition"]
            assert rejection["obligation_id"] == obligation["obligation_id"]
            assert rejection["host_action"] == "write_typed_semantic_delta"
            assert rejection["reason_code"] == reason_code
            assert rejection["next_cli_actions"]
            restart_effect_runtime(wait_for_exit=True)
            assert call_progress_cli(fixture, *fixture["refresh"], expected_code=1) == result
            assert {key: fixture[key].read_bytes() for key in original} == original
        assert fixture["registry"].read_bytes() == original["registry"]
        displayed = parse_active_state_todos(fixture["state"].read_text(), item_limit=None)["agent_todos"]["items"]
        assert [(todo["todo_id"], todo["status"]) for todo in displayed] == [(TODO, "open")]
        assert _authority(fixture) == authority
        assert authority["todos"][0]["status"] == "open"
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("comparison_binding", [False, True], ids=["original", "source_bound"])
def test_stalled_stride_review_drives_an_executed_mechanism_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    comparison_binding: bool,
) -> None:
    """A real stall review reaches work and a later decision under one Goal.

    The bounded assignment problem is synthetic, with an unchanged minimum-cost
    objective. It tests the control path, not benchmark or model effectiveness.
    """
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    registry = json.loads(fixture["registry"].read_text())
    registry["goals"][0]["quota"]["allowed_slots"] = 8
    fixture["registry"].write_text(json.dumps(registry))

    def call(*args: str, expected_code: int = 0) -> dict:
        return call_progress_cli(fixture, *args, expected_code=expected_code)

    def guard(turn: str, *, todo: str | None = TODO) -> dict:
        return call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", turn,
            *(["--todo-id", todo] if todo else []), "--scan-path", str(fixture["project"]))

    def spend(turn: str, *, todo: str | None = TODO, obligation: str | None = None) -> dict:
        return call("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", turn, *(["--todo-id", todo] if todo else []),
            *(["--replan-obligation-id", obligation] if obligation else []),
            "--slots", "1", "--source", "heartbeat", "--execute")

    try:
        call("configure-goal", "--goal-id", GOAL, "--execution-replan-after-turns", "1",
             "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
        if comparison_binding:
            call("configure-goal", "--goal-id", GOAL, "--explore-mode", "planning", "--execute")
            call("explore", "node", "--goal-id", GOAL, "--agent-id", AGENT,
                 "--node-id", "recorded-local-route", "--kind", "question",
                 "--title", "Do the existing observations establish useful progress?")
            call("explore", "finding", "--goal-id", GOAL, "--agent-id", AGENT,
                 "--node", "recorded-local-route", "--finding-id", "finding-recorded-local",
                 "--title", "The recorded local observations are unchanged.", "--status", "tentative",
                 "--summary", "Only the fixture's recorded observations; alternate mechanisms remain untested.",
                 "--tag", "writeback-result", "--evidence-ref", "evidence-existing")
            call("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
                 "--explore-result-node-ref", "recorded-local-route")
        guard(TURN, todo=None)
        admitted = guard(TURN)
        obligation = admitted["autonomous_replan_obligation"]
        assert [item["kind"] for item in obligation["triggers"]] == ["typed_progress_repeat"]
        assert "fresh_vision_path_outcome" in obligation["replan_context"]["uncovered_frontier"]["required_any_of"]
        assert admitted["replan_action_packet"]["uncovered_frontier"] == obligation["replan_context"]["uncovered_frontier"]
        original_index = fixture["index"].read_bytes()
        refused = call(*fixture["refresh"], expected_code=1)
        assert refused["replan_transition"]["reason_code"] == "stride_decision_required"
        assert fixture["index"].read_bytes() == original_index

        control = admitted["replan_action_packet"]["stride_control"]
        decision = {**control["minimal_example"], "basis": "unexplained_gap", "action": "verify",
            "axes": {
                "work_slice": {"plan": "Run and compare three complete assignment methods.",
                               "reason": "Repeated local changes have not resolved the cost gap."},
                "route_distance": {"plan": "Compare local choice, reverse priority and joint assignment.",
                                   "reason": "Row interactions may defeat independent greedy choices."},
                "validation_scope": {"plan": "Use the same costs and one-use-per-column constraint.",
                                     "reason": "A changed objective cannot establish a better route."}},
            "next_probe": {"scope": "One complete bounded assignment comparison.",
                "prediction": "Joint choices can beat independent local choices.",
                "minimum_evidence": "Executable methods, assignments, constraint checks and costs.",
                "adoption_criterion": "A valid lower-cost assignment under the original objective.",
                "cost_bound": "Three methods on a three-row fixture; at most six joint assignments."}}
        choice = None
        if comparison_binding:
            basis = control["explore_basis"]
            card = next(card for card in basis["comparison_candidates"] if card["todo_id"] == TODO)
            choice = {"todo_id": TODO, **card["outcomes"][0]}
            assert choice["finding_id"] == "finding-recorded-local"
            decision["explore_review"] = {"revision": basis["revision"], "disposition": "used",
                "considered_refs": [choice["finding_id"]], "selected_candidate": choice,
                "reason": "Use the recorded outcome to compare mechanisms within the original unfinished task."}
        packet = {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
            "vision_patch": {"acceptance_summary": "The original minimum-cost validation remains open."},
            "path_delta": {"outcome": "continue", "prior_assumption": "Local changes can resolve the gap.",
                "observed_reality": "Repeated observations do not establish that explanation.",
                "retained": ["Original objective and unfinished validation"],
                "evidence_refs": ["evidence-existing"]},
            "stride_decision": decision, "todo_delta": [f"retain:{TODO}"]}
        vision = tmp_path / "comparison-review.json"
        vision.write_text(json.dumps(packet))
        refresh_args = ("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", TODO, "--turn-instance-id", TURN, "--agent-vision-json", str(vision),
            "--classification", "bounded_replan_progress", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks")
        if comparison_binding:
            # Reuse the admitted current context, but falsify only the source
            # digest. The typed progress exit must not hide this mismatch.
            current_source = _authority(fixture)
            current_state = fixture["state"].read_bytes()
            wrong = json.loads(json.dumps(packet))
            digest = choice["source_sha256"]
            wrong["stride_decision"]["explore_review"]["selected_candidate"]["source_sha256"] = (
                ("0" if digest[0] != "0" else "1") + digest[1:])
            vision.write_text(json.dumps(wrong))
            refused = call(*refresh_args, "--progress-result-class", "advanced",
                "--progress-probe-kind", "probe-independent", "--progress-evidence-id", "evidence-current",
                expected_code=1)
            assert "exact scoped result" in refused["error"], refused
            assert fixture["index"].read_bytes() == original_index
            assert fixture["state"].read_bytes() == current_state and _authority(fixture) == current_source
            vision.write_text(json.dumps(packet))
        accepted = call(*refresh_args)
        assert accepted["autonomous_replan_ack"]["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
        if comparison_binding:
            persisted = json.loads(Path(accepted["json_path"]).read_text())
            assert persisted["agent_vision"]["stride_decision"]["explore_review"]["selected_candidate"] == choice
            restart_effect_runtime(wait_for_exit=True)
        assert call(*refresh_args)["appended"] is False
        spend(TURN)

        work_turn = "turn-executed-route-comparison"
        selected = guard(work_turn)
        assert selected["selected_todo"]["todo_id"] == TODO
        artifact = fixture["project"] / "assignment-comparison.json"
        subprocess.run([sys.executable, "-c", """
import itertools, json, sys
from pathlib import Path
costs = [[1, 2, 9], [1, 9, 9], [9, 1, 2]]
def greedy(order):
    assignment, available = [None] * len(costs), set(range(len(costs)))
    for row in order:
        column = min(available, key=lambda column: (costs[row][column], column))
        assignment[row] = column
        available.remove(column)
    return assignment
def cost(assignment):
    assert sorted(assignment) == list(range(len(costs)))
    return sum(costs[row][column] for row, column in enumerate(assignment))
assignments = {'local': greedy(range(3)), 'reverse_priority': greedy(range(2, -1, -1)),
    'joint': min(itertools.permutations(range(3)), key=cost)}
results = {name: {'assignment': list(value), 'cost': cost(value)} for name, value in assignments.items()}
winner = min(results, key=lambda name: results[name]['cost'])
Path(sys.argv[1]).write_text(json.dumps({'results': results, 'winner': winner,
    'objective': 'minimum_total_cost', 'constraint': 'each_column_once'}))
""", str(artifact)], check=True, cwd=fixture["project"], timeout=10)
        measured = json.loads(artifact.read_text())
        assert measured["results"]["local"]["cost"] == 12
        assert measured["results"]["reverse_priority"]["cost"] == 11
        assert measured["results"]["joint"]["cost"] == 5
        assert measured["winner"] == "joint"
        summary = "Local cost 12; reverse-priority cost 11; joint cost 5. Retain the joint result; validate transfer."
        work = call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", TODO, "--turn-instance-id", work_turn,
            "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
            "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
            "--recommended-action", summary, "--no-global-sync", "--suppress-external-sinks")
        spend(work_turn)

        next_turn = "turn-review-executed-comparison"
        next_guard = guard(next_turn, todo=None)
        next_obligation = next_guard["autonomous_replan_obligation"]
        follow = next_obligation["replan_context"]["decision_followthrough"]
        planned = next(item for item in follow["planned_work"] if item["todo_id"] == TODO)
        result_ref = planned["ordinary_evidence_refs"][0]
        readback = call("history", "--goal-id", GOAL, "--agent-id", AGENT,
                        "--evidence-ref", result_ref)["evidence"]
        assert readback["recommended_action"] == summary
        assert readback["generated_at"] == json.loads(Path(work["json_path"]).read_text())["generated_at"]
        assert follow["execution_and_goal_gain"] == "unknown", "Synthetic provenance is not authoritative transfer"
        if comparison_binding:
            assert follow["previous_decision"]["stride_decision"]["explore_review"]["selected_candidate"] == choice

        # The measured choice changes the next executable work, not just prose.
        successor = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
            "--text", f"Validate transfer of {measured['winner']} assignment on independent costs",
            "--task-class", "advancement_task", "--action-kind", "validate",
            "--target-key", "joint-assignment-transfer", "--replan-obligation-id", next_obligation["obligation_id"])
        next_guard = guard(next_turn, todo=successor["todo_id"])
        next_control = next_guard["replan_action_packet"]["stride_control"]
        next_decision = {**decision, "action": "switch", "basis": "useful_new_route",
            "reference_context_id": next_control["reference_context_id"],
            "reference_obligation_id": next_obligation["obligation_id"],
            "previous_review": {"reference_decision_ref": follow["previous_decision"]["evidence_ref"],
                "adoption": "executed", "information_gain": "new_evidence", "goal_gain": "unknown",
                "evidence_refs": [result_ref], "lesson": "Joint choices improved this fixture; transfer remains unproven."}}
        next_decision["axes"] = {**decision["axes"], "route_distance": {
            "plan": "Carry joint assignment into an independent transfer check.",
            "reason": "The paired experiment exposed the local-choice limitation."}}
        if comparison_binding:
            # B is new work justified by the actual comparison. The optional
            # retained-A choice is not a routing command or evidence of B.
            next_decision["explore_review"] = {"revision": next_control["explore_basis"]["revision"],
                "disposition": "deferred", "considered_refs": [],
                "reason": "Transfer remains untested; the next work uses the executed comparison's history evidence."}
        packet = {**packet, "stride_decision": next_decision, "todo_delta": [f"create:{successor['todo_id']}"]}
        vision.write_text(json.dumps(packet))
        adopted = call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--replan-obligation-id", next_obligation["obligation_id"], "--turn-instance-id", next_turn,
            "--agent-vision-json", str(vision), "--classification", "bounded_replan_progress",
            "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks")
        assert adopted["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
        spend(next_turn, todo=None, obligation=next_obligation["obligation_id"])
        if comparison_binding:
            spent = [json.loads(line) for line in fixture["index"].read_text().splitlines()
                     if json.loads(line).get("quota_spend_commit")]
            assert len(spent) == 3
            assert {row["turn_instance_id"] for row in spent} == {TURN, work_turn, next_turn}
    finally:
        restart_effect_runtime(wait_for_exit=True)
