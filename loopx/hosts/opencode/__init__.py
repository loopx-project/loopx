from __future__ import annotations

from .. import read_host_asset


def plugin_source() -> str:
    return read_host_asset(__package__, "loopx-goal.js")


def runtime_source() -> str:
    return read_host_asset(__package__, "goal-bridge-runtime.mjs")
