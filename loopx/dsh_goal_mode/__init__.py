"""Compatibility shim forwarding ``loopx.dsh_goal_mode`` to ``loopx.hosts.dsh``."""

from __future__ import annotations

import importlib

from ..hosts import dsh as _target

_SUBMODULES = frozenset({"host_failure_map", "turn_host_adapter"})
__all__ = list(_target.__all__)


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        return importlib.import_module(f"loopx.hosts.dsh.{name}")
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | _SUBMODULES)
