"""The Goal and agent one Kiro CLI session is bound to.

Shared by the MCP server and the preToolUse gate, so both answer "who is this
session?" with the same rule: the ``bind-agent-thread`` binding for this exact
``KIRO_SESSION_ID`` under the ``kiro-cli`` host surface, and nothing else. It
imports no MCP SDK, because the hook runs under whatever interpreter Kiro
launches and must stay cheap to start.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from loopx.goal_mode_context import find_registry, resolve_goal_context
from loopx.kiro_cli_goal_mode import KIRO_CLI_AGENT_TYPE, KIRO_CLI_SESSION_ID_ENV
from loopx.thread_agent_binding import (
    ThreadBindingRequestError,
    resolve_registry_thread_agent_binding,
)


def session_goal_context(
    cwd: str | Path | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """The bound Goal context for ``session_id`` in ``cwd``'s project, or None.

    A missing id, a missing registry, a missing binding and an ambiguous
    binding all resolve to None so callers fail closed rather than guessing an
    agent from registry order.
    """
    if not cwd or not str(session_id or "").strip():
        return None
    registry = find_registry(cwd)
    if registry is None:
        return None
    try:
        binding = resolve_registry_thread_agent_binding(
            registry_path=registry,
            host_surface=KIRO_CLI_AGENT_TYPE,
            thread_id=str(session_id),
        )
    except (ThreadBindingRequestError, OSError, ValueError):
        return None
    if binding.get("status") != "bound":
        return None
    return resolve_goal_context(
        cwd,
        preferred_goal_id=str(binding["goal_id"]),
        preferred_agent_id=str(binding["agent_id"]),
        require_preferred_binding=True,
    )


def goal_context(
    cwd: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """:func:`session_goal_context` for the session id in ``environ``."""
    source = os.environ if environ is None else environ
    return session_goal_context(cwd, source.get(KIRO_CLI_SESSION_ID_ENV))
