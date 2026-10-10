from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..capabilities.explore.composition_frontier import (
    project_live_explore_composition_frontier,
)
from ..capabilities.reward_memory import (
    run_configured_turn_outcome_ingest_fail_open,
)
from ..cli_rollout import append_cli_rollout_event
from ..control_plane.agents.workspace_guard import capture_delivery_workspace
from ..control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
)
from ..control_plane.quota.heartbeat_receipt import (
    ensure_turn_heartbeat_settlement_receipt,
)
from ..control_plane.quota.live_decision import (
    build_live_quota_should_run_decision,
)
from ..control_plane.quota.settlement import (
    SettlementIdentity,
    SettlementStepKind,
    read_heartbeat_settlement,
)
from ..control_plane.work_items.autonomous_replan_obligation import (
    replan_obligation_id_from_packet,
)
from ..control_plane.todos.durable_completion import (
    project_durable_completion_intent,
    project_durable_completion_outcome,
    project_durable_terminal_completion_readback,
    read_persisted_todo_record,
    read_persisted_todo_record_with_source,
)
from ..control_plane.turn_driver import (
    build_loopx_turn_command_validator,
    codex_cli_session_binding,
    inspect_loopx_turn_journal,
    run_codex_cli_host,
    run_loopx_turn_once,
    selected_turn_todo,
)
from ..quota import spend_quota_slot
from ..state_refresh import refresh_state_run
from ..todos import resolve_todo_state_path
from .turn_cadence import managed_cadence_start
from .turn_decision import collect_turn_status_payload
from .turn_dsh_host import build_dsh_host_runner
from .turn_rendering import build_turn_error_payload
from .turn_post_writeback import drain_committed_turn_post_writeback_hooks
from .turn_todo_writeback import (
    write_turn_repair_update,
    write_turn_validated_completion,
)

EXACT_SETTLEMENT_READBACK_NOT_FOUND = (
    "exact settlement readback unexpectedly returned not-found"
)


def execute_turn_run_once(
    args: argparse.Namespace,
    *,
    payload: dict[str, Any],
    registry_path: Path,
    runtime_root: Path,
    runtime_root_arg: str | None,
    goal_ref: Mapping[str, object] | None,
    goal_admission: FirstPartyHostGoalAdmission | None,
    operator_environ: Mapping[str, str],
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Adapt one prepared CLI Turn plan to the bounded Turn driver."""
    execution_started = False
    try:
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
                raise ValueError(f"{args.host} does not accept --host-command-json")
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
        stable_envelope: Mapping[str, Any] = (
            envelope if isinstance(envelope, Mapping) else {}
        )
        replan_guard_scoped = "replan_action_packet" in stable_envelope
        replan_obligation_id = (
            replan_obligation_id_from_packet(
                stable_envelope.get("replan_action_packet")
            )
            if replan_guard_scoped
            else None
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
                goal_ref=goal_ref,
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
                    *(("goal_ref",) if goal_ref is not None else ()),
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
                # A host's next_action is follow-up guidance, not refresh-state's
                # explicit within-task step edit (which requires a runnable Todo).
                # Keep both host texts in the durable host_result, including for
                # completion/repair, without decorating a completed or blocked task.
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
                goal_ref=goal_ref,
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
                registry_path=registry_path,
                goal_ref=goal_ref,
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
                    registry_path=registry_path,
                    goal_ref=goal_ref,
                )
                if readback is None:
                    raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
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
                expected_completion_turn_key=settlement_identity.turn_instance_id,
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
                    registry_path=registry_path,
                    goal_ref=goal_ref,
                )
                if readback is None:
                    raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
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
                    registry_path=registry_path,
                    goal_ref=goal_ref,
                )
                if readback is None:
                    raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
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
                    registry_path=registry_path,
                    goal_ref=goal_ref,
                )
                if readback is None:
                    raise RuntimeError(EXACT_SETTLEMENT_READBACK_NOT_FOUND)
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
                goal_ref=goal_ref,
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
                options = {
                    "runtime_root": runtime_root,
                    "project": project,
                    "codex_bin": args.codex_bin,
                    "sandbox": args.codex_sandbox,
                    "model": args.codex_model,
                    "reasoning_effort": args.codex_reasoning_effort,
                    "mcp_server": args.codex_mcp_server_json,
                    "timeout_seconds": None if args.timeout_seconds is None else max(1.0, args.timeout_seconds - 5.0),
                }
                if goal_admission is not None:
                    options["goal_admission"] = goal_admission
                if getattr(args, "codex_operation_tools", False):
                    from ..control_plane.turn_driver.codex_operation_host import (
                        run_codex_operation_host,
                    )

                    return run_codex_operation_host(
                        request,
                        registry_path=registry_path,
                        confirmed_operation_id=getattr(
                            args, "codex_confirmed_operation_id", None
                        ),
                        source_route=getattr(
                            args, "codex_operation_source_route_json", None
                        ),
                        **options,
                    )
                return run_codex_cli_host(request, registry_path=registry_path, **options)

            host_runner = run_built_in_host

            def resolve_built_in_session_binding(
                turn_envelope: Mapping[str, Any],
            ) -> dict[str, str] | None:
                session_scope = str((payload.get("session", {}).get("context_policy") or {}).get("binding_scope") or "todo")
                return (
                    codex_cli_session_binding(
                        runtime_root,
                        turn_envelope,
                        goal_admission=goal_admission,
                        session_scope=session_scope,
                    )
                    if goal_admission is not None
                    else codex_cli_session_binding(
                        runtime_root,
                        turn_envelope,
                        session_scope=session_scope,
                    )
                )

            session_binding_resolver = resolve_built_in_session_binding
        elif args.host == "dsh":
            host_runner = build_dsh_host_runner(
                args,
                workspace=project,
                environ=operator_environ,
            )

        def post_settlement_reward_memory(
            plan: Mapping[str, Any],
            result: Mapping[str, Any],
            settlement_evidence: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            transaction = plan.get("transaction")
            transaction = transaction if isinstance(transaction, Mapping) else {}
            return run_configured_turn_outcome_ingest_fail_open(
                registry_path=registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                turn_key=str(transaction.get("turn_key") or ""),
                host_result=result,
                settlement_evidence=settlement_evidence,
            )

        def on_managed_start_admitted() -> None:
            replan_packet = stable_envelope.get("replan_action_packet")
            capability_guard = (
                replan_packet.get("capability_guard")
                if isinstance(replan_packet, Mapping)
                else None
            )
            ensure_turn_heartbeat_settlement_receipt(
                runtime_root,
                settlement_identity,
                semantic_replan_guard_scoped=replan_guard_scoped,
                semantic_replan_obligation_id=replan_obligation_id,
                semantic_replan_capability_guard=capability_guard,
                goal_ref=goal_ref,
            )

        managed_cadence = managed_cadence_start(
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            automation_id=args.automation_id,
            manual_reason=args.manual_interval_bypass_reason,
            on_admitted=on_managed_start_admitted,
        )

        execution_started = bool(args.execute)
        result = run_loopx_turn_once(
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
            admit_start=managed_cadence.admit if args.execute else None,
            confirm_start=managed_cadence.confirm if args.execute else None,
            goal_admission=goal_admission,
        )
        if args.execute:
            result.update(drain_committed_turn_post_writeback_hooks(
                registry_path=registry_path, runtime_root=runtime_root,
                identity=settlement_identity, goal_ref=goal_ref,
                available_capabilities=args.available_capabilities,
            ))
        return result
    except Exception as exc:  # noqa: BLE001 - CLI boundary renders typed JSON failure
        from ..usage_ping import capture_failure

        capture_failure(exc)
        journal_readback = None
        if execution_started:
            transaction = payload.get("transaction") or {}
            try:
                journal_readback = inspect_loopx_turn_journal(
                    runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    turn_key=str(transaction.get("turn_key") or ""),
                )
            except Exception:  # noqa: BLE001 - retain original error and unknown effects
                pass
        return build_turn_error_payload(
            payload,
            exc,
            turn_command="run-once",
            execution_started=execution_started,
            journal_readback=journal_readback,
        )
