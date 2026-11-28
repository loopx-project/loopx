from __future__ import annotations

from .. import read_host_asset


def extension_source() -> str:
    return read_host_asset(__package__, "loopx-goal.ts")


def runtime_source() -> str:
    return read_host_asset(__package__, "pi-goal-loop-runtime.mjs")
