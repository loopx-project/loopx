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
from .control_plane.turn_driver.execution_profile import managed_execution_profile
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

    operator_environ = operator_provider_environ(runtime_root)
    binding = managed_executor_binding(
        MANAGED_TURN_HOST,
        # A credential stored from a product surface authenticates this host,
        # so the availability verdict has to be resolved against the machine
        # store and not only against the service environment.
        environ=operator_environ,
    )
    profile = binding.get("execution_profile") or None
    profile_details = managed_execution_profile(operator_environ)
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
        # The UI needs the resolved field sources to point at the real repair
        # owner. In particular, a bad Turn reasoning effort is an environment
        # problem for the managed executor, not a Steward session default.
        "execution_profile_details": profile_details,
        "credential_env": binding.get("credential_env"),
        "endpoint_env": binding.get("endpoint_env"),
        "unavailable_remediation": binding.get("unavailable_remediation", []),
        "output_token_budget": binding.get("output_token_budget"),
        "source": "builtin",
    }


def _executable_readiness(executable: str) -> dict[str, Any]:
    """Live PATH probe for a CLI-backed endpoint, naming the missing command."""

    if shutil.which(executable):
        return {"available": True, "executable": executable}
    return {"available": False, "executable": executable,
            "unavailable_reason": "executable_not_found"}


def _environment_readiness(variable: str) -> dict[str, Any]:
    """Presence probe for an API-key endpoint, naming the unset variable."""

    if os.environ.get(variable, "").strip():
        return {"available": True, "environment_variable": variable}
    return {"available": False, "environment_variable": variable,
            "unavailable_reason": "api_key_not_set"}


def builtin_chat_endpoints(
    *,
    codex_bin: str,
    claude_bin: str,
    kiro_cli_bin: str,
    runtime_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the built-in endpoints, managed host included.

    An unavailable built-in row names what is missing so the owner can repair
    it in place: ``unavailable_reason`` is ``executable_not_found`` with the
    probed ``executable``, or ``api_key_not_set`` with the ``environment_variable``.
    Availability itself stays a live probe; nothing is installed or granted.
    """

    return [
        {
            "agent_id": "codex",
            "display_name": "Codex",
            "adapter_kind": "codex_app_server",
            **_executable_readiness(codex_bin),
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
            **_executable_readiness(claude_bin),
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
            **_executable_readiness(kiro_cli_bin),
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
            **_environment_readiness("ANTHROPIC_API_KEY"),
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
            **_environment_readiness("OPENAI_API_KEY"),
            "streaming": False,
            "resume": True,
            "interrupt": False,
            "tool_calls": True,
            "trust_scope": "read_only",
            "source": "builtin",
        },
        managed_host_capability(runtime_root),
    ]
