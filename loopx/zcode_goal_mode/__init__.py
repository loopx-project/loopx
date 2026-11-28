"""Compatibility shim forwarding ``loopx.zcode_goal_mode`` to ``loopx.hosts.zcode``."""

from __future__ import annotations

import importlib

from loopx.hosts import zcode as _target

_SUBMODULES = frozenset({"api", "bridge", "diagnostics"})


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        return importlib.import_module(f"loopx.hosts.zcode.{name}")
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | _SUBMODULES)
