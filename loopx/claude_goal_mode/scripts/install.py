"""Compatibility entrypoint forwarding ``loopx.claude_goal_mode.scripts.install`` to ``loopx.hosts.claude.scripts.install``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[3])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.claude.scripts import install as _target  # noqa: E402
from loopx.hosts.claude.scripts.install import main  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | {"main"})


if __name__ == "__main__":
    raise SystemExit(main())
