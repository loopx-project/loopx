"""Read adapters for Explore's typed, bounded turn context and existing hooks."""

from typing import Any, cast

from collections.abc import Mapping
from pathlib import Path
import shlex

from ...agent_registry import load_goal_from_registry, require_registered_agent_id
from ...control_plane.capability_hooks import (
    INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    InteractionProjectionHookRegistration,
    dispatch_interaction_projection_hooks,
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    TurnStartContextProjector,
    dispatch_turn_start_hooks,
)
from ...control_plane.effect_runtime import effect_runtime_request_scope, effect_runtime_result
from ...explore_graph import compact_explore_graph_policy
from ...control_plane.todos.list_readback import list_goal_todos
from .result_log import (
    EVENT_KIND_FINDING,
    _fold_by_result_id,
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
)
from .todo_branch_plan import (
    build_explore_todo_branch_plan,
    resolve_todo_branch_plan_gate,
)


def _policy(registry_path: Path, goal_id: str, runtime_root: Path | None = None) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    goal = load_goal_from_registry(registry_path, goal_id)
    if goal is None:
        raise ValueError(f"Goal {goal_id!r} is not registered")
    from .machine_defaults import resolve_explore_goal
    if runtime_root is None:
        from ...control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
        route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
        runtime_root = Path(route["source_runtime_root"])
    goal = resolve_explore_goal(goal, runtime_root)
    graph = compact_explore_graph_policy(
        goal.get("explore_graph"),
        (goal.get("spawn_policy") or {}).get("explore_harness"),
    )["enabled"]
    gate = resolve_todo_branch_plan_gate(goal.get("spawn_policy"), requested_width=3)
    return goal, graph, gate


def read_replan_explore_context(*, source_status: Mapping[str, Any], goal_id: str, agent_id: str | None,
                               review_pages: Any = None, preferred_yardstick_todo_id: str | None = None) -> dict[str, Any] | None:
    """Outer read adapter; the replan owner invokes it only for enabled reviews.

    Unavailable IO is distinct from an empty or disabled evidence source. No
    policy, ranking or applicability decision is made by this transport.
    """
    if not agent_id or not source_status.get("registry") or not source_status.get("runtime_root"):
        return None
    try:
        return explore_turn_context(
            registry_path=Path(source_status["registry"]), runtime_root=Path(source_status["runtime_root"]),
            goal_id=goal_id, agent_id=agent_id, include_finding_digests=True,
            preferred_yardstick_todo_id=preferred_yardstick_todo_id,
            **({"review_pages": review_pages} if review_pages is not None else {}))
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "goal_id": goal_id, "agent_id": agent_id}


def project_work_yardstick_sources(
    *, events: list[dict[str, Any]], projection: Mapping[str, Any],
    goal_id: str, agent_id: str, work_todos: list[dict[str, Any]],
    enabled: bool, todo_id: str | None = None, result_node: str | None = None,
    preferred_yardstick_todo_id: str | None = None,
) -> list[dict[str, Any]]:
    """Replay typed-owner-selected prefixes from one already-read canonical log.

    Ordinary reads and immediate finding receipts share this I/O adapter; the
    typed owner selects current captures, verifies their prefixes and scopes
    the resulting measurements. This performs no write or additional log read.
    """
    if not enabled:
        return []
    requests = effect_runtime_result("work_item.yardstick.sources", {
        "yardstick_enabled": enabled, "work_todos": work_todos,
        "goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id, "result_node": result_node,
        "preferred_yardstick_todo_id": preferred_yardstick_todo_id,
        "findings": projection.get("findings", []),
        "source_events": [{"goal_id": row["goal_id"], "event_id": row["event_id"]} for row in events],
    })
    sources, prefixes = [], {}
    for request in requests["sources"]:
        count = request["source_snapshot"]["event_count"]
        if count not in prefixes:
            prefixes[count] = build_explore_result_projection(
                events[:count], goal_id=goal_id, finding_limit=count, mermaid_node_limit=1,
            )["findings"]
        sources.append({**request, "findings": prefixes[count]})
    return sources


def explore_read_route(*, registry_path: Path, runtime_root: Path) -> list[str]:
    """Retain the caller's exact source when a bounded result needs more detail."""
    return ["loopx", "--registry", str(registry_path), "--runtime-root", str(runtime_root), "--format", "json"]


@effect_runtime_request_scope()
def explore_turn_context(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str,
    result_limit: int = 3, result_offset: int = 0,
    result_node: str | None = None, result_revision: str | None = None,
    include_finding_digests: bool = False,
    review_pages: Any = None, todo_id: str | None = None,
    preferred_yardstick_todo_id: str | None = None,
) -> dict[str, Any]:
    if type(include_finding_digests) is not bool:
        raise ValueError("include_finding_digests must be an explicit boolean")
    goal, graph, gate = _policy(registry_path, goal_id, runtime_root)
    require_registered_agent_id(
        registry_path=registry_path, goal_id=goal_id, agent_id=agent_id
    )
    from ..todo_replan_cadence.machine_defaults import work_yardstick_enabled
    yardstick = work_yardstick_enabled(goal, runtime_root)
    projection, plan, focus, todos = {}, {}, {}, {}
    page_tasks = {row["todo_id"] for row in review_pages
                  if isinstance(row, Mapping) and isinstance(row.get("todo_id"), str)} if isinstance(review_pages, list) else set()
    if graph or gate["enabled"]:
        events = load_explore_result_events_strict(
            explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
        )
        projection = build_explore_result_projection(
            # Resolve explicit Todo links before the typed output budget is
            # applied. New unrelated findings must not hide an older refutation.
            events, goal_id=goal_id, finding_limit=len(events),
            mermaid_node_limit=3,
        )
        if yardstick and any("execution_observation" in row and "recorded_order" not in row
                             for row in projection["findings"]):
            # Supply canonical ordinals only to the opted-in work reader.
            # Folding remains with the existing result-history owner.
            orders = {row["result_id"]: row["_recorded_order"]
                      for row in _fold_by_result_id(events, event_kind=EVENT_KIND_FINDING)}
            projection["findings"] = [
                {**row, "recorded_order": orders[row["finding_id"]]}
                if "execution_observation" in row and "recorded_order" not in row else row
                for row in projection["findings"]
            ]
    if gate["enabled"] or graph and (todo_id is not None or page_tasks or yardstick):
        todos = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
            role="agent",
            status="open",
            agent_id=agent_id,
            runtime_root_arg=str(runtime_root),
        )
    if gate["enabled"]:
        plan = build_explore_todo_branch_plan(
            goal_id=goal_id,
            agent_id=agent_id,
            todos=todos.get("todos") or [],
            projection=projection,
            orchestration=goal.get("spawn_policy"),
            width=3,
        )
    # Transport only the existing identity/link fields consumed by the typed
    # reader, not task descriptions, notes or unrelated acceptance material.
    scopes = {row["todo_id"]: {key: row[key] for key in (
        "todo_id", "role", "status", "done", "claimed_by", "explore_result_node_refs") if key in row}
        for row in todos.get("todos", []) if row.get("todo_id") == todo_id or row.get("todo_id") in page_tasks}
    if (graph or gate["enabled"]) and todo_id is not None:
        # Reuse the canonical open-task snapshot, including waiting tasks
        # omitted by the independent planner. The typed owner checks scope.
        focus = {"todo_id": todo_id, "focus_todo": scopes.get(todo_id)}
    route = explore_read_route(registry_path=registry_path, runtime_root=runtime_root)
    work_todos = [{key: row[key] for key in (
        "todo_id", "role", "status", "done", "claimed_by", "explore_result_node_refs") if key in row}
        for row in todos.get("todos", [])] if yardstick else []
    sources = []
    if yardstick and graph:
        sources = project_work_yardstick_sources(events=events, projection=projection,
            goal_id=goal_id, agent_id=agent_id, work_todos=work_todos, enabled=yardstick,
            todo_id=todo_id, result_node=result_node, preferred_yardstick_todo_id=preferred_yardstick_todo_id)
    return cast(dict[str, Any], effect_runtime_result(
        "explore.turn_context",
        {
            "goal_id": goal_id,
            "agent_id": agent_id,
            "graph_enabled": graph,
            "harness_gate": gate,
            "projection": projection,
            "plan": plan,
            **({"yardstick_enabled": True, "work_todos": work_todos, "yardstick_sources": sources,
                "preferred_yardstick_todo_id": preferred_yardstick_todo_id} if yardstick else {}),
            **focus,
            **({"review_todos": [scopes[key] for key in sorted(page_tasks) if key in scopes]} if page_tasks else {}),
            "route": route,
            "result_limit": result_limit,
            "result_offset": result_offset,
            "result_node": result_node,
            "result_revision": result_revision,
            **({"include_finding_digests": True} if include_finding_digests else {}),
            **({"review_pages": review_pages} if review_pages is not None else {}),
        },
    ))


def turn_context_projector(
    *, registry_path: Path, runtime_root: Path, goal_id: str,
    agent_id: str | None, todo_id: str | None = None,
) -> TurnStartContextProjector | None:
    """Bind an entrypoint's existing read request after governing selection.

    Merely enabling a Goal or supplying unrelated hooks cannot request this
    read. The decision owner supplies facts; this adapter changes no admission.
    """
    if not agent_id:
        return None

    def project(payload: Mapping[str, Any], dispatch: Mapping[str, Any] | None) -> dict[str, Any] | None:
        channel = (payload.get("interaction_contract") or {}).get("agent_channel") or {}
        selected = payload.get("selected_todo") or {}
        focus = (selected.get("todo_id") if payload.get("normal_delivery_allowed") is True
                 and channel.get("delivery_allowed") is True and selected.get("status") == "open" else None)
        return extend_turn_start_dispatch(dict(dispatch or {}),
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, agent_id=agent_id, todo_id=todo_id, selected_todo_id=focus)

    return project


def extend_turn_start_dispatch(
    dispatch: dict[str, Any] | None,
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    todo_id: str | None = None,
    selected_todo_id: str | None = None,
) -> dict[str, Any] | None:
    if not agent_id:
        return dispatch
    goal, graph, gate = _policy(registry_path, goal_id, runtime_root)
    if not graph and not gate["enabled"]:
        return dispatch
    if selected_todo_id is not None:
        from ..todo_replan_cadence.machine_defaults import work_yardstick_enabled

        # This optional policy's current-work card follows the resolved work
        # identity. With the policy off, preserve the caller's original scope.
        if work_yardstick_enabled(goal, runtime_root):
            todo_id = selected_todo_id

    def produce() -> dict[str, Any]:
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
            *(["--todo-id", todo_id] if todo_id is not None else []),
        ]
    )
    hook = TurnStartHookRegistration(
        hook_id="explore.turn_context",
        capability_id="explore",
        requested_read_scope=("goal_capability_configuration",),
        requested_write_scope=(),
        producer=produce,
        context_reader=lambda: explore_turn_context(
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, agent_id=agent_id, todo_id=todo_id),
        required_read={
            "kind": "explore_turn_context",
            "command": command,
            "reason": "Read enabled Explore evidence and branch guidance before choosing work. Preserve negative evidence; planning grants no claim, spawn or execution authority.",
            "ordering": "before_work",
        },
    )
    extra = dispatch_turn_start_hooks((hook,))
    result = dict(dispatch or {})
    for key in ("results", "required_reads", "failures", "contexts"):
        if key == "contexts" and not extra.get(key):
            continue
        result[key] = list(result.get(key) or []) + list(extra.get(key) or [])
    for key in ("registered_count", "invoked_count"):
        result[key] = int(result.get(key) or 0) + int(extra.get(key) or 0)
    return result


def project_settlement_attachment(plan: dict[str, Any], *, registry_path: Path, runtime_root: Path | None = None) -> dict[str, Any]:
    """Compose the existing optional result option into a Todo-bound CLI plan.

    Registry access and hook dispatch are adapters. The typed Explore owner
    supplies the guidance; neither the ordinary command nor obligations change.
    """
    identity = plan.get("identity") or {}
    if not identity.get("todo_id") or not identity.get("agent_id"):
        return plan

    def produce() -> dict[str, Any]:
        _, graph, _ = _policy(registry_path, identity["goal_id"], runtime_root)
        return {
            "schema_version": INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "explore.settlement_attachment",
            "capability_id": "explore",
            "phase": "interaction_projection",
            "status": "candidate" if graph else "not_applicable",
            "projection_slot": "explore_result_attachment" if graph else None,
            "payload": {} if graph else None,
        }

    dispatch = dispatch_interaction_projection_hooks((InteractionProjectionHookRegistration(
        hook_id="explore.settlement_attachment", capability_id="explore",
        projection_slots=("explore_result_attachment",),
        requested_read_scope=("goal.explore_policy",), producer=produce,
    ),))
    attachment = dispatch["projections"].get("explore_result_attachment")
    if attachment is None:
        return ({**plan, "capability_hook_failures": dispatch["failures"]}
                if dispatch["failures"] else plan)
    # Only the result-aware refresh command can consume this attachment. The
    # caller's identity, lease, validation, spend and recovery rules stay intact.
    steps = []
    for step in plan.get("ordered_steps", []):
        if (isinstance(step, Mapping) and step.get("kind") == "durable_writeback"
                and step.get("command_template")):
            step = {**step, "optional_attachments": [attachment]}
        steps.append(step)
    return {**plan, "ordered_steps": steps}
