from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..agent_onboarding import (
    build_agent_onboarding_packet,
    render_agent_onboarding_markdown,
)
from ..bootstrap_command_pack import (
    build_loopx_bootstrap_command_pack,
    render_loopx_bootstrap_command_pack_markdown,
)
from ..host_loop_activation import (
    SUPPORTED_AGENT_TYPES,
    AgentTypeError,
    build_agent_type_catalog,
    render_agent_type_catalog_markdown,
)
from ..project_prompt import (
    build_codex_cli_bootstrap_message,
    build_codex_cli_exec_handoff,
    build_codex_cli_tui_bootstrap_smoke_bundle,
    build_new_project_prompt,
    render_codex_cli_bootstrap_message_markdown,
    render_codex_cli_exec_handoff_markdown,
    render_codex_cli_tui_bootstrap_smoke_bundle_markdown,
    render_new_project_prompt_markdown,
)
from ._host_thread import current_host_thread_id
from .start_goal import handle_start_goal_command

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def handle_new_project_prompt_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
) -> int:
    payload = build_new_project_prompt(
        project=Path(args.project),
        goal_doc=Path(args.goal_doc),
        goal_id=args.goal_id,
        objective=args.objective,
        domain=args.domain,
        adapter_kind=args.adapter_kind,
        adapter_status=args.adapter_status,
        next_probe=args.next_probe,
        spawn_allowed=bool(args.spawn_allowed),
        allowed_domains=args.allowed_domain,
        write_scope=args.write_scope,
    )
    print_payload(payload, args.format, render_new_project_prompt_markdown)
    return 0


def handle_agent_onboard_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
    *,
    runtime_root_arg: str | None = None,
) -> int:
    if bool(getattr(args, "list_agent_types", False)):
        print_payload(build_agent_type_catalog(), args.format, render_agent_type_catalog_markdown)
        return 0
    if not args.agent_type:
        exc = AgentTypeError(
            value=None,
            reason="--agent-type is required unless --list-agent-types is used",
            suggestions=SUPPORTED_AGENT_TYPES,
        )
        print_payload(exc.to_payload(), args.format, render_agent_onboarding_markdown)
        return 2
    try:
        payload = build_agent_onboarding_packet(
            project=Path(args.project),
            agent_type=args.agent_type,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            cli_bin=args.cli_bin,
            task_text=args.task_text,
            available_capabilities=args.available_capabilities,
            runtime_root_arg=runtime_root_arg,
        )
    except AgentTypeError as exc:
        print_payload(exc.to_payload(), args.format, render_agent_onboarding_markdown)
        return 2
    print_payload(payload, args.format, render_agent_onboarding_markdown)
    return 0


def handle_loopx_bootstrap_command_pack_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
    *,
    runtime_root_arg: str | None = None,
) -> int:
    payload = build_loopx_bootstrap_command_pack(
        project=Path(args.project),
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        thread_id=current_host_thread_id(args),
        new_peer=bool(getattr(args, "new_peer", False)),
        cli_bin=args.cli_bin,
        host_surface=args.host_surface,
        goal_text=args.goal_text,
        available_capabilities=args.available_capabilities,
        capability_route=args.capability_route,
        fine_grained=bool(args.fine_grained),
        runtime_root_arg=runtime_root_arg,
    )
    if bool(getattr(args, "message_only", False)):
        print(str(payload.get("message") or ""))
        return 0
    print_payload(payload, args.format, render_loopx_bootstrap_command_pack_markdown)
    return 0


def handle_codex_cli_bootstrap_message_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
) -> int:
    payload = build_codex_cli_bootstrap_message(
        project=Path(args.project),
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        cli_bin=args.cli_bin,
    )
    if bool(getattr(args, "message_only", False)):
        print(str(payload.get("message") or ""))
        return 0
    print_payload(payload, args.format, render_codex_cli_bootstrap_message_markdown)
    return 0


def handle_codex_cli_tui_bootstrap_smoke_bundle_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
) -> int:
    payload = build_codex_cli_tui_bootstrap_smoke_bundle(
        project=Path(args.project),
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        cli_bin=args.cli_bin,
    )
    print_payload(payload, args.format, render_codex_cli_tui_bootstrap_smoke_bundle_markdown)
    return 0


def handle_codex_cli_exec_handoff_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
) -> int:
    payload = build_codex_cli_exec_handoff(
        project=Path(args.project),
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        cli_bin=args.cli_bin,
        codex_bin=args.codex_bin,
    )
    print_payload(payload, args.format, render_codex_cli_exec_handoff_markdown)
    return 0 if payload.get("ok") else 1


def handle_starter_bootstrap_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
) -> int | None:
    handlers: dict[str, Callable[[argparse.Namespace, PrintPayload], int]] = {
        "agent-onboard": handle_agent_onboard_command,
        "bootstrap-command-pack": handle_loopx_bootstrap_command_pack_command,
        "start-goal": handle_start_goal_command,
        "new-project-prompt": handle_new_project_prompt_command,
        "codex-cli-bootstrap-message": handle_codex_cli_bootstrap_message_command,
        "codex-cli-tui-bootstrap-smoke-bundle": handle_codex_cli_tui_bootstrap_smoke_bundle_command,
        "codex-cli-exec-handoff": handle_codex_cli_exec_handoff_command,
    }
    handler = handlers.get(str(getattr(args, "command", "")))
    if handler is None:
        return None
    if handler is handle_start_goal_command:
        return handler(args, print_payload, runtime_root_arg=args.runtime_root)
    if handler is handle_loopx_bootstrap_command_pack_command:
        return handler(args, print_payload, runtime_root_arg=args.runtime_root)
    if handler is handle_agent_onboard_command:
        return handler(args, print_payload, runtime_root_arg=args.runtime_root)
    return handler(args, print_payload)
