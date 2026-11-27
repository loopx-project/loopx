"""Compatibility entrypoint forwarding ``loopx.agy_goal_mode.host_runtime`` to ``loopx.hosts.agy.host_runtime``."""

from __future__ import annotations

from ..hosts.agy import host_runtime as _target
from ..hosts.agy.host_runtime import main


def __getattr__(name: str) -> object:
    return getattr(_target, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_target)) | {"main"})


if __name__ == "__main__":
    raise SystemExit(main())
