"""The built-in chat endpoint rows every LoopX Chat channel ships.

``ChatRuntimeController.capabilities()`` reports two kinds of rows: the hosts
LoopX ships as built-ins, and the rows an owner registered through the endpoint
registry. Keeping the built-in rows here keeps each host's availability probe,
trust scope and transport kind in one place, so the controller's read model
stays a join over the catalog and the registry instead of a large literal.

Availability is a live probe, never a cached verdict: an uninstalled host must
render as *needs configuration* rather than as a session-open failure.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .chat_dsh import STEWARD_SEGMENT_ENV
from .control_plane.operator_provider import operator_provider_environ
from .control_plane.turn_driver.host_binding import (
    MANAGED_TURN_HOST,
    managed_executor_binding,
)
from .kiro_cli_goal_mode import (
    KIRO_CLI_CHAT_ADAPTER_KIND,
    KIRO_CLI_CHAT_AGENT_ID,
    KIRO_CLI_CHAT_DISPLAY_NAME,
)


def managed_host_capability(runtime_root: Path | None = None) -> dict[str, Any]:
    """Project the managed host as a chat endpoint with its own verdict.

    The channel can hold this host through the segment transport, and whether it
    can launch here is quoted from the governed Turn surface's own executor
    readback. Streaming, steering and tool calls stay ``False`` because the
    transport really does not offer them.
    """

    binding = managed_executor_binding(
        MANAGED_TURN_HOST,
        # A credential stored from a product surface authenticates this host,
        # so the availability verdict has to be resolved against the machine
        # store and not only against the service environment.
        environ=operator_provider_environ(runtime_root),
    )
    profile = binding.get("execution_profile") or {}
    return {
        "agent_id": MANAGED_TURN_HOST,
        "display_name": "DeepSeek Harness (managed)",
        "adapter_kind": "deepseek_harness_segment",
        "available": binding.get("available") is True,
        "unavailable_reason": binding.get("unavailable_reason"),
        "streaming": False,
        "resume": True,
        "interrupt": False,
        "tool_calls": False,
        "trust_scope": "read_only",
        # The channel pins this for its own segments; naming it here keeps the
        # frontend's execution chip honest about the boundary.
        "sandbox_mode": STEWARD_SEGMENT_ENV["DSH_PERMISSION_MODE"],
        "execution_profile": profile,
        "output_token_budget": binding.get("output_token_budget"),
        "source": "builtin",
    }


def builtin_chat_endpoints(
    *,
    codex_bin: str,
    claude_bin: str,
    kiro_cli_bin: str,
    runtime_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the built-in endpoints, managed host included."""

    return [
        {
            "agent_id": "codex",
            "display_name": "Codex",
            "adapter_kind": "codex_app_server",
            "available": bool(shutil.which(codex_bin)),
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "tool_calls": True,
            "trust_scope": "read_only",
            "source": "builtin",
        },
        {
            "agent_id": "claude-code",
            "display_name": "Claude Code",
            "adapter_kind": "claude_code_cli",
            "available": bool(shutil.which(claude_bin)),
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "tool_calls": True,
            "trust_scope": "read_only",
            "source": "builtin",
        },
        {
            # Kiro CLI ships an ACP stdio agent (`kiro-cli acp`), so it is
            # reachable through the existing ACP adapter without a new
            # transport. It is a built-in row rather than something the owner
            # must hand-register, because LoopX already owns the host's facts;
            # `available` stays a live PATH probe so an uninstalled host renders
            # as needing configuration instead of failing at session open.
            "agent_id": KIRO_CLI_CHAT_AGENT_ID,
            "display_name": KIRO_CLI_CHAT_DISPLAY_NAME,
            "adapter_kind": KIRO_CLI_CHAT_ADAPTER_KIND,
            "available": bool(shutil.which(kiro_cli_bin)),
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "tool_calls": True,
            # Kiro owns its persistent permission rules. LoopX cancels
            # interactive ACP permission requests, but cannot turn an existing
            # host-level `allow` rule into a read-only sandbox.
            "trust_scope": "workspace_write",
            "source": "builtin",
        },
        {
            "agent_id": "anthropic-api",
            "display_name": "Claude API",
            "adapter_kind": "anthropic_messages_api",
            "available": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            "streaming": False,
            "resume": True,
            "interrupt": False,
            "tool_calls": True,
            "trust_scope": "read_only",
            "source": "builtin",
        },
        {
            "agent_id": "openai-api",
            "display_name": "OpenAI API",
            "adapter_kind": "openai_messages_api",
            "available": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "streaming": False,
            "resume": True,
            "interrupt": False,
            "tool_calls": True,
            "trust_scope": "read_only",
            "source": "builtin",
        },
        managed_host_capability(runtime_root),
    ]
