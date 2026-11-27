"""Compatibility shim forwarding ``loopx.kunluncode_goal_mode.guards`` to ``loopx.hosts.kunluncode.guards``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[2])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.kunluncode import guards as _target  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))
