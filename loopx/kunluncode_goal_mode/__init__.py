"""Compatibility shim forwarding ``loopx.kunluncode_goal_mode`` to ``loopx.hosts.kunluncode``."""

from __future__ import annotations

import importlib

from loopx.hosts import kunluncode as _target

_SUBMODULES = frozenset(
    {"app_server", "cli", "context", "control_plane", "guards", "runtime", "server"}
)


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        return importlib.import_module(f"loopx.hosts.kunluncode.{name}")
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | _SUBMODULES)
