from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable, Sequence
from functools import partial
from operator import itemgetter
from pathlib import Path

from ..command_invocation import cli_option_args, loopx_cli_argv
from ..control_plane.agents.workspace_guard import capture_delivery_workspace
from ..control_plane.capability_hooks import PostWritebackHookRegistration
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
    local_authority_is_promoted,
)
from ..control_plane.effect_runtime import effect_runtime_result
from ..control_plane.quota.effect_program import SettlementBindingKind
from ..control_plane.quota.settlement import (
    SettlementFailureKind,
    SettlementIdentity,
    read_heartbeat_settlement,
    settlement_result_payload,
)
from ..control_plane.quota.heartbeat_receipt import (
    bind_replan_successor_heartbeat_receipt,
)
from ..control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    capture_first_party_host_goal_ref,
)
from ..control_plane.goals.source_session_registry_state import exact_goal_ref
from ..control_plane.goals.task_planning import (
    build_task_planning_packet,
    render_task_planning_packet,
)
from ..control_plane.todos.completion_result import read_completion_result
from ..control_plane.todos.list_readback import list_goal_todos
from ..control_plane.todos.markdown import render_todo_markdown
from ..control_plane.todos.mutation_api import (
    add_goal_todo,
    archive_completed_todos,
    complete_goal_todo,
    supersede_goal_todo,
    update_goal_todo,
)
from ..control_plane.todos.provider_projection import (
    project_current_canonical_todos,
)
from ..history import load_registry
from ..paths import resolve_runtime_root
from .post_writeback import PostWritebackProjectionBuilder
from .todo_argument_validation import (
    validate_capability_gap_options,
    validate_shared_todo_options,
    validate_todo_add_options,
    validate_todo_archive_completed_options,
    validate_todo_claim_options,
    validate_todo_complete_options,
    validate_todo_execution_recovery_options,
    validate_todo_plan_options,
    validate_todo_project_markdown_options,
    validate_todo_receipt_options,
    validate_todo_result_read_options,
    validate_todo_supersede_options,
    validate_todo_update_options,
)
from .todo_event import (
    RolloutEventAppender,
    append_todo_rollout_event,
    todo_error_payload,
)
from .todo_recovery import (
    _recover_todo_execution_once as _recover_todo_execution_once,
    todo_path_args as _todo_path_args,
)
from .todo_settlement import (
    _completion_settlement_error as _completion_settlement_error,
    _read_todo_turn_settlement as _read_todo_turn_settlement,
    _todo_settlement_plan as _todo_settlement_plan,
    _validated_replan_successor_obligation as _validated_replan_successor_obligation,
    dispatch_todo_complete_post_writeback,
    reconcile_todo_complete_settlement,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


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


def _thin_claim_projection(payload: dict[str, object], route: Sequence[str]) -> None:
    """Omit per-Todo display diagnostics from a successfully delivered claim."""
    outbox = payload.get("projection_outbox")
    if (
        not isinstance(outbox, dict)
        or outbox.get("schema_version") != "loopx_todo_projection_delivery_v0"
        or outbox.get("status") not in ("delivered", "current")
        or outbox.get("next_action") != "finish"
        or outbox.get("parse_render_parity") is not True
        or outbox.get("narrative_preserved") is not True
        or outbox.get("legacy_fallback_used") is not False
    ):
        return
    rows = outbox.get("omitted_active_records")
    revision = outbox.get("provider_revision")
    goal_id = payload.get("goal_id")
    if (
        not isinstance(rows, list)
        or not rows
        or outbox.get("active_fields_omitted") != len(rows)
        or not isinstance(revision, str)
        or not revision
        or not isinstance(goal_id, str)
        or not goal_id
    ):
        return
    command = [
        *route,
        "todo",
        "project-markdown",
        "--goal-id",
        goal_id,
        "--provider-revision",
        revision,
    ]
    for field in ("project", "state_file"):
        path = outbox.get(field)
        if isinstance(path, str) and path:
            command.extend(["--" + field.replace("_", "-"), path])
    payload["projection_outbox"] = {
        **{key: value for key, value in outbox.items() if key != "omitted_active_records"},
        "detail_command": shlex.join(command),
    }


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
    if args.todo_command == "list":
        from .todo_list import handle_todo_list_command

        return handle_todo_list_command(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            print_payload=print_payload,
            append_cli_rollout_event=append_cli_rollout_event,
            format_name=format_name,
            list_reader=list_goal_todos,
        )
    renderer = render_todo_markdown
    if args.todo_command == "plan":
        renderer = render_task_planning_packet
    elif args.todo_command == "receipt":
        renderer = _render_todo_receipt
    elif args.todo_command == "result-read":
        renderer = itemgetter("text")
    goal_ref: dict[str, str] | None = None
    settlement_identity: SettlementIdentity | None = None
    try:
        if args.todo_command is None:
            raise ValueError(
                "`loopx todo` requires an explicit command; use `loopx todo add`, "
                "`loopx todo claim`, `loopx todo update`, or another command shown "
                "by `loopx todo --help`"
            )
        if args.todo_command == "claim":
            validate_todo_claim_options(args)
        validate_shared_todo_options(args)
        validate_todo_execution_recovery_options(args)
        validate_capability_gap_options(args)
        if getattr(args, "turn_instance_id", None):
            goal_instance_id = str(
                getattr(args, "goal_instance_id", None) or ""
            ).strip()
            goal_ref = (
                exact_goal_ref(args.goal_id, goal_instance_id)
                if goal_instance_id
                else capture_first_party_host_goal_ref(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                )
            )
        if args.todo_command == "plan":
            validate_todo_plan_options(args)
            payload = build_task_planning_packet(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                text=args.text,
                project=Path(args.project).expanduser() if args.project else None,
            )
        elif args.todo_command == "receipt":
            validate_todo_receipt_options(args)
            runtime_root = resolve_runtime_root(
                load_registry(registry_path), runtime_root_arg
            )
            if not local_authority_is_promoted(
                runtime_root=runtime_root, goal_id=args.goal_id
            ):
                raise ValueError(
                    "todo receipt requires promoted canonical authority; no legacy fallback"
                )
            result = effect_runtime_result(
                "coordination.local_authority.operation_receipt",
                {
                    "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
                    "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                    "goal_id": args.goal_id,
                    "operation_id": args.operation_id,
                },
                timeout=15.0,
            )
            if not isinstance(result, dict):
                raise RuntimeError("canonical operation receipt returned an invalid result")
            payload = {
                "ok": result.get("status") in {"found", "missing"},
                "command": "receipt",
                **result,
            }
        elif args.todo_command == "result-read":
            validate_todo_result_read_options(args)
            registry = load_registry(registry_path)
            payload = read_completion_result(
                registry_path=registry_path,
                runtime_root=resolve_runtime_root(registry, runtime_root_arg),
                goal_id=args.goal_id,
                todo_id=args.todo_id,
            )
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
            add_todo = partial(
                add_goal_todo,
                operation_id=args.operation_id,
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
            )
            successor_settlement = None
            runtime_root = None
            if replan_obligation_id and args.turn_instance_id and not args.dry_run:
                # A failed Todo validation must not leave an otherwise ordinary
                # Turn receipt irreversibly bound to a successor that was never
                # created. Preview through the same mutation owner first.
                preview = add_todo(dry_run=True)
                if preview.get("ok") is not True:
                    raise ValueError(
                        str(
                            preview.get("error")
                            or "replan successor could not be validated"
                        )
                    )
                runtime_root = resolve_runtime_root(
                    load_registry(registry_path), runtime_root_arg
                )
                successor_settlement = read_heartbeat_settlement(
                    runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.claimed_by,
                    todo_id=None,
                    turn_instance_id=args.turn_instance_id,
                    resolve_original_binding=True,
                    registry_path=registry_path,
                    goal_ref=goal_ref,
                )
                if (
                    successor_settlement is not None
                    and successor_settlement.identity.value is None
                    and successor_settlement.identity.failure is not None
                    and successor_settlement.identity.failure.kind
                    is SettlementFailureKind.RECEIPT_UNBOUND
                ):
                    bind_replan_successor_heartbeat_receipt(
                        runtime_root,
                        goal_id=args.goal_id,
                        agent_id=args.claimed_by,
                        turn_instance_id=args.turn_instance_id,
                        replan_obligation_id=replan_obligation_id,
                        goal_ref=goal_ref,
                    )
                    successor_settlement = read_heartbeat_settlement(
                        runtime_root,
                        goal_id=args.goal_id,
                        agent_id=args.claimed_by,
                        todo_id=None,
                        turn_instance_id=args.turn_instance_id,
                        resolve_original_binding=True,
                        registry_path=registry_path,
                        goal_ref=goal_ref,
                    )
                if successor_settlement is None or successor_settlement.identity.value is None:
                    raise ValueError(
                        "replan successor requires the original admitted Turn binding"
                    )
                if successor_settlement.identity.failure is not None:
                    raise ValueError(successor_settlement.identity.failure.reason)
                identity = successor_settlement.identity.value
                if (
                    identity.binding_kind is not SettlementBindingKind.AUTONOMOUS_REPLAN
                    or identity.replan_obligation_id != replan_obligation_id
                ):
                    raise ValueError(
                        "replan successor has a different settlement identity"
                    )
                selected = (successor_settlement.semantic_replan_guard or {}).get(
                    "selected_obligation_id"
                )
                if selected != replan_obligation_id:
                    raise ValueError(
                        "replan successor does not match the original Turn obligation"
                    )
            payload = add_todo(dry_run=bool(args.dry_run))
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
                    if successor_settlement is None:
                        payload["host_action"] = "end_current_heartbeat"
                    else:
                        assert runtime_root is not None
                        identity = successor_settlement.identity.value
                        assert identity is not None
                        payload["settlement_identity"] = identity.as_dict()
                        payload["settlement_plan"] = _todo_settlement_plan(
                            identity,
                            args=args,
                            registry_path=registry_path,
                            runtime_root=runtime_root,
                            goal_ref=goal_ref,
                        )
                        payload["replan_transition"]["turn_boundary"] = (
                            "settle_original_turn_before_end"
                        )
                        payload["instruction"] = (
                            "Validate the successor and finish the returned original Turn settlement plan, "
                            "then end this heartbeat. Do not select or execute the successor in this Turn. "
                            "Creation alone is not a settled review."
                        )
        elif args.todo_command == "claim":
            payload = update_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                role=args.role,
                claimed_by=args.claimed_by,
                agent_id=args.agent_id,
                claim_only=True,
                manage_execution=(
                    args.agent_id is not None
                    and args.agent_id == args.claimed_by
                    and args.task_lease_idempotency_key is None
                    and args.task_lease_expected_version is None
                ),
                claim_operation_id=args.claim_operation_id,
                task_lease_idempotency_key=args.task_lease_idempotency_key,
                task_lease_expected_version=args.task_lease_expected_version,
                **_todo_path_args(args),
                dry_run=bool(args.dry_run),
            )
            if payload.get("ok") and payload.get("execution_managed") and not args.dry_run:
                runtime_root = resolve_runtime_root(
                    load_registry(registry_path), runtime_root_arg
                )
                route = [
                    *loopx_cli_argv(),
                    *cli_option_args("--registry", registry_path),
                    *cli_option_args("--runtime-root", runtime_root),
                    "--format",
                    "json",
                ]
                selected = [
                    *cli_option_args("--goal-id", args.goal_id),
                    *cli_option_args("--todo-id", args.todo_id),
                    *cli_option_args("--agent-id", args.agent_id),
                    *cli_option_args(
                        "--claim-operation-id", payload["claim_operation_id"]
                    ),
                ]
                payload["next_cli_actions"] = [
                    shlex.join([*route, "todo", action, *selected, *extra])
                    for action, extra in (
                        (
                            "update",
                            [
                                "--update-operation-id",
                                "<progress-operation-id>",
                                "--note",
                                "<progress>",
                            ],
                        ),
                        ("complete", ["--evidence", "<validated-evidence>"]),
                    )
                ]
                payload["instruction"] = (
                    "Continue the task. Use this original claim reference for progress "
                    "or completion; retain normal Turn settlement when applicable."
                )
                # Normal callers need the accepted task reference, not lease
                # counters or a second inline copy of the native receipt.
                for internal in (
                    "lease",
                    "lease_changed",
                    "lease_idempotent",
                    "original_receipt",
                ):
                    payload.pop(internal, None)
                if args.todo_thin:
                    _thin_claim_projection(payload, route)
        elif args.todo_command == "update":
            validate_todo_update_options(args)
            update_operation = partial(
                update_goal_todo,
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
            try:
                payload = update_operation(
                    claim_operation_id=args.claim_operation_id,
                )
            except LocalCoordinationAuthorityRejection as initial_error:
                if not args.recover_execution:
                    raise
                payload = _recover_todo_execution_once(
                    args,
                    initial_error,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    retry_operation=lambda reference: update_operation(
                        claim_operation_id=reference,
                    ),
                )
        elif args.todo_command == "complete":
            validate_todo_complete_options(args)
            settlement_result = None
            settlement_readback = None
            completion_error = None
            completion_turn_key = None
            completion_identity_source = None
            completion_delivery_workspace = None
            if getattr(args, "turn_instance_id", None):
                runtime_root = resolve_runtime_root(
                    load_registry(registry_path), runtime_root_arg
                )
                settlement_readback = _read_todo_turn_settlement(
                    args,
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_ref=goal_ref,
                )
                settlement_result = settlement_readback.identity
                assert settlement_result.value is not None
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
                    progress = settlement_readback.progress
                    assert progress is not None
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
                        "settlement_plan": _todo_settlement_plan(
                            identity,
                            args=args,
                            registry_path=registry_path,
                            runtime_root=runtime_root,
                            goal_ref=goal_ref,
                            quota_spend_source=progress["quota_spend_source"],
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
                complete_operation = partial(
                    complete_goal_todo,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    goal_id=args.goal_id,
                    todo_id=args.todo_id,
                    role=args.role,
                    decision_outcome=args.decision_outcome,
                    evidence=args.evidence,
                    completion_result_file=(
                        Path(args.result_file).expanduser()
                        if args.result_file
                        else None
                    ),
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
                try:
                    payload = complete_operation(
                        claim_operation_id=args.claim_operation_id,
                    )
                except LocalCoordinationAuthorityRejection as initial_error:
                    if not args.recover_execution:
                        raise
                    payload = _recover_todo_execution_once(
                        args,
                        initial_error,
                        registry_path=registry_path,
                        runtime_root_arg=runtime_root_arg,
                        retry_operation=lambda reference: complete_operation(
                            claim_operation_id=reference,
                        ),
                        completion_turn_key=completion_turn_key,
                    )
                if settlement_identity is not None:
                    payload["settlement_identity"] = settlement_identity.as_dict()
                    payload["settlement_result"] = settlement_result_payload(
                        settlement_result
                    )
        elif args.todo_command == "supersede":
            validate_todo_supersede_options(args)
            supersede_readback = (
                _read_todo_turn_settlement(
                    args,
                    registry_path=registry_path,
                    runtime_root=resolve_runtime_root(
                        load_registry(registry_path), runtime_root_arg
                    ),
                    goal_ref=goal_ref,
                )
                if args.turn_instance_id
                else None
            )
            payload = supersede_goal_todo(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                role=args.role,
                reason=args.reason,
                successor_todo_ids=args.successor_todo_ids,
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
                claim_operation_id=args.claim_operation_id,
                **_todo_path_args(args),
                dry_run=bool(args.dry_run),
            )
            if supersede_readback is not None:
                retirement_identity = supersede_readback.identity.value
                assert retirement_identity is not None
                payload["settlement_identity"] = retirement_identity.as_dict()
                payload["settlement_result"] = settlement_result_payload(
                    supersede_readback.identity
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
        if goal_ref is not None:
            payload["goal_ref"] = dict(goal_ref)
    except Exception as exc:
        from ..usage_ping import capture_failure

        capture_failure(exc)
        payload = todo_error_payload(
            args, exc, registry_path=registry_path, runtime_root_arg=runtime_root_arg
        )
    if (
        args.todo_command == "complete"
        and payload.get("ok")
        and settlement_identity is None
        and isinstance(payload.get("settlement_identity"), dict)
    ):
        settlement_identity = SettlementIdentity.from_runtime_payload(
            payload["settlement_identity"]
        )
    append_todo_rollout_event(
        payload,
        args=args,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        append_cli_rollout_event=append_cli_rollout_event,
    )

    if (
        args.todo_command == "complete"
        and payload.get("ok")
        and settlement_identity is not None
        and not payload.get("dry_run")
    ):
        reconcile_todo_complete_settlement(
            payload,
            args=args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_ref=goal_ref,
            settlement_identity=settlement_identity,
        )
    if (
        args.todo_command == "complete"
        and payload.get("ok")
        and payload.get("completed")
        and not payload.get("dry_run")
        and post_writeback_hooks
        and settlement_identity is not None
        and not getattr(args, "completion_identity_key", None)
    ):
        dispatch_todo_complete_post_writeback(
            payload,
            args=args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_ref=goal_ref,
            settlement_identity=settlement_identity,
            post_writeback_hooks=post_writeback_hooks,
            post_writeback_projection_builder=post_writeback_projection_builder,
            admission_factory=FirstPartyHostGoalAdmission,
        )
    output_format = format_name or str(getattr(args, "format", None) or "markdown")
    print_payload(
        payload,
        output_format,
        renderer,
    )
    return 0 if payload.get("ok") else 1
