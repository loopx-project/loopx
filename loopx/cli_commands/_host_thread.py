from __future__ import annotations

import argparse
import os

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
    KIRO_CLI_AGENT_TYPE: KIRO_CLI_SESSION_ID_ENV,
}


def ambient_host_thread_id(host_surface: str) -> str | None:
    """Read the host-provided local context, never a caller's route override.

    This is a trusted-local-runtime fence, not authentication against another
    process that can change this OS user's environment or canonical files.
    """
    variable = HOST_THREAD_ID_ENV.get(host_surface)
    return (os.environ.get(variable) or None) if variable else None


def current_host_thread_id(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "thread_id", None)
    if explicit:
        return str(explicit)
    return ambient_host_thread_id(str(getattr(args, "host_surface", None) or ""))
