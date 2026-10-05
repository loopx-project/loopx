from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..agent_registry import (
    agent_profile_from_registry,
    load_goal_from_registry,
    registered_agent_ids_from_registry,
    require_registered_agent_id,
)
from ..chat_server import (
    DEFAULT_CHAT_HOST,
    DEFAULT_CHAT_PORT,
    serve_chat,
)
from ..control_plane.reward_memory import reward_memory_goal_policy
from ..control_plane.scheduler.execution_context import SchedulerRuntimeProfile
from ..dashboard_launcher import launch_dashboard, replace_existing_loopx_chat
from ..execution_profile import execution_profile_turn_granularity
from ..heartbeat_prequota import (
    render_heartbeat_pre_quota_markdown,
    run_heartbeat_pre_quota,
)
from ..heartbeat_prompt import (
    build_heartbeat_prompt,
    build_heartbeat_prompt_error_payload,
    project_heartbeat_agent_input,
    render_heartbeat_prompt_markdown,
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
from .support_control_chat import register_chat_and_dashboard_commands
from .support_control_chat_endpoint import (
    handle_chat_endpoint_command,
)
from .support_control_heartbeat_registration import (
    register_heartbeat_control_commands,
)
from .support_control_promotion import (
    handle_promotion_control_command,
    register_promotion_control_commands,
)
from .support_control_registry import (
    explicit_global_registry,
    resolve_heartbeat_active_state,
)
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
    "configuration-backup",
    "automation-prompts",
    "backup-state",
    "chat",
    "chat-endpoint",
    "dashboard",
    "heartbeat-prequota",
    "heartbeat-prompt",
    "promotion-gate",
    "promotion-readiness",
    "upgrade-plan",
    "update",
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
    from .configuration_backup import register_configuration_backup
    register_configuration_backup(subparsers, add_subcommand_format)
    register_heartbeat_control_commands(subparsers, add_subcommand_format)

    register_supervisor_control_commands(subparsers, add_subcommand_format)

    register_promotion_control_commands(subparsers, add_subcommand_format)

    register_update_command(subparsers, add_subcommand_format)

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

    if args.command == "configuration-backup":
        from .configuration_backup import handle_configuration_backup
        return handle_configuration_backup(args, registry_path=registry_path,
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

    if args.command == "heartbeat-prompt":
        active_state = None
        resolved_active_state = None
        active_state_source = None
        registered_agents = None
        effective_agent_id = args.agent_id
        requested_runtime_profile = (
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT.value
            if args.codex_app
            else SchedulerRuntimeProfile.TRAE_APP.value
            if getattr(args, "trae_app", False)
            else args.runtime_profile
        )
        try:
            active_state, resolved_active_state, active_state_source = (
                resolve_heartbeat_active_state(
                    goal_id=args.goal_id,
                    active_state_arg=args.active_state,
                    registry_path=registry_path,
                    runtime_root_arg=args.runtime_root,
                    allow_global_goal_lookup_fallback=not registry_was_supplied,
                )
            )
            agent_registry_path = registry_path
            if active_state_source.startswith("registry:"):
                agent_registry_path = Path(
                    active_state_source.removeprefix("registry:")
                )
            registered_agents = registered_agent_ids_from_registry(
                agent_registry_path, args.goal_id
            )
            registry_goal = load_goal_from_registry(
                agent_registry_path,
                args.goal_id,
            )
            turn_granularity = execution_profile_turn_granularity(
                registry_goal.get("execution_profile")
                if isinstance(registry_goal, dict)
                else None
            )
            reward_memory_policy = reward_memory_goal_policy(
                registry_goal if isinstance(registry_goal, dict) else {}
            )
            reward_memory_enabled = bool(
                reward_memory_policy["enabled"]
                and reward_memory_policy["automation"].get("automatic_ingest") is True
            )
            agent_profile = None
            if args.agent_id:
                effective_agent_id = require_registered_agent_id(
                    registry_path=agent_registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    field="agent_id",
                )
                agent_profile = agent_profile_from_registry(
                    agent_registry_path, args.goal_id, effective_agent_id
                )
            explicit_scheduler_fields = (
                args.host_surface,
                args.scheduler_owner,
                args.execution_mode,
            )
            app_alias_count = int(bool(args.codex_app)) + int(
                bool(getattr(args, "trae_app", False))
            )
            if app_alias_count > 1:
                raise ValueError(
                    "--codex-app and --trae_app are mutually exclusive"
                )
            if app_alias_count and (
                args.runtime_profile or any(explicit_scheduler_fields)
            ):
                raise ValueError(
                    "app runtime aliases cannot be combined with --runtime-profile, "
                    "--host-surface, --scheduler-owner, or --execution-mode"
                )
            if args.runtime_profile and any(explicit_scheduler_fields):
                raise ValueError(
                    "--runtime-profile cannot be combined with --host-surface, "
                    "--scheduler-owner, or --execution-mode"
                )
            runtime_profile = requested_runtime_profile
            payload = build_heartbeat_prompt(
                goal_id=args.goal_id,
                active_state=active_state,
                active_state_source=active_state_source,
                resolved_active_state=resolved_active_state,
                material_queue_rule=args.material_rule,
                permission_rule=args.permission_rule,
                full=bool(args.full),
                compact=bool(args.compact),
                brief=bool(args.brief),
                thin=bool(args.thin),
                cli_bin=args.cli_bin,
                runtime_root=args.runtime_root,
                agent_id=effective_agent_id,
                agent_scopes=args.agent_scopes,
                agent_profile=agent_profile,
                registered_agents=registered_agents,
                available_capabilities=args.available_capabilities,
                runtime_profile=runtime_profile,
                scheduler_execution_context=(
                    {
                        "host_surface": args.host_surface,
                        "scheduler_owner": args.scheduler_owner,
                        "execution_mode": args.execution_mode,
                    }
                    if any(explicit_scheduler_fields)
                    else None
                ),
                visible_goal_host=args.visible_goal_host,
                turn_granularity=turn_granularity,
                turn_instance_id=args.turn_instance_id,
                reward_memory_enabled=reward_memory_enabled,
            )
            if args.bootstrap and payload.get("ok"):
                from ..control_plane.heartbeat.bootstrap_prompt import goal_bootstrap
                from ..control_plane.heartbeat.budget import build_interface_budget
                body = goal_bootstrap(args, registry=agent_registry_path)
                payload["task_body"] = body
                payload["bootstrap"] = True
                payload["interface_budget"] = build_interface_budget(
                    task_body=body, goal_id=args.goal_id,
                    active_state=str(payload.get("active_state") or ""), thin=True,
                )
                if not payload["interface_budget"]["within_budget"]:
                    raise ValueError("bootstrap exceeds the thin budget; move lengthy policy into registered state")
        except Exception as exc:
            fallback_active_state = active_state
            fallback_resolved_active_state = resolved_active_state
            fallback_active_state_source = active_state_source
            if fallback_active_state is None and args.active_state:
                fallback_active_state = Path(args.active_state).expanduser()
                fallback_resolved_active_state = (
                    fallback_resolved_active_state or fallback_active_state
                )
                fallback_active_state_source = (
                    fallback_active_state_source or "explicit"
                )
            elif fallback_active_state_source is None:
                fallback_active_state_source = "registry"
            payload = build_heartbeat_prompt_error_payload(
                goal_id=args.goal_id,
                error=str(exc),
                active_state=fallback_active_state,
                active_state_source=fallback_active_state_source,
                resolved_active_state=fallback_resolved_active_state,
                material_queue_rule=args.material_rule,
                permission_rule=args.permission_rule,
                full=bool(args.full),
                compact=bool(args.compact),
                brief=bool(args.brief),
                thin=bool(args.thin),
                cli_bin=args.cli_bin,
                runtime_root=args.runtime_root,
                agent_id=effective_agent_id or args.agent_id,
                agent_scopes=args.agent_scopes,
                registered_agents=registered_agents,
                available_capabilities=args.available_capabilities,
            )
        selected_output_format = output_format(args)
        recurring_runtime_profile = requested_runtime_profile in {
            None,
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT.value,
            SchedulerRuntimeProfile.TRAE_APP.value,
            SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP.value,
            # Kiro CLI moved off generic_cli onto its own profile; its facade
            # still reads the thin Agent input, so keep that output shape.
            SchedulerRuntimeProfile.KIRO_CLI_VISIBLE.value,
        }
        recurring_thin_surface = bool(
            recurring_runtime_profile and args.visible_goal_host is None
        )
        output_payload = (
            project_heartbeat_agent_input(payload)
            if selected_output_format == "json"
            and payload.get("thin") is True
            and recurring_thin_surface
            else payload
        )
        print_payload(
            output_payload,
            selected_output_format,
            render_heartbeat_prompt_markdown,
        )
        return 0 if payload.get("ok") else 1

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
            chat_registry_path = (
                explicit_global_registry(args.runtime_root, registry_path=registry_path)
                if args.global_registry
                else registry_path
            )
            scan_roots = [Path(item).expanduser() for item in args.scan_path]
            if not scan_roots:
                scan_roots = [Path(args.scan_root).expanduser()]
            if bool(getattr(args, "replace_existing_loopx_chat", False)):
                replace_existing_loopx_chat(args.host, args.port)
            serve_chat(
                project_workspace_grant=args.project_workspace_grant,
                registry_path=chat_registry_path,
                runtime_root_override=args.runtime_root,
                scan_roots=scan_roots,
                limit=max(0, args.limit),
                host=args.host,
                port=args.port,
                goal_id=args.goal_id,
                codex_bin=args.codex_bin,
                claude_bin=args.claude_bin,
                kiro_cli_bin=getattr(args, "kiro_cli_bin", KIRO_CLI_BIN),
                lark_cli_bin=args.lark_cli_bin,
                startup_timeout_sec=max(0.1, float(args.startup_timeout_seconds)),
                idle_timeout_sec=max(0.1, float(args.idle_timeout_seconds)),
                hard_timeout_sec=max(0.1, float(args.hard_timeout_seconds)),
                assets_dir=Path(args.assets_dir).expanduser()
                if args.assets_dir
                else None,
                open_browser=not bool(args.no_open),
                verbose=bool(args.verbose),
                enable_goal_subagent_configuration=bool(
                    args.enable_goal_subagent_configuration
                ),
            )
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
