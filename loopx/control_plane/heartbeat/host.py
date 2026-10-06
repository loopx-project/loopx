"""Heartbeat host detection helpers inside the heartbeat bounded context."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from ...turn_identity import normalize_turn_instance_id
from ..scheduler.execution_context import (
    ExecutionMode,
    HostSurface,
    NATIVE_GOAL_RUNTIME_PROFILES,
    SchedulerOwner,
    SchedulerRuntimeProfile,
    resolve_scheduler_execution_context,
    scheduler_runtime_profile_for_execution_context,
)

HEARTBEAT_PROMPT_SCHEMA_VERSION = "loopx_heartbeat_prompt_v0"


def resolve_heartbeat_capture_root(
    root: str | Path | None,
    *,
    thin: bool,
    full: bool,
    compact: bool,
    brief: bool,
    native_goal_host: bool,
    turn_instance_id: str | None,
) -> Path | None:
    """Validate capture transport for an already resolved host-owned Turn."""
    if root is None:
        return None
    if not thin or full or compact or brief or native_goal_host or not turn_instance_id:
        raise ValueError("--decision-output-root requires thin mode and an explicit host-owned Turn ID")
    directory = Path(root).expanduser().absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("--decision-output-root must be an existing directory, not a symlink")
    return directory


def uses_native_goal_host_loop(
    *,
    runtime_profile: str | None,
    scheduler_execution_context: dict[str, Any] | None,
) -> bool:
    if runtime_profile:
        try:
            profile = SchedulerRuntimeProfile(runtime_profile)
        except ValueError:
            return False
        return profile in NATIVE_GOAL_RUNTIME_PROFILES
    if scheduler_execution_context is None:
        return False
    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    if not resolution.ok or resolution.context is None:
        return False
    context = resolution.context
    if context.execution_mode is not ExecutionMode.INTERACTIVE:
        return False
    if context.host_surface is HostSurface.ARK_MANAGED_AGENT:
        return context.scheduler_owner is SchedulerOwner.GOAL_RUNTIME
    return (
        context.host_surface in {HostSurface.CODEX_APP_SSH, HostSurface.CODEX_CLI}
        and context.scheduler_owner is SchedulerOwner.AGENT_CLI_LOOP
    )


def uses_ark_managed_agent_goal_host(
    *,
    runtime_profile: str | None,
    scheduler_execution_context: dict[str, Any] | None,
) -> bool:
    if runtime_profile:
        try:
            return (
                SchedulerRuntimeProfile(runtime_profile)
                is SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL
            )
        except ValueError:
            return False
    if scheduler_execution_context is None:
        return False
    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    return bool(
        resolution.ok
        and resolution.context is not None
        and resolution.context.host_surface is HostSurface.ARK_MANAGED_AGENT
    )


def resolve_exact_heartbeat_turn_identity(
    *,
    turn_instance_id: str | None,
    agent_id: str | None,
    runtime_profile: str | None,
    scheduler_execution_context: dict[str, Any] | None,
) -> tuple[str | None, str, dict[str, str]]:
    """Validate and project an optional receipt-producing heartbeat identity."""

    normalized = normalize_turn_instance_id(turn_instance_id)
    if normalized is None:
        return None, "", {}
    if not agent_id:
        raise ValueError("--turn-instance-id requires exact --agent-id identity")
    profile = (
        SchedulerRuntimeProfile(runtime_profile)
        if runtime_profile is not None
        else scheduler_runtime_profile_for_execution_context(
            scheduler_execution_context
        )
    )
    if profile not in {
        SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT,
        SchedulerRuntimeProfile.TRAE_APP,
    }:
        raise ValueError(
            "--turn-instance-id requires runtime-profile generic_cli or "
            "codex_app_heartbeat, or trae_app so quota guard creates "
            "a heartbeat receipt"
        )
    return normalized, f" --turn-instance-id {shlex.quote(normalized)}", {
        "schema_version": HEARTBEAT_PROMPT_SCHEMA_VERSION,
        "turn_instance_id": normalized,
    }
