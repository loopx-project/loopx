from __future__ import annotations
from ..control_plane.quota.effective_action import EffectiveAction

import argparse
import json
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..cli_rollout import append_cli_rollout_event
from ..capabilities.explore.composition_frontier import (
    project_live_explore_composition_frontier,
)
from ..capabilities.agent_turn_recall import (
    run_configured_agent_turn_recall_fail_open,
)
from ..capabilities.reward_memory import (
    run_configured_turn_outcome_ingest_fail_open,
)
from ..capabilities.periodic_report.cadence_runtime import extend_cadence_turn_start_dispatch
from ..control_plane.quota.live_decision import build_live_quota_should_run_decision
from ..control_plane.agents.workspace_guard import capture_delivery_workspace
from ..control_plane.quota.heartbeat_receipt import (
    ensure_turn_heartbeat_settlement_receipt,
)
from ..control_plane.quota.settlement import (
    SettlementIdentity,
    SettlementStepKind,
    read_heartbeat_settlement,
)
from ..control_plane.quota.turn_envelope import build_turn_envelope
from ..control_plane.runtime.status_projection_cache import (
    resolve_status_projection_cache_runtime_root,
)
from ..control_plane.todos.durable_completion import (
    project_durable_completion_intent,
    project_durable_completion_outcome,
    project_durable_terminal_completion_readback,
    read_persisted_todo_record,
    read_persisted_todo_record_with_source,
)
from ..control_plane.turn_driver import (
    LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
    TurnRecoveryBlockedError,
    build_loopx_turn_command_validator,
    build_loopx_turn_plan,
    codex_cli_session_binding,
    load_loopx_turn_plan_from_journal,
    run_codex_cli_host,
    run_loopx_turn_once,
    selected_turn_todo,
)
from ..control_plane.turn_driver.host_binding import managed_executor_binding
from ..control_plane.operator_provider import operator_provider_environ
from ..quota import spend_quota_slot
from ..state_refresh import refresh_state_run
from ..todos import resolve_todo_state_path
from .lark_inbox import dispatch_goal_lark_turn_start_hooks
from .turn_decision import (
    build_fresh_turn_decision_owner,
    collect_turn_status_payload,
)
from .turn_dsh_host import build_dsh_host_runner
from .turn_registration import register_turn_commands as register_turn_commands
from .turn_inspection import handle_turn_journal_inspection
from .turn_managed_step import handle_turn_managed_step
from .turn_rendering import (
    render_loopx_turn_execution_markdown as _render_loopx_turn_execution_markdown,
    render_loopx_turn_plan_markdown as _render_loopx_turn_plan_markdown,
)
from .turn_selection import resolve_turn_resume_session_binding
from .turn_todo_writeback import (
    write_turn_repair_update,
    write_turn_validated_completion,
)

EXACT_SETTLEMENT_READBACK_NOT_FOUND = (
    "exact settlement readback unexpectedly returned not-found"
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]




def handle_turn_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "turn":
        return None
    inspection_result = handle_turn_journal_inspection(
        args,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        output_format=output_format,
        print_payload=print_payload,
    )
    if inspection_result is not None:
        return inspection_result
    if args.turn_command == "managed-step":
        return handle_turn_managed_step(
            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
            output_format=output_format, print_payload=print_payload,
        )
    try:
        if getattr(args, "todo_id", None) is not None and (
            getattr(args, "resume_turn_key", None)
            or any(getattr(args, key, None) for key in ("resume_goal_id", "resume_agent_id", "resume_todo_id"))
        ):
            raise ValueError("--todo-id selects fresh work and cannot retarget a resumed Turn or session")
        runtime_root = resolve_status_projection_cache_runtime_root(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
        )
        # Planning and dry-run execution inspect existing admitted intents.
        # Only an executing wake may sync inboxes or reserve a calendar window.
        turn_start_hook_dispatch = {}
        if args.turn_command == "run-once" and args.execute:
            turn_start_hook_dispatch = dispatch_goal_lark_turn_start_hooks(
                registry_path=registry_path,
                runtime_root_arg=runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
            )
            turn_start_hook_dispatch = extend_cadence_turn_start_dispatch(
                turn_start_hook_dispatch, registry_path=registry_path, runtime_root=runtime_root,
                goal_id=args.goal_id, agent_id=args.agent_id)
            from ..control_plane.agents.capability_memory import (
                extend_turn_start_dispatch as extend_capability_memory_dispatch,
            )

            turn_start_hook_dispatch = extend_capability_memory_dispatch(
                turn_start_hook_dispatch,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                available=args.available_capabilities,
            )
        # `run-once` and `managed-step` must resolve the same governing decision
        # from the same live status, scheduler context and capability hooks, so
        # this Turn takes all of them -- and its later settle-against inputs --
        # from the shared decision owner instead of rebuilding them per command.
        decision_owner = build_fresh_turn_decision_owner(
            args,
            registry_path=registry_path,
            runtime_root=runtime_root,
            runtime_root_arg=runtime_root_arg,
            turn_start_hook_dispatch=turn_start_hook_dispatch,
        )
        operator_inbox_urgency_projector = decision_owner.operator_inbox_urgency_projector
        scheduler_context = decision_owner.scheduler_execution_context
        decision = decision_owner.resolve()
        resume_requested, session_binding = resolve_turn_resume_session_binding(args)
        turn_envelope = build_turn_envelope(
            decision,
            scheduler_execution_context=scheduler_context,
        )
        if (
            args.turn_command == "run-once"
            and args.host == "codex-cli"
            and not resume_requested
            and turn_envelope.get("effective_action") != EffectiveAction.GOVERNED_CAPABILITY_INTENT.value
        ):
            session_binding = codex_cli_session_binding(runtime_root, turn_envelope)
        payload = build_loopx_turn_plan(
            turn_envelope,
            host=args.host,
            execution_mode=args.execution_mode,
            scheduler_owner=args.scheduler_owner,
            session_binding=session_binding,
            turn_instance_id=args.turn_instance_id,
            iteration_context_policy=args.iteration_context.replace("-", "_"),
        )
        # The executor readback names where this Turn's model work runs and
        # whether that host can launch here, so a caller never has to infer it
        # from the host id. The explicit runner hook is the one launchability
        # fact only this command layer knows.
        payload["managed_executor"] = managed_executor_binding(
            args.host,
            # The credential a managed Turn authenticates with is this
            # machine's resolved pair, not whatever the invoking shell happens
            # to export: the readback above the launch and the launch itself
            # have to name the same credential.
            environ=operator_provider_environ(runtime_root),
            dsh_runner_configured=bool(getattr(args, "dsh_runner", None)),
            provider=getattr(args, "dsh_provider", None),
            model=getattr(args, "dsh_model", None),
            reasoning_effort=getattr(args, "dsh_reasoning_effort", None),
            max_tokens=getattr(args, "dsh_max_tokens", None),
        )
        if (
            args.turn_command == "run-once"
            and args.execute
            and (payload.get("route") or {}).get("would_invoke_host") is True
        ):
            transaction = payload.get("transaction")
            transaction = transaction if isinstance(transaction, Mapping) else {}
            turn_instance_id = str(
                transaction.get("turn_instance_id")
                or transaction.get("turn_key")
                or ""
            )
            payload["reward_memory_recall"] = (
                run_configured_agent_turn_recall_fail_open(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    quota_decision=decision,
                    turn_instance_id=turn_instance_id,
                    execute=True,
                )
            )
        capability_action = payload.get("capability_action")
        if isinstance(capability_action, dict):
            # Bind the adapter handoff to this invocation, while leaving the
            # signed, provider-neutral intent untouched. This only projects
            # argv; the Turn driver never executes a projected shell string.
            command = shlex.split(capability_action["intent"]["command"])
            capability_action["command_argv"] = [command[0], "--registry", str(registry_path),
                "--runtime-root", str(runtime_root), *command[1:]]
            capability_action["command"] = shlex.join(capability_action["command_argv"])
        if turn_start_hook_dispatch.get("registered_count") or (
            turn_start_hook_dispatch.get("failures")
        ):
            payload["turn_start_capability_hook_dispatch"] = turn_start_hook_dispatch
            if any(isinstance(result, Mapping) and result.get("local_private_state_mutated") is True
                   for result in turn_start_hook_dispatch.get("results", [])):
                # The pure plan builder has no effects, but live preflight
                # hooks may journal an inbox or calendar admission. Disclose
                # that write without claiming a host turn or report ran.
                payload["effects"]["state_written"] = True
                payload["boundary"]["read_only"] = False
        if args.turn_command == "plan":
            if not args.include_transaction_detail:
                payload.pop("session", None)
                payload.pop("transaction", None)
                boundary = payload.get("boundary")
                if isinstance(boundary, dict):
                    boundary.pop("opaque_session_handle_omitted", None)
            else:
                # The typed settlement plan is used by validation and
                # execution, but is not part of the agent-facing CLI contract.
                transaction = payload.get("transaction")
                if isinstance(transaction, dict):
                    transaction.pop("settlement_plan", None)
        elif args.turn_command == "run-once":
            if args.resume_turn_key:
                if args.turn_instance_id:
                    raise ValueError(
                        "--resume-turn-key cannot be combined with --turn-instance-id"
                    )
                if resume_requested:
                    raise ValueError(
                        "--resume-turn-key cannot be combined with host session identity flags"
                    )
                payload = load_loopx_turn_plan_from_journal(
                    runtime_root,
                    goal_id=args.goal_id,
                    turn_key=args.resume_turn_key,
                )
                envelope = (
                    payload.get("turn_envelope")
                    if isinstance(payload.get("turn_envelope"), dict)
                    else {}
                )
                if envelope.get("agent_id") != args.agent_id:
                    raise ValueError(
                        "LoopX Turn resume journal belongs to another agent"
                    )
            if payload.get("route", {}).get("kind") == "capability_action_required":
                # The normal host transaction forbids Core mutations. A
                # capability may prepare artifacts and require authored input;
                # never run its command as an arbitrary host/shell adapter.
                payload.update(mode="run_once", status="capability_action_required",
                               execute=bool(args.execute), executed=False)
                print_payload(payload, output_format(args), _render_loopx_turn_plan_markdown)
                return 0
            project = Path(args.project).expanduser().resolve()
            planned_host = (
                payload.get("host") if isinstance(payload.get("host"), dict) else {}
            )
            if planned_host.get("kind") != args.host:
                raise ValueError("--host must match the journaled LoopX Turn plan")
            if args.host == "generic-cli":
                if not args.host_command_json:
                    raise ValueError(
                        "generic-cli requires --host-adapter-command-json "
                        "(alias --host-command-json)"
                    )
                raw_argv = json.loads(args.host_command_json)
                if not isinstance(raw_argv, list) or not all(
                    isinstance(item, str) for item in raw_argv
                ):
                    raise ValueError(
                        "--host-adapter-command-json must be a JSON string array"
                    )
            else:
                if args.host_command_json:
                    raise ValueError(
                        f"{args.host} does not accept --host-command-json"
                    )
                raw_argv = None
            if args.validation_command_json:
                raw_validation_argv = json.loads(args.validation_command_json)
                if not isinstance(raw_validation_argv, list) or not all(
                    isinstance(item, str) for item in raw_validation_argv
                ):
                    raise ValueError(
                        "--validation-command-json must be a JSON string array"
                    )
                task_validator = build_loopx_turn_command_validator(
                    raw_validation_argv,
                    project=project,
                    timeout_seconds=args.validation_timeout_seconds,
                    failure_recovery_kind=args.validation_failure_kind,
                )
            else:
                task_validator = None
            envelope = (
                payload.get("turn_envelope")
                if isinstance(payload.get("turn_envelope"), dict)
                else {}
            )
            selected_todo = selected_turn_todo(envelope)
            transaction = (
                payload.get("transaction")
                if isinstance(payload.get("transaction"), Mapping)
                else {}
            )
            settlement_plan = (
                transaction.get("settlement_plan")
                if isinstance(transaction.get("settlement_plan"), Mapping)
                else {}
            )
            raw_identity = (
                settlement_plan.get("identity")
                if isinstance(settlement_plan.get("identity"), Mapping)
                else {}
            )
            settlement_identity = SettlementIdentity(
                goal_id=str(raw_identity.get("goal_id") or args.goal_id),
                agent_id=str(raw_identity.get("agent_id") or args.agent_id),
                todo_id=str(
                    raw_identity.get("todo_id") or selected_todo.get("todo_id") or ""
                ),
                turn_instance_id=str(
                    raw_identity.get("turn_instance_id")
                    or transaction.get("turn_instance_id")
                    or transaction.get("turn_key")
                    or ""
                ),
            )
            persisted_effect_id = str(raw_identity.get("effect_id") or "")
            if (
                persisted_effect_id
                and persisted_effect_id != settlement_identity.effect_id
            ):
                raise ValueError("Turn settlement identity effect_id is inconsistent")
            if args.execute:
                ensure_turn_heartbeat_settlement_receipt(
                    runtime_root,
                    settlement_identity,
                )

            def require_effect_ref(
                effect_ref: str,
                step_kind: SettlementStepKind,
            ) -> None:
                expected = f"{settlement_identity.effect_id}#{step_kind.value}"
                if effect_ref != expected:
                    raise ValueError(
                        f"{step_kind.value} effect ref does not match Turn identity"
                    )

            def append_settlement_event(
                effect_payload: Mapping[str, object],
                *,
                event_kind: str,
                status: str,
                details: Mapping[str, object],
            ) -> None:
                event_payload = {
                    **dict(effect_payload),
                    "ok": True,
                    "goal_id": args.goal_id,
                    "runtime_root": str(runtime_root),
                }
                append_cli_rollout_event(
                    event_payload,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    event_kind=event_kind,
                    agent_id=settlement_identity.agent_id,
                    todo_id=settlement_identity.todo_id,
                    run_id=settlement_identity.turn_instance_id,
                    status=status,
                    summary=f"Turn settlement {event_kind} recorded",
                    details={
                        **dict(details),
                        "settlement_effect_id": settlement_identity.effect_id,
                    },
                    idempotency_fields=[
                        "goal_id",
                        "event_kind",
                        "agent_id",
                        "todo_id",
                        "run_id",
                        *(("status",) if event_kind == "todo_complete" else ()),
                    ],
                )
                if event_payload.get("rollout_event_log_error"):
                    raise OSError(f"failed to persist {event_kind} settlement receipt")

            writeback_contract = (
                envelope.get("writeback")
                if isinstance(envelope.get("writeback"), dict)
                else {}
            )
            delivery_workspace_path = (
                project
                if isinstance(
                    writeback_contract.get("delivery_workspace_causality"), dict
                )
                else None
            )
            completion_delivery_workspace = (
                capture_delivery_workspace(
                    delivery_workspace_path,
                    peer_independent_worktree_required=bool(
                        selected_todo.get("task_repository")
                    ),
                    repository_source="turn.delivery_workspace",
                )
                if delivery_workspace_path is not None
                else None
            )

            def writeback(
                result: dict[str, object],
                *,
                completion_todo_id: str | None = None,
                completion_turn_key: str | None = None,
                effect_ref: str,
            ) -> dict[str, object]:
                require_effect_ref(effect_ref, SettlementStepKind.DURABLE_WRITEBACK)
                # The host workspace is execution context, not state authority.
                state_project = None
                result_kind = str(result.get("result_kind") or "")
                if result_kind in {"repair_required", "replan_required"}:
                    todo_id = str(selected_todo.get("todo_id") or "")
                    if not todo_id:
                        raise ValueError(
                            f"{result_kind} requires one selected todo for typed writeback"
                        )
                    write_turn_repair_update(
                        registry_path=registry_path,
                        runtime_root_arg=runtime_root_arg,
                        goal_id=args.goal_id,
                        todo_id=todo_id,
                        note=str(result.get("summary") or result["classification"]),
                        evidence=f"LoopX Turn {result_kind}: {result['next_action']}",
                        agent_id=args.agent_id,
                    )
                refresh = refresh_state_run(
                    registry_path=registry_path,
                    runtime_root_override=runtime_root_arg,
                    goal_id=args.goal_id,
                    project=state_project,
                    state_file=None,
                    classification=str(result["classification"]),
                    recommended_action=str(result["recommended_action"]),
                    next_action=str(result["next_action"]),
                    delivery_batch_scale=str(result["delivery_batch_scale"]),
                    delivery_outcome=str(result["delivery_outcome"]),
                    delivery_workspace_path=delivery_workspace_path,
                    todo_id=settlement_identity.todo_id,
                    turn_instance_id=settlement_identity.turn_instance_id,
                    agent_id=args.agent_id,
                    progress_scope="goal",
                    autonomous_replan_recorded=result_kind == "replan_required",
                    agent_vision_packet=(
                        dict(result["agent_vision"])
                        if isinstance(result.get("agent_vision"), dict)
                        else None
                    ),
                    vision_unchanged_reason=(
                        str(result.get("vision_unchanged_reason") or "") or None
                    ),
                    completion_todo_id=completion_todo_id,
                    completion_turn_key=completion_turn_key,
                    dry_run=False,
                    sync_global=not bool(args.no_global_sync),
                )
                if refresh.get("ok") and (
                    refresh.get("appended")
                    or refresh.get("idempotent_replay")
                    or refresh.get("receipt_repair_required")
                ):
                    append_settlement_event(
                        refresh,
                        event_kind="refresh_state",
                        status="appended",
                        details={"command": "turn run-once"},
                    )
                return refresh

            def completion_intent(_result: dict[str, object]) -> dict[str, object]:
                todo_id = str(selected_todo.get("todo_id") or "")
                if not todo_id:
                    raise ValueError(
                        "validated_completion requires one selected todo for lifecycle writeback"
                    )
                _state_project, state_file = resolve_todo_state_path(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    project=None,
                    state_file=None,
                )
                durable_todo, existing_todo_ids = read_persisted_todo_record(
                    state_file,
                    todo_id=todo_id,
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    runtime_root=runtime_root,
                )
                return project_durable_completion_intent(
                    todo=durable_todo,
                    expected_todo_id=todo_id,
                    existing_todo_ids=existing_todo_ids,
                )

            def todo_completion(
                result: dict[str, object],
                *,
                effect_ref: str,
            ) -> dict[str, object]:
                todo_id = str(selected_todo.get("todo_id") or "")
                if not todo_id:
                    raise ValueError(
                        "validated_completion requires one selected todo for lifecycle writeback"
                    )
                completion = write_turn_validated_completion(
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    goal_id=args.goal_id,
                    todo_id=todo_id,
                    completion_turn_key=settlement_identity.turn_instance_id,
                    evidence=(
                        "LoopX Turn validated completion: "
                        + str(result.get("summary") or result["classification"])
                    ),
                    note=str(result["next_action"]),
                    agent_id=args.agent_id,
                    completion_delivery_workspace=completion_delivery_workspace,
                    completion_validation_workspace_path=delivery_workspace_path,
                )
                # Project the continuation the Todo lifecycle durably recorded,
                # never a host-normalized continuation. Contradictory or
                # dangling durable state fails closed before any further
                # writeback so the typed settlement sees the truthful outcome.
                state_file = completion.get("state_file")
                if not isinstance(state_file, str) or not state_file:
                    return {
                        "ok": False,
                        "appended": False,
                        "reason": (
                            "validated completion lifecycle did not report its "
                            "durable Todo state file"
                        ),
                    }
                try:
                    durable_todo, existing_todo_ids = read_persisted_todo_record(
                        Path(state_file),
                        todo_id=todo_id,
                        registry_path=registry_path,
                        goal_id=args.goal_id,
                        runtime_root=runtime_root,
                    )
                    completion_outcome = project_durable_completion_outcome(
                        todo=durable_todo,
                        expected_todo_id=todo_id,
                        existing_todo_ids=existing_todo_ids,
                    )
                except ValueError as exc:
                    return {
                        "ok": False,
                        "appended": False,
                        "reason": f"durable completion projection failed: {exc}",
                    }
                completion_payload = {
                    "ok": bool(completion.get("ok")),
                    # A completed Todo is idempotent under Turn replay: after an
                    # interrupted journal write, the retry may observe it done.
                    "appended": bool(completion.get("completed"))
                    and bool(
                        completion.get("changed") or completion.get("idempotent_replay")
                    ),
                    "completion": completion_outcome,
                }
                if completion_payload["ok"] and completion_payload["appended"]:
                    append_settlement_event(
                        completion_payload,
                        event_kind="todo_complete",
                        status=(
                            "terminal_no_followup"
                            if completion_outcome.get("continuation") == "no_followup"
                            else "completed"
                        ),
                        details={
                            "command": "turn run-once",
                            "no_followup": (
                                completion_outcome.get("continuation") == "no_followup"
                            ),
                        },
                    )
                return completion_payload

            def completion_writeback(
                result: dict[str, object],
                *,
                effect_ref: str,
            ) -> dict[str, object]:
                completion = todo_completion(result, effect_ref=effect_ref)
                if not completion.get("ok"):
                    return completion
                todo_id = str(selected_todo.get("todo_id") or "")
                refresh = writeback(
                    result,
                    completion_todo_id=todo_id,
                    completion_turn_key=settlement_identity.turn_instance_id,
                    effect_ref=effect_ref,
                )
                return {
                    "ok": bool(refresh.get("ok")),
                    "appended": bool(completion.get("appended"))
                    and bool(refresh.get("appended")),
                    "classification": refresh.get("classification"),
                    "completion": completion["completion"],
                }

            def terminal_closeout(
                result: dict[str, object],
                *,
                effect_ref: str,
            ) -> dict[str, object]:
                require_effect_ref(effect_ref, SettlementStepKind.TERMINAL_CLOSEOUT)
                return todo_completion(result, effect_ref=effect_ref)

            def current_status() -> dict[str, object]:
                return collect_turn_status_payload(
                    args,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                )

            def spend(*, effect_ref: str) -> dict[str, object]:
                require_effect_ref(effect_ref, SettlementStepKind.QUOTA_SPEND)
                spent = spend_quota_slot(
                    current_status(),
                    goal_id=args.goal_id,
                    slots=1,
                    execute=True,
                    source="adapter",
                    agent_id=args.agent_id,
                    workspace_path=delivery_workspace_path,
                    available_capabilities=args.available_capabilities,
                    scheduler_execution_context=(
                        payload.get("scheduler_execution_context")
                        if isinstance(payload.get("scheduler_execution_context"), dict)
                        else None
                    ),
                    operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                    effect_ref=effect_ref,
                )
                if spent.get("ok") and (
                    spent.get("appended")
                    or spent.get("idempotent_replay")
                    or spent.get("receipt_repair_required")
                ):
                    readback = read_heartbeat_settlement(
                        runtime_root,
                        goal_id=settlement_identity.goal_id,
                        agent_id=settlement_identity.agent_id,
                        todo_id=settlement_identity.todo_id,
                        turn_instance_id=settlement_identity.turn_instance_id,
                        replan_obligation_id=settlement_identity.replan_obligation_id,
                    )
                    if readback is None:
                        raise RuntimeError(
                            EXACT_SETTLEMENT_READBACK_NOT_FOUND
                        )
                    event = readback.spend_event
                    if event is None:
                        append_settlement_event(
                            spent,
                            event_kind="quota_spend",
                            status="appended",
                            details={"command": "turn run-once"},
                        )
                    return {**spent, "appended": True, "effect_ref": effect_ref}
                return spent

            def completion_readback() -> dict[str, object] | None:
                todo_id = str(selected_todo.get("todo_id") or "")
                if not todo_id:
                    return None
                try:
                    _state_project, state_file = resolve_todo_state_path(
                        registry_path=registry_path,
                        goal_id=args.goal_id,
                        project=None,
                        state_file=None,
                    )
                    durable_todo, existing_todo_ids = read_persisted_todo_record(
                        state_file,
                        todo_id=todo_id,
                        registry_path=registry_path,
                        goal_id=args.goal_id,
                        runtime_root=runtime_root,
                    )
                    return project_durable_completion_outcome(
                        todo=durable_todo,
                        expected_todo_id=todo_id,
                        existing_todo_ids=existing_todo_ids,
                    )
                except (OSError, ValueError):
                    return None

            def terminal_completion_readback() -> dict[str, object] | None:
                todo_id = str(selected_todo.get("todo_id") or "")
                if not todo_id:
                    raise ValueError("terminal completion requires one selected Todo")
                _state_project, state_file = resolve_todo_state_path(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    project=None,
                    state_file=None,
                )
                (
                    durable_todo,
                    existing_todo_ids,
                    projection_source,
                ) = read_persisted_todo_record_with_source(
                    state_file,
                    todo_id=todo_id,
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    runtime_root=runtime_root,
                )
                readback = project_durable_terminal_completion_readback(
                    todo=durable_todo,
                    expected_todo_id=todo_id,
                    expected_completion_turn_key=(
                        settlement_identity.turn_instance_id
                    ),
                    projection_source=projection_source,
                    existing_todo_ids=existing_todo_ids,
                )
                if readback["kind"] == "absent":
                    return None
                completion = readback.get("completion")
                if not isinstance(completion, dict):
                    raise ValueError(
                        "terminal completion readback is missing its completion"
                    )
                return completion

            def writeback_resolver(effect_ref: str) -> dict[str, object]:
                try:
                    require_effect_ref(
                        effect_ref,
                        SettlementStepKind.DURABLE_WRITEBACK,
                    )
                    readback = read_heartbeat_settlement(
                        runtime_root,
                        goal_id=settlement_identity.goal_id,
                        agent_id=settlement_identity.agent_id,
                        todo_id=settlement_identity.todo_id,
                        turn_instance_id=settlement_identity.turn_instance_id,
                        replan_obligation_id=settlement_identity.replan_obligation_id,
                    )
                    if readback is None:
                        raise RuntimeError(
                            EXACT_SETTLEMENT_READBACK_NOT_FOUND
                        )
                    run = readback.writeback_run
                    event = readback.writeback_event
                    if run is None and event is None:
                        return {"kind": "absent"}
                    if run is None:
                        return {
                            "kind": "unknown",
                            "reason": "refresh-state receipt has no durable run",
                        }
                    if event is None:
                        append_settlement_event(
                            run,
                            event_kind="refresh_state",
                            status="receipt_repaired",
                            details={"command": "turn recovery probe"},
                        )
                    committed: dict[str, object] = {
                        "ok": True,
                        "appended": True,
                        "effect_ref": effect_ref,
                        "classification": run.get("classification"),
                    }
                    completion = completion_readback()
                    if completion is not None:
                        committed["completion"] = completion
                    return {"kind": "committed", "payload": committed}
                except (OSError, ValueError):
                    return {"kind": "unknown", "reason": "writeback readback failed"}

            def spend_resolver(effect_ref: str) -> dict[str, object]:
                try:
                    require_effect_ref(effect_ref, SettlementStepKind.QUOTA_SPEND)
                    readback = read_heartbeat_settlement(
                        runtime_root,
                        goal_id=settlement_identity.goal_id,
                        agent_id=settlement_identity.agent_id,
                        todo_id=settlement_identity.todo_id,
                        turn_instance_id=settlement_identity.turn_instance_id,
                        replan_obligation_id=settlement_identity.replan_obligation_id,
                    )
                    if readback is None:
                        raise RuntimeError(
                            EXACT_SETTLEMENT_READBACK_NOT_FOUND
                        )
                    run = readback.spend_run
                    event = readback.spend_event
                    if run is not None and run.get("effect_ref") != effect_ref:
                        run = None
                    if run is None and event is None:
                        return {"kind": "absent"}
                    if run is None:
                        return {
                            "kind": "unknown",
                            "reason": "quota receipt has no durable spend run",
                        }
                    if event is None:
                        append_settlement_event(
                            run,
                            event_kind="quota_spend",
                            status="receipt_repaired",
                            details={"command": "turn recovery probe"},
                        )
                    return {
                        "kind": "committed",
                        "payload": {
                            "ok": True,
                            "appended": True,
                            "effect_ref": effect_ref,
                            "mode": "spend-slot",
                        },
                    }
                except (OSError, ValueError):
                    return {"kind": "unknown", "reason": "quota readback failed"}

            def terminal_closeout_resolver(effect_ref: str) -> dict[str, object]:
                try:
                    require_effect_ref(
                        effect_ref,
                        SettlementStepKind.TERMINAL_CLOSEOUT,
                    )
                    readback = read_heartbeat_settlement(
                        runtime_root,
                        goal_id=settlement_identity.goal_id,
                        agent_id=settlement_identity.agent_id,
                        todo_id=settlement_identity.todo_id,
                        turn_instance_id=settlement_identity.turn_instance_id,
                        replan_obligation_id=settlement_identity.replan_obligation_id,
                    )
                    if readback is None:
                        raise RuntimeError(
                            EXACT_SETTLEMENT_READBACK_NOT_FOUND
                        )
                    event = readback.completion_event
                    completion = terminal_completion_readback()
                    if event is None and completion is None:
                        return {"kind": "absent"}
                    if (
                        completion is None
                        or completion.get("continuation") != "no_followup"
                    ):
                        return {
                            "kind": "unknown",
                            "reason": "Todo state does not prove terminal no-followup",
                        }
                    if event is None:
                        append_settlement_event(
                            {"ok": True, "appended": True},
                            event_kind="todo_complete",
                            status="terminal_no_followup",
                            details={
                                "command": "turn recovery probe",
                                "no_followup": True,
                            },
                        )
                    return {
                        "kind": "committed",
                        "payload": {
                            "ok": True,
                            "appended": True,
                            "effect_ref": effect_ref,
                            "completion": completion,
                        },
                    }
                except (OSError, ValueError):
                    return {"kind": "unknown", "reason": "closeout readback failed"}

            def scheduler(_spend_payload: dict[str, object]) -> dict[str, object]:
                turn_scheduler_context = (
                    payload.get("scheduler_execution_context")
                    if isinstance(payload.get("scheduler_execution_context"), dict)
                    else None
                )
                latest = build_live_quota_should_run_decision(
                    current_status(),
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    available_capabilities=args.available_capabilities,
                    include_scheduler_detail=False,
                    codex_app_current_rrule=None,
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    route_source="loopx_turn_run_once",
                    scheduler_execution_context=turn_scheduler_context,
                    operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                    bounded_research_frontier_projector=(
                        project_live_explore_composition_frontier
                    ),
                )
                hint = (
                    latest.get("scheduler_hint")
                    if isinstance(latest.get("scheduler_hint"), dict)
                    else {}
                )
                phase = hint.get("execution_phase")
                return (
                    dict(phase)
                    if isinstance(phase, dict)
                    else {
                        "disposition": "contract_error",
                        "completed": False,
                        "acknowledged": False,
                        "apply_needed": False,
                    }
                )

            host_runner: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None
            session_binding_resolver = None
            if args.host == "codex-cli":

                def run_built_in_host(
                    request: Mapping[str, Any],
                ) -> dict[str, Any]:
                    return run_codex_cli_host(
                        request,
                        runtime_root=runtime_root,
                        project=project,
                        codex_bin=args.codex_bin,
                        sandbox=args.codex_sandbox,
                        model=args.codex_model,
                        timeout_seconds=max(1.0, args.timeout_seconds - 5.0),
                    )

                host_runner = run_built_in_host

                def resolve_built_in_session_binding(
                    turn_envelope: Mapping[str, Any],
                ) -> dict[str, str] | None:
                    return codex_cli_session_binding(runtime_root, turn_envelope)

                session_binding_resolver = resolve_built_in_session_binding
            elif args.host == "dsh":
                host_runner = build_dsh_host_runner(
                    args,
                    workspace=project,
                )

            def post_settlement_reward_memory(
                plan: Mapping[str, Any],
                result: Mapping[str, Any],
                settlement_evidence: Mapping[str, Any],
            ) -> Mapping[str, Any]:
                transaction = plan.get("transaction")
                transaction = (
                    transaction if isinstance(transaction, Mapping) else {}
                )
                return run_configured_turn_outcome_ingest_fail_open(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    turn_key=str(transaction.get("turn_key") or ""),
                    host_result=result,
                    settlement_evidence=settlement_evidence,
                )

            payload = run_loopx_turn_once(
                payload,
                host_argv=raw_argv,
                host_runner=host_runner,
                session_binding_resolver=session_binding_resolver,
                project=project,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                timeout_seconds=args.timeout_seconds,
                execute=bool(args.execute),
                retry_failed=bool(args.retry_failed_turn),
                task_validator=task_validator,
                writeback=writeback if args.execute else None,
                completion_writeback=completion_writeback if args.execute else None,
                completion_intent=completion_intent if args.execute else None,
                terminal_closeout=terminal_closeout if args.execute else None,
                spend=spend if args.execute else None,
                writeback_resolver=writeback_resolver if args.execute else None,
                spend_resolver=spend_resolver if args.execute else None,
                terminal_closeout_resolver=(
                    terminal_closeout_resolver if args.execute else None
                ),
                scheduler=scheduler if args.execute else None,
                post_settlement=(
                    post_settlement_reward_memory
                    if args.execute and args.host == "codex-cli"
                    else None
                ),
            )
        else:
            raise ValueError("turn requires the `plan` or `run-once` subcommand")
    except Exception as exc:  # noqa: BLE001 - CLI boundary renders typed JSON failure
        payload = {
            **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            "ok": False,
            "schema_version": (
                LOOPX_TURN_EXECUTION_SCHEMA_VERSION
                if args.turn_command == "run-once"
                else "loopx_turn_plan_v0"
            ),
            "mode": "run_once" if args.turn_command == "run-once" else "plan",
            "error": str(exc),
            "effects": {
                "host_invoked": False,
                "state_written": False,
                "scheduler_acknowledged": False,
                "quota_spent": False,
            },
            **(
                {"recovery_decision": exc.decision}
                if isinstance(exc, TurnRecoveryBlockedError)
                else {}
            ),
        }
    renderer = (
        _render_loopx_turn_execution_markdown
        if args.turn_command == "run-once"
        else _render_loopx_turn_plan_markdown
    )
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
