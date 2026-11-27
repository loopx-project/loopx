"""Compatibility shim forwarding ``loopx.agy_goal_mode.launchd_watchdog`` to ``loopx.hosts.agy.launchd_watchdog``."""

from __future__ import annotations

from ..hosts.agy import launchd_watchdog as _target


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))
