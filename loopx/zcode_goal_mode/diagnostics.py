"""Compatibility shim forwarding ``loopx.zcode_goal_mode.diagnostics`` to ``loopx.hosts.zcode.diagnostics``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[2])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.zcode import diagnostics as _target  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))
