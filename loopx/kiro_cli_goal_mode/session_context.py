"""The Goal and agent one Kiro CLI session is bound to.

Shared by the MCP server and the preToolUse gate, so both answer "who is this
session?" with the same rule: the ``bind-agent-thread`` binding for this exact
``KIRO_SESSION_ID`` under the ``kiro-cli`` host surface, and nothing else. It
imports no MCP SDK, because the hook runs under whatever interpreter Kiro
launches and must stay cheap to start.

:func:`resolve_session_binding` reports *why* a session has no context as a
typed status. Callers must not collapse those reasons: "this project has no
LoopX registry" and "this session has not been bound yet" are ordinary
pre-binding states, while an unreadable registry, an ambiguous binding or a
binding that names a Goal or agent the registry no longer holds are faults.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from loopx.goal_mode_context import find_registry, resolve_goal_context
from loopx.kiro_cli_goal_mode import KIRO_CLI_AGENT_TYPE, KIRO_CLI_SESSION_ID_ENV
from loopx.thread_agent_binding import (
    ThreadBindingRequestError,
    resolve_registry_thread_agent_binding,
)


class SessionBindingStatus(str, Enum):
    # Resolved to exactly one registered Goal and agent.
    BOUND = "bound"
    # The host event carried no session id.
    NO_SESSION_ID = "no_session_id"
    # No LoopX registry above the session's working directory.
    NO_REGISTRY = "no_registry"
    # A registry exists and holds no binding for this session.
    UNBOUND = "unbound"
    # The registry exists but could not be read.
    REGISTRY_UNREADABLE = "registry_unreadable"
    # One session is bound to more than one Goal or agent.
    AMBIGUOUS = "ambiguous"
    # The binding names a Goal or agent the registry no longer holds.
    STALE = "stale"


# The states a session can be in before `/loopx` has bound it. Every other
# non-bound state is a fault in the authority the gate depends on.
PRE_BINDING_STATUSES = frozenset(
    {SessionBindingStatus.NO_REGISTRY, SessionBindingStatus.UNBOUND}
)


@dataclass(frozen=True)
class SessionBinding:
    status: SessionBindingStatus
    context: dict[str, Any] | None = None

    @property
    def bound(self) -> bool:
        return self.status is SessionBindingStatus.BOUND and self.context is not None


def resolve_session_binding(
    cwd: str | Path | None,
    session_id: str | None,
) -> SessionBinding:
    """Resolve ``session_id`` in ``cwd``'s project to a typed binding state."""
    if not str(session_id or "").strip():
        return SessionBinding(SessionBindingStatus.NO_SESSION_ID)
    if not cwd:
        return SessionBinding(SessionBindingStatus.NO_REGISTRY)
    registry = find_registry(cwd)
    if registry is None:
        return SessionBinding(SessionBindingStatus.NO_REGISTRY)
    try:
        binding = resolve_registry_thread_agent_binding(
            registry_path=registry,
            host_surface=KIRO_CLI_AGENT_TYPE,
            thread_id=str(session_id),
        )
    except (ThreadBindingRequestError, OSError, ValueError):
        return SessionBinding(SessionBindingStatus.REGISTRY_UNREADABLE)
    status = binding.get("status")
    if status == "missing":
        return SessionBinding(SessionBindingStatus.UNBOUND)
    if status != "bound":
        return SessionBinding(SessionBindingStatus.AMBIGUOUS)
    context = resolve_goal_context(
        cwd,
        preferred_goal_id=str(binding["goal_id"]),
        preferred_agent_id=str(binding["agent_id"]),
        require_preferred_binding=True,
    )
    if context is None:
        return SessionBinding(SessionBindingStatus.STALE)
    return SessionBinding(SessionBindingStatus.BOUND, context)


def session_goal_context(
    cwd: str | Path | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """The bound Goal context for ``session_id``, or None when not bound."""
    return resolve_session_binding(cwd, session_id).context


def goal_context(
    cwd: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """:func:`session_goal_context` for the session id in ``environ``."""
    source = os.environ if environ is None else environ
    return session_goal_context(cwd, source.get(KIRO_CLI_SESSION_ID_ENV))
