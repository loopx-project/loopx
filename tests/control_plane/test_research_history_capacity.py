"""Retained Todo history must not turn research closeout into a capacity failure."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.capabilities.explore.research_evidence import (
    ResearchTransportCapacityError, _research_result, append_research_observation,
)
from loopx.capabilities.explore.research_frontier import _lineage_todo_facts, build_research_composition_frontier
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_edge_event, build_explore_node_event,
    build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.work_items.task_lease import acquire_task_lease
from loopx.todos import complete_goal_todo

GOAL, AGENT = "research-history-fixture", "fixture-agent"
HARNESS = {"enabled": True, "composition_mode": "explicit_only", "composition_scope_id": "scope-joint"}
# Large enough that the unbounded Todo facts alone exceed the 2 MiB request.
HISTORY = 20_000


def observation(node: str) -> dict:
    return {
        "schema_version": "typed_research_observation_v0", "explore_node_id": node,
        "progress": {"schema_version": "typed_progress_observation_v0", "work_item_id": f"todo_{node}",
                     "result_class": "exploration_exhausted", "coverage_scope_id": f"scope-{node}",
                     "coverage_complete": True, "evidence_ids": [f"ev-{node}"]},
        "closure_basis": {"schema_version": "research_closure_basis_v0", "disposition": "bounded",
                          "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}],
                          "evidence_ids": [f"ev-{node}"]},
    }


def todo(todo_id: str, index: int, **fields) -> dict:
    # Seed the same lossless priority display that native Todo authoring produces.
    priority = fields.get("priority", "P2")
    return {"schema_version": "todo_item_v0", "todo_id": todo_id, "index": index, "role": "agent",
            "status": "open", "done": False, "text": f"[{priority}] Synthetic {todo_id}.", "archive_state": "active",
            "source_section": "Agent Todo", "priority": priority, "claimed_by": AGENT,
            "task_class": "advancement_task", **fields}


def history(count: int, start: int, obligation: str) -> list[dict]:
    # Completed, archived rows carrying an ordinary (non-research) replan binding.
    return [todo(f"todo_history_{n:05d}", start + n, status="done", done=True, archive_state="archive",
                 claimed_by=None, replan_obligation_id=obligation) for n in range(count)]


def research_graph(log: Path) -> dict:
    for name in ["a", "b", "joint"]:
        append_explore_result_event(log, build_explore_node_event(
            goal_id=GOAL, node_id=name, title=f"Research {name}", status="open" if name == "joint" else "resolved",
            node_kind="experiment" if name == "joint" else "hypothesis"))
    append_research_observation(log, goal_id=GOAL, observation=observation("b"))
    source = observation("a")
    source["composition_candidates"] = [{"basis": "explicit", "target_node_id": "b",
        "interaction_kind": "state_interference", "evidence_ids": ["ev-a", "ev-b"]}]
    append_research_observation(log, goal_id=GOAL, observation=source)
    for name in ["a", "b"]:
        append_explore_result_event(log, build_explore_edge_event(goal_id=GOAL, from_node="joint", to_node=name,
                                                                 edge_type="depends_on"))
    events = load_explore_result_events_strict(log, goal_id=GOAL)
    return build_research_composition_frontier(build_explore_result_projection(events, goal_id=GOAL),
        candidate_sources=[{"node_id": e["result_id"], "research_observation": e["research_observation"]}
                           for e in events if e.get("research_observation")],
        harness=HARNESS, todos=[], agent_id=AGENT)["selected_gap"]


def goal_fixture(tmp_path: Path, harness: dict) -> tuple[Path, Path, Path]:
    state, runtime, registry = tmp_path / "ACTIVE_GOAL_STATE.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text("# Goal\n\n## Agent Todo\n\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name, "status": "active",
        "spawn_policy": {"explore_harness": harness},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]}}]}))
    return state, runtime, registry


def complete(registry: Path, runtime: Path, todo_id: str, key: str, **fields) -> dict:
    lease = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=GOAL, todo_id=todo_id,
                               owner=AGENT, idempotency_key=key, ttl_seconds=300)
    assert lease["ok"], lease
    result = complete_goal_todo(registry_path=registry, goal_id=GOAL, todo_id=todo_id, agent_id=AGENT,
        claimed_by=AGENT, no_followup=True, evidence="Synthetic closeout.", task_lease_idempotency_key=key,
        task_lease_expected_version=lease["lease"]["version"], **fields)
    assert result["projection_delivery"] == "delivered", result.get("projection_outbox")
    assert result["projection_outbox"]["parse_render_parity"] is True
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_retained_history_does_not_block_research_or_ordinary_closeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    try:
        state, runtime, registry = goal_fixture(tmp_path, HARNESS)
        log = explore_result_log_path(runtime, GOAL)
        gap = research_graph(log)
        unrelated = "replan-0123456789abcdef"
        rows = [*history(HISTORY, 3, unrelated),
                todo("todo_plain", 1, task_domain="research", replan_obligation_id=unrelated),
                todo("todo_joint", 2, priority="P1", action_kind="joint_probe", target_key="joint",
                     explore_result_node_refs=["joint"], replan_obligation_id=gap["obligation_id"])]
        # The unbounded transport is the reviewed failure; the facts alone are oversized.
        assert len(json.dumps([{key: row[key] for key in ("todo_id", "status", "archive_state")} for row in rows])) > 1024 * 1024
        initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(goal_id=GOAL, todos=rows),
                                       state_path=state, provider=provider)

        # An ordinary Todo with a generic replan binding closes despite the history.
        plain = complete(registry, runtime, "todo_plain", "plain-closeout")
        assert plain["completed"] and not plain.get("capability_completion_evidence")

        # The research Todo still needs its exact typed result, not a capacity error.
        lease = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=GOAL, todo_id="todo_joint",
                                   owner=AGENT, idempotency_key="joint-closeout", ttl_seconds=300)["lease"]["version"]
        def close_joint(evidence: str) -> dict:
            return complete_goal_todo(registry_path=registry, goal_id=GOAL, todo_id="todo_joint", agent_id=AGENT,
                claimed_by=AGENT, no_followup=True, evidence=evidence,
                task_lease_idempotency_key="joint-closeout", task_lease_expected_version=lease)
        with pytest.raises(ValueError, match="current typed experiment observation"):
            close_joint("No result yet.")
        append_explore_result_event(log, build_explore_node_event(goal_id=GOAL, node_id="joint",
            title="Research joint", node_kind="experiment", status="resolved"))
        result = observation("joint")
        result["input_observations"] = gap["input_observations"]
        result["execution_lineage"] = {"schema_version": "research_execution_lineage_v0", "goal_id": GOAL,
            "gap_id": gap["gap_id"], "replan_obligation_id": gap["obligation_id"],
            "successor_todo_id": "todo_joint", "agent_id": AGENT}
        assert append_research_observation(log, goal_id=GOAL, observation=result, agent_id=AGENT,
                                           registry_path=registry, runtime_root=runtime)["written"]
        # A different result for the now-covered gap is stale and stays refused.
        stale = json.loads(json.dumps(result))
        stale["progress"]["evidence_ids"].append("ev-new")
        with pytest.raises(ValueError, match="pending gap"):
            append_research_observation(log, goal_id=GOAL, observation=stale, agent_id=AGENT,
                                        registry_path=registry, runtime_root=runtime)

        accepted = close_joint("Typed synthetic result.")
        assert accepted["completed"]
        assert accepted["projection_delivery"] == "delivered", accepted.get("projection_outbox")
        assert accepted["projection_outbox"]["parse_render_parity"] is True
        assert accepted["projection_outbox"]["todo_count"] == HISTORY + 2
        assert accepted["capability_completion_evidence"]["experiment_node_id"] == "joint"
        revision = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)["provider_revision"]
        replay = close_joint("Same identity retry.")
        assert replay["provider_status"] == "replayed"
        assert replay["projection_delivery"] == "current", replay.get("projection_outbox")
        assert replay["capability_completion_evidence"] == accepted["capability_completion_evidence"]
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)["provider_revision"] == revision
    finally:
        restart_effect_runtime()


def test_feature_off_closeout_never_reads_the_research_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    try:
        state, runtime, registry = goal_fixture(tmp_path, {**HARNESS, "composition_mode": "disabled"})
        log = explore_result_log_path(runtime, GOAL)
        log.parent.mkdir(parents=True)
        # Reading this graph would fail closed; feature-off closeout must not touch it.
        log.write_text("{not json\n")
        unrelated = "replan-0123456789abcdef"
        rows = [*history(HISTORY, 2, unrelated), todo("todo_plain", 1, replan_obligation_id=unrelated)]
        initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(goal_id=GOAL, todos=rows),
                                       state_path=state, provider="file")
        assert complete(registry, runtime, "todo_plain", "plain-off")["completed"]
    finally:
        restart_effect_runtime()


def test_bounded_lineage_transport_preserves_the_typed_join(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    try:
        log = explore_result_log_path(tmp_path / "runtime", GOAL)
        gap = research_graph(log)
        events = load_explore_result_events_strict(log, goal_id=GOAL)
        projection = build_explore_result_projection(events, goal_id=GOAL)
        sources = [{"node_id": e["result_id"], "research_observation": e["research_observation"]}
                   for e in events if e.get("research_observation")]
        rows = [*history(40, 10, "replan-0123456789abcdef"),
                # Retained lineage on the gap's own obligation must still travel.
                todo("todo_prior_joint", 2, status="done", done=True, archive_state="archive", claimed_by=None,
                     action_kind="joint_probe", target_key="joint", explore_result_node_refs=["joint"],
                     replan_obligation_id=gap["obligation_id"]),
                todo("todo_joint", 3, action_kind="joint_probe", target_key="joint",
                     explore_result_node_refs=["joint"], replan_obligation_id=gap["obligation_id"]),
                todo("todo_open", 4, replan_obligation_id="replan-fedcba9876543210")]
        facts = _lineage_todo_facts(rows, {gap["obligation_id"]})
        assert {fact["todo_id"] for fact in facts} == {"todo_prior_joint", "todo_joint", "todo_open"}
        with pytest.raises(ValueError, match="unique"):
            _lineage_todo_facts([*rows, rows[0]], {gap["obligation_id"]})

        bounded = build_research_composition_frontier(projection, candidate_sources=sources, harness=HARNESS,
                                                      todos=rows, agent_id=AGENT)
        # Characterize against the unbounded request the owner received before.
        params = {"goal_id": GOAL, "nodes": projection["nodes"], "edges": projection["edges"],
                  "candidate_sources": sources, "harness": HARNESS, "agent_id": AGENT}
        from loopx.capabilities.explore.research_evidence import _research_todo_facts
        from loopx.capabilities.explore.research_frontier import research_composition_obligation
        bindings = [{"gap_id": row["gap_id"], "obligation": research_composition_obligation(row, agent_id=AGENT)}
                    for row in _research_result("explore.research.composition_facts", params)["gaps"]]
        full = _research_result("explore.research.composition", {**params, "bindings": bindings,
                                "todos": [_research_todo_facts(row) for row in rows]})
        for key in ("state", "lineage_gaps", "gaps", "selected_gap", "pending_count", "scheduled_count"):
            assert bounded[key] == full[key], key
        assert bounded["lineage_gaps"][0]["successor_todo_id"] == "todo_joint"
        # Retirement reads only open, unarchived obligation rows; both views agree.
        def open_rows(frontier):
            return [row for row in frontier["obligation_tasks"]
                    if row["archive_state"] != "archive" and row["status"] in {"open", "claimed"}]
        assert open_rows(bounded) == open_rows(full)
    finally:
        restart_effect_runtime()


def test_oversized_active_lineage_is_a_capacity_error_not_an_invalid_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    try:
        state, runtime, registry = goal_fixture(tmp_path, HARNESS)
        log = explore_result_log_path(runtime, GOAL)
        gap = research_graph(log)
        # Open rows are current authority and are never dropped to fit.
        rows = [*[todo(f"todo_open_{n:05d}", n + 2, replan_obligation_id="replan-0123456789abcdef")
                  for n in range(HISTORY)],
                todo("todo_joint", 1, action_kind="joint_probe", target_key="joint",
                     explore_result_node_refs=["joint"], replan_obligation_id=gap["obligation_id"])]
        events = load_explore_result_events_strict(log, goal_id=GOAL)
        with pytest.raises(ResearchTransportCapacityError):
            build_research_composition_frontier(build_explore_result_projection(events, goal_id=GOAL),
                candidate_sources=[{"node_id": e["result_id"], "research_observation": e["research_observation"]}
                                   for e in events if e.get("research_observation")],
                harness=HARNESS, todos=rows, agent_id=AGENT)

        initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(goal_id=GOAL, todos=rows),
                                       state_path=state, provider="file")
        lease = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=GOAL, todo_id="todo_joint",
                                   owner=AGENT, idempotency_key="joint-oversized", ttl_seconds=300)["lease"]["version"]
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)["provider_revision"]
        with pytest.raises(ValueError, match="snapshot transport capacity") as refused:
            complete_goal_todo(registry_path=registry, goal_id=GOAL, todo_id="todo_joint", agent_id=AGENT,
                claimed_by=AGENT, no_followup=True, evidence="Oversized lineage.",
                task_lease_idempotency_key="joint-oversized", task_lease_expected_version=lease)
        assert "research graph is invalid" not in str(refused.value)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)["provider_revision"] == before
    finally:
        restart_effect_runtime()
