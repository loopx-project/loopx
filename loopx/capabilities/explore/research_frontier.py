"""Live Explore transport. TypeScript owns policy, eligibility and lineage.

The common replan owner retains obligation identity. No provider mutation or
work authority is performed while projecting research evidence.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...control_plane.work_items.autonomous_replan_obligation import build_autonomous_replan_obligation_payload
from .research_evidence import _research_result, _research_todo_facts


def read_research_todo_history(
    *, runtime_root: Path, goal: dict[str, Any], state_text: str | None = None,
) -> list[dict[str, Any]]:
    """Read canonical active and retained rows, never a compact display list.

    The existing Todo codecs supply authority and lifecycle facts. Archives
    are evidence lineage only; the typed owner decides their eligibility.
    """
    from ...control_plane.coordination.local_authority import canonical_todo_items, read_canonical_todos_if_promoted
    from ...control_plane.todos.active_state_todo_parser import parse_active_state_todos, parse_todo_source
    from ...materials import goal_state_path

    canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal["id"])
    if canonical is not None:
        guards = canonical.get("goal_acceptance_work_guards") or {}
        return [{**todo, **({"goal_acceptance_guard": guards[todo["todo_id"]]} if todo["todo_id"] in guards else {})}
                for todo in canonical_todo_items(canonical["todos"]) if todo.get("role") != "user"]
    if state_text is None:
        path = goal_state_path(goal)
        if path is None:
            raise ValueError("research lineage requires the Goal's canonical Todo source")
        state_text = path.read_text(encoding="utf-8")
    active = parse_active_state_todos(state_text, item_limit=None, goal=goal).get("agent_todos", {}).get("items", [])
    _, archived, _ = parse_todo_source(state_text, goal=goal)
    return [*active, *[todo for todo in archived if todo.get("role") != "user"]]


def research_composition_obligation(gap: Mapping[str, Any], *, agent_id: str | None) -> dict[str, Any]:
    guard = {"schema_version": "semantic_replan_capability_guard_v0", "capability_id": "explore",
             "gap_id": gap["gap_id"], "frontier_revision": gap["frontier_revision"]}
    return build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0", agent_id=agent_id, include_agent_id=True,
        stall_threshold=0, trigger_count=1,
        triggers=[{"kind": "capability_evidence_gap", "capability_id": "explore", "agent_id": agent_id,
                   "frontier_identity": gap["gap_id"], "frontier_revision": gap["frontier_revision"],
                   "text": "An explicit evidence gap requires a bound experiment and typed result."}],
        guidance_actions=["create_successor", "record_typed_result"],
        todo_actions=[{"action": "add", "role": "agent", "priority": "P1",
                       "text": gap.get("successor_summary") or "Run a bounded experiment for the explicit input set."}],
        stop_condition="Respect the Goal's existing authority, budget and protected-operation gates.",
        recommended_action="Bind a runnable experiment to the exact evidence gap or record its typed, attributable result.",
        extra_fields={"capability_guard": guard, "satisfying_semantic_outcomes": [
            "new_runnable_successor", "capability_evidence_observed", "new_concrete_blocker", "capability_duty_retired"]},
    )


def build_research_composition_frontier(
    projection: Mapping[str, Any], *, candidate_sources: list[dict[str, Any]],
    harness: Mapping[str, Any], todos: Sequence[Mapping[str, Any]], agent_id: str | None,
) -> dict[str, Any]:
    params = {"goal_id": projection["goal_id"], "nodes": projection["nodes"], "edges": projection["edges"],
              "candidate_sources": candidate_sources, "harness": dict(harness), "agent_id": agent_id}
    facts = _research_result("explore.research.composition_facts", params)
    bindings = [{"gap_id": gap["gap_id"], "obligation": research_composition_obligation(gap, agent_id=agent_id)}
                for gap in facts["gaps"]]
    frontier = _research_result("explore.research.composition", {**params, "bindings": bindings,
                                "todos": [_research_todo_facts(todo) for todo in todos]})
    by_gap = {binding["gap_id"]: binding["obligation"] for binding in bindings}
    # These internal candidates are needed for the original Turn's settlement
    # after its exact successor changes the actionable frontier. They are not
    # added to the public quota packet.
    transitions = []
    for gap in frontier["lineage_gaps"]:
        obligation = by_gap[gap["gap_id"]]
        if gap["status"] == "scheduled":
            delta = {"schema_version": "replan_semantic_delta_v0", "accepted": True,
                     "obligation_id": obligation["obligation_id"], "outcomes": ["new_runnable_successor"],
                     "satisfying_outcomes": ["new_runnable_successor"], "successor_todo_id": gap["successor_todo_id"],
                     "capability_guard": obligation["capability_guard"],
                     "capability_outcome": "new_runnable_composition_experiment"}
            transitions.append({"obligation": obligation, "ack": {
                "schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "source": "todo_replan_successor_transition", "semantic_delta": delta}})
    frontier["settlement_transitions"] = transitions
    selected = frontier.get("selected_gap")
    frontier["obligation"] = by_gap[selected["gap_id"]] if selected else None
    # Compact guard facts include every candidate, so invalidation and omitted
    # display cards cannot be mistaken for a missing settled obligation.
    frontier["guard_facts"] = [{"capability_guard": binding["obligation"]["capability_guard"],
                                 "obligation_id": binding["obligation"]["obligation_id"]} for binding in bindings]
    frontier["public_projection"] = public_research_composition_frontier(frontier)
    return frontier


def public_research_composition_frontier(frontier: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if frontier is None:
        return None
    public = {key: value for key, value in frontier.items()
              if key not in {"lineage_gaps", "settlement_transitions", "guard_facts", "obligation", "obligation_tasks", "public_projection"}}
    if frontier.get("schema_version") == "research_composition_frontier_v0":
        public["gaps"] = [{key: value for key, value in gap.items() if key != "execution_results"}
                          for gap in frontier["gaps"]]
        if frontier.get("selected_gap"):
            public["selected_gap"] = {key: value for key, value in frontier["selected_gap"].items() if key != "execution_results"}
    return public


def prepare_research_replan_evidence(
    *, runtime_root: Path, goal_id: str, agent_id: str, registry_goal: dict[str, Any] | None,
    state_text: str, capability_guard: Mapping[str, Any] | None = None,
):
    """IO composition root adapter; the shared gate never imports Explore."""
    import shlex
    from ...control_plane.work_items.semantic_replan_writeback import CapabilityReplanEvidence
    from .composition_frontier import project_live_explore_composition_frontier

    goal = {**(registry_goal or {}), "id": goal_id}
    harness = (goal.get("spawn_policy") or {}).get("explore_harness") or {}
    if not (harness.get("enabled") is True and harness.get("composition_mode") == "explicit_only") and capability_guard is None:
        return None
    frontier = project_live_explore_composition_frontier(runtime_root=runtime_root, goal_id=goal_id,
        agent_id=agent_id, state_text=state_text, capability_guard=capability_guard,
        status_payload={"run_history": {"goals": [goal]}}) or {}

    def qualify(guard: Mapping[str, Any], obligation_id: str, progress: dict[str, Any] | None, runs: list[dict[str, Any]]):
        if guard.get("capability_id") != "explore":
            raise ValueError("selected capability writeback owner is not available")
        obligation = research_composition_obligation(
            {"gap_id": guard["gap_id"], "frontier_revision": guard["frontier_revision"]}, agent_id=agent_id)
        if obligation["obligation_id"] != obligation_id:
            raise ValueError("selected capability guard does not match its obligation identity")
        delta = _research_result("explore.research.composition_writeback", {"frontier": frontier,
            "capability_guard": dict(guard), "obligation_id": obligation_id, "progress_observation": progress,
            "claimed_progress_fingerprints": [(run.get("progress_observation") or {}).get("fingerprint") for run in runs],
            "claimed_blocker_ids": [(run.get("progress_observation") or {}).get("blocker_id") for run in runs]})
        delta["readback_actions"] = [f"loopx --runtime-root {shlex.quote(str(runtime_root))} explore summary "
                                    f"--goal-id {shlex.quote(goal_id)} --agent-id {shlex.quote(agent_id)} --format json"]
        return obligation, delta
    return CapabilityReplanEvidence(frontier=frontier, qualify=qualify)


def attach_research_execution_projection(
    projection: dict[str, Any], *, events: list[dict[str, Any]], registry: dict[str, Any],
    runtime_root: Path, agent_id: str | None,
) -> None:
    """Render the same live facts through existing Explore and sink fields."""
    from ...agent_registry import registered_agent_ids_for_goal
    from ...materials import find_registry_goal
    from ...control_plane.todos.contract import normalize_todo_claimed_by

    goal = find_registry_goal(registry, projection["goal_id"]) or {}
    harness = (goal.get("spawn_policy") or {}).get("explore_harness") or {}
    if harness.get("enabled") is not True or harness.get("composition_mode") != "explicit_only":
        return
    registered = registered_agent_ids_for_goal(goal)
    actor = normalize_todo_claimed_by(agent_id) if agent_id else registered[0] if len(registered) == 1 else None
    if actor is None or actor not in registered:
        raise ValueError("Live research presentation requires --agent-id naming one registered Goal agent")
    frontier = build_research_composition_frontier(projection,
        candidate_sources=[{"node_id": e["result_id"], "research_observation": e["research_observation"]}
                           for e in events if e.get("research_observation")],
        harness=harness, todos=read_research_todo_history(runtime_root=runtime_root, goal=goal), agent_id=actor)
    projection["research_execution_frontier"] = public_research_composition_frontier(frontier)
    by_node: dict[str, list[dict[str, Any]]] = {}
    for gap in frontier["lineage_gaps"]:
        for node_id in [*gap["input_node_ids"], *gap["experiment_node_ids"]]:
            by_node.setdefault(node_id, []).append(gap)
    for node in projection["nodes"]:
        gaps = by_node.get(node["node_id"], [])
        if not gaps:
            continue
        facts = [f"Execution composition ({actor}): {gap['gap_id']} {gap['status']}"
                 for gap in gaps[:3]]
        if len(gaps) > 3:
            facts.append(f"{len(gaps) - 3} additional execution candidates omitted")
        node["research_summary"] = "\n".join([node.get("research_summary") or "", *facts]).strip()


@contextmanager
def hold_research_completion_evidence(
    *, registry_path: Path, runtime_root: Path, goal_id: str, todo: Mapping[str, Any],
    state_text: str, actor_agent_id: str | None,
):
    """Legacy writer transport; the same typed rule guards canonical commits."""
    from ...history import load_registry
    from ...materials import find_registry_goal
    from ...file_lock import exclusive_file_lock
    from .result_log import build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict
    if not (todo.get("action_kind") == "joint_probe" or todo.get("explore_result_node_refs")
            or todo.get("replan_obligation_id") or todo.get("capability_binding_ref")):
        yield None
        return
    goal = find_registry_goal(load_registry(registry_path), goal_id) or {}
    harness = (goal.get("spawn_policy") or {}).get("explore_harness") or {}
    policy = _research_result("explore.research.composition_policy", {"harness": harness})
    if not policy["enabled"] or todo.get("status") == "done" or todo.get("role") == "user":
        yield None
        return
    todos = read_research_todo_history(runtime_root=runtime_root, goal=goal, state_text=state_text)
    path = explore_result_log_path(runtime_root, goal_id)
    with exclusive_file_lock(path, operation="research-legacy-completion"):
        events = load_explore_result_events_strict(path, goal_id=goal_id)
        projection = build_explore_result_projection(events, goal_id=goal_id)
        frontier = build_research_composition_frontier(projection,
            candidate_sources=[{"node_id": e["result_id"], "research_observation": e["research_observation"]}
                               for e in events if e.get("research_observation")],
            harness=harness, todos=todos, agent_id=actor_agent_id or todo.get("claimed_by"))
        guard = _research_result("explore.research.completion", {"frontier": frontier,
            "todo": _research_todo_facts(todo), "actor_agent_id": actor_agent_id, "goal_id": goal_id})
        if guard["allowed"] is not True:
            raise ValueError(str(guard["reason"]))
        yield guard["evidence"]
