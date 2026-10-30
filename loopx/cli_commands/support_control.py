from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..chat_server import (
    DEFAULT_CHAT_HOST,
    DEFAULT_CHAT_PORT,
    serve_chat,
)
from ..dashboard_launcher import launch_dashboard
from ..runtime_activation import replace_managed_loopx_chat as replace_existing_loopx_chat
from ..heartbeat_prequota import (
    render_heartbeat_pre_quota_markdown,
    run_heartbeat_pre_quota,
)
from ..kiro_cli_goal_mode import KIRO_CLI_BIN
from ..paths import default_public_scan_root
from ..registry import (
    inspect_registry,
    inspect_registry_boundary,
    render_registry_boundary_markdown,
    render_registry_markdown,
)
from ..status_server import (
    DEFAULT_STATUS_HOST,
    DEFAULT_STATUS_PATH,
    DEFAULT_STATUS_PORT,
    serve_status,
)
from .support_control_backup import (
    handle_backup_state_command,
    register_backup_state_command,
)
from .support_control_chat import chat_server_options, register_chat_and_dashboard_commands
from .support_control_chat_endpoint import (
    handle_chat_endpoint_command,
)
from .dsh_launch import handle_dsh_launch_command, register_dsh_launch_command
from .support_control_heartbeat_registration import (
    register_heartbeat_control_commands,
)
from .support_control_promotion import (
    handle_promotion_control_command,
    register_promotion_control_commands,
)
from .support_control_registry import explicit_global_registry
from .support_control_supervisor import (
    SUPERVISOR_CONTROL_COMMANDS,
    handle_supervisor_control_command,
    register_supervisor_control_commands,
)
from .support_control_update import (
    UPDATE_CONTROL_COMMANDS,
    handle_update_command,
    register_update_command,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

SUPPORT_CONTROL_COMMANDS = {
    "research-checkpoint",
    "configuration-backup",
    "automation-prompts",
    "backup-state",
    "chat",
    "chat-endpoint",
    "dashboard",
    "dsh-launch",
    "heartbeat-prequota",
    "promotion-gate",
    "promotion-readiness",
    "upgrade-plan",
    "update",
    "upgrade",
    "registry",
    "registry-boundary",
    "serve-status",
} | SUPERVISOR_CONTROL_COMMANDS



def register_support_control_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    from .automation_prompts import register_automation_prompts
    register_automation_prompts(subparsers, add_subcommand_format)
    register_backup_state_command(subparsers, add_subcommand_format)
    from .research_checkpoint import register_research_checkpoint
    register_research_checkpoint(subparsers, add_subcommand_format)
    from .configuration_backup import register_configuration_backup
    register_configuration_backup(subparsers, add_subcommand_format)
    register_heartbeat_control_commands(subparsers, add_subcommand_format)

    register_supervisor_control_commands(subparsers, add_subcommand_format)

    register_promotion_control_commands(subparsers, add_subcommand_format)

    register_update_command(subparsers, add_subcommand_format)
    register_dsh_launch_command(subparsers, add_subcommand_format)

    subparsers.add_parser(
        "registry", help="Inspect registry goals and adapter declarations."
    )
    registry_boundary_parser = subparsers.add_parser(
        "registry-boundary",
        help="Classify a registry file as local-only, global-local, public projection, or public fixture.",
    )
    registry_boundary_parser.add_argument(
        "--path",
        help="Registry path to classify. Defaults to the active --registry path.",
    )
    registry_boundary_parser.add_argument(
        "--require-not-tracked",
        action="store_true",
        help="Return non-zero if the registry is tracked while publication policy disallows pushing it.",
    )
    registry_boundary_parser.add_argument(
        "--require-gitignored",
        action="store_true",
        help="Return non-zero if the registry should be ignored but is neither ignored nor tracked.",
    )

    serve_status_parser = subparsers.add_parser(
        "serve-status", help="Serve live status JSON for the local dashboard."
    )
    serve_status_parser.add_argument(
        "--host",
        default=DEFAULT_STATUS_HOST,
        help="Bind host. Defaults to localhost only.",
    )
    serve_status_parser.add_argument("--port", type=int, default=DEFAULT_STATUS_PORT)
    serve_status_parser.add_argument(
        "--path", default=DEFAULT_STATUS_PATH, help="Status JSON route."
    )
    serve_status_parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help="Public files to scan for obvious private material. Defaults to the LoopX install root.",
    )
    serve_status_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help="Specific public file or directory to scan. Repeatable. Overrides --scan-root when set.",
    )
    serve_status_parser.add_argument("--limit", type=int, default=5)
    serve_status_parser.add_argument(
        "--enable-reward-write-api",
        action="store_true",
        help="Enable POST /reward/append on loopback only so the dashboard can append human_reward overlays.",
    )
    serve_status_parser.add_argument(
        "--enable-control-plane-write-api",
        action="store_true",
        help="Enable POST /control-plane/configure-goal/apply on loopback only so the dashboard can write registry settings.",
    )
    serve_status_parser.add_argument(
        "--enable-goal-subagent-configuration",
        action="store_true",
        help=(
            "Expose the Goal sub-agent status projection. Disabled by default."
        ),
    )
    serve_status_parser.add_argument(
        "--global-registry",
        action="store_true",
        help="Serve the shared global registry view even when invoked from a project directory.",
    )
    serve_status_parser.add_argument(
        "--verbose", action="store_true", help="Print HTTP request logs."
    )
    register_chat_and_dashboard_commands(subparsers, add_subcommand_format)


def _start_failure_markdown(
    command: str,
) -> Callable[[dict[str, object]], str]:
    """Render a local service start failure without the status projection.

    A failed start has no status payload, so the status renderer would print an
    empty status table and drop the error that tells the operator what to fix.
    """

    def render(payload: dict[str, object]) -> str:
        lines = [f"# LoopX {command} could not start", "", f"- error: {payload.get('error')}"]
        if payload.get("registry"):
            lines.append(f"- registry: `{payload['registry']}`")
        gate = payload.get("gate")
        if isinstance(gate, dict) and gate.get("next_action"):
            lines.append(f"- next action: {gate['next_action']}")
        return "\n".join(lines)

    return render


def handle_support_control_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    registry_was_supplied: bool,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int | None:
    if args.command not in SUPPORT_CONTROL_COMMANDS:
        return None

    if args.command == "automation-prompts":
        from .automation_prompts import render, run
        try:
            payload = run(args, registry_path)
        except Exception as error:
            payload = {"ok": False, "error": str(error)}
        print_payload(payload, output_format(args), render)
        return 0 if payload.get("ok") else 1

    if args.command == "chat-endpoint":
        return handle_chat_endpoint_command(
            args,
            registry_path=registry_path,
            print_payload=print_payload,
        )

    if args.command == "dsh-launch":
        return handle_dsh_launch_command(args)

    if args.command == "configuration-backup":
        from .configuration_backup import handle_configuration_backup
        return handle_configuration_backup(args, registry_path=registry_path,
            print_payload=print_payload, output_format=output_format)

    if args.command == "research-checkpoint":
        from .research_checkpoint import handle_research_checkpoint
        return handle_research_checkpoint(args, registry_path=registry_path,
            print_payload=print_payload, output_format=output_format)

    if args.command == "backup-state":
        return handle_backup_state_command(
            args,
            registry_path=registry_path,
            print_payload=print_payload,
            output_format=output_format,
        )

    if args.command == "heartbeat-prequota":
        prequota_registry = (
            registry_path
            if registry_was_supplied
            else explicit_global_registry(args.runtime_root)
        )
        payload = run_heartbeat_pre_quota(
            registry_path=prequota_registry,
            runtime_root_arg=args.runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            fetch_timeout_seconds=args.fetch_timeout_seconds,
        )
        print_payload(
            payload,
            output_format(args),
            render_heartbeat_pre_quota_markdown,
        )
        return 0

    supervisor_result = handle_supervisor_control_command(
        args,
        registry_path=registry_path,
        registry_was_supplied=registry_was_supplied,
        print_payload=print_payload,
        output_format=output_format,
    )
    if supervisor_result is not None:
        return supervisor_result

    promotion_result = handle_promotion_control_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
        output_format=output_format,
    )
    if promotion_result is not None:
        return promotion_result

    if args.command in UPDATE_CONTROL_COMMANDS:
        return handle_update_command(
            args,
            registry_path=registry_path,
            registry_was_supplied=registry_was_supplied,
            print_payload=print_payload,
            output_format=output_format,
        )

    if args.command == "registry":
        payload = inspect_registry(registry_path)
        print_payload(payload, args.format, render_registry_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "registry-boundary":
        boundary_path = Path(args.path).expanduser() if args.path else registry_path
        payload = inspect_registry_boundary(boundary_path)
        git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
        if (
            args.require_not_tracked
            and payload.get("ok")
            and git.get("tracked")
            and not payload.get("github_push_allowed")
        ):
            payload = dict(payload)
            payload["ok"] = False
            payload.setdefault("risks", []).append(
                "registry_tracked_but_not_push_allowed"
            )
        if (
            args.require_gitignored
            and payload.get("ok")
            and payload.get("should_be_gitignored")
        ):
            if (
                git.get("inside_worktree")
                and not git.get("ignored")
                and not git.get("tracked")
            ):
                payload = dict(payload)
                payload["ok"] = False
                payload.setdefault("risks", []).append("registry_should_be_gitignored")
        print_payload(payload, args.format, render_registry_boundary_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "serve-status":
        try:
            status_registry_path = (
                explicit_global_registry(args.runtime_root, registry_path=registry_path)
                if args.global_registry
                else registry_path
            )
            scan_roots = [Path(item).expanduser() for item in args.scan_path]
            if not scan_roots:
                scan_roots = [Path(args.scan_root).expanduser()]
            serve_status(
                registry_path=status_registry_path,
                runtime_root_override=args.runtime_root,
                scan_roots=scan_roots,
                limit=max(0, args.limit),
                host=args.host,
                port=args.port,
                status_path=args.path,
                enable_reward_write_api=bool(args.enable_reward_write_api),
                enable_control_plane_write_api=bool(
                    args.enable_control_plane_write_api
                ),
                verbose=bool(args.verbose),
                enable_goal_subagent_configuration=bool(
                    args.enable_goal_subagent_configuration
                ),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "registry": str(
                    status_registry_path
                    if "status_registry_path" in locals()
                    else registry_path
                ),
                "runtime_root": args.runtime_root,
                "error": str(exc),
            }
            print_payload(payload, args.format, _start_failure_markdown("serve-status"))
            return 1
        return 0

    if args.command == "dashboard":
        try:
            dashboard_registry_path = (
                explicit_global_registry(args.runtime_root, registry_path=registry_path)
                if getattr(args, "global_registry", False)
                else registry_path
            )
            scan_roots = [
                Path(item).expanduser() for item in getattr(args, "scan_path", []) or []
            ]
            if not scan_roots and getattr(args, "scan_root", None):
                scan_roots = [Path(args.scan_root).expanduser()]
            return launch_dashboard(
                registry_path=dashboard_registry_path,
                runtime_root_override=args.runtime_root,
                scan_roots=scan_roots,
                limit=max(0, getattr(args, "limit", 20)),
                host=getattr(args, "host", DEFAULT_CHAT_HOST),
                port=getattr(args, "port", DEFAULT_CHAT_PORT),
                goal_id=getattr(args, "goal_id", None),
                codex_bin=getattr(args, "codex_bin", "codex"),
                claude_bin=getattr(args, "claude_bin", "claude"),
                kiro_cli_bin=getattr(args, "kiro_cli_bin", KIRO_CLI_BIN),
                lark_cli_bin=getattr(args, "lark_cli_bin", None),
                assets_dir=Path(args.assets_dir).expanduser().resolve()
                if getattr(args, "assets_dir", None)
                else None,
                verbose=getattr(args, "verbose", False),
                open_browser=not getattr(args, "no_open", False),
                prefer_dev=getattr(args, "dev", False),
                enable_goal_subagent_configuration=bool(
                    getattr(args, "enable_goal_subagent_configuration", False)
                ),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "schema_version": "loopx_dashboard_start_v0",
                "error": str(exc),
            }
            print_payload(payload, args.format, _start_failure_markdown("dashboard"))
            return 1

    if args.command == "chat":
        try:
            options = chat_server_options(args, registry_path=registry_path)
            if bool(getattr(args, "replace_existing_loopx_chat", False)):
                replace_existing_loopx_chat(args.host, args.port)
            serve_chat(**options)
        except Exception as exc:
            payload = {
                "ok": False,
                "schema_version": "loopx_chat_start_v0",
                "error": str(exc),
                "gate": {
                    "kind": "host_tool_gate",
                    "summary": "LoopX Chat could not start on this host.",
                    "next_action": "Resolve the reported local host capability, then retry loopx chat.",
                },
            }
            print_payload(payload, args.format, _start_failure_markdown("chat"))
            return 1
        return 0

    return None
