"""Public synthetic workloads for long-lived Goal packet budgets.

Match production dimensions (large append history, capped review windows,
multilingual cumulative requirements, real Explore logs), not private content.
No snapshots or copied outputs are checked in.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from loopx.control_plane.work_items.progress_observation import build_replan_action_packet
from loopx.control_plane.work_items.replan_context_codec import project_replan_context
from test_replan_context_order import _rows
from test_replan_host_context_projection import AGENT_ID, GOAL_ID, _quota_payload


def populated_replan_source(root, *, acceptance=False, explore_context=None):
    source = _quota_payload(stride_strategy="evidence_conditioned_v1")
    source["replan_read_path"] = "/synthetic/decision.json"
    obligation = deepcopy(source["autonomous_replan_obligation"])
    _, decision, work = _rows()

    def scoped(value):
        if isinstance(value, dict):
            return {key: scoped(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scoped(item) for item in value]
        if value == "evidence-goal":
            return GOAL_ID
        if value == "agent-a":
            return AGENT_ID
        return value

    work, decision = scoped(work), scoped(decision)
    decision["agent_vision"]["todo_delta"] = ["retain:todo-a"]
    decision["agent_vision"]["stride_decision"] = deepcopy(source["replan_action_packet"]["stride_control"]["minimal_example"])
    decision["agent_vision"]["path_delta"].update(
        prior_assumption="The retained implementation should resolve the scoped failure under the accepted conditions. " * 2,
        observed_reality="The observed failure persists, while independent implementation work remains feasible. " * 2,
        retained=["Keep all original requirements, authority limits and the strongest qualified artifact."],
        changed=["Implement and validate the independent path while the scoped measurement remains unavailable."])
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    # 1,500 records, one accepted review, then 21 observations with mixed results.
    for index in range(1500):
        if index == 1478:
            row = deepcopy(decision)
        else:
            row = deepcopy(work)
            row.pop("quota_spend_commit", None)
            row["health_check"] = "Synthetic scoped counterexample; the alternative remains independently runnable. " * 7
            row["recommended_action"] = "Use applicable evidence to select the next implementation or discriminating check. " * 3
            result = "advanced" if index % 7 == 0 else "blocked" if index % 3 else "unchanged"
            row["progress_observation"].update(result_class=result,
                surface_id=f"surface-{index % 630}", hypothesis_id=f"hypothesis-{index % 17}",
                probe_kind=f"probe-{index % 5}", evidence_ids=[f"evidence:synthetic-{index}"])
            row["delivery_outcome"] = "outcome_progress" if result == "advanced" else "outcome_gap"
        row["generated_at"] = (start + timedelta(minutes=index)).isoformat()
        row["turn_instance_id"] = f"turn-synthetic-{index}"
        row["settlement_identity"]["turn_instance_id"] = row["turn_instance_id"]
        rows.append(row)
    contract = None
    if acceptance:
        outcomes = []
        for i in range(24):
            requirements = [{"id": f"requirement-{j}",
                "description": f"Verify synthetic outcome {i}, condition {j}, including recovery and readback.",
                "status": "unverified", "planning_satisfied": False,
                "agent_review": {"conclusion": "unmet", "basis_current": True,
                    "independently_verified": False, "evidence_refs": [f"probe:negative-{i}-{j}"],
                    "note": "合成观察保留未通过的条件；成功的局部检查不能替代完整验收。 " * 18}}
                for j in range(4 + i % 17)]
            outcomes.append({"id": f"outcome-{i}", "owner_agent_id": AGENT_ID,
                "objective": f"Deliver independently verifiable synthetic outcome {i} with recovery and current evidence.",
                "revision": 2, "requirements": requirements,
                "todo_ids": [f"todo-outcome-{i}"], "sources": [], "basis_digest": f"{i:064x}",
                "work": [{"todo_id": f"todo-outcome-{i}", "status": "open"}]})
        contract = {"outcome_requirements": {"schema_version": "loopx_goal_outcomes_v0", "revision": 24,
            "current_lifetime": True, "execution_authority_granted": False, "goal_completion_assessed": False,
            "acceptance_gaps": [], "outcomes": outcomes}}
    root.mkdir(parents=True, exist_ok=True)
    runtime = root / "runtime"
    registry = root / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": GOAL_ID, "repo": str(root),
        "objective": "Deliver a complete, independently verifiable artifact under the retained constraints.",
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT_ID]}}]}))
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    context = project_replan_context(goal_id=GOAL_ID, agent_id=AGENT_ID, obligation=obligation,
        runs=(), source_status={"registry": str(registry), "runtime_root": str(runtime), "run_history": {"runs": []}}, stride_strategy="evidence_conditioned_v1",
        goal_acceptance_contract=contract, explore_context=explore_context,
        todo_items=[{"todo_id": "todo-a", "goal_id": GOAL_ID, "role": "agent", "status": "waiting",
                     "claimed_by": AGENT_ID, "task_class": "advancement_task", "action_kind": "validate"}])
    obligation["replan_context"] = context
    source["autonomous_replan_obligation"] = obligation
    source["replan_action_packet"] = build_replan_action_packet(obligation, goal_id=GOAL_ID, agent_id=AGENT_ID)
    from loopx.control_plane.effect_runtime import effect_runtime_result
    selected = {"todo_id": "todo_independent", "text": "Implement the independent path.", "index": 1,
        "priority": "P1", "status": "open", "task_class": "advancement_task", "action_kind": "implement",
        "claimed_by": AGENT_ID}
    waiting = {**selected, "todo_id": "todo_waiting", "text": "Compare the remote measurements.", "index": 2,
        "status": "deferred", "resume_when": "todo_done:todo_measurement"}
    extra = [{**selected, "todo_id": f"todo_alternative_{i}", "index": i + 3,
        "text": (f"Deliver alternative {i} with independent behavior, failure recovery and complete acceptance readback. " * 2),
        "successor_todo_ids": ["todo_waiting"]} for i in range(3)]
    from loopx.control_plane.work_items.action_portfolio import QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION
    projected = effect_runtime_result("work_item.action_portfolio.project", {
        "schema_version": QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION,
        "planning_inventory_request": {
            "schema_version": "todo_planning_inventory_request_v0", "goal_id": GOAL_ID, "agent_id": AGENT_ID,
            "selected_todo": selected, "source_items": [selected, waiting, *extra], "runnable_candidates": [selected, *extra],
            "unavailable_higher_priority": [], "source_context_todo_count": 5},
        "projection_enabled": True, "include_detail": False, "acceptance_gaps": [],
        "goal_facts": {"registry_objective": context["core_goal"]["objective"],
            "goal_read_action": context["core_goal"].get("read_action"), "acceptance_contract": contract}})
    source.update({key: projected[key] for key in ("planning_horizon", "action_portfolio")})
    return source


def populated_explore_context(root):
    from loopx.capabilities.explore.result_log import (
        append_explore_result_event, build_explore_node_event, build_explore_finding_event,
        explore_result_log_path,
    )
    from loopx.capabilities.explore.turn_context import read_replan_explore_context

    registry = root / "registry.json"
    runtime = root / "runtime"
    registry.write_text(json.dumps({"goals": [{"id": GOAL_ID, "repo": str(root),
        "explore_graph": {"enabled": True},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT_ID]}}]}))
    log = explore_result_log_path(runtime, GOAL_ID)
    for i in range(12):
        append_explore_result_event(log, build_explore_node_event(goal_id=GOAL_ID, agent_id=AGENT_ID,
            node_id=f"route-{i}", title=f"Synthetic alternative mechanism {i}", status="open"))
        for j, status in enumerate(["confirmed", "refuted", "tentative"]):
            append_explore_result_event(log, build_explore_finding_event(goal_id=GOAL_ID, agent_id=AGENT_ID,
                node_id=f"route-{i}", finding_id=f"finding-{i}-{j}", title=f"Scoped comparison {i}-{j}", status=status,
                summary="Independent synthetic evidence with retained counterexamples. " * 12,
                evidence_refs=[f"probe:route-{i}-{j}"], tags=["writeback-result"]))
    return read_replan_explore_context(source_status={"registry": str(registry), "runtime_root": str(runtime)},
        goal_id=GOAL_ID, agent_id=AGENT_ID)
