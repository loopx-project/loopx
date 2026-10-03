from __future__ import annotations

from typing import Any


_HOST_CHILD_CONTEXT_ADAPTERS: dict[str, dict[str, dict[str, Any]]] = {
    "codex-cli": {
        "fresh": {
            "native_operation": "spawn_agent",
            "arguments": {"fork_turns": "none"},
            "requires_session": False,
        },
        "forked_snapshot": {
            "native_operation": "spawn_agent",
            "arguments": {"fork_turns": "all"},
            "requires_session": False,
        },
    },
    "claude-code": {
        "fresh": {
            "native_operation": "Task",
            "arguments": {},
            "requires_session": False,
        },
    },
}


def supported_child_context_modes(host: str) -> tuple[str, ...]:
    return tuple(_HOST_CHILD_CONTEXT_ADAPTERS.get(host, {}))


def project_child_context_adapter(
    *,
    host: str,
    context_mode: str,
) -> dict[str, Any] | None:
    adapter = _HOST_CHILD_CONTEXT_ADAPTERS.get(host, {}).get(context_mode)
    if adapter is None:
        return None
    return {
        "host": host,
        **adapter,
        "arguments": dict(adapter["arguments"]),
    }
