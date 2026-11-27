"""Compatibility shim forwarding ``loopx.agy_goal_mode`` to ``loopx.hosts.agy``."""

from __future__ import annotations

import importlib

from ..hosts import agy as _target

_SUBMODULES = frozenset({"host_runtime", "launchd_watchdog"})


def __getattr__(name: str) -> object:
    if name in _SUBMODULES:
        return importlib.import_module(f"loopx.hosts.agy.{name}")
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | _SUBMODULES)
