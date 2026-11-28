from __future__ import annotations

import os
import shlex
from typing import Any
from pathlib import Path

ZCODE_INSTALL_SURFACE = "zcode"
ZCODE_HOME_ENV = "ZCODE_HOME"
DEFAULT_ZCODE_HOME = ".zcode"
SKILLS_SUBDIR = "skills"
SKILLS_ROOT_LABEL = "ZCODE_HOME/skills"


def zcode_home(value: str | None = None) -> Path:
    """ZCode discovers user skills from ZCODE_HOME/skills (default ~/.zcode).

    The default skill entry gates the session turn loop by quota. A separate
    native CLI binding is an explicit opt-in and does not change this root.
    """
    raw = (
        value
        or os.environ.get(ZCODE_HOME_ENV)
        or os.environ.get("ZCODE_AGENTS_HOME")
        or str(Path.home() / DEFAULT_ZCODE_HOME)
    )
    return Path(raw).expanduser()


def native_goal_activation(
    *, cli_bin: str, runtime_root: str | None, goal_id: str,
    agent_id: str | None, activation_allowed: bool,
) -> dict[str, Any]:
    """Discover the optional provider; generating commands performs no effects."""
    prefix = [cli_bin]
    if runtime_root:
        prefix.extend(["--runtime-root", runtime_root])
    commands = {
        action: shlex.join([*prefix, "--format", "json", "zcode-goal", action,
                           "--goal-id", goal_id, "--agent-id", str(agent_id)])
        for action in ("bind", "start", "pause", "resume", "stop", "status")
    } if activation_allowed else {}
    return {
        "default_off": True, "execution_mode": "managed_runtime", "host_surface": "zcode_cli",
        "commands": commands, "requires_explicit_host_selection": True,
        "quota_boundary": "Admission before start/resume and serial revocation checks during execution; no per-model-call token limit.",
        "session_boundary": "One managed app-server session. This does not attach the current Desktop or terminal conversation.",
    }
