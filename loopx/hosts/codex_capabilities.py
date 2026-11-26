"""Lightweight Codex host capability descriptors with zero heavy imports.

Each Codex surface descriptor is registered lazily on demand when queried or
when an owning runtime module re-exports it, keeping cold capability lookups
free of subprocess, sqlite, CLI, or chat transport dependencies.
"""

from __future__ import annotations

from .capabilities import (
    HostHarnessCapabilities,
    _register_builtin_host_capabilities,
)

CODEX_APP_HOST_CAPABILITIES: HostHarnessCapabilities = (
    _register_builtin_host_capabilities(
        HostHarnessCapabilities(
            host_id="codex-app",
            integration_tier="orchestrated_app",
            adapter_module="loopx.codex_app_thread_activity",
            supported_capabilities=(),
            hook_events=(),
            requires_utf8_stdio=True,
            native_goal_primitive=False,
            native_scheduler_primitive=True,
            description=(
                "Codex App orchestrated host surface using native automation.toml "
                "RRULE heartbeat scheduling and local thread activity inspection; "
                "Goal admission stays outer-driven via skills and CLI."
            ),
        )
    )
)

CODEX_APP_SERVER_HOST_CAPABILITIES: HostHarnessCapabilities = (
    _register_builtin_host_capabilities(
        HostHarnessCapabilities(
            host_id="codex-app-server",
            integration_tier="orchestrated_app",
            adapter_module="loopx.control_plane.turn_driver.codex_operation_host",
            supported_capabilities=("session_fork", "managed_thread_resume"),
            hook_events=(),
            requires_utf8_stdio=True,
            native_goal_primitive=False,
            native_scheduler_primitive=False,
            description=(
                "Long-lived Codex app-server JSON-RPC stdio runner orchestrated by "
                "LoopX Turn/Steward controllers with managed thread resume and native "
                "thread/fork support; outer scheduler and Goals remain external."
            ),
        )
    )
)

CODEX_CLI_HOST_CAPABILITIES: HostHarnessCapabilities = (
    _register_builtin_host_capabilities(
        HostHarnessCapabilities(
            host_id="codex-cli",
            integration_tier="boundary_cli",
            adapter_module="loopx.control_plane.turn_driver.codex_cli",
            supported_capabilities=(
                "epoch_reset",
                "fold_and_prefetch",
                "quiet_turn",
                "managed_thread_resume",
            ),
            hook_events=(),
            requires_utf8_stdio=True,
            native_goal_primitive=False,
            native_scheduler_primitive=False,
            description=(
                "Outer-driven Codex CLI (codex exec) boundary Turn runner with UTF-8 "
                "stdio, managed thread resume, Quiet-Turn pre-admission skips, "
                "EpochReset transitions, and bounded epoch prefetch delivery."
            ),
        )
    )
)

CODEX_CLI_HOOKS_HOST_CAPABILITIES: HostHarnessCapabilities = (
    _register_builtin_host_capabilities(
        HostHarnessCapabilities(
            host_id="codex-cli-hooks",
            integration_tier="boundary_cli",
            adapter_module="loopx.control_plane.turn_driver.codex_context",
            supported_capabilities=(
                "epoch_reset",
                "fold_and_prefetch",
                "quiet_turn",
                "compact_hook",
            ),
            hook_events=(
                "SessionStart",
                "PreCompact",
                "PostCompact",
                "PostToolUse",
                "Interrupt",
            ),
            requires_utf8_stdio=True,
            native_goal_primitive=False,
            native_scheduler_primitive=False,
            description=(
                "Codex CLI native lifecycle hook runner wired through loopx quota "
                "context-hook, handling SessionStart, PreCompact, PostCompact, "
                "PostToolUse, and Interrupt with semantic-unit folding, Quiet-Turn, "
                "and EpochReset."
            ),
        )
    )
)
