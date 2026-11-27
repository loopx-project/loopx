"""Compatibility shim forwarding ``loopx.claude_goal_mode.hooks.goal_state`` to ``loopx.hosts.claude.hooks.goal_state``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[3])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.claude.hooks import goal_state as _target  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))
