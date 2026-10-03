from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..capabilities.agent_turn_recall import (
    run_configured_agent_turn_recall_fail_open,
)
from ..capabilities.periodic_report.cadence_runtime import (
    extend_cadence_turn_start_dispatch,
)
from ..control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    capture_first_party_host_goal_ref,
)
from ..control_plane.operator_provider import operator_provider_environ
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.turn_envelope import build_turn_envelope
from ..control_plane.runtime.status_projection_cache import (
    resolve_status_projection_cache_runtime_root,
)
from ..control_plane.turn_driver import (
    build_loopx_turn_plan,
    codex_cli_session_binding,
    load_loopx_turn_plan_from_journal,
)
from .lark_inbox import dispatch_goal_lark_turn_start_hooks
from .turn_decision import build_fresh_turn_decision_owner
from .turn_inspection import handle_turn_journal_inspection
from .turn_managed_step import handle_turn_managed_step
from .turn_registration import register_turn_commands as register_turn_commands
from .turn_rendering import (
    build_turn_error_payload,
    render_loopx_turn_execution_markdown as _render_loopx_turn_execution_markdown,
    render_loopx_turn_plan_markdown as _render_loopx_turn_plan_markdown,
)
from .turn_selection import (
    managed_executor_cli_binding,
    resolve_turn_resume_session_binding,
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
    payload: dict[str, Any] = {}
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
        goal_ref = capture_first_party_host_goal_ref(
            registry_path=registry_path,
            goal_id=args.goal_id,
        )
        goal_admission = FirstPartyHostGoalAdmission.for_plan(
            registry_path=registry_path,
            goal_id=args.goal_id,
            planned_goal_ref=goal_ref,
        )
        strict_goal_admission = goal_admission if goal_admission.enabled else None
        if getattr(args, "codex_operation_tools", False) and args.host != "codex-cli":
            raise ValueError("--codex-operation-tools requires the codex-cli host")
        if getattr(args, "codex_confirmed_operation_id", None) and not getattr(
            args, "codex_operation_tools", False
        ):
            raise ValueError(
                "confirmed operation continuation requires the owned operation transport"
            )
        if (
            getattr(args, "codex_operation_source_route_json", None) is not None
            and not getattr(args, "codex_operation_tools", False)
        ):
            raise ValueError(
                "--codex-operation-source-route-json requires --codex-operation-tools"
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
        from ..capabilities.semantic_preference.agent_preferences import extend_turn_start_dispatch as extend_preferences
        turn_start_hook_dispatch = extend_preferences(turn_start_hook_dispatch, runtime_root=runtime_root,
            registry_path=registry_path, goal_id=args.goal_id, agent_id=args.agent_id)
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
            goal_ref=goal_ref,
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
            and not args.resume_turn_key
            and turn_envelope.get("effective_action") != EffectiveAction.GOVERNED_CAPABILITY_INTENT.value
        ):
            session_binding = (
                codex_cli_session_binding(
                    runtime_root,
                    turn_envelope,
                    goal_admission=strict_goal_admission,
                )
                if strict_goal_admission is not None
                else codex_cli_session_binding(runtime_root, turn_envelope)
            )
        payload = build_loopx_turn_plan(
            turn_envelope,
            host=args.host,
            execution_mode=args.execution_mode,
            scheduler_owner=args.scheduler_owner,
            session_binding=session_binding,
            turn_instance_id=args.turn_instance_id,
            iteration_context_policy=args.iteration_context.replace("-", "_"),
            goal_ref=goal_ref,
        )
        # Resolve machine authentication once for the readback and host launch.
        operator_environ = operator_provider_environ()
        payload["managed_executor"] = managed_executor_cli_binding(args, environ=operator_environ)
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
                goal_admission = FirstPartyHostGoalAdmission.for_plan(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    planned_goal_ref=payload.get("goal_ref"),
                )
                strict_goal_admission = goal_admission if goal_admission.enabled else None
                if strict_goal_admission is not None:
                    strict_goal_admission.require_current()
                resumed_goal_ref = payload.get("goal_ref")
                goal_ref = (
                    dict(resumed_goal_ref)
                    if isinstance(resumed_goal_ref, Mapping)
                    else None
                )
            if payload.get("route", {}).get("kind") == "capability_action_required":
                # The normal host transaction forbids Core mutations. A
                # capability may prepare artifacts and require authored input;
                # never run its command as an arbitrary host/shell adapter.
                payload.update(mode="run_once", status="capability_action_required",
                               execute=bool(args.execute), executed=False)
                print_payload(payload, output_format(args), _render_loopx_turn_plan_markdown)
                return 0

            from .turn_run_once import execute_turn_run_once

            payload = execute_turn_run_once(
                args,
                payload=payload,
                registry_path=registry_path,
                runtime_root=runtime_root,
                runtime_root_arg=runtime_root_arg,
                goal_ref=goal_ref,
                goal_admission=strict_goal_admission,
                operator_environ=operator_environ,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            )
        else:
            raise ValueError("turn requires the `plan` or `run-once` subcommand")
    except Exception as exc:  # noqa: BLE001 - CLI boundary renders typed JSON failure
        from ..usage_ping import capture_failure

        capture_failure(exc)
        payload = build_turn_error_payload(
            payload, exc, turn_command=args.turn_command,
        )
    renderer = (
        _render_loopx_turn_execution_markdown
        if args.turn_command == "run-once"
        else _render_loopx_turn_plan_markdown
    )
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
