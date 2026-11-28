"""Compatibility entrypoint forwarding ``loopx.claude_goal_mode.statusline.goal_status`` to ``loopx.hosts.claude.statusline.goal_status``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[3])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.claude.statusline import goal_status as _target  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(dir(_target))


if __name__ == "__main__":
    getattr(_target, "pin_utf8_stdio")()
    getattr(_target, "main")()
