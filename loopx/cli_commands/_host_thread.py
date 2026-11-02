from __future__ import annotations

import argparse
import os

from ..agy_goal_mode import ANTIGRAVITY_APP_AGENT_TYPE
from ..kiro_cli_goal_mode import KIRO_CLI_AGENT_TYPE, KIRO_CLI_SESSION_ID_ENV

# Host surface -> the environment variable that host exports for its own
# session/thread id. A binding is only durable if the host guarantees the id, so
# a surface stays out of this table unless its runtime actually exports one; it
# then resolves to no thread id instead of guessing from prose or reusing
# another host's variable.
HOST_THREAD_ID_ENV: dict[str, str] = {
    "codex-app": "CODEX_THREAD_ID",
    "codex-app-ssh": "CODEX_THREAD_ID",
    "codex-ide-plugin": "CODEX_THREAD_ID",
    "codex-cli-tui": "CODEX_THREAD_ID",
    "trae_app": "TRAECLI_THREAD_ID",
    "agy": "ANTIGRAVITY_CONVERSATION_ID",
    ANTIGRAVITY_APP_AGENT_TYPE: "ANTIGRAVITY_CONVERSATION_ID",
    "antigravity_app": "ANTIGRAVITY_CONVERSATION_ID",
    KIRO_CLI_AGENT_TYPE: KIRO_CLI_SESSION_ID_ENV,
}


def ambient_host_thread_id(
    host_surface: str,
    *,
    workspace: str | None = None,
) -> str | None:
    """Read the host-provided local context, never a caller's route override.

    This is a trusted-local-runtime fence, not authentication against another
    process that can change this OS user's environment or canonical files.
    """
    variable = HOST_THREAD_ID_ENV.get(host_surface)
    env_value = (os.environ.get(variable) or None) if variable else None
    if env_value:
        return env_value
    if host_surface in {"agy", ANTIGRAVITY_APP_AGENT_TYPE, "antigravity_app"}:
        if os.environ.get("PYTEST_CURRENT_TEST") and not (
            os.environ.get("ANTIGRAVITY_LS_ADDRESS")
            or os.environ.get("ANTIGRAVITY_LS_URL")
        ):
            return None
        from ..agy_goal_mode.host_runtime import discover_active_conversation_id

        return discover_active_conversation_id(workspace=workspace)
    return None


def current_host_thread_id(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "thread_id", None)
    if explicit:
        return str(explicit)
    return ambient_host_thread_id(
        str(getattr(args, "host_surface", None) or ""),
        workspace=getattr(args, "project", None),
    )
