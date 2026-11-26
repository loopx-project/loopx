"""Registration and dispatch for the built-in Goal Channel runtime CLI."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.goals.botmux_runtime import (
    BOTMUX_DEFAULT_ENDPOINT,
    BOTMUX_DEFAULT_TOKEN_ENV,
    default_botmux_runtime_binding_path,
    disable_botmux_runtime,
    doctor_botmux_runtime,
    setup_botmux_runtime,
    status_botmux_runtime,
    trigger_botmux_runtime,
)


def register_goal_channel_runtime_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    runtime = subparsers.add_parser(
        "runtime",
        help="Bind and drive an optional IM/runtime provider for a Goal Channel.",
    )
    runtime_sub = runtime.add_subparsers(
        dest="goal_channel_runtime_command",
        required=True,
    )
    runtime_setup = runtime_sub.add_parser(
        "setup",
        help="Verify and bind one botmux runtime. Dry-run unless --execute.",
    )
    add_subcommand_format(runtime_setup)
    _add_runtime_common_args(runtime_setup)
    runtime_setup.add_argument("--endpoint", default=BOTMUX_DEFAULT_ENDPOINT)
    runtime_setup.add_argument("--bot-id", required=True)
    runtime_setup.add_argument("--chat-id", required=True)
    runtime_setup.add_argument("--token-env", default=BOTMUX_DEFAULT_TOKEN_ENV)
    runtime_setup.add_argument("--execute", action="store_true")

    runtime_doctor = runtime_sub.add_parser(
        "doctor",
        help="Verify the configured botmux daemon, bot, project, and chat route.",
    )
    add_subcommand_format(runtime_doctor)
    _add_runtime_common_args(runtime_doctor)

    runtime_trigger = runtime_sub.add_parser(
        "trigger",
        help="Queue one bounded LoopX turn through the configured botmux runtime.",
    )
    add_subcommand_format(runtime_trigger)
    _add_runtime_common_args(runtime_trigger)
    runtime_trigger.add_argument("--instruction")
    runtime_trigger.add_argument(
        "--turn-key",
        help="Explicit semantic revision key for a deliberate new runtime turn.",
    )
    runtime_trigger.add_argument("--execute", action="store_true")

    runtime_status = runtime_sub.add_parser(
        "status",
        help="Read the typed lifecycle result of the current botmux runtime turn.",
    )
    add_subcommand_format(runtime_status)
    _add_runtime_common_args(runtime_status)
    runtime_status.add_argument(
        "--execute",
        action="store_true",
        help="Persist the observed runtime state into the local-private receipt.",
    )

    runtime_disable = runtime_sub.add_parser(
        "disable",
        help="Disable this goal's botmux runtime binding. Dry-run unless --execute.",
    )
    add_subcommand_format(runtime_disable)
    _add_runtime_common_args(runtime_disable)
    runtime_disable.add_argument("--execute", action="store_true")


def _add_runtime_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--runtime-binding-path",
        help="Local-private botmux runtime binding path beside the project registry.",
    )


def run_goal_channel_runtime(
    args: argparse.Namespace,
    *,
    registry: Mapping[str, object],
    registry_path: Path,
) -> dict[str, object]:
    goal_id = str(args.goal_id)
    execute = bool(getattr(args, "execute", False))
    runtime_binding_path = (
        Path(str(args.runtime_binding_path)).expanduser()
        if getattr(args, "runtime_binding_path", None)
        else default_botmux_runtime_binding_path(registry_path)
    )
    runtime_command = str(args.goal_channel_runtime_command)
    try:
        if runtime_command == "setup":
            payload = setup_botmux_runtime(
                registry=registry,
                registry_path=registry_path,
                goal_id=goal_id,
                binding_path=runtime_binding_path,
                endpoint=args.endpoint,
                bot_id=args.bot_id,
                chat_id=args.chat_id,
                token_env=args.token_env,
                execute=execute,
            )
        elif runtime_command == "doctor":
            payload = doctor_botmux_runtime(
                goal_id=goal_id,
                binding_path=runtime_binding_path,
            )
        elif runtime_command == "trigger":
            payload = trigger_botmux_runtime(
                registry=registry,
                registry_path=registry_path,
                goal_id=goal_id,
                binding_path=runtime_binding_path,
                instruction=args.instruction,
                turn_key=args.turn_key,
                execute=execute,
            )
        elif runtime_command == "status":
            payload = status_botmux_runtime(
                goal_id=goal_id,
                binding_path=runtime_binding_path,
                execute=execute,
            )
        elif runtime_command == "disable":
            payload = disable_botmux_runtime(
                goal_id=goal_id,
                binding_path=runtime_binding_path,
                execute=execute,
            )
        else:
            raise ValueError(f"unknown goal-channel runtime command: {runtime_command}")
    except ValueError:
        payload = {
            "schema_version": "loopx_goal_channel_runtime_operation_v0",
            "ok": False,
            "goal_id": goal_id,
            "provider": "botmux",
            "operation": f"runtime_{runtime_command}",
            "execute": execute,
            "status": "blocked",
            "external_write_performed": False,
            "readback_verified": False,
            "idempotency_key": None,
            "receipt_id": None,
            "public_summary": "the botmux runtime configuration is invalid",
            "private_provider_payload_captured": False,
            "blocker": "invalid_configuration",
        }
    return payload
