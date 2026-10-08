"""Native Codex process context for the Core-owned project boundary.

This is host adaptation, not another Session or authentication authority.
Ordinary Chat keeps its existing host configuration. Workspace-only Chat uses
an independent native store; no credentials or history are seeded into it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping


def codex_home(base_home: Path, policy: Mapping[str, Any] | None) -> Path:
    base = base_home.expanduser().resolve()
    key = policy.get("host_store_key") if policy else None
    if key is None:
        return base
    if not isinstance(key, str):
        raise ValueError("invalid Core-owned Codex store key")
    home = base / "loopx-projects" / key / ".codex"
    # An operator's pre-existing symlink must not silently select personal
    # skills, authentication or a different upstream thread store.
    if home.resolve() != home:
        raise ValueError("workspace-only Codex home cannot follow a symlink")
    return home


def home_for_project(base_home: Path, context: dict[str, str] | None) -> Path:
    from ...control_plane.effect_runtime import effect_runtime_result
    policy = effect_runtime_result("collaboration.project.session_identity", {"context": context}) if context else None
    return codex_home(base_home, policy)


def require_session_home(base_home: Path, session: Mapping[str, Any]) -> None:
    """A managed upstream thread's store is identity, never a routing override."""
    from ...chat_agent import CodexChatAgentError
    context = session.get("project_context")
    bound_home = session.get("codex_home")
    isolated = context is not None and context.get("filesystem_scope") == "workspace_only"
    expected = str(home_for_project(base_home, context))
    if (bound_home is not None and bound_home != expected) or (isolated and bound_home is None):
        raise CodexChatAgentError(
            "This managed Session belongs to a different Codex home. Restart LoopX Chat "
            "with its original LOOPX_CHAT_CODEX_HOME; do not copy or rebind its history. "
            "A workspace-only Session created with shared host context must be explicitly replaced.",
            error_code="codex_home_mismatch", gate=None,
        )


def process_environment(home: Path, *, isolated: bool) -> dict[str, str]:
    if not isolated:
        return {**os.environ, "CODEX_HOME": str(home)}
    host_home = home.parent
    host_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.mkdir(exist_ok=True, mode=0o700)
    env = {"HOME": str(host_home), "CODEX_HOME": str(home), "PATH": os.defpath}
    # Windows process startup needs these public OS paths, not the account's
    # PATH, provider keys, proxy credentials or other inherited variables.
    if os.name == "nt":
        env.update({key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ})
        env["USERPROFILE"] = str(host_home)
    return env


def disable_mcp_servers(config: Mapping[str, Any], host_config: Mapping[str, Any]) -> dict[str, Any]:
    """Disable effective native MCP servers, including project/managed layers.

    An empty map does not erase lower-layer entries. Only names are forwarded;
    commands, environment and credentials remain with the native config owner.
    """
    effective = config.get("mcp_servers", {})
    requested = host_config.get("mcp_servers", {})
    if not isinstance(effective, dict) or not isinstance(requested, dict):
        raise ValueError("invalid native MCP server configuration")
    return {**host_config, "mcp_servers": {
        name: {"enabled": False} for name in effective.keys() | requested.keys()
    }}
