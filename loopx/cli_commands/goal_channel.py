from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..extensions.lark import (
    LARK_EXTENSION_ID,
    LARK_GOAL_CHANNEL_PERMISSION,
)
from ..extensions.lark.goal_channel import (
    add_lark_goal_channel_target,
    configure_lark_goal_channel_automation,
    default_goal_channel_binding_path,
    default_goal_channel_target_path,
    doctor_lark_goal_channel,
    goal_channel_target_for_name,
    list_goal_channel_targets,
    notify_lark_goal_channel_gate,
    read_goal_channel_binding,
    read_goal_channel_targets,
    setup_lark_goal_channel,
    sync_lark_goal_channel,
)
from ..extensions.lark.goal_channel_contracts import (
    binding_for_goal,
    notification_request_snapshot,
    operation_packet,
)
from ..extensions.lark.goal_topic_batch import upgrade_lark_goal_topics
from ..extensions.runtime import (
    default_extension_state_file,
    resolve_extension_activation,
)
from ..control_plane.runtime.runtime_projection_route import (
    resolve_goal_source_runtime_route,
)
from .goal_channel_runtime import (
    register_goal_channel_runtime_commands,
    run_goal_channel_runtime,
)
from .goal_channel_operation import (
    GoalChannelOperationContext,
    register_goal_channel_operation_commands,
    run_goal_channel_operation,
)
from ..history import load_registry
from ..paths import registry_project_root, resolve_runtime_root
from ..quota import build_quota_should_run
from ..status import collect_status


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
OutputFormat = Callable[[argparse.Namespace], str]


def register_goal_channel_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "goal-channel",
        help="Project one LoopX goal into a provider-backed collaboration channel.",
    )
    sub = parser.add_subparsers(dest="goal_channel_command", required=True)

    upgrade = sub.add_parser(
        "upgrade",
        help="Upgrade old connections to Agent inboxes in place. Dry-run unless --execute.",
    )
    add_subcommand_format(upgrade)
    _add_common_args(upgrade)
    upgrade.add_argument("--connection-id", help="Select one existing connection.")
    upgrade.add_argument(
        "--agent-id", help="Recipient for an exact unassigned connection."
    )
    upgrade.add_argument("--execute", action="store_true")

    setup = sub.add_parser(
        "setup",
        help="Create or reuse one provider channel for a goal. Dry-run unless --execute.",
    )
    add_subcommand_format(setup)
    _add_common_args(setup)
    setup.add_argument(
        "--target",
        help="Reuse a named local-private provider target instead of direct channel flags.",
    )
    setup.add_argument("--provider", default="lark", choices=["lark"])
    setup.add_argument("--chat-id", help="Reuse an existing Lark group chat.")
    setup.add_argument("--chat-name")
    setup.add_argument("--base-url", help="Reuse an existing Lark Base URL.")
    setup.add_argument("--base-token", help=argparse.SUPPRESS)
    setup.add_argument("--table-id", help=argparse.SUPPRESS)
    setup.add_argument(
        "--identity-mode",
        choices=["local_user", "project_bot"],
    )
    setup.add_argument("--sender-profile")
    setup.add_argument("--sender-identity", choices=["bot"])
    setup.add_argument(
        "--bot-app-id",
        help="Explicit Lark app id for the bot that will join and send messages.",
    )
    setup.add_argument("--bot-display-name")
    setup.add_argument("--cli-bin")
    setup.add_argument("--execute", action="store_true")

    attach = sub.add_parser(
        "attach",
        help="Attach up to eight goals to one shared provider target.",
    )
    add_subcommand_format(attach)
    attach.add_argument("--target", required=True)
    attach.add_argument("--goal-id", action="append", required=True)
    attach.add_argument("--target-path", help=argparse.SUPPRESS)
    attach.add_argument("--execute", action="store_true")

    target = sub.add_parser(
        "target",
        help="Manage local-private shared provider targets.",
    )
    target_sub = target.add_subparsers(
        dest="goal_channel_target_command", required=True
    )
    target_add = target_sub.add_parser(
        "add",
        help="Create or update one local-private shared provider target.",
    )
    add_subcommand_format(target_add)
    target_add.add_argument("--name", required=True)
    target_add.add_argument("--provider", default="lark", choices=["lark"])
    target_add.add_argument("--chat-id", required=True)
    target_add.add_argument("--chat-name")
    target_add.add_argument(
        "--identity-mode",
        default="local_user",
        choices=["local_user", "project_bot"],
    )
    target_add.add_argument("--sender-profile")
    target_add.add_argument("--sender-identity", default="bot", choices=["bot"])
    target_add.add_argument("--bot-app-id", required=True)
    target_add.add_argument("--bot-display-name")
    target_add.add_argument("--cli-bin")
    target_add.add_argument("--target-path", help=argparse.SUPPRESS)
    target_add.add_argument("--execute", action="store_true")
    target_list = target_sub.add_parser(
        "list",
        help="List configured target names without exposing private provider values.",
    )
    add_subcommand_format(target_list)
    target_list.add_argument("--target-path", help=argparse.SUPPRESS)

    configure = sub.add_parser(
        "configure",
        help="Configure an existing Goal Channel. Dry-run unless --execute.",
    )
    add_subcommand_format(configure)
    _add_common_args(configure)
    automation = configure.add_mutually_exclusive_group(required=True)
    automation.add_argument(
        "--auto-notify-human-gates",
        dest="auto_notify_human_gates",
        action="store_true",
        help="Send LoopX-selected human gates after authorized refresh-state writes.",
    )
    automation.add_argument(
        "--no-auto-notify-human-gates",
        dest="auto_notify_human_gates",
        action="store_false",
        help="Disable automatic human gate notifications.",
    )
    automation.add_argument(
        "--auto-notify-blocked-notices",
        dest="auto_notify_blocked_notices",
        action="store_true",
        help="Send new or materially changed blocked Todo notices to the Goal Channel.",
    )
    automation.add_argument(
        "--no-auto-notify-blocked-notices",
        dest="auto_notify_blocked_notices",
        action="store_false",
        help="Disable automatic blocked Todo notices.",
    )
    configure.set_defaults(
        auto_notify_human_gates=None, auto_notify_blocked_notices=None
    )
    configure.add_argument("--execute", action="store_true")

    doctor = sub.add_parser(
        "doctor",
        help="Verify extension, identity, channel, Kanban, and readback readiness.",
    )
    add_subcommand_format(doctor)
    _add_common_args(doctor)

    sync = sub.add_parser(
        "sync",
        help="Sync accepted LoopX todos into the configured channel Kanban.",
    )
    add_subcommand_format(sync)
    _add_common_args(sync)
    sync.add_argument("--agent-id")
    sync.add_argument("--execute", action="store_true")

    notify = sub.add_parser(
        "notify-gate",
        help="Send a bounded notification for a LoopX-selected human gate.",
    )
    add_subcommand_format(notify)
    _add_common_args(notify)
    notify.add_argument("--agent-id")
    notify.add_argument(
        "--cooldown-seconds",
        type=int,
        default=None,
        help=(
            "Accepted for compatibility; delivery repeats only for a material "
            "gate-state generation or an explicit quota reminder window."
        ),
    )
    notify.add_argument("--execute", action="store_true")

    register_goal_channel_operation_commands(
        sub,
        add_subcommand_format,
        _add_common_args,
    )

    work = sub.add_parser("work", help="Publish canonical room orientation or claim through the local Agent CLI.")
    work_sub = work.add_subparsers(dest="goal_channel_work_command", required=True)
    for work_command in ("project", "claim", "offer", "revoke", "resume"):
        work_parser = work_sub.add_parser(work_command)
        add_subcommand_format(work_parser)
        _add_common_args(work_parser)
        work_parser.add_argument("--agent-id", required=True)
        work_parser.add_argument("--execute", action="store_true")
        if work_command in {"claim", "offer"}:
            work_parser.add_argument("--todo-id", required=True)
            work_parser.add_argument("--expected-revision", required=True)
            work_parser.add_argument("--idempotency-key", required=True)
        if work_command == "offer":
            work_parser.add_argument("--principal", action="append", required=True)
            work_parser.add_argument("--expires-at", required=True)
        if work_command == "revoke":
            work_parser.add_argument("--request-id", required=True)
        if work_command == "resume":
            work_parser.add_argument("--turn-instance-id", required=True)
            work_parser.add_argument("--quota-decision-json", required=True,
                help="Exact admitted quota packet saved in local private state.")
            work_parser.add_argument("--artifact-ref", action="append",
                help="Carry only this reference if accepted by the current scoped recall; repeat up to eight.")

    register_goal_channel_runtime_commands(sub, add_subcommand_format)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--binding-path",
        help="Local-private Goal Channel binding path beside the project registry.",
    )
    parser.add_argument("--target-path", help=argparse.SUPPRESS)


def _error_packet(
    *,
    goal_id: str | None,
    operation: str,
    execute: bool,
    blocker: str,
    summary: str,
    external_write_performed: bool = False,
    failure_stage: str | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema_version": "loopx_goal_channel_operation_v0",
        "ok": False,
        "goal_id": goal_id,
        "provider": "lark",
        "operation": operation,
        "execute": execute,
        "status": "blocked",
        "external_write_performed": external_write_performed,
        "readback_verified": False,
        "idempotency_key": None,
        "receipt_id": None,
        "public_summary": summary,
        "private_provider_payload_captured": False,
        "blocker": blocker,
    }
    if failure_stage:
        packet["failure_stage"] = failure_stage
    if details:
        packet["details"] = dict(details)
    return packet


def _target_path(args: argparse.Namespace, runtime_root: Path) -> Path:
    value = getattr(args, "target_path", None)
    return (
        Path(str(value)).expanduser()
        if value
        else default_goal_channel_target_path(runtime_root)
    )


def _provider_target(
    *,
    target_path: Path,
    target_name: str,
) -> dict[str, Any] | None:
    return goal_channel_target_for_name(
        read_goal_channel_targets(target_path),
        target_name,
    )


def _binding_target_name(binding_path: Path, goal_id: str) -> str:
    binding = binding_for_goal(read_goal_channel_binding(binding_path), goal_id) or {}
    return str(binding.get("target_ref") or "")


def _source_context(
    *,
    registry: dict[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path_arg: str | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    source_route = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
        registry=registry,
    )
    source_registry_path = Path(str(source_route["source_registry"]))
    source_registry = (
        registry
        if source_registry_path.expanduser().resolve()
        == registry_path.expanduser().resolve()
        else load_registry(source_registry_path)
    )
    binding_path = (
        Path(binding_path_arg).expanduser()
        if binding_path_arg
        else default_goal_channel_binding_path(source_registry_path)
    )
    source_runtime_root = Path(str(source_route["source_runtime_root"]))
    return source_registry, source_registry_path, binding_path, source_runtime_root


def _attach_goals(
    *,
    registry: dict[str, Any],
    registry_path: Path,
    goal_ids: list[str],
    target_name: str,
    provider_target: Mapping[str, Any],
    execute: bool,
) -> dict[str, Any]:
    unique_goal_ids = list(dict.fromkeys(str(goal_id) for goal_id in goal_ids))
    if not unique_goal_ids:
        return operation_packet(
            ok=False,
            goal_id=None,
            operation="attach",
            execute=execute,
            status="blocked",
            blocker="goal_selection_required",
            public_summary="attach requires at least one goal",
            details={"goal_count": 0, "completed_count": 0},
        )
    if len(unique_goal_ids) > 8:
        return operation_packet(
            ok=False,
            goal_id=None,
            operation="attach",
            execute=execute,
            status="blocked",
            blocker="bounded_batch_exceeded",
            public_summary="attach accepts at most eight goals per invocation",
            details={"goal_count": len(unique_goal_ids), "completed_count": 0},
        )
    results: list[dict[str, Any]] = []
    external_write_performed = False
    readback_verified = True
    for goal_id in unique_goal_ids:
        source_registry, source_registry_path, binding_path, _ = _source_context(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
        )
        result = setup_lark_goal_channel(
            registry=source_registry,
            registry_path=source_registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            target_name=target_name,
            provider_target=provider_target,
            execute=execute,
        )
        external_write_performed = bool(
            external_write_performed or result.get("external_write_performed") is True
        )
        readback_verified = bool(
            readback_verified and result.get("readback_verified") is True
        )
        results.append(
            {
                "goal_id": goal_id,
                "ok": bool(result.get("ok")),
                "status": str(result.get("status") or ""),
                **(
                    {"blocker": str(result["blocker"])} if result.get("blocker") else {}
                ),
            }
        )
        if execute and not result.get("ok"):
            break
    ok = len(results) == len(unique_goal_ids) and all(item["ok"] for item in results)
    return operation_packet(
        ok=ok,
        goal_id=None,
        operation="attach",
        execute=execute,
        status="configured" if execute and ok else "preview_ready" if ok else "blocked",
        blocker=None if ok else "goal_attach_failed",
        public_summary=(
            "attached the selected goals to one shared provider target"
            if execute and ok
            else "previewed the selected shared-target attachments"
            if ok
            else "shared-target attachment stopped at the first failed goal"
        ),
        external_write_performed=external_write_performed,
        readback_verified=bool(execute and ok and readback_verified),
        details={
            "target_name": target_name,
            "goal_count": len(unique_goal_ids),
            "completed_count": len(results),
            "results": results,
        },
    )


def _quota_packet(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    project_root = registry_project_root(registry_path)
    status = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
        scan_roots=[project_root],
        limit=20,
        goal_id=goal_id,
    )
    packet = build_quota_should_run(
        status,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    # Transport adapter: retain the complete same-read Todo projection only
    # for this notification. Quota's 180-character hot path stays unchanged;
    # the shared TS presentation owner checks identity/version/lifecycle.
    packet["request_snapshot"] = notification_request_snapshot(status, goal_id)
    return packet


def handle_goal_channel_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.command != "goal-channel":
        return None
    command = str(args.goal_channel_command)
    execute = bool(getattr(args, "execute", False))
    goal_id = str(args.goal_id) if command not in {"attach", "target"} else None
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_arg,
        registry_path=registry_path,
    )
    if command == "work":
        from ..extensions.lark.goal_channel_work import run_goal_channel_work

        assert goal_id is not None
        _, source_registry_path, work_binding_path, source_runtime_root = _source_context(
            registry=registry, registry_path=registry_path, goal_id=goal_id,
            binding_path_arg=getattr(args, "binding_path", None))
        if args.goal_channel_work_command == "resume":
            from ..capabilities.agent_turn_recall.cli import load_turn_quota_decision
            from ..extensions.lark.room_resume import run_room_resume

            payload = run_room_resume(registry_path=source_registry_path, authority_root=source_runtime_root,
                broker_root=runtime_root, binding_path=work_binding_path, target_path=_target_path(args, runtime_root),
                goal_id=goal_id, actor_id=args.agent_id, turn_instance_id=args.turn_instance_id,
                quota_decision=load_turn_quota_decision(args.quota_decision_json), execute=execute,
                artifact_refs=args.artifact_ref,
                read_current_quota=lambda: _quota_packet(registry_path=source_registry_path,
                    runtime_root_arg=str(source_runtime_root), goal_id=goal_id, agent_id=args.agent_id))
        elif args.goal_channel_work_command in {"offer", "revoke"}:
            from ..extensions.lark.room_claim import run_room_claim_offer

            payload = run_room_claim_offer(registry_path=source_registry_path,
                authority_root=source_runtime_root, broker_root=runtime_root,
                binding_path=work_binding_path, target_path=_target_path(args, runtime_root),
                goal_id=goal_id, actor_id=args.agent_id, command=args.goal_channel_work_command,
                execute=execute, todo_id=getattr(args, "todo_id", None),
                expected_revision=getattr(args, "expected_revision", None),
                idempotency_key=getattr(args, "idempotency_key", None),
                principals=getattr(args, "principal", None), expires_at=getattr(args, "expires_at", None),
                request_id=getattr(args, "request_id", None))
        else:
            payload = run_goal_channel_work(registry_path=source_registry_path,
                runtime_root=source_runtime_root, binding_path=work_binding_path,
                target_path=_target_path(args, runtime_root), goal_id=goal_id, actor_id=args.agent_id,
                command=args.goal_channel_work_command, execute=execute,
                todo_id=getattr(args, "todo_id", None), expected_revision=getattr(args, "expected_revision", None),
                idempotency_key=getattr(args, "idempotency_key", None))
        print_payload(payload, output_format(args), render_goal_channel_markdown)
        return 0 if payload.get("ok") else 1
    if command == "runtime":
        assert goal_id is not None
        source_registry, source_registry_path, _, _ = _source_context(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
        )
        payload = run_goal_channel_runtime(
            args, registry=source_registry, registry_path=source_registry_path
        )
        print_payload(payload, output_format(args), render_goal_channel_markdown)
        return 0 if payload.get("ok") else 1
    if command in {"inspect-operation", "consume-operation", "report-operation"}:
        # Original-Agent continuations do not call Lark. They must remain
        # readable/settleable even if that transport extension is unavailable.
        assert goal_id is not None
        _, source_path, source_binding, source_root = _source_context(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
        )
        payload = run_goal_channel_operation(
            args,
            context=GoalChannelOperationContext(
                invoked_runtime_root=runtime_root,
                source_registry_path=source_path,
                source_runtime_root=source_root,
                binding_path=source_binding,
            ),
        )
        assert payload is not None
        print_payload(payload, output_format(args), render_goal_channel_markdown)
        return 0 if payload.get("ok") else 1
    if command == "configure" and (
        args.auto_notify_human_gates is True
        or getattr(args, "auto_notify_blocked_notices", None) is True
    ):
        assert goal_id is not None
        _, source_registry_path, binding_path, _ = _source_context(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path_arg=getattr(args, "binding_path", None),
        )
        default_binding_path = default_goal_channel_binding_path(source_registry_path)
    else:
        binding_path = None
        default_binding_path = None
    if (
        command == "configure"
        and (
            args.auto_notify_human_gates is True
            or getattr(args, "auto_notify_blocked_notices", None) is True
        )
        and binding_path is not None
        and default_binding_path is not None
        and binding_path.resolve() != default_binding_path.resolve()
    ):
        payload = _error_packet(
            goal_id=goal_id,
            operation="configure",
            execute=execute,
            blocker="noncanonical_binding_path",
            summary=(
                "automatic Goal Channel delivery requires the project-local "
                "default Goal Channel binding"
            ),
        )
        print_payload(payload, output_format(args), render_goal_channel_markdown)
        return 1
    if command == "configure" and (
        args.auto_notify_human_gates is False
        or getattr(args, "auto_notify_blocked_notices", None) is False
    ):
        assert goal_id is not None
        source_registry, _, binding_path, _ = _source_context(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path_arg=getattr(args, "binding_path", None),
        )
        try:
            payload = configure_lark_goal_channel_automation(
                registry=source_registry,
                goal_id=goal_id,
                binding_path=binding_path,
                human_gate_auto_notify=args.auto_notify_human_gates,
                blocked_notice_auto_notify=getattr(
                    args, "auto_notify_blocked_notices", None
                ),
                execute=execute,
            )
        except Exception:
            payload = _error_packet(
                goal_id=goal_id,
                operation="configure",
                execute=execute,
                blocker="provider_api_failed",
                summary="the Goal Channel automation setting could not be updated",
            )
        print_payload(payload, output_format(args), render_goal_channel_markdown)
        return 0 if payload.get("ok") else 1
    try:
        activation = resolve_extension_activation(
            LARK_EXTENSION_ID,
            state_file=default_extension_state_file(runtime_root),
            required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,),
        )
    except Exception:
        payload = _error_packet(
            goal_id=goal_id,
            operation=command.replace("-", "_"),
            execute=execute,
            blocker="extension_unavailable",
            summary="install, enable, and doctor the bundled LoopX Lark extension",
        )
    else:
        try:
            target_path = _target_path(args, runtime_root)
            if command == "target":
                target_command = str(args.goal_channel_target_command)
                if target_command == "add":
                    payload = add_lark_goal_channel_target(
                        target_path=target_path,
                        target_name=args.name,
                        chat_id=args.chat_id,
                        chat_name=args.chat_name,
                        identity_mode=args.identity_mode,
                        sender_profile=args.sender_profile,
                        sender_identity=args.sender_identity,
                        bot_app_id=args.bot_app_id,
                        bot_display_name=args.bot_display_name,
                        cli_bin=args.cli_bin,
                        execute=execute,
                    )
                elif target_command == "list":
                    payload = list_goal_channel_targets(target_path=target_path)
                else:
                    raise ValueError(
                        f"unknown goal-channel target command: {target_command}"
                    )
            elif command == "attach":
                target_name = str(args.target)
                provider_target = _provider_target(
                    target_path=target_path,
                    target_name=target_name,
                )
                if provider_target is None:
                    payload = _error_packet(
                        goal_id=None,
                        operation="attach",
                        execute=execute,
                        blocker="provider_target_missing",
                        summary="configure the named shared provider target first",
                    )
                else:
                    payload = _attach_goals(
                        registry=registry,
                        registry_path=registry_path,
                        goal_ids=list(args.goal_id),
                        target_name=target_name,
                        provider_target=provider_target,
                        execute=execute,
                    )
            else:
                assert goal_id is not None
                (
                    source_registry,
                    source_registry_path,
                    binding_path,
                    source_runtime_root,
                ) = _source_context(
                    registry=registry,
                    registry_path=registry_path,
                    goal_id=goal_id,
                    binding_path_arg=getattr(args, "binding_path", None),
                )
                operation_payload = run_goal_channel_operation(
                    args,
                    context=GoalChannelOperationContext(
                        invoked_runtime_root=runtime_root,
                        source_registry_path=source_registry_path,
                        source_runtime_root=source_runtime_root,
                        binding_path=binding_path,
                    ),
                )
                if operation_payload is not None:
                    payload = operation_payload
                else:
                    target_name = str(getattr(args, "target", None) or "")
                    if not target_name:
                        target_name = _binding_target_name(binding_path, goal_id)
                    provider_target = (
                        _provider_target(
                            target_path=target_path,
                            target_name=target_name,
                        )
                        if target_name
                        else None
                    )
                    if command == "upgrade":
                        payload = upgrade_lark_goal_topics(
                            registry=source_registry,
                            registry_path=source_registry_path,
                            goal_id=goal_id,
                            binding_path=binding_path,
                            target_path=target_path,
                            connection_id=args.connection_id,
                            agent_id=args.agent_id,
                            execute=execute,
                        )
                    elif target_name and provider_target is None:
                        payload = _error_packet(
                            goal_id=goal_id,
                            operation=command.replace("-", "_"),
                            execute=execute,
                            blocker="provider_target_missing",
                            summary="configure the named shared provider target first",
                        )
                    elif command == "setup":
                        payload = setup_lark_goal_channel(
                            registry=source_registry,
                            registry_path=source_registry_path,
                            goal_id=goal_id,
                            binding_path=binding_path,
                            target_name=target_name or None,
                            provider_target=provider_target,
                            chat_id=args.chat_id,
                            chat_name=args.chat_name,
                            base_url=args.base_url,
                            base_token=args.base_token,
                            table_id=args.table_id,
                            identity_mode=args.identity_mode,
                            sender_profile=args.sender_profile,
                            sender_identity=args.sender_identity,
                            bot_app_id=getattr(args, "bot_app_id", None),
                            bot_display_name=args.bot_display_name,
                            cli_bin=args.cli_bin,
                            execute=execute,
                        )
                    elif command == "configure":
                        payload = configure_lark_goal_channel_automation(
                            registry=source_registry,
                            goal_id=goal_id,
                            binding_path=binding_path,
                            human_gate_auto_notify=args.auto_notify_human_gates,
                            blocked_notice_auto_notify=getattr(
                                args, "auto_notify_blocked_notices", None
                            ),
                            execute=execute,
                        )
                    elif command == "doctor":
                        payload = doctor_lark_goal_channel(
                            registry=source_registry,
                            registry_path=source_registry_path,
                            goal_id=goal_id,
                            binding_path=binding_path,
                            provider_target=provider_target,
                        )
                    elif command == "sync":
                        payload = sync_lark_goal_channel(
                            registry=source_registry,
                            registry_path=source_registry_path,
                            goal_id=goal_id,
                            binding_path=binding_path,
                            provider_target=provider_target,
                            agent_id=args.agent_id,
                            execute=execute,
                        )
                    elif command == "notify-gate":
                        payload = notify_lark_goal_channel_gate(
                            registry=source_registry,
                            goal_id=goal_id,
                            binding_path=binding_path,
                            provider_target=provider_target,
                            quota_packet=_quota_packet(
                                registry_path=registry_path,
                                runtime_root_arg=runtime_root_arg,
                                goal_id=goal_id,
                                agent_id=args.agent_id,
                            ),
                            execute=execute,
                            registry_path=source_registry_path,
                            runtime_root=runtime_root,
                        )
                    else:
                        raise ValueError(f"unknown goal-channel command: {command}")
            if payload.get("ok"):
                payload["extension_activation"] = activation
        except ValueError:
            payload = _error_packet(
                goal_id=goal_id,
                operation=command.replace("-", "_"),
                execute=execute,
                blocker="invalid_configuration",
                summary="the Goal Channel configuration is invalid",
            )
        except Exception:
            payload = _error_packet(
                goal_id=goal_id,
                operation=command.replace("-", "_"),
                execute=execute,
                blocker="provider_api_failed",
                summary="the Goal Channel operation failed before a verified provider receipt",
            )
    print_payload(payload, output_format(args), render_goal_channel_markdown)
    return 0 if payload.get("ok") else 1


def render_goal_channel_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Goal Channel",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- provider: `{payload.get('provider')}`",
        f"- operation: `{payload.get('operation')}`",
        f"- status: `{payload.get('status')}`",
        f"- execute: `{payload.get('execute')}`",
        f"- external_write_performed: `{payload.get('external_write_performed')}`",
        f"- readback_verified: `{payload.get('readback_verified')}`",
    ]
    if payload.get("blocker"):
        lines.append(f"- blocker: `{payload.get('blocker')}`")
    lines.extend(["", str(payload.get("public_summary") or "")])
    details = payload.get("details")
    if isinstance(details, dict) and details:
        lines.extend(["", "## Details"])
        lines.extend(f"- {key}: `{value}`" for key, value in details.items())
    return "\n".join(lines)
