"""Compatibility shim forwarding ``loopx.dsh_goal_mode.turn_host_adapter`` to ``loopx.hosts.dsh.turn_host_adapter``."""

from __future__ import annotations

from ..hosts.dsh import turn_host_adapter as _target


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))
