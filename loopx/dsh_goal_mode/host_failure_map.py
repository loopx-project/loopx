"""Compatibility shim forwarding ``loopx.dsh_goal_mode.host_failure_map`` to ``loopx.hosts.dsh.host_failure_map``."""

from __future__ import annotations

from ..hosts.dsh import host_failure_map as _target


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))
