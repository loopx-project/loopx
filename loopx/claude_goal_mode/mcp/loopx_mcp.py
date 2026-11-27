"""Compatibility entrypoint forwarding ``loopx.claude_goal_mode.mcp.loopx_mcp`` to ``loopx.hosts.claude.mcp.loopx_mcp``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[3])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.claude.mcp import loopx_mcp as _target  # noqa: E402
from loopx.hosts.claude.mcp.loopx_mcp import mcp  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | {"mcp"})


if __name__ == "__main__":
    mcp.run()
