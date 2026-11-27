"""Compatibility shim forwarding ``loopx.claude_goal_mode`` to ``loopx.hosts.claude``."""

from __future__ import annotations

import importlib

from loopx.hosts import claude as _target

_SUBMODULES = frozenset({"hooks", "mcp", "scripts", "statusline"})
__all__ = list(_target.__all__)


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        return importlib.import_module(f"loopx.hosts.claude.{name}")
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | _SUBMODULES)
