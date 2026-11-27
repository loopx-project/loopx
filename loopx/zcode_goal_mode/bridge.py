"""Compatibility entrypoint forwarding ``loopx.zcode_goal_mode.bridge`` to ``loopx.hosts.zcode.bridge``."""

from __future__ import annotations

from pathlib import Path
import sys

if not __package__:
    _package_parent = str(Path(__file__).resolve().parents[2])
    if _package_parent not in sys.path:
        sys.path.insert(0, _package_parent)

from loopx.hosts.zcode import bridge as _target  # noqa: E402
from loopx.hosts.zcode.bridge import _validate_main  # noqa: E402


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | {"_validate_main"})


if __name__ == "__main__":
    raise SystemExit(_validate_main())
