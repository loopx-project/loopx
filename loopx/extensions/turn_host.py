"""Supported integration seam for separately installed generic-cli Turn hosts.

These are the existing kernel owners, not another implementation of admission,
candidate validation or process supervision. Importing this module grants no
execution, writeback, lease, acceptance or quota authority.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..control_plane.effect_runtime import _node_executable
from ..control_plane.goals.goal_ref_validation import exact_goal_ref
from ..control_plane.turn_driver.executor import validate_loopx_turn_host_result
from ..control_plane.turn_driver.host_candidate import (
    build_result,
    extract_turn_authority,
    parse_model_json,
    render_prompt,
)

__all__ = [
    "build_result", "exact_goal_ref", "extract_turn_authority", "parse_model_json",
    "render_prompt", "supervised_stdio_command", "validate_loopx_turn_host_result",
]


def supervised_stdio_command(command: Sequence[str]) -> list[str]:
    """Keep an SDK-detached stdio server under the existing TS process owner.

    The relay's stdin is its parent-lifetime channel. It remains alive through
    tool-process cleanup after EOF, cancellation or the parent's hard exit.
    The caller still owns command selection and the tool environment.
    """
    if not command or any(not isinstance(arg, str) or "\0" in arg for arg in command) or not command[0]:
        raise ValueError("a stdio command argv is required")
    bridge = Path(__file__).resolve().parents[1] / "control_plane" / "turn_driver" / "host_stdio_bridge.ts"
    return [_node_executable(), "--no-warnings", "--experimental-strip-types", str(bridge), *command]
