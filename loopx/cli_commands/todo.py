from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from ..control_plane.coordination.local_authority import (
    local_authority_is_promoted,
    read_canonical_todo_fields_if_promoted,
)
from ..control_plane.effect_runtime import effect_runtime_result
from ..control_plane.agents.workspace_guard import capture_delivery_workspace
from ..control_plane.todos.contract import (
    replan_successor_semantic_binding,
)
from ..control_plane.capability_hooks import PostWritebackHookRegistration
from ..control_plane.quota.settlement import (
    QuotaSettlementReadback,
    read_heartbeat_settlement,
    settlement_result_payload,
)
from ..control_plane.runtime.time import chronology_key
from ..control_plane.todos.markdown import render_todo_markdown
from ..control_plane.todos.provider_projection import (
    project_current_canonical_todos,
)
from ..history import load_index, load_registry
from ..paths import resolve_runtime_root
from ..registry import registry_goals
from ..control_plane.work_items.semantic_replan_writeback import (
    qualify_replan_writeback,
)
from ..control_plane.goals.task_planning import (
    build_task_planning_packet,
    render_task_planning_packet,
)
from ..todos import (
    add_goal_todo,
    archive_completed_todos,
    complete_goal_todo,
    list_goal_todos,
    resolve_todo_state,
    supersede_goal_todo,
    update_goal_todo,
)
from .todo_argument_validation import (
    validate_capability_gap_options,
    validate_shared_todo_options,
    validate_todo_add_options,
    validate_todo_archive_completed_options,
    validate_todo_claim_options,
    validate_todo_complete_options,
    validate_todo_list_options,
    validate_todo_receipt_options,
    validate_todo_project_markdown_options,
    validate_todo_plan_options,
    validate_todo_supersede_options,
    validate_todo_update_options,
)
from .todo_event import (
    RolloutEventAppender,
    append_todo_rollout_event,
    todo_error_payload,
)
from .post_writeback import (
    PostWritebackProjectionBuilder,
    dispatch_committed_cli_post_writeback_hooks,
)
from ..control_plane.agents.capability_gate import (
    runtime_capabilities_for_cli_projection,
)
from ..control_plane.turn_driver.journal_store import (
    turn_journal_observed_capabilities,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def _completion_settlement_error(
    settlement_readback: QuotaSettlementReadback,
    *,
    no_follow_up: bool,
) -> str | None:
    # Todo acceptance and Turn settlement are distinct facts. Ordinary
    # completion can precede accounting (including controller validation).
    # Only terminal intent requires the full chain before closing out.
    if not no_follow_up or settlement_readback.settlement.failure is None:
        return None
    return (
        "terminal no-follow-up closeout requires matching writeback and quota spend receipts: "
        + settlement_readback.settlement.failure.reason
    )


def _validated_replan_successor_obligation(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> str | None:
    requested = str(getattr(args, "replan_obligation_id", None) or "").strip()
    if not requested:
        return None
    if not (
        args.role == "agent"
        and args.task_class == "advancement_task"
        and args.claimed_by
    ):
        raise ValueError(
            "--replan-obligation-id requires --role agent --task-class "
            "advancement_task and --claimed-by"
        )
    if replan_successor_semantic_binding(
        action_kind=args.action_kind,
        target_key=args.monitor_target_key,
        explore_result_node_refs=args.explore_result_node_refs,
    ) is None:
        raise ValueError(
            "--replan-obligation-id requires --action-kind and either "
            "--target-key or --explore-result-node-ref"
        )
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    todo_fields = read_canonical_todo_fields_if_promoted(
        runtime_root=runtime_root, goal_id=args.goal_id,
    )
    state_text = ""
    if todo_fields is None:
        _, _, state_text, _ = resolve_todo_state(
            registry_path=registry_path,
            goal_id=args.goal_id,
            **_todo_path_args(args),
        )
    existing_runs, _ = load_index(
        runtime_root / "goals" / args.goal_id / "runs" / "index.jsonl"
    )
    newest_first_runs = [
        run
        for _, run in sorted(
            enumerate(existing_runs),
            key=lambda item: (
                *chronology_key(item[1].get("generated_at")),
                item[0],
            ),
            reverse=True,
        )
    ]
    registry_goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "").strip() == args.goal_id
        ),
        None,
    )
    obligation, _ = qualify_replan_writeback(
        todo_fields=todo_fields,
        newest_first_runs=newest_first_runs,
        state_text=state_text,
        agent_id=args.claimed_by,
        goal_id=args.goal_id,
        registry_goal=registry_goal,
    )
    current = str((obligation or {}).get("obligation_id") or "").strip()
    if not current:
        raise ValueError(
            "--replan-obligation-id was provided but this agent has no open "
            "replan obligation"
        )
    if current != requested:
        raise ValueError(
            "--replan-obligation-id does not match the current open obligation: "
            f"expected {current}"
        )
    return current



def _todo_path_args(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        "project": Path(args.project).expanduser() if args.project else None,
        "state_file": Path(args.state_file).expanduser() if args.state_file else None,
    }


def _render_todo_receipt(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Canonical Operation Receipt",
        "",
        f"- status: `{payload.get('status')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- operation_id: `{payload.get('operation_id')}`",
        f"- source_authority: `{payload.get('source_authority')}`",
        f"- provider_revision: `{payload.get('provider_revision')}`",
        f"- cursor: `{payload.get('cursor')}`",
        "- note: Historical readback only; it does not grant a current lease or a retry.",
    ]
    if payload.get("error") or payload.get("reason"):
        lines.append(f"- error: `{payload.get('error') or payload.get('reason')}`")
    return "\n".join(lines)


def handle_todo_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    append_cli_rollout_event: RolloutEventAppender,
    format_name: str | None = None,
    post_writeback_hooks: Sequence[PostWritebackHookRegistration] | None = None,
    post_writeback_projection_builder: PostWritebackProjectionBuilder | None = None,
) -> int:
    renderer = (
        render_task_planning_packet if args.todo_command == "plan"
        else _render_todo_receipt if args.todo_command == "receipt"
        else render_todo_markdown
    )
    try:
        if args.todo_command is None:
            raise ValueError(
                "`loopx todo` requires an explicit command; use `loopx todo add`, "
                "`loopx todo claim`, `loopx todo update`, or another command shown "
                "by `loopx todo --help`"
            )
        validate_shared_todo_options(args)
        validate_capability_gap_options(args)
        if args.todo_command == "plan":
            validate_todo_plan_options(args)
            payload = build_task_planning_packet(
                registry_path=registry_path, runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id, agent_id=args.agent_id, text=args.text,
                project=Path(args.project).expanduser() if args.project else None,
            )
        elif args.todo_command == "list":
            validate_todo_list_options(args)
            payload = list_goal_todos(
                registry_path=registry_path,
                goal_id=args.goal_id,
                role=args.role,
                status=args.status,
                todo_id=args.todo_id,
                agent_id=args.agent_id,
                limit=args.todo_limit,
                thin=bool(args.todo_thin),
                **_todo_path_args(args),
                runtime_root_arg=runtime_root_arg,
            )
        elif args.todo_command == "receipt":
            validate_todo_receipt_options(args)
            runtime_root = resolve_runtime_root(load_registry(registry_path), runtime_root_arg)
            if not local_authority_is_promoted(runtime_root=runtime_root, goal_id=args.goal_id):
                raise ValueError("todo receipt requires promoted canonical authority; no legacy fallback")
            result = effect_runtime_result(
                "coordination.local_authority.operation_receipt",
                {"schema_version": "loopx_local_coordination_operation_receipt_request_v0",
                 "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                 "goal_id": args.goal_id, "operation_id": args.operation_id},
                timeout=15.0,
            )
            if not isinstance(result, dict):
                raise RuntimeError("canonical operation receipt returned an invalid result")
            payload = {"ok": result.get("status") in {"found", "missing"},
                       "command": "receipt", **result}
        elif args.todo_command == "project-markdown":
            validate_todo_project_markdown_options(args)
            registry = load_registry(registry_path)
            projection = project_current_canonical_todos(
                registry_path=registry_path,
                runtime_root=resolve_runtime_root(registry, runtime_root_arg),
                goal_id=args.goal_id,
                expected_provider_revision=args.provider_revision,
                execute=bool(args.execute),
                registry_data=registry,
                **_todo_path_args(args),
            )
            payload = {
                "ok": True,
                "dry_run": not bool(args.execute),
                "command": "project-markdown",
                **projection,
            }
        elif args.todo_command == "add":
            validate_todo_add_options(args)
            replan_obligation_id = _validated_replan_successor_obligation(
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
            )
            payload = add_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                role=args.role,
                text=args.text,
                priority=args.priority,
                status=args.status,
                note=args.note,
                task_class=args.task_class,
                action_kind=args.action_kind,
                task_domain=args.task_domain,
                capability_binding_ref=args.capability_binding_ref,
                task_repository=args.task_repository,
                continuation_policy=args.continuation_policy,
                required_write_scopes=args.required_write_scopes,
                required_capabilities=args.required_capabilities,
                target_capabilities=args.target_capabilities,
                explore_result_node_refs=args.explore_result_node_refs,
                decision_scope=args.decision_scope,
                required_decision_scopes=args.required_decision_scopes,
                claimed_by=args.claimed_by,
                bound_agent=args.bound_agent,
                goal_bound=bool(args.goal_bound),
                blocks_agent=args.blocks_agent,
                excluded_agents=args.excluded_agents,
                global_gate=bool(args.global_gate),
                agent_id=args.agent_id,
                unblocks_todo_id=args.unblocks_todo_id,
                replan_obligation_id=replan_obligation_id,
                resume_when=args.resume_when,
                validation_command=args.validation_command,
                validation_command_json=args.validation_command_json,
                validation_label=args.validation_label,
                validation_timeout_seconds=args.validation_timeout_seconds,
                monitor_metadata={
                    key: value
                    for key, value in {
                        "target_key": args.monitor_target_key,
                        "cadence": args.cadence,
                        "next_due_at": args.next_due_at,
                        "expires_at": args.expires_at,
                        "watch_only": "true" if args.watch_only else None,
                    }.items()
                    if value is not None
                },
                **_todo_path_args(args),
                dry_run=bool(args.dry_run),
            )
            if replan_obligation_id and payload.get("ok"):
                payload["replan_transition"] = {
                    "schema_version": "replan_successor_transition_v0",
                    "obligation_id": replan_obligation_id,
                    "outcome": "new_runnable_successor",
                    "successor_todo_id": payload.get("todo_id"),
                    "recorded": not bool(payload.get("dry_run")),
                    "turn_boundary": "end_current_heartbeat",
                }
                if not payload.get("dry_run"):
                    payload["host_action"] = "end_current_heartbeat"
        elif args.todo_command == "claim":
            validate_todo_claim_options(args)
            payload = update_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                role=args.role,
                claimed_by=args.claimed_by,
                agent_id=args.agent_id,
                claim_only=True,
                claim_operation_id=args.claim_operation_id,
                task_lease_idempotency_key=args.task_lease_idempotency_key,
                task_lease_expected_version=args.task_lease_expected_version,
                **_todo_path_args(args),
                dry_run=bool(args.dry_run),
            )
        elif args.todo_command == "update":
            validate_todo_update_options(args)
            payload = update_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                text=args.text,
                priority=args.priority,
                clear_priority=args.clear_priority,
                status=args.status,
                role=args.role,
                note=args.note,
                validation_command=args.validation_command,
                validation_command_json=args.validation_command_json,
                validation_label=args.validation_label,
                validation_timeout_seconds=args.validation_timeout_seconds,
                evidence=args.evidence,
                reason=args.reason,
                task_class=args.task_class,
                action_kind=args.action_kind,
                task_domain=args.task_domain,
                task_repository=args.task_repository,
                continuation_policy=args.continuation_policy,
                required_write_scopes=args.required_write_scopes,
                required_capabilities=args.required_capabilities,
                target_capabilities=args.target_capabilities,
                explore_result_node_refs=(
                    []
                    if args.clear_explore_result_node_refs
                    else args.explore_result_node_refs
                ),
                decision_scope=args.decision_scope,
                required_decision_scopes=args.required_decision_scopes,
                claimed_by=args.claimed_by,
                bound_agent=args.bound_agent,
                goal_bound=bool(args.goal_bound),
                blocks_agent=args.blocks_agent,
                clear_blocks_agent=bool(args.clear_blocks_agent),
                excluded_agents=args.excluded_agents,
                clear_excluded_agents=bool(args.clear_excluded_agents),
                global_gate=bool(args.global_gate),
                clear_global_gate=bool(args.clear_global_gate),
                agent_id=args.agent_id,
                authority_reason=args.authority_reason,
                unblocks_todo_id=args.unblocks_todo_id,
                successor_todo_ids=args.successor_todo_ids,
                resume_when=args.resume_when,
                clear_resume_when=bool(args.clear_resume_when),
                no_followup=True if args.no_follow_up else None,
                monitor_metadata={
                    key: value
                    for key, value in {
                        "target_key": args.monitor_target_key,
                        "cadence": args.cadence,
                        "next_due_at": args.next_due_at,
                        "expires_at": args.expires_at,
                        "watch_only": "true" if args.watch_only else None,
                    }.items()
                    if value is not None
                },
                clear_claim=bool(args.clear_claim),
                update_operation_id=args.update_operation_id,
                update_expected_provider_revision=args.update_expected_provider_revision,
                task_lease_idempotency_key=args.task_lease_idempotency_key,
                task_lease_expected_version=args.task_lease_expected_version,
                **_todo_path_args(args),
                dry_run=bool(args.dry_run),
            )
        elif args.todo_command == "complete":
            validate_todo_complete_options(args)
            settlement_result = None
            settlement_identity = None
            settlement_readback = None
            completion_error = None
            completion_turn_key = None
            completion_identity_source = None
            completion_delivery_workspace = None
            if getattr(args, "turn_instance_id", None):
                runtime_root = resolve_runtime_root(
                    load_registry(registry_path),
                    runtime_root_arg,
                )
                settlement_readback = read_heartbeat_settlement(
                    runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    todo_id=args.todo_id,
                    turn_instance_id=getattr(args, "turn_instance_id", None),
                )
                if settlement_readback is None:
                    raise RuntimeError(
                        "exact settlement readback unexpectedly returned not-found"
                    )
                settlement_result = settlement_readback.identity
                if settlement_result.failure is not None:
                    raise ValueError(settlement_result.failure.reason)
                if settlement_result.value is None:
                    raise ValueError("turn-scoped Todo completion has no identity")
                identity = settlement_result.value
                settlement_identity = identity
                todo_payload = list_goal_todos(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    todo_id=args.todo_id,
                    project=Path(args.project).expanduser() if args.project else None,
                    state_file=(
                        Path(args.state_file).expanduser()
                        if args.state_file
                        else None
                    ),
                    runtime_root_arg=runtime_root_arg,
                )
                todo = (
                    todo_payload.get("todo")
                    if isinstance(todo_payload.get("todo"), dict)
                    else None
                )
                if todo is None:
                    raise ValueError(
                        "turn-scoped Todo completion requires one durable Todo"
                    )
                completion_error = _completion_settlement_error(
                    settlement_readback=settlement_readback,
                    no_follow_up=bool(args.no_follow_up),
                )
                if completion_error is not None:
                    settlement_result = settlement_readback.settlement
                    payload = {
                        "ok": False,
                        "dry_run": bool(args.dry_run),
                        "completed": False,
                        "changed": False,
                        "goal_id": args.goal_id,
                        "todo_id": args.todo_id,
                        "settlement_blocked_completion": True,
                        "settlement_identity": identity.as_dict(),
                        "settlement_result": settlement_result_payload(
                            settlement_result
                        ),
                        "error": completion_error,
                    }
                completion_turn_key = identity.effect_id
                completion_identity_source = "turn_settlement"
                writeback_run = settlement_readback.writeback_run
                if isinstance(writeback_run, dict) and isinstance(
                    writeback_run.get("delivery_workspace"), dict
                ):
                    completion_delivery_workspace = dict(
                        writeback_run["delivery_workspace"]
                    )
                elif todo.get("task_repository"):
                    # Completion validation precedes accountable refresh, so
                    # the exact Turn can legitimately have no writeback row
                    # yet. Bind a freshly verified current-worktree snapshot
                    # to this already-read settlement identity rather than
                    # introducing an arbitrary cwd option or a circular gate.
                    completion_delivery_workspace = capture_delivery_workspace(
                        Path.cwd(),
                        peer_independent_worktree_required=True,
                        repository_source="todo.complete.turn_settlement",
                    )
            elif getattr(args, "completion_identity_key", None):
                completion_turn_key = str(args.completion_identity_key)
                completion_identity_source = "lifecycle_reentry"
            if completion_error is None:
                payload = complete_goal_todo(
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    goal_id=args.goal_id,
                    todo_id=args.todo_id,
                    role=args.role,
                    decision_outcome=args.decision_outcome,
                    evidence=args.evidence,
                    completion_turn_key=completion_turn_key,
                    completion_identity_source=completion_identity_source,
                    completion_delivery_workspace=completion_delivery_workspace,
                    completion_validation_workspace_path=Path.cwd(),
                    task_lease_idempotency_key=args.task_lease_idempotency_key,
                    task_lease_expected_version=args.task_lease_expected_version,
                    note=args.note,
                    no_followup=bool(args.no_follow_up),
                    successor_todo_ids=args.successor_todo_ids,
                    claimed_by=args.claimed_by,
                    clear_claim=bool(args.clear_claim),
                    next_agent_todo=args.next_agent_todo,
                    next_user_todo=args.next_user_todo,
                    next_user_task_class=args.next_user_task_class,
                    next_claimed_by=args.next_claimed_by,
                    next_task_class=args.next_task_class,
                    next_action_kind=args.next_action_kind,
                    next_task_repository=args.next_task_repository,
                    next_required_capabilities=args.next_required_capabilities,
                    next_continuation_policy=args.next_continuation_policy,
                    next_excluded_agents=args.next_excluded_agents,
                    self_merged=bool(args.self_merged),
                    agent_id=args.agent_id,
                    authority_reason=args.authority_reason,
                    **_todo_path_args(args),
                    dry_run=bool(args.dry_run),
                )
                if settlement_identity is not None:
                    payload["settlement_identity"] = settlement_identity.as_dict()
                    payload["settlement_result"] = settlement_result_payload(
                        settlement_result
                    )
        elif args.todo_command == "supersede":
            validate_todo_supersede_options(args)
            payload = supersede_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                role=args.role,
                reason=args.reason,
                next_agent_todo=args.next_agent_todo,
                next_user_todo=args.next_user_todo,
                next_user_task_class=args.next_user_task_class,
                next_claimed_by=args.next_claimed_by,
                next_task_class=args.next_task_class,
                next_action_kind=args.next_action_kind,
                next_task_repository=args.next_task_repository,
                next_required_capabilities=args.next_required_capabilities,
                next_continuation_policy=args.next_continuation_policy,
                next_excluded_agents=args.next_excluded_agents,
                agent_id=args.agent_id,
                authority_reason=args.authority_reason,
                task_lease_idempotency_key=args.task_lease_idempotency_key,
                task_lease_expected_version=args.task_lease_expected_version,
                **_todo_path_args(args),
                dry_run=bool(args.dry_run),
            )
        elif args.todo_command == "archive-completed":
            validate_todo_archive_completed_options(args)
            payload = archive_completed_todos(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                role=args.role or "agent",
                max_active_done=args.max_active_done,
                **_todo_path_args(args),
                dry_run=not bool(args.execute),
            )
        else:
            raise ValueError("unsupported todo command")
    except Exception as exc:
        payload = todo_error_payload(args, exc)
    append_todo_rollout_event(
        payload,
        args=args,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        append_cli_rollout_event=append_cli_rollout_event,
    )

    if (
        args.todo_command == "complete"
        and getattr(args, "turn_instance_id", None)
        and payload.get("ok")
        and not payload.get("dry_run")
    ):
        runtime_root = resolve_runtime_root(
            load_registry(registry_path),
            runtime_root_arg,
        )
        settlement_readback = read_heartbeat_settlement(
            runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            todo_id=args.todo_id,
            turn_instance_id=getattr(args, "turn_instance_id", None),
        )
        if settlement_readback is None:
            raise RuntimeError("exact settlement readback unexpectedly returned not-found")
        settlement_result = (
            settlement_readback.terminal_settlement
            if args.no_follow_up and settlement_identity is not None
            else settlement_readback.identity
        )
        payload["settlement_result"] = settlement_result_payload(
            settlement_result
        )
        if settlement_result.failure is not None:
            payload["ok"] = False
            payload["receipt_repair_required"] = True
            payload["error"] = settlement_result.failure.reason
    if (
        args.todo_command == "complete"
        and payload.get("ok")
        and payload.get("completed")
        and not payload.get("dry_run")
        and post_writeback_hooks
        and settlement_identity is not None
    ):
        identity = settlement_identity.as_dict()
        committed_at = str(payload.get("updated_at") or "").strip()
        if committed_at:
            # Capability evidence comes only from a Turn journal the TS
            # journal owner validated against this completion's full
            # settlement identity (goal/agent/binding/turn/effect): the
            # journaled envelope froze what this exact Turn's scheduler
            # observed. No fully-bound journal means no evidence, and gated
            # successors stay excluded (fail closed).
            observed = turn_journal_observed_capabilities(
                resolve_runtime_root(load_registry(registry_path), runtime_root_arg),
                settlement_identity=identity,
            )
            projected = runtime_capabilities_for_cli_projection(observed)
            if projected:
                payload["available_capabilities"] = projected
            payload["post_writeback_hooks"] = (
                dispatch_committed_cli_post_writeback_hooks(
                    payload=payload,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    goal_id=args.goal_id,
                    event_kind="todo_complete",
                    identity=identity,
                    state_version=committed_at,
                    committed_at=committed_at,
                    hooks=post_writeback_hooks,
                    projection_builder=post_writeback_projection_builder,
                )
            )
    print_payload(
        payload,
        format_name or str(getattr(args, "format", None) or "markdown"),
        renderer,
    )
    return 0 if payload.get("ok") else 1
