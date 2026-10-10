"""Real CLI admission, successor and writeback paths with synthetic evidence."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.explore.research_evidence import append_research_observation
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_edge_event, build_explore_node_event, explore_result_log_path,
)
from loopx.control_plane.work_items.semantic_replan_writeback import qualify_replan_writeback
from loopx.capabilities.explore.research_frontier import prepare_research_replan_evidence

GOAL = "research-gate-fixture"
AGENT = "fixture-agent"


def observation(node: str, target: str | None = None) -> dict:
    return {"schema_version": "typed_research_observation_v0", "explore_node_id": node,
            "progress": {"schema_version": "typed_progress_observation_v0", "work_item_id": f"todo_{node}",
                         "result_class": "exploration_exhausted", "coverage_scope_id": f"scope-{node}",
                         "coverage_complete": True, "evidence_ids": [f"ev-{node}"]},
            "closure_basis": {"schema_version": "research_closure_basis_v0", "disposition": "bounded",
                              "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}],
                              "evidence_ids": [f"ev-{node}"]},
            "composition_candidates": [{"basis": "explicit", "target_node_id": target,
                                         "interaction_kind": "state_interference", "evidence_ids": [f"ev-{node}", f"ev-{target}"]}] if target else []}


@pytest.fixture
def fixture(tmp_path: Path):
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text('---\nstatus: active\nowner_mode: goal\nobjective: "Test the explicit research boundary."\n'
                     'updated_at: 2026-09-28T00:00:00Z\n---\n\n# Research fixture\n\n'
                     '## Objective\n\nTest the explicit research boundary.\n\n## Next Action\n\nInspect the current evidence.\n\n'
                     '## Agent Todo\n\n- [ ] Observe the synthetic fixture.\n'
                     '  <!-- loopx:todo todo_id=todo_monitor status=open task_class=continuous_monitor claimed_by=fixture-agent '
                     'target_key=synthetic-monitor cadence=1d next_due_at=2999-01-01T00%3A00%3A00Z -->\n')
    runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": "0.1", "common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name, "status": "active", "domain": "research-fixture",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"}, "authority_sources": [],
        "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 20},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]},
        "spawn_policy": {"explore_harness": {"enabled": True}},
    }]}))
    log = explore_result_log_path(runtime, GOAL)
    for name in ["a", "b", "joint"]:
        append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id=name, title=f"Research {name}",
            node_kind="experiment" if name == "joint" else "hypothesis", status="open" if name == "joint" else "resolved"))
    append_research_observation(log, goal_id=GOAL, observation=observation("b"))
    append_research_observation(log, goal_id=GOAL, observation=observation("a", "b"))
    for name in ["a", "b"]:
        append_explore_result_event(log, build_explore_edge_event(goal_id=GOAL, from_node="joint", to_node=name, edge_type="depends_on"))

    def cli(*args: str, success: bool = True) -> dict:
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
                                 "--runtime-root", str(runtime), *args], capture_output=True, text=True, check=False,
                                cwd=Path(__file__).resolve().parents[2])
        assert (result.returncode == 0) is success, result.stdout + result.stderr
        return json.loads(result.stdout)

    return cli, log, runtime, registry, state


def activate(cli) -> None:
    assert cli("configure-goal", "--goal-id", GOAL, "--explore-composition-mode", "explicit_only",
               "--explore-composition-scope-id", "scope-joint", "--execute")["ok"]


def test_inactive_policy_never_requires_a_research_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("loopx.capabilities.explore.composition_frontier.project_live_explore_composition_frontier",
                        lambda **_kwargs: pytest.fail("disabled policy must not read research state"))
    for harness in [{"enabled": True}, {"enabled": True, "composition_mode": "disabled"},
                    {"enabled": False, "composition_mode": "explicit_only", "composition_scope_id": "scope"}]:
        obligation, _ = qualify_replan_writeback(newest_first_runs=[], state_text="## Agent Todo\n\n", agent_id=AGENT,
            goal_id=GOAL, registry_goal={"id": GOAL, "spawn_policy": {"explore_harness": harness}})
        assert obligation is None


def test_real_guard_and_successor_share_the_current_gap(fixture) -> None:
    cli, log, runtime, registry, state = fixture
    old = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "fixture-off")
    assert old.get("effective_action") != "autonomous_replan_required"
    assert "research_execution_frontier" not in cli("explore", "summary", "--goal-id", GOAL)
    off_status = cli("status", "--goal-id", GOAL, "--agent-id", AGENT)
    assert all("bounded_research_frontier" not in item.get("project_asset", {})
               for item in off_status["attention_queue"]["items"])
    activate(cli)
    guard = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "fixture-on")
    assert guard["effective_action"] == "autonomous_replan_required"
    packet = guard["replan_action_packet"]
    obligation = packet["obligation_id"]
    assert packet["capability_guard"]["gap_id"] == guard["bounded_research_frontier"]["selected_gap"]["gap_id"]
    status = cli("status", "--goal-id", GOAL, "--agent-id", AGENT)
    item = next(item for item in status["attention_queue"]["items"] if item["goal_id"] == GOAL)
    assert item["project_asset"]["bounded_research_frontier"] == guard["bounded_research_frontier"]
    assert item["project_asset"]["autonomous_replan_obligation"]["obligation_id"] == obligation
    summary = cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT)
    assert summary["research_execution_frontier"] == guard["bounded_research_frontier"]
    from loopx.cli_commands.explore import render_explore_markdown
    from loopx.presentation.renderers.status_markdown import render_status_markdown
    assert packet["capability_guard"]["gap_id"] in render_explore_markdown(summary)
    assert packet["capability_guard"]["gap_id"] in render_status_markdown(status)
    assert "lineage_gaps" not in guard["bounded_research_frontier"]
    assert "settlement_transitions" not in guard["bounded_research_frontier"]
    rejected = cli("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
                   "--replan-obligation-id", obligation, "--turn-instance-id", "fixture-on",
                   "--classification", "bounded_fixture_probe", "--delivery-batch-scale", "implementation",
                   "--delivery-outcome", "outcome_progress", "--progress-result-class", "advanced",
                   "--progress-surface-id", "unrelated", "--progress-evidence-id", "ev-unrelated", success=False)
    assert "evidence duty" in json.dumps(rejected)
    base = ["todo", "add", "--goal-id", GOAL, "--role", "agent", "--task-class", "advancement_task",
            "--action-kind", "joint_probe", "--claimed-by", AGENT, "--replan-obligation-id", obligation]
    bad = cli(*base, "--text", "Unrelated work.", "--explore-result-node-ref", "a", success=False)
    assert "bind one current binary experiment" in json.dumps(bad)
    deferred = cli(*base, "--text", "Deferred joint experiment.", "--explore-result-node-ref", "joint",
                   "--status", "deferred", "--resume-when", "capacity_available:fixture", success=False)
    assert "no deferral" in json.dumps(deferred)
    created = cli(*base, "--text", "Run the bounded joint experiment.", "--explore-result-node-ref", "joint", "--target-key", "joint")
    assert created["replan_transition"]["recorded"]
    before_closeout = state.read_bytes()
    missing = cli("todo", "complete", "--goal-id", GOAL, "--todo-id", created["todo_id"],
                  "--agent-id", AGENT, "--claimed-by", AGENT, "--no-follow-up",
                  "--evidence", "No experiment result recorded.", success=False)
    assert "current typed experiment observation" in json.dumps(missing)
    assert state.read_bytes() == before_closeout
    retired = cli("todo", "supersede", "--goal-id", GOAL, "--todo-id", created["todo_id"],
                  "--agent-id", AGENT, "--reason", "Attempt to close through another verb.", success=False)
    assert "current typed experiment observation" in json.dumps(retired)
    assert state.read_bytes() == before_closeout
    replay = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "fixture-on")
    assert replay["heartbeat_receipt"]["semantic_replan_capability_guard"] == packet["capability_guard"]
    assert replay["replan_action_packet"]["obligation_id"] == obligation
    refresh = cli("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
                  "--replan-obligation-id", obligation, "--turn-instance-id", "fixture-on",
                  "--classification", "bounded_fixture_probe", "--delivery-batch-scale", "implementation",
                  "--delivery-outcome", "outcome_progress", "--vision-summary", "Test this explicit research question.",
                  "--vision-acceptance", "The typed joint experiment addresses the question.",
                  "--vision-replan-trigger", "Research result remains open.")
    assert refresh["ok"]
    spent = cli("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT, "--slots", "1", "--source", "heartbeat",
                "--execute", "--replan-obligation-id", obligation, "--turn-instance-id", "fixture-on")
    assert spent["settlement_progress"]["state"] == "settled"
    append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="joint", title="Research joint",
                                                           node_kind="experiment", status="resolved"))
    result = observation("joint")
    result["progress"]["work_item_id"] = created["todo_id"]
    result["input_observations"] = guard["bounded_research_frontier"]["selected_gap"]["input_observations"]
    result["execution_lineage"] = {"schema_version": "research_execution_lineage_v0", "goal_id": GOAL,
        "gap_id": packet["capability_guard"]["gap_id"], "replan_obligation_id": obligation,
        "successor_todo_id": created["todo_id"], "agent_id": AGENT}
    append_research_observation(log, goal_id=GOAL, observation=result, agent_id=AGENT,
                               registry_path=registry, runtime_root=runtime)
    complete = cli("todo", "complete", "--goal-id", GOAL, "--todo-id", created["todo_id"],
                   "--agent-id", AGENT, "--claimed-by", AGENT, "--no-follow-up", "--evidence", "Typed result recorded.")
    assert complete["completed"]
    assert complete["capability_completion_evidence"]["experiment_node_id"] == "joint"
    archived = cli("todo", "archive-completed", "--goal-id", GOAL, "--role", "agent",
                   "--max-active-done", "0", "--execute")
    assert archived["moved_count"] == 1
    assert cli("todo", "list", "--goal-id", GOAL, "--todo-id", created["todo_id"])["todo"]["archive_state"] == "archive"
    from loopx.capabilities.explore.composition_frontier import project_live_explore_composition_frontier
    frontier = project_live_explore_composition_frontier(runtime_root=runtime, goal_id=GOAL, agent_id=AGENT,
        status_payload={"run_history": {"goals": json.loads(registry.read_text())["goals"]}})
    assert frontier["observed_count"] == 1
    assert frontier["scheduled_count"] == 0
    summary = cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT)
    assert summary["research_execution_frontier"]["observed_count"] == 1
    assert "lineage_gaps" not in summary["research_execution_frontier"]
    from loopx.extensions.lark.presentation.explore_results import _node_record_values
    joint = next(node for node in summary["nodes"] if node["node_id"] == "joint")
    lark = _node_record_values(joint, goal_id=GOAL, source_id="synthetic-source")
    assert "observed" in lark["Summary"] and packet["capability_guard"]["gap_id"] in lark["Summary"]


def test_selected_guard_cannot_be_erased_by_input_invalidation(fixture) -> None:
    cli, log, _runtime, _registry, _state = fixture
    activate(cli)
    guard = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "fixture-stale")
    obligation = guard["replan_action_packet"]["obligation_id"]
    append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="a", title="Research a", status="open"))
    rejected = cli("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
                   "--replan-obligation-id", obligation, "--turn-instance-id", "fixture-stale",
                   "--classification", "bounded_fixture_probe", "--delivery-batch-scale", "implementation",
                   "--delivery-outcome", "outcome_progress", "--progress-result-class", "advanced",
                   "--progress-evidence-id", "ev-unrelated", success=False)
    assert "evidence duty" in json.dumps(rejected)


def test_live_presentation_requires_explicit_registered_actor_for_multi_agent_goal(fixture) -> None:
    cli, log, _runtime, registry, state = fixture
    activate(cli)
    source = json.loads(registry.read_text())
    source["goals"][0]["coordination"]["registered_agents"].append("other-agent")
    registry.write_text(json.dumps(source))
    before = log.read_bytes(), state.read_bytes(), registry.read_bytes()
    for args in [[], ["--agent-id", "unregistered"]]:
        rejected = cli("explore", "summary", "--goal-id", GOAL, *args, success=False)
        assert "registered Goal agent" in json.dumps(rejected)
    summary = cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT)
    assert summary["research_execution_frontier"]["agent_id"] == AGENT
    assert summary["research_execution_frontier"]["grants_execution_authority"] is False
    assert (log.read_bytes(), state.read_bytes(), registry.read_bytes()) == before


def test_real_replan_guard_accepts_exact_blocker_wait_and_resumes_without_closure(fixture) -> None:
    cli, log, runtime, registry, state = fixture
    activate(cli)
    guard = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "fixture-blocked")
    packet = guard["replan_action_packet"]
    task = cli("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--task-class", "advancement_task", "--action-kind", "joint_probe", "--target-key", "joint",
        "--explore-result-node-ref", "joint", "--replan-obligation-id", packet["obligation_id"],
        "--text", "Run the bounded joint experiment.")
    blocker = cli("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--task-class", "blocker", "--action-kind", "investigate", "--unblocks-todo-id", task["todo_id"],
        "--text", "Resolve the bounded dependency.")
    append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="joint", title="Research joint",
        node_kind="experiment", status="blocked", blocked_reason="An identified dependency remains open."))
    result = observation("joint")
    result["progress"].update(work_item_id=task["todo_id"], result_class="blocked", blocker_id=blocker["todo_id"])
    result["progress"].pop("coverage_complete")
    result["closure_basis"] = None
    result["input_observations"] = guard["bounded_research_frontier"]["selected_gap"]["input_observations"]
    result["execution_lineage"] = {"schema_version": "research_execution_lineage_v0", "goal_id": GOAL,
        "gap_id": packet["capability_guard"]["gap_id"], "replan_obligation_id": packet["obligation_id"],
        "successor_todo_id": task["todo_id"], "agent_id": AGENT}
    result["composition_resolution"] = {"schema_version": "research_composition_resolution_v0",
        "disposition": "deferred", "evidence_ids": ["ev-joint"]}
    recorded = append_research_observation(log, goal_id=GOAL, observation=result, agent_id=AGENT,
        registry_path=registry, runtime_root=runtime)
    cli("todo", "update", "--goal-id", GOAL, "--todo-id", task["todo_id"], "--agent-id", AGENT,
        "--status", "blocked", "--resume-when", f"todo_done:{blocker['todo_id']}", "--reason", "Await the bounded dependency.")
    frontier = cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT)["research_execution_frontier"]
    assert frontier["deferred_count"] == 1 and frontier["observed_count"] == 0
    refresh = cli("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--replan-obligation-id", packet["obligation_id"], "--turn-instance-id", "fixture-blocked",
        "--classification", "bounded_fixture_blocker", "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--progress-work-item-id", task["todo_id"], "--progress-result-class", "blocked",
        "--progress-blocker-id", blocker["todo_id"], "--progress-coverage-scope-id", "scope-joint", "--progress-evidence-id", "ev-joint",
        "--vision-summary", "Test this explicit research question.", "--vision-acceptance", "The typed experiment addresses the question.",
        "--vision-replan-trigger", "The experiment remains blocked with a typed resume condition.")
    assert refresh["ok"]
    saved = json.loads(Path(refresh["json_path"]).read_text())
    assert saved["autonomous_replan_ack"]["semantic_delta"]["capability_outcome"] == "composition_temporarily_deferred"
    assert saved["progress_observation"] == recorded["observation"]["progress"]
    spent = cli("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT, "--slots", "1", "--source", "heartbeat",
        "--execute", "--replan-obligation-id", packet["obligation_id"], "--turn-instance-id", "fixture-blocked")
    assert spent["settlement_progress"]["state"] == "settled"
    source = json.loads(registry.read_text())["goals"][0]
    _, repeated = qualify_replan_writeback(newest_first_runs=[{"agent_id": AGENT,
        "progress_observation": recorded["observation"]["progress"]}], state_text=state.read_text(), agent_id=AGENT,
        goal_id=GOAL, registry_goal=source, guard_scoped=True,
        capability_evidence=prepare_research_replan_evidence(runtime_root=runtime, goal_id=GOAL,
            agent_id=AGENT, registry_goal=source, state_text=state.read_text(), capability_guard=packet["capability_guard"]),
        guard_semantic_replan_obligation_id=packet["obligation_id"], guard_capability=packet["capability_guard"],
        progress_observation=recorded["observation"]["progress"])
    assert repeated["accepted"] is False
    cli("todo", "complete", "--goal-id", GOAL, "--todo-id", blocker["todo_id"], "--agent-id", AGENT,
        "--no-follow-up", "--evidence", "Dependency resolved in the fixture.")
    frontier = cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT)["research_execution_frontier"]
    assert frontier["deferred_count"] == 0 and frontier["pending_count"] == 1
    cli("todo", "update", "--goal-id", GOAL, "--todo-id", task["todo_id"], "--agent-id", AGENT,
        "--status", "open", "--clear-resume-when", "--reason", "Dependency resolved; resume the experiment.")
    append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="joint", title="Research joint",
        node_kind="experiment", status="open"))
    frontier = cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT)["research_execution_frontier"]
    assert frontier["scheduled_count"] == 1 and frontier["observed_count"] == 0


@pytest.mark.parametrize("disposition", ["observed", "dismissed"])
def test_real_replan_guard_accepts_exact_result_source_and_candidate_dismissal(fixture, disposition: str) -> None:
    cli, log, runtime, registry, _state = fixture
    activate(cli)
    guard = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "fixture-terminal")
    packet = guard["replan_action_packet"]
    task = cli("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--task-class", "advancement_task", "--action-kind", "joint_probe", "--target-key", "joint",
        "--explore-result-node-ref", "joint", "--replan-obligation-id", packet["obligation_id"],
        "--text", "Run the bounded joint experiment.")
    append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="joint", title="Research joint",
        node_kind="experiment", status="dead_end" if disposition == "dismissed" else "resolved"))
    result = observation("joint")
    result["progress"]["work_item_id"] = task["todo_id"]
    result["input_observations"] = guard["bounded_research_frontier"]["selected_gap"]["input_observations"]
    result["execution_lineage"] = {"schema_version": "research_execution_lineage_v0", "goal_id": GOAL,
        "gap_id": packet["capability_guard"]["gap_id"], "replan_obligation_id": packet["obligation_id"],
        "successor_todo_id": task["todo_id"], "agent_id": AGENT}
    if disposition == "dismissed":
        result["progress"]["result_class"] = "no_followup"
        result["closure_basis"]["disposition"] = "no_followup"
        result["composition_resolution"] = {"schema_version": "research_composition_resolution_v0",
            "disposition": "dismissed", "basis": "outside_scope", "evidence_ids": ["ev-joint"]}
    recorded = append_research_observation(log, goal_id=GOAL, observation=result, agent_id=AGENT,
        registry_path=registry, runtime_root=runtime)
    refresh = cli("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--replan-obligation-id", packet["obligation_id"], "--turn-instance-id", "fixture-terminal",
        "--classification", "bounded_fixture_terminal", "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--progress-work-item-id", task["todo_id"], "--progress-result-class", result["progress"]["result_class"],
        "--progress-coverage-scope-id", "scope-joint", "--progress-coverage-complete", "--progress-evidence-id", "ev-joint",
        "--vision-summary", "Test this explicit research question.", "--vision-acceptance", "The typed result addresses the question.",
        "--vision-replan-trigger", "Retain the remaining Goal acceptance beyond this bounded candidate.")
    saved = json.loads(Path(refresh["json_path"]).read_text())
    expected = "composition_candidate_dismissed" if disposition == "dismissed" else "composition_experiment_observed"
    assert saved["autonomous_replan_ack"]["semantic_delta"]["capability_outcome"] == expected
    assert saved["progress_observation"] == recorded["observation"]["progress"]
    spent = cli("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT, "--slots", "1", "--source", "heartbeat",
        "--execute", "--replan-obligation-id", packet["obligation_id"], "--turn-instance-id", "fixture-terminal")
    assert spent["settlement_progress"]["state"] == "settled"
    complete = cli("todo", "supersede" if disposition == "dismissed" else "complete", "--goal-id", GOAL,
        "--todo-id", task["todo_id"], "--agent-id", AGENT,
        *( ["--reason", "Candidate dismissal is recorded."] if disposition == "dismissed"
          else ["--evidence", "Typed scoped evidence is recorded.", "--no-follow-up"]))
    assert complete["capability_completion_evidence"]["disposition"] == (
        "candidate_dismissed" if disposition == "dismissed" else "experiment_observed")


@pytest.mark.parametrize("change", ["input", "scope", "disabled"])
def test_invalidated_original_turn_retires_with_no_spend_and_current_frontier_is_preserved(fixture, change: str) -> None:
    cli, log, _runtime, _registry, state = fixture
    activate(cli)
    original = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--codex-app",
        "--turn-instance-id", "fixture-invalidated")
    packet = original["replan_action_packet"]
    if change == "input":
        append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="a", title="Research a", status="open"))
    elif change == "scope":
        cli("configure-goal", "--goal-id", GOAL, "--explore-composition-scope-id", "replacement-scope", "--execute")
    else:
        cli("configure-goal", "--goal-id", GOAL, "--explore-composition-mode", "disabled", "--execute")
    identity = ["--goal-id", GOAL, "--agent-id", AGENT, "--replan-obligation-id", packet["obligation_id"],
                "--turn-instance-id", "fixture-invalidated"]
    rejected = cli("refresh-state", *identity, "--classification", "fixture_retirement_probe",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--progress-result-class", "advanced", "--progress-evidence-id", "unrelated", success=False)
    contract = rejected["replan_transition"]["retirement_contract"]
    assert contract["blocking_todo_count"] == 0
    assert contract["original_guard"] == packet["capability_guard"]
    progress = contract["progress_observation"]
    # This is a causal lifecycle exit, not a way to count progress or debit.
    invalid_progress = cli("refresh-state", *identity, "--classification", "fixture_false_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--progress-result-class", "blocked", "--progress-blocker-id", progress["blocker_id"],
        "--progress-evidence-id", progress["evidence_ids"][0], success=False)
    assert "requires outcome_gap" in json.dumps(invalid_progress)
    retired = cli("refresh-state", *identity, "--classification", "fixture_duty_invalidated",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_gap",
        "--progress-result-class", "blocked", "--progress-blocker-id", progress["blocker_id"],
        "--progress-evidence-id", progress["evidence_ids"][0],
        "--vision-summary", "Continue the bounded research question from current evidence.",
        "--vision-acceptance", "Authoritative evidence must satisfy the research question.",
        "--vision-replan-trigger", "Reassess the current frontier after this admitted basis changed.")
    assert retired["settlement_progress"]["state"] == "settled"
    assert retired["settlement_progress"]["closeout_kind"] == "capability_duty_retired_no_spend"
    assert "settlement_owed" not in retired
    denied = cli("quota", "spend-slot", *identity, "--slots", "1", "--source", "heartbeat", "--execute")
    assert denied["appended"] is False and denied["idempotent_replay"] is True
    assert denied["settlement_progress"]["closeout_kind"] == "capability_duty_retired_no_spend"
    assert not any(receipt["step_kind"] == "quota_spend" for receipt in denied["settlement_result"]["receipts"])
    replay = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", "fixture-invalidated", "--codex-app")
    assert replay["heartbeat_receipt"]["semantic_replan_capability_guard"] == packet["capability_guard"]
    assert replay["effective_action"] == "heartbeat_settled_skip"
    fresh = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--codex-app",
        "--turn-instance-id", "fixture-after-retirement")
    assert fresh["effective_action"] != "unsettled_host_turn_recovery"
    assert fresh["goal_frontier_projection"]["acceptance_gaps"]
    assert "quota_slot_spent" not in json.dumps(retired)
    assert "completed_at=" not in state.read_text()


def test_retirement_cannot_hide_a_runnable_bound_task_and_keeps_that_task_open(fixture) -> None:
    cli, _log, _runtime, _registry, _state = fixture
    activate(cli)
    original = cli("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, "--codex-app",
        "--turn-instance-id", "fixture-active-duty")
    packet = original["replan_action_packet"]
    task = cli("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--task-class", "advancement_task", "--action-kind", "joint_probe", "--target-key", "joint",
        "--explore-result-node-ref", "joint", "--replan-obligation-id", packet["obligation_id"], "--text", "Run the bounded experiment.")
    cli("configure-goal", "--goal-id", GOAL, "--explore-composition-scope-id", "replacement-scope", "--execute")
    base = ["refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--replan-obligation-id", packet["obligation_id"],
        "--turn-instance-id", "fixture-active-duty", "--classification", "fixture_retirement", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked"]
    rejected = cli(*base, "--progress-blocker-id", "unrelated", "--progress-evidence-id", "unrelated", success=False)
    contract = rejected["replan_transition"]["retirement_contract"]
    assert contract["blocking_todo_ids"] == [task["todo_id"]]
    progress = contract["progress_observation"]
    cli(*base, "--progress-blocker-id", progress["blocker_id"], "--progress-evidence-id", progress["evidence_ids"][0], success=False)
    assert cli("todo", "list", "--goal-id", GOAL, "--todo-id", task["todo_id"])["todo"]["status"] == "open"
    # An explicit lifecycle pause keeps work and evidence visible; retirement
    # still closes only the original duty, never the Todo or its Goal.
    cli("todo", "update", "--goal-id", GOAL, "--todo-id", task["todo_id"], "--agent-id", AGENT,
        "--status", "blocked", "--clear-resume-when", "--reason", "The admitted basis changed; reassess before execution.")
    retired = cli(*base, "--progress-blocker-id", progress["blocker_id"], "--progress-evidence-id", progress["evidence_ids"][0],
        "--vision-summary", "Reassess the bounded research question.", "--vision-acceptance", "Current evidence must satisfy the question.",
        "--vision-replan-trigger", "Keep the acceptance and paused work visible.")
    assert retired["settlement_progress"]["closeout_kind"] == "capability_duty_retired_no_spend"
    assert cli("todo", "list", "--goal-id", GOAL, "--todo-id", task["todo_id"])["todo"]["status"] == "blocked"
