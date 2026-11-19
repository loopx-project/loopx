"""Transport for explicit results on normal writeback; existing owners perform effects.

The hook is pure. Its consumer uses the result log and ordinary Todo update,
never acquires a claim or lease, and can replay after partial delivery.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json

from ...agent_registry import require_registered_agent_id
from ...control_plane.capability_hooks import PostWritebackHookRegistration
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from ...cli_commands.post_writeback import dispatch_committed_cli_post_writeback_hooks
from ...control_plane.todos.list_readback import list_goal_todos
from ...control_plane.todos.mutation_api import update_goal_todo
from ...control_plane.work_items.task_lease import lease_is_active
from .turn_context import _policy, project_work_yardstick_sources
from .result_log import (
    append_explore_result_events,
    build_explore_node_event,
    build_explore_finding_event,
    build_explore_edge_event,
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
    # Only attachments with omitted scope need a state read.
    # TypeScript owns scope eligibility and normalization; this transports the
    # canonical Todo links and question records, never infers facts or authority.
    referenced = [row for row in (attachment, other_attachment) if (
        isinstance(row, dict)
        and row.get("schema_version") in {
            "explore_result_attachment_v0", "explore_result_from_path_delta_v0",
        }
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
    detail = list_goal_todos(
        registry_path=source_registry, runtime_root_arg=str(source_runtime),
        goal_id=goal_id, todo_id=todo_id,
    )
    todo = detail.get("todo")
    if (not detail.get("matched") or detail.get("ambiguous")
            or not isinstance(todo, dict) or todo.get("claimed_by") != agent_id):
        raise ValueError("Explore result attachment must belong to the caller's claimed Todo")
    projection = build_explore_result_projection(
        load_explore_result_events_strict(
            explore_result_log_path(source_runtime, goal_id), goal_id=goal_id
        ), goal_id=goal_id,
    )
    refs = todo.get("explore_result_node_refs") or []
    return {"requested_node_refs": refs,
            "nodes": [node for node in projection.get("nodes", [])
                      if node.get("node_id") in refs
                      and (None in node_ids or node.get("node_id") in node_ids)]}


def _evolves_question(attachment):
    return "question_update" in attachment or "relations" in attachment


def _evolution_plan(attachment, projection, *, agent_id):
    return effect_runtime_result("explore.result.evolution", {
        "attachment": attachment, "agent_id": agent_id,
        "nodes": projection["nodes"], "edges": projection["edges"],
    })


def _events(attachment, *, goal_id, agent_id, source_id, recorded_at, existing_node=None,
            todo_id=None, turn_instance_id=None, work_binding=None,
            source_snapshot=None,
            _check_execution_measurements=True):
    previous = existing_node or {}
    update = attachment.get("question_update") or {}
    node = build_explore_node_event(
        goal_id=goal_id,
        agent_id=agent_id,
        node_id=attachment["node_id"],
        node_kind="question",
        title=attachment["question"],
        summary=attachment["applicability"],
        status=update.get("status", previous.get("status", "open")),
        blocked_reason=(update.get("blocked_reason") if update else previous.get("blocked_reason")),
        parent_id=previous.get("parent_id"),
        evidence_refs=previous.get("evidence_refs"),
        tags=previous.get("tags"),
        supersedes=previous.get("supersedes"),
        run_id=source_id if _evolves_question(attachment) else None,
        research_observation=previous.get("research_observation"),
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
        execution_observation=attachment.get("execution_observation"),
        measurements=attachment.get("measurements"),
        work_binding=work_binding,
        work_yardstick=({"decision": attachment["work_yardstick_decision"], "binding": {
            "goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
            "turn_instance_id": turn_instance_id,
        }, **({"source_snapshot": source_snapshot} if source_snapshot is not None else {})}
            if "work_yardstick_decision" in attachment else None),
        _check_execution_measurements=_check_execution_measurements,
        recorded_at=recorded_at,
    )
    relations = [build_explore_edge_event(
        goal_id=goal_id, agent_id=agent_id, from_node=attachment["node_id"],
        to_node=relation["to_node"], edge_type=relation["edge_type"],
        summary=relation["summary"], run_id=source_id, recorded_at=recorded_at,
    ) for relation in attachment.get("relations", [])]
    return [node, finding, *relations]


def _link_arguments(
    *, registry_path, runtime_root, goal_id, agent_id, todo_id, node_id, require_active=False,
    _authority_basis=None,
):
    # Read the active authority. Normal update rechecks ownership/lease on commit.
    snapshot = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id=goal_id,
        include_leases=True,
    )
    proof = {}
    if snapshot is not None:
        if _authority_basis is not None:
            _authority_basis["provider_revision"] = snapshot["provider_revision"]
        leases = [item for item in snapshot["leases"] if item.get("todo_id") == todo_id]
        if snapshot.get("handoff_mode") == "hard_lease" or leases:
            owned = [
                item
                for item in leases
                if item.get("owner") == agent_id and (not require_active or lease_is_active(item))
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
    _replay_committed_at=None,
    _current_events=None,
    _link_options=None,
    _prepared_source=None,
    _committed_source_snapshot=...,
):
    """Validate before primary commit; this path never appends graph evidence."""
    result = normalize_result_attachment(attachment)
    if not (agent_id and todo_id and turn_instance_id):
        raise ValueError(
            "Explore result attachment requires agent, Todo and Turn identity"
        )
    goal, graph, gate = _policy(registry_path, goal_id, runtime_root)
    if not (graph or gate["enabled"]):
        raise ValueError(
            "Explore result attachment requires enabled Explore evidence or planning mode"
        )
    detail = list_goal_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        todo_id=todo_id,
    ).get("todo")
    if not isinstance(detail, dict) or detail.get("claimed_by") != agent_id:
        raise ValueError(
            "Explore result attachment must belong to the caller's claimed Todo"
        )
    events = _events(
        result,
        goal_id=goal_id,
        agent_id=agent_id,
        source_id="validation",
        recorded_at="2000-01-01T00:00:00Z",
        todo_id=todo_id, turn_instance_id=turn_instance_id,
    )
    current_events = _current_events if _current_events is not None else load_explore_result_events_strict(
        explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
    )
    existing = build_explore_result_projection(current_events, goal_id=goal_id,
        **({"finding_limit": len(current_events)} if "work_yardstick_decision" in result else {}))
    if "work_yardstick_decision" in result:
        from ..todo_replan_cadence.machine_defaults import work_yardstick_enabled
        checked = effect_runtime_result("work_item.yardstick.continuation", {
            # Only replay of the original committed attachment may finish after
            # disable; it cannot grant a new capture or change its contents.
            "enabled": _replay_committed_at is not None or work_yardstick_enabled(goal, runtime_root),
            "replay_committed_at": _replay_committed_at,
            "decision": result["work_yardstick_decision"], "evidence_refs": result["evidence_refs"],
            "binding": {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id},
            "findings": existing["findings"],
            "source_events": [{"goal_id": row["goal_id"], "event_id": row["event_id"]}
                              for row in current_events],
            **({"source_snapshot": _committed_source_snapshot}
               if _committed_source_snapshot is not ... else {}),
        })
        if _prepared_source is not None and "source_snapshot" in checked:
            _prepared_source["source_snapshot"] = checked["source_snapshot"]
    if _evolves_question(result):
        _evolution_plan(result, existing, agent_id=agent_id)
    for node in existing.get("nodes", []):
        if node.get("node_id") != result["node_id"]:
            continue
        conflicts = [key for key in ("node_kind", "title", "summary")
                     if node.get(key) != events[0].get(key)]
        if conflicts:
            raise ValueError(
                "Explore question identity conflicts with its existing scope "
                f"({', '.join(conflicts)}). Reuse the canonical question and "
                "applicability for this question id; for a distinct scoped question, "
                "omit node_id and provide explicit question and applicability. "
                "An area node cannot be reused as a question; do not rewrite its scope."
            )
    readback = update_goal_todo(
        **(_link_options or _link_arguments(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            node_id=result["node_id"],
        )),
        dry_run=True,
    )
    if not readback.get("ok"):
        raise ValueError("Explore Todo link was not admitted")
    return result


@contextmanager
def _work_finding_commit_guard(*, registry_path, runtime_root, goal_id, agent_id, todo_id,
                               node_id, proof, registry_sha256, provider_revision, yardstick_requested):
    from ...agent_registry import registered_agent_ids_from_registry
    from ...file_lock import (exclusive_cross_runtime_file_lock, exclusive_file_lock,
                              cross_runtime_lock_witness)
    from ...history import load_registry
    from ..machine_configuration.store import machine_configuration_store_path
    from ...control_plane.coordination.shadow_management import shadow_maintenance_lock_target
    from ...control_plane.goals.activation import goal_activation_state
    from ..todo_replan_cadence.machine_defaults import work_yardstick_enabled

    target = shadow_maintenance_lock_target(runtime_root, goal_id)
    with ExitStack() as locks:
        # Same marker as canonical claim/lease/lifecycle writers. A registry
        # writer may already hold R before requesting a source lock: never wait
        # for R in reverse order, matching the existing team-plan adapter.
        locks.enter_context(exclusive_cross_runtime_file_lock(target, operation="explore-work-finding"))
        locks.enter_context(exclusive_cross_runtime_file_lock(
            registry_path, operation="explore-work-finding", timeout_seconds=0))
        locks.enter_context(exclusive_file_lock(machine_configuration_store_path(runtime_root),
                                                operation="explore-work-finding"))
        goal, graph, gate = _policy(registry_path, goal_id, runtime_root)
        request = {
            "runtime_root": str(runtime_root.resolve()), "goal_id": goal_id,
            "agent_id": agent_id, "todo_id": todo_id, "node_id": node_id,
            "lock": cross_runtime_lock_witness(target),
            "registry_source": {"path": str(registry_path.resolve()),
                                "sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest()},
            "expected_registry_sha256": registry_sha256,
            "expected_provider_revision": provider_revision,
            "profile_id": load_registry(registry_path).get("profile_id"),
            "goal_authority": {"kind": "present", "goal_id": goal_id,
                               "registration_status": goal.get("status", "active"),
                               "activation_state": goal_activation_state(goal).value},
            "registered_agents": registered_agent_ids_from_registry(registry_path, goal_id),
            "explore_enabled": bool(graph or gate["enabled"]),
            "yardstick_requested": yardstick_requested,
            "yardstick_enabled": work_yardstick_enabled(goal, runtime_root),
            "proof": {"idempotency_key": proof.get("task_lease_idempotency_key"),
                      "expected_version": proof.get("task_lease_expected_version")},
        }
        # The log invokes this single admission after acquiring its own lock
        # and checking CAS, immediately before the prepared bytes are written.
        yield lambda: effect_runtime_result("explore.finding.admit", request)


def _append_prepared_result(attachment, current_events, *, runtime_root, goal_id, agent_id,
                            todo_id, turn_instance_id, source_id, recorded_at, work_binding=None,
                            source_snapshot=None, require_current_graph=False, commit_authority=None):
    evolves = _evolves_question(attachment)
    plan = (_evolution_plan(attachment, build_explore_result_projection(current_events, goal_id=goal_id),
                            agent_id=agent_id) if evolves else {})
    events = _events(
        attachment, goal_id=goal_id, agent_id=agent_id, source_id=source_id,
        recorded_at=recorded_at, existing_node=plan.get("existing_node"), todo_id=todo_id,
        turn_instance_id=turn_instance_id, work_binding=work_binding, source_snapshot=source_snapshot,
    )
    guard = (_work_finding_commit_guard(
        runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        node_id=attachment["node_id"], yardstick_requested="work_yardstick_decision" in attachment,
        **commit_authority,
    ) if commit_authority is not None else nullcontext())
    with guard as admit:
        graph = append_explore_result_events(
            explore_result_log_path(runtime_root, goal_id), events, expected_goal_id=goal_id,
            create_only_node_ids=[] if evolves else [attachment["node_id"]],
            expected_event_ids=([row["event_id"] for row in current_events]
                                if evolves or require_current_graph or "work_yardstick_decision" in attachment
                                else None),
            **({"before_commit": admit} if admit is not None else {}),
        )
    return events, graph


def _committed_finding_feedback(*, registry_path, runtime_root, goal_id, agent_id,
                                todo_id, turn_instance_id, finding_id, events,
                                detail, goal=None, preserve_disabled_wire=False):
    """Read one original canonical prefix for both ordinary write entrypoints."""
    from .turn_context import explore_read_route
    from ..todo_replan_cadence.machine_defaults import work_yardstick_enabled

    if goal is None:
        goal, _, _ = _policy(registry_path, goal_id, runtime_root)
    enabled = work_yardstick_enabled(goal, runtime_root)
    # Refresh's existing default-off wire is retained. Ordinary finding keeps
    # its pre-existing quantitative feedback when Yardstick is disabled.
    if preserve_disabled_wire and not enabled:
        return {}
    prefix = []
    for event in events:
        prefix.append(event)
        if event.get("event_kind") == "finding" and event.get("result_id") == finding_id:
            break
    else:
        raise ValueError("Explore feedback requires its committed finding")
    written = prefix[-1]
    # A retained legacy event has no claimed-work assertion to compare. Keep
    # its old readback rather than manufacture a new binding on replay.
    if "work_binding" not in written and "work_yardstick" not in written:
        return {}
    projection = build_explore_result_projection(
        prefix, goal_id=goal_id, finding_limit=len(prefix), mermaid_node_limit=3,
    )
    cited_sources = project_work_yardstick_sources(
        events=prefix, projection=projection, goal_id=goal_id, agent_id=agent_id,
        work_todos=[detail], enabled=enabled, todo_id=todo_id, result_node=written["node_id"],
    )
    return effect_runtime_result("explore.finding.receipt", dict(
        goal_id=goal_id, agent_id=agent_id, todo_id=todo_id, turn_instance_id=turn_instance_id,
        finding_id=finding_id, findings=projection["findings"], enabled=enabled,
        cited_sources=cited_sources,
        route=explore_read_route(registry_path=registry_path, runtime_root=runtime_root),
    ))


def record_work_finding(attachment, *, registry_path, runtime_root, goal_id, agent_id,
                        todo_id, turn_instance_id):
    """Record a finding on an open owned Todo without committing a Turn."""
    if not (agent_id and todo_id and turn_instance_id):
        raise ValueError("A work finding requires --agent-id, --todo-id and --turn-instance-id")
    require_registered_agent_id(registry_path=registry_path, goal_id=goal_id, agent_id=agent_id)
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    goal, graph_enabled, gate = _policy(registry_path, goal_id, runtime_root)
    if not (graph_enabled or gate["enabled"]):
        raise ValueError("A work finding requires enabled Explore evidence or planning mode")
    detail = list_goal_todos(registry_path=registry_path, runtime_root_arg=str(runtime_root),
                            goal_id=goal_id, todo_id=todo_id).get("todo")
    if not detail or detail.get("status") != "open" or detail.get("claimed_by") != agent_id:
        raise ValueError("A work finding requires the caller's open claimed Todo")
    binding = dict(goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
                   turn_instance_id=turn_instance_id)
    scope = dict(registry_path=registry_path, runtime_root=runtime_root, **binding)
    attachment = normalize_result_attachment(attachment, scope_context=dict(
        registry_path=registry_path, runtime_root_override=str(runtime_root), **binding,
    ))
    node_id = attachment["node_id"]
    authority_basis = {}
    link = _link_arguments(registry_path=registry_path, runtime_root=runtime_root,
                           goal_id=goal_id, agent_id=agent_id, todo_id=todo_id, node_id=node_id,
                           require_active=True, _authority_basis=authority_basis)
    # Exact retries of a normalized observation in this Turn keep one finding.
    digest = hashlib.sha256(json.dumps([binding, attachment], sort_keys=True).encode()).hexdigest()
    finding_id = "finding_" + digest
    log = explore_result_log_path(runtime_root, goal_id)
    current = load_explore_result_events_strict(log, goal_id=goal_id)
    prior = next((row for row in current if row.get("event_kind") == "finding"
                  and row.get("result_id") == finding_id), None)
    result = dict(ok=True, goal_id=goal_id, todo_id=todo_id, node_id=node_id,
                  finding_id=finding_id, idempotent_replay=prior is not None)
    recorded_at = prior["recorded_at"] if prior else datetime.now(timezone.utc).isoformat()
    event_scope = dict(binding, source_id="explore:work-finding:" + digest,
                       recorded_at=recorded_at, work_binding=binding)
    if prior:
        event_scope["source_snapshot"] = (prior.get("work_yardstick") or {}).get("source_snapshot")
        if _events(attachment, **event_scope)[1] != prior:
            raise ValueError("Explore work finding conflicts with its recorded evidence")
    else:
        prepared = {}
        attachment = prepare_result_attachment(attachment, **scope, _current_events=current,
                                               _link_options=link, _prepared_source=prepared)
        event_scope["source_snapshot"] = prepared.get("source_snapshot")
        _, result["graph"] = _append_prepared_result(
            attachment, current, runtime_root=runtime_root, require_current_graph=True,
            commit_authority=dict(registry_path=registry_path, proof=link,
                                  registry_sha256=registry_sha256,
                                  provider_revision=authority_basis.get("provider_revision")), **event_scope,
        )
        result["primary_committed"] = True
    try:
        if not prior or node_id not in (detail.get("explore_result_node_refs") or []):
            linked = update_goal_todo(**link)
            if not linked.get("ok"):
                raise ValueError("Explore finding persisted; Todo association needs retry")
            detail = linked.get("todo") or list_goal_todos(
                registry_path=registry_path, runtime_root_arg=str(runtime_root),
                goal_id=goal_id, todo_id=todo_id,
            ).get("todo")
            if not isinstance(detail, dict):
                raise ValueError("Explore finding persisted; Todo association readback needs retry")
        events = current if prior else load_explore_result_events_strict(log, goal_id=goal_id)
        result.update(_committed_finding_feedback(**scope, finding_id=finding_id,
            events=events, detail=detail, goal=goal))
    except Exception as error:
        result.update(ok=False, retryable=True, error=str(error))
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
        # Exact old payloads remain readable; equivalent current shorthand
        # still normalizes through the typed owner before comparing intents.
        if (attachment != intent["payload"]["attachment"]
                and normalize_result_attachment(attachment) != intent["payload"]["attachment"]):
            raise ValueError("Explore attachment conflicts with its committed ingestion intent")
        # Readback-only replay after success needs no new lease or Todo write.
        existing_events = load_explore_result_events_strict(
            explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
        )
        finding_id = "finding_" + intent["idempotency_key"].rsplit(":", 1)[-1]
        prior = next((row for row in existing_events if row.get("event_kind") == "finding"
                      and row.get("result_id") == finding_id), None)
        binding = dict(goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
                       turn_instance_id=turn_instance_id)
        expected = _events(
            intent["payload"]["attachment"],
            goal_id=goal_id,
            agent_id=agent_id,
            source_id=intent["idempotency_key"],
            recorded_at=committed_at,
            todo_id=todo_id, turn_instance_id=turn_instance_id,
            work_binding=binding if prior is None or "work_binding" in prior else None,
            source_snapshot=payload.get("explore_result_source_snapshot"),
            # A durable terminal intent can describe an old writer's event.
            # Structural reconstruction is only for exact canonical readback;
            # a missing event still goes through strict preparation below.
            _check_execution_measurements=(
                "explore.result_writeback" not in dispatch.get("replayed_hooks", [])
            ),
        )
        if prior is not None and expected[1] != prior:
            raise ValueError("Explore writeback conflicts with its recorded finding")
        detail = list_goal_todos(
            registry_path=registry_path,
            runtime_root_arg=str(runtime_root),
            goal_id=goal_id,
            todo_id=todo_id,
        ).get("todo")
        if (
            expected[1] in existing_events
            and isinstance(detail, dict)
            and attachment["node_id"] in (detail.get("explore_result_node_refs") or [])
        ):
            feedback = (_committed_finding_feedback(registry_path=registry_path, runtime_root=runtime_root,
                **binding, finding_id=finding_id, events=existing_events, detail=detail, preserve_disabled_wire=True)
                if attachment.get("measurements") or attachment.get("work_yardstick_decision") else {})
            return {
                **result,
                "ok": True,
                "retryable": False,
                "idempotent_replay": True,
                "node_id": attachment["node_id"],
                "finding_id": expected[1]["result_id"],
                "todo_id": todo_id,
                **feedback,
            }
        if expected[1] in existing_events:
            # The graph batch committed atomically; only its Todo link is missing.
            # A later owner update must survive recovery of this old receipt.
            events = expected
            result["graph"] = {"ok": True, "appended_event_count": 0,
                               "idempotent_replay": True}
        else:
            # The continuation check, evolution plan and append CAS must use
            # one snapshot. A second read after validation could silently admit
            # a predecessor made stale by a concurrent same-work delivery.
            current_events = load_explore_result_events_strict(
                explore_result_log_path(runtime_root, goal_id), goal_id=goal_id
            )
            attachment = prepare_result_attachment(
                intent["payload"]["attachment"],
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                todo_id=todo_id,
                turn_instance_id=turn_instance_id,
                _replay_committed_at=(committed_at if payload.get("idempotent_replay") is True else None),
                _current_events=current_events,
                _committed_source_snapshot=payload.get("explore_result_source_snapshot"),
            )
            events, result["graph"] = _append_prepared_result(
                attachment, current_events, runtime_root=runtime_root,
                goal_id=goal_id, agent_id=agent_id,
                source_id=intent["idempotency_key"], recorded_at=committed_at,
                todo_id=todo_id, turn_instance_id=turn_instance_id,
                work_binding=binding,
                source_snapshot=payload.get("explore_result_source_snapshot"),
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
        feedback = {}
        if attachment.get("measurements") or attachment.get("work_yardstick_decision"):
            detail = linked.get("todo") or list_goal_todos(registry_path=registry_path,
                runtime_root_arg=str(runtime_root), goal_id=goal_id, todo_id=todo_id).get("todo")
            if not isinstance(detail, dict):
                raise ValueError("Explore feedback needs its Todo association readback")
            feedback = _committed_finding_feedback(registry_path=registry_path, runtime_root=runtime_root,
                **binding, finding_id=events[1]["result_id"],
                events=load_explore_result_events_strict(explore_result_log_path(runtime_root, goal_id), goal_id=goal_id),
                detail=detail, preserve_disabled_wire=True)
        result.update(
            ok=True,
            retryable=False,
            node_id=attachment["node_id"],
            finding_id=events[1]["result_id"],
            todo_id=todo_id,
            **feedback,
        )
    except Exception as error:
        # Preserve primary truth. Replaying its exact refresh retries delivery;
        # graph event IDs and the additive owner operation are idempotent.
        result["error"] = str(error)
    return result
