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
from ..control_plane.scheduler.execution_context import SchedulerRuntimeProfile
from ..execution_profile import execution_profile_turn_granularity
from ..heartbeat_prompt import (
    build_heartbeat_prompt,
    build_heartbeat_prompt_error_payload,
    project_heartbeat_agent_input,
    render_heartbeat_prompt_markdown,
)
from .support_control_registry import resolve_heartbeat_active_state

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]


def handle_heartbeat_prompt_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    registry_was_supplied: bool,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int:
    active_state = None
    resolved_active_state = None
    active_state_source = None
    registered_agents = None
    effective_agent_id = args.agent_id
    agent_registry_path = registry_path
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
        reward_memory_enabled = False
        if effective_agent_id is not None:
            from ..capabilities.reward_memory.configuration import (
                reward_memory_automatic_ingest_for_agent,
            )

            reward_memory_enabled = reward_memory_automatic_ingest_for_agent(
                registry_goal if isinstance(registry_goal, dict) else {},
                effective_agent_id,
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
        from copy import copy
        from .context_machine import apply_context_machine_options

        # Persist caller choices only. Machine defaults and private paths
        # must resolve again on the next wake, including after rollback.
        bootstrap_args = copy(args)
        apply_context_machine_options(args, registry_path=agent_registry_path,
            runtime_root_arg=args.runtime_root, heartbeat=True)
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
            registry_path=agent_registry_path.resolve(),
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
            decision_output_root=args.decision_output_root,
            decision_handoff_socket=args.decision_handoff_socket,
            codex_context_dir=args.codex_context_dir,
            codex_context_root=args.codex_context_root,
            context_delivery=args.context_delivery,
            turn_envelope=args.turn_envelope,
            reward_memory_enabled=reward_memory_enabled,
        )
        if args.bootstrap and payload.get("ok"):
            from ..control_plane.heartbeat.bootstrap_prompt import goal_bootstrap
            from ..control_plane.heartbeat.budget import build_interface_budget
            body = goal_bootstrap(bootstrap_args, registry=agent_registry_path)
            payload["task_body"] = body
            payload["bootstrap"] = True
            payload["interface_budget"] = build_interface_budget(
                task_body=body, goal_id=args.goal_id,
                active_state=str(payload.get("active_state") or ""), thin=True,
                registry_path=str(agent_registry_path.resolve()),
                runtime_root=(str(Path(args.runtime_root).expanduser().resolve())
                              if args.runtime_root else None),
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
            registry_path=agent_registry_path.resolve(),
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
