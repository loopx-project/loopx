from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...control_plane.quota.goal_boundary import registry_goal_by_id
from ...control_plane.todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_explore_result_node_refs,
    normalize_todo_claimed_by,
)
from ...control_plane.todos.todo_semantics import (
    todo_item_is_actionable_open,
    todo_item_task_class,
)
from ...orchestration import compact_orchestration_policy
from .result_log import (
    NODE_KIND_EXPERIMENT,
    NODE_STATUS_DEAD_END,
    NODE_STATUS_EXPLORING,
    NODE_STATUS_OPEN,
    NODE_STATUS_RESOLVED,
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
)

COMPOSITION_FRONTIER_SCHEMA_VERSION = "loopx_explore_composition_frontier_v0"
COMPOSITION_GAP_SCHEMA_VERSION = "loopx_explore_composition_gap_v0"
COMPOSITION_EDGE_TYPE = "depends_on"
MAX_PROJECTED_GAPS = 3

_CLOSED_INPUT_STATUSES = {
    NODE_STATUS_RESOLVED,
    NODE_STATUS_DEAD_END,
}
_OPEN_EXPERIMENT_STATUSES = {
    NODE_STATUS_OPEN,
    NODE_STATUS_EXPLORING,
}


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _todo_items(*sources: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for raw in source.get("items") or []:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            identity = str(item.get("todo_id") or item.get("index") or "").strip()
            if not identity:
                identity = _digest(item)
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)
    return items


def _bound_experiment_refs(
    todos: Sequence[Mapping[str, Any]],
    *,
    agent_id: str | None,
) -> set[str]:
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    refs: set[str] = set()
    for todo in todos:
        if not todo_item_is_actionable_open(dict(todo)):
            continue
        if todo_item_task_class(dict(todo)) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        claimed_by = normalize_todo_claimed_by(todo.get("claimed_by"))
        if safe_agent_id and claimed_by != safe_agent_id:
            continue
        refs.update(normalize_explore_result_node_refs(todo.get("explore_result_node_refs")))
    return refs


def _evidence_backed(node: Mapping[str, Any]) -> bool:
    return bool(node.get("evidence_refs") or int(node.get("finding_count") or 0) > 0)


def build_explore_composition_frontier(
    projection: Mapping[str, Any],
    *,
    todos: Sequence[Mapping[str, Any]] = (),
    agent_id: str | None = None,
    enabled: bool,
) -> dict[str, Any]:
    """Project explicit, evidence-backed composition experiments as bounded gaps.

    A composition experiment is an existing open Explore ``experiment`` node
    with at least two outgoing ``depends_on`` edges to closed, evidence-backed
    input nodes.  The projection never infers arbitrary node pairs, so its
    runtime and reviewer surface remain linear in the recorded graph.
    """

    goal_id = str(projection.get("goal_id") or "").strip()
    base: dict[str, Any] = {
        "schema_version": COMPOSITION_FRONTIER_SCHEMA_VERSION,
        "goal_id": goal_id,
        "enabled": bool(enabled),
        "derivation": "explicit_experiment_dependencies",
        "candidate_count": 0,
        "pending_count": 0,
        "scheduled_count": 0,
        "gaps": [],
        "selected_gap": None,
    }
    if not enabled:
        base["state"] = "disabled"
        base["reason"] = "explore_harness_opt_in_required"
        return base

    nodes = [node for node in projection.get("nodes") or [] if isinstance(node, Mapping)]
    edges = [edge for edge in projection.get("edges") or [] if isinstance(edge, Mapping)]
    nodes_by_id = {
        str(node.get("node_id") or ""): node
        for node in nodes
        if str(node.get("node_id") or "")
    }
    inputs_by_experiment: dict[str, set[str]] = {}
    for edge in edges:
        if str(edge.get("edge_type") or "") != COMPOSITION_EDGE_TYPE:
            continue
        experiment_id = str(edge.get("from_node") or "")
        input_id = str(edge.get("to_node") or "")
        if experiment_id and input_id:
            inputs_by_experiment.setdefault(experiment_id, set()).add(input_id)

    bound_refs = _bound_experiment_refs(todos, agent_id=agent_id)
    candidates: list[dict[str, Any]] = []
    for experiment_id, input_ids in inputs_by_experiment.items():
        experiment = nodes_by_id.get(experiment_id)
        if not isinstance(experiment, Mapping):
            continue
        if str(experiment.get("node_kind") or "") != NODE_KIND_EXPERIMENT:
            continue
        if str(experiment.get("status") or "") not in _OPEN_EXPERIMENT_STATUSES:
            continue
        inputs = [nodes_by_id.get(input_id) for input_id in sorted(input_ids)]
        if len(inputs) < 2 or any(not isinstance(node, Mapping) for node in inputs):
            continue
        if any(str(node.get("status") or "") not in _CLOSED_INPUT_STATUSES for node in inputs):
            continue
        if any(not _evidence_backed(node) for node in inputs):
            continue
        scheduled = experiment_id in bound_refs
        input_refs = [str(node.get("node_id") or "") for node in inputs]
        gap_identity = {
            "goal_id": goal_id,
            "experiment_node_ref": experiment_id,
            "input_node_refs": input_refs,
        }
        candidates.append(
            {
                "schema_version": COMPOSITION_GAP_SCHEMA_VERSION,
                "gap_id": "composition-" + _digest(gap_identity),
                "experiment_node_ref": experiment_id,
                "input_node_refs": input_refs,
                "input_count": len(input_refs),
                "title": str(experiment.get("title") or "")[:200],
                "status": "scheduled" if scheduled else "pending",
                "required_outcome": "joint_experiment_result",
                "successor_summary": (
                    "Run the bounded joint experiment: "
                    + str(experiment.get("title") or experiment_id)
                )[:240],
                "successor_binding": {
                    "action_kind": "joint_probe",
                    "task_domain": "research",
                    "target_key": experiment_id,
                    "explore_result_node_refs": [experiment_id],
                },
            }
        )

    candidates.sort(
        key=lambda gap: (
            gap["status"] != "pending",
            -int(gap["input_count"]),
            str(gap["gap_id"]),
        )
    )
    pending = [gap for gap in candidates if gap["status"] == "pending"]
    base.update(
        {
            "state": "pending" if pending else "satisfied",
            "candidate_count": len(candidates),
            "pending_count": len(pending),
            "scheduled_count": len(candidates) - len(pending),
            "gaps": candidates[:MAX_PROJECTED_GAPS],
            "selected_gap": pending[0] if pending else None,
        }
    )
    return base


def project_live_explore_composition_frontier(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    status_payload: Mapping[str, Any],
    state_text: str | None = None,
    capability_guard: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read the goal's Explore graph for one live quota decision.

    The goal boundary remains the only opt-in authority.  Invalid logs fail
    closed into a compact unavailable projection rather than breaking quota.
    """

    goal = registry_goal_by_id(status_payload).get(goal_id) or next(
        (
            candidate
            for candidate in (
                status_payload.get("run_history", {}).get("goals", [])
                if isinstance(status_payload.get("run_history"), Mapping)
                else []
            )
            if isinstance(candidate, Mapping)
            and str(candidate.get("id") or "") == goal_id
        ),
        {},
    )
    orchestration = compact_orchestration_policy(goal.get("spawn_policy"))
    harness = (
        orchestration.get("explore_harness")
        if isinstance(orchestration.get("explore_harness"), Mapping)
        else {}
    )
    enabled = harness.get("enabled") is True
    log_path = explore_result_log_path(runtime_root, goal_id)
    pinned_research = (capability_guard or {}).get("capability_id") == "explore"
    if pinned_research and (not enabled or harness.get("composition_mode") != "explicit_only"):
        from .research_frontier import build_research_composition_frontier, read_research_todo_history
        return build_research_composition_frontier({"goal_id": goal_id, "nodes": [], "edges": []},
            candidate_sources=[], harness=harness, todos=read_research_todo_history(
                runtime_root=runtime_root, goal=dict(goal), state_text=state_text), agent_id=agent_id)
    if not enabled:
        return None
    try:
        events = load_explore_result_events_strict(log_path, goal_id=goal_id)
    except ValueError:
        return {
            "schema_version": COMPOSITION_FRONTIER_SCHEMA_VERSION,
            "goal_id": goal_id,
            "enabled": enabled,
            "state": "unavailable",
            "reason": "invalid_explore_result_log",
            "candidate_count": 0,
            "pending_count": 0,
            "scheduled_count": 0,
            "gaps": [],
            "selected_gap": None,
        }
    projection = build_explore_result_projection(events, goal_id=goal_id)
    item = next(
        (
            candidate
            for candidate in (
                status_payload.get("attention_queue", {}).get("items", [])
                if isinstance(status_payload.get("attention_queue"), Mapping)
                else []
            )
            if isinstance(candidate, Mapping)
            and str(candidate.get("goal_id") or "") == goal_id
        ),
        {},
    )
    project_asset = (
        item.get("project_asset")
        if isinstance(item.get("project_asset"), Mapping)
        else {}
    )
    item_todos = item.get("agent_todos") if isinstance(item.get("agent_todos"), Mapping) else {}
    asset_todos = (
        project_asset.get("agent_todos")
        if isinstance(project_asset.get("agent_todos"), Mapping)
        else {}
    )
    if harness.get("composition_mode") == "explicit_only":
        from .research_frontier import build_research_composition_frontier, read_research_todo_history
        return build_research_composition_frontier(
            projection, candidate_sources=[
                {"node_id": event["result_id"], "research_observation": event["research_observation"]}
                for event in events if event.get("research_observation")
            ], harness=harness, todos=read_research_todo_history(
                runtime_root=runtime_root, goal=dict(goal), state_text=state_text), agent_id=agent_id,
        )
    return build_explore_composition_frontier(
        projection,
        todos=_todo_items(item_todos, asset_todos),
        agent_id=agent_id,
        enabled=enabled,
    )
