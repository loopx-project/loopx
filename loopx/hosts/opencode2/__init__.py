from __future__ import annotations

from .. import read_host_asset


def worker_source() -> str:
    return read_host_asset(__package__, "opencode2-goal-worker.mjs")
