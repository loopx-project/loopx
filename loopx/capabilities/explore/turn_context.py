"""Read adapters for Explore's typed, bounded turn context and existing hooks."""

from pathlib import Path
import shlex

from ...agent_registry import load_goal_from_registry, require_registered_agent_id
from ...control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    dispatch_turn_start_hooks,
)
from ...control_plane.effect_runtime import effect_runtime_result
from ...explore_graph import compact_explore_graph_policy
from ...todos import list_goal_todos
from .result_log import (
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events,
)
from .todo_branch_plan import (
    build_explore_todo_branch_plan,
    resolve_todo_branch_plan_gate,
)


def _policy(registry_path, goal_id):
    goal = load_goal_from_registry(registry_path, goal_id)
    if goal is None:
        raise ValueError(f"Goal {goal_id!r} is not registered")
    graph = compact_explore_graph_policy(
        goal.get("explore_graph"),
        (goal.get("spawn_policy") or {}).get("explore_harness"),
    )["enabled"]
    gate = resolve_todo_branch_plan_gate(goal.get("spawn_policy"), requested_width=3)
    return goal, graph, gate


def explore_turn_context(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str
):
    goal, graph, gate = _policy(registry_path, goal_id)
    require_registered_agent_id(
        registry_path=registry_path, goal_id=goal_id, agent_id=agent_id
    )
    projection, plan = {}, {}
    if graph or gate["enabled"]:
        events = load_explore_result_events(
            explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
        )
        projection = build_explore_result_projection(
            # Resolve explicit Todo links before the typed output budget is
            # applied. New unrelated findings must not hide an older refutation.
            events, goal_id=goal_id, finding_limit=len(events) if gate["enabled"] else 3,
            mermaid_node_limit=3,
        )
    if gate["enabled"]:
        todos = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
            role="agent",
            status="open",
            agent_id=agent_id,
            runtime_root_arg=str(runtime_root),
        )
        plan = build_explore_todo_branch_plan(
            goal_id=goal_id,
            agent_id=agent_id,
            todos=todos.get("todos") or [],
            projection=projection,
            orchestration=goal.get("spawn_policy"),
            width=3,
        )
    route = [
        "loopx",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
    ]
    return effect_runtime_result(
        "explore.turn_context",
        {
            "goal_id": goal_id,
            "agent_id": agent_id,
            "graph_enabled": graph,
            "harness_gate": gate,
            "projection": projection,
            "plan": plan,
            "route": route,
        },
    )


def extend_turn_start_dispatch(
    dispatch,
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
):
    if not agent_id:
        return dispatch
    _, graph, gate = _policy(registry_path, goal_id)
    if not graph and not gate["enabled"]:
        return dispatch

    def produce():
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "explore.turn_context",
            "capability_id": "explore",
            "phase": "turn_start",
            "status": "observed",
            "observation_count": 1,
            "agent_read_required": True,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "local_private_state_mutated": False,
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": None,
        }

    command = shlex.join(
        [
            "loopx",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "explore",
            "turn-context",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
        ]
    )
    hook = TurnStartHookRegistration(
        hook_id="explore.turn_context",
        capability_id="explore",
        requested_read_scope=("goal_capability_configuration",),
        requested_write_scope=(),
        producer=produce,
        required_read={
            "kind": "explore_turn_context",
            "command": command,
            "reason": "Read enabled Explore evidence and branch guidance before choosing work. Preserve negative evidence; planning grants no claim, spawn or execution authority.",
            "ordering": "before_work",
        },
    )
    extra = dispatch_turn_start_hooks((hook,))
    result = dict(dispatch or {})
    for key in ("results", "required_reads", "failures"):
        result[key] = list(result.get(key) or []) + list(extra.get(key) or [])
    for key in ("registered_count", "invoked_count"):
        result[key] = int(result.get(key) or 0) + int(extra.get(key) or 0)
    return result
