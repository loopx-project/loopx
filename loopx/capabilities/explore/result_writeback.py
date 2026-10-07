"""Transport for explicit results on normal writeback; existing owners perform effects.

The hook is pure. Its consumer uses the result log and ordinary Todo update,
never acquires a claim or lease, and can replay after partial delivery.
"""

from __future__ import annotations

from ...control_plane.capability_hooks import PostWritebackHookRegistration
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from ...cli_commands.post_writeback import dispatch_committed_cli_post_writeback_hooks
from ...todos import list_goal_todos, update_goal_todo
from .turn_context import _policy
from .result_log import (
    append_explore_result_events,
    build_explore_node_event,
    build_explore_finding_event,
    explore_result_log_path,
    load_explore_result_events_strict,
    build_explore_result_projection,
)


def normalize_result_attachment(
    attachment, *, other_attachment=..., vision_packet=None, scope_context=None
):
    params = {"attachment": attachment}
    if vision_packet is not None:
        params["vision_packet"] = vision_packet
    if other_attachment is not ...:
        params["other_attachment"] = other_attachment
    # Only explicit path-delta references with omitted scope need a state read.
    # TypeScript owns scope eligibility and normalization; this transports the
    # canonical Todo links and question records, never infers facts or authority.
    referenced = [row for row in (attachment, other_attachment) if (
        isinstance(row, dict)
        and row.get("schema_version") == "explore_result_from_path_delta_v0"
        and "question" not in row and "applicability" not in row
    )]
    if scope_context is not None and referenced:
        params["linked_scope"] = _linked_scope_context(
            node_ids=[row.get("node_id") for row in referenced], **scope_context
        )
    return effect_runtime_result("explore.result.normalize", params)


def _linked_scope_context(
    *, registry_path, runtime_root_override, goal_id, agent_id, todo_id,
    turn_instance_id, node_ids,
):
    from pathlib import Path
    from ...control_plane.runtime.runtime_projection_route import (
        resolve_goal_source_runtime_route,
    )

    if not (agent_id and todo_id and turn_instance_id):
        raise ValueError("Explore result attachment requires agent, Todo and Turn identity")
    route = resolve_goal_source_runtime_route(
        registry_path=registry_path, goal_id=goal_id,
        runtime_root_override=runtime_root_override,
    )
    source_registry = Path(route["source_registry"])
    source_runtime = Path(route["source_runtime_root"])
    _, graph, gate = _policy(source_registry, goal_id)
    if not (graph or gate["enabled"]):
        raise ValueError("Explore result attachment requires enabled Explore evidence or planning mode")
    rows = list_goal_todos(
        registry_path=source_registry, runtime_root_arg=str(source_runtime),
        goal_id=goal_id, todo_id=todo_id,
    ).get("todos", [])
    if len(rows) != 1 or rows[0].get("claimed_by") != agent_id:
        raise ValueError("Explore result attachment must belong to the caller's claimed Todo")
    projection = build_explore_result_projection(
        load_explore_result_events_strict(
            explore_result_log_path(source_runtime, goal_id), goal_id=goal_id
        ), goal_id=goal_id,
    )
    return {"requested_node_refs": rows[0].get("explore_result_node_refs", []),
            "nodes": [node for node in projection.get("nodes", [])
                      if node.get("node_id") in node_ids]}


def _events(attachment, *, goal_id, agent_id, source_id, recorded_at):
    node = build_explore_node_event(
        goal_id=goal_id,
        agent_id=agent_id,
        node_id=attachment["node_id"],
        node_kind="question",
        title=attachment["question"],
        summary=attachment["applicability"],
        status="open",
        recorded_at=recorded_at,
    )
    finding = build_explore_finding_event(
        goal_id=goal_id,
        agent_id=agent_id,
        node_id=attachment["node_id"],
        finding_id="finding_" + source_id.rsplit(":", 1)[-1],
        title=attachment["question"],
        status=attachment["status"],
        summary=(
            f"Input revision: {attachment['input_revision']}\n"
            f"Applicability: {attachment['applicability']}\n"
            f"Observation: {attachment['observation']}\n"
            f"Interpretation: {attachment['interpretation']}"
        ),
        evidence_refs=attachment["evidence_refs"],
        tags=["writeback-result"],
        recorded_at=recorded_at,
    )
    return [node, finding]


def _link_arguments(
    *, registry_path, runtime_root, goal_id, agent_id, todo_id, node_id
):
    # Read the active authority. Normal update rechecks ownership/lease on commit.
    snapshot = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id=goal_id,
        include_leases=True,
    )
    proof = {}
    if snapshot is not None:
        leases = [item for item in snapshot["leases"] if item.get("todo_id") == todo_id]
        if snapshot.get("handoff_mode") == "hard_lease" or leases:
            owned = [
                item
                for item in leases
                if item.get("owner") == agent_id
            ]
            if len(owned) != 1:
                raise ValueError(
                    "Explore result delivery requires the caller's current Todo lease"
                )
            proof = {
                "task_lease_idempotency_key": owned[0]["idempotency_key"],
                "task_lease_expected_version": owned[0]["version"],
            }
    return dict(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        append_explore_result_node_refs=[node_id],
        **proof,
    )


def prepare_result_attachment(
    attachment,
    *,
    registry_path,
    runtime_root,
    goal_id,
    agent_id,
    todo_id,
    turn_instance_id,
):
    """Validate before primary commit; this path never appends graph evidence."""
    result = normalize_result_attachment(attachment)
    if not (agent_id and todo_id and turn_instance_id):
        raise ValueError(
            "Explore result attachment requires agent, Todo and Turn identity"
        )
    _, graph, gate = _policy(registry_path, goal_id)
    if not (graph or gate["enabled"]):
        raise ValueError(
            "Explore result attachment requires enabled Explore evidence or planning mode"
        )
    rows = list_goal_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        todo_id=todo_id,
    ).get("todos", [])
    if len(rows) != 1 or rows[0].get("claimed_by") != agent_id:
        raise ValueError(
            "Explore result attachment must belong to the caller's claimed Todo"
        )
    events = _events(
        result,
        goal_id=goal_id,
        agent_id=agent_id,
        source_id="validation",
        recorded_at="2000-01-01T00:00:00Z",
    )
    existing = build_explore_result_projection(
        load_explore_result_events_strict(
            explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
        ),
        goal_id=goal_id,
    )
    for node in existing.get("nodes", []):
        if node.get("node_id") == result["node_id"] and any(
            node.get(key) != events[0].get(key)
            for key in ("node_kind", "title", "summary")
        ):
            raise ValueError(
                "Explore question identity conflicts with its existing scope"
            )
    readback = update_goal_todo(
        **_link_arguments(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            node_id=result["node_id"],
        ),
        dry_run=True,
    )
    if not readback.get("ok"):
        raise ValueError("Explore Todo link was not admitted")
    return result


def deliver_result_attachment(
    *,
    payload,
    registry_path,
    runtime_root,
    goal_id,
    agent_id,
    todo_id,
    turn_instance_id,
):
    """Consume only a committed primary record; report partial failure for replay."""
    attachment = payload.get("explore_result")
    if attachment is None:
        return None
    committed_at = payload.get("explore_result_recorded_at") or payload["generated_at"]
    identity = dict(
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        effect_id=(payload.get("settlement_identity") or {}).get("effect_id", ""),
    )
    hook = PostWritebackHookRegistration(
        hook_id="explore.result_writeback",
        capability_id="explore",
        event_kinds=("refresh_state",),
        intent_kinds=("explore.result_ingestion",),
        requested_read_scope=("explore_result",),
        producer=lambda value: effect_runtime_result("explore.result.intent", value),
    )
    result = {"ok": False, "retryable": True, "primary_committed": True}
    try:
        dispatch = dispatch_committed_cli_post_writeback_hooks(
            payload=payload,
            registry_path=registry_path,
            runtime_root_arg=str(runtime_root),
            goal_id=goal_id,
            event_kind="refresh_state",
            identity=identity,
            state_version=committed_at,
            committed_at=committed_at,
            hooks=(hook,),
            projection_builder=lambda **_: {"explore_result": attachment},
        )
        result["hook"] = dispatch
        if not (
            payload.get("appended") or payload.get("idempotent_replay")
        ) or payload.get("dry_run"):
            raise ValueError("Explore ingestion requires a committed writeback")
        intents = dispatch.get("intents") or []
        if len(intents) != 1:
            raise ValueError("Explore result hook did not admit one ingestion intent")
        intent = intents[0]
        # Readback-only replay after success needs no new lease or Todo write.
        existing_events = load_explore_result_events_strict(
            explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
        )
        expected = _events(
            intent["payload"]["attachment"],
            goal_id=goal_id,
            agent_id=agent_id,
            source_id=intent["idempotency_key"],
            recorded_at=committed_at,
        )
        rows = list_goal_todos(
            registry_path=registry_path,
            runtime_root_arg=str(runtime_root),
            goal_id=goal_id,
            todo_id=todo_id,
        ).get("todos", [])
        if (
            expected[1] in existing_events
            and len(rows) == 1
            and attachment["node_id"] in (rows[0].get("explore_result_node_refs") or [])
        ):
            return {
                **result,
                "ok": True,
                "retryable": False,
                "idempotent_replay": True,
                "node_id": attachment["node_id"],
                "finding_id": expected[1]["result_id"],
                "todo_id": todo_id,
            }
        attachment = prepare_result_attachment(
            intent["payload"]["attachment"],
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
        )
        events = _events(
            attachment,
            goal_id=goal_id,
            agent_id=agent_id,
            source_id=intent["idempotency_key"],
            recorded_at=committed_at,
        )
        result["graph"] = append_explore_result_events(
            explore_result_log_path(runtime_root, goal_id),
            events,
            expected_goal_id=goal_id,
            create_only_node_ids=[attachment["node_id"]],
        )
        linked = update_goal_todo(
            **_link_arguments(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                todo_id=todo_id,
                node_id=attachment["node_id"],
            )
        )
        if not linked.get("ok"):
            raise ValueError("Explore graph persisted; Todo association needs retry")
        result.update(
            ok=True,
            retryable=False,
            node_id=attachment["node_id"],
            finding_id=events[1]["result_id"],
            todo_id=todo_id,
        )
    except Exception as error:
        # Preserve primary truth. Replaying its exact refresh retries delivery;
        # graph event IDs and the additive owner operation are idempotent.
        result["error"] = str(error)
    return result
