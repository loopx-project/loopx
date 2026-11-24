"""Configuration transport codec; stride decisions belong to the TS component.

The root package asset is shared with TypeScript and already included in wheels.
Reading these lexical options does not start Node or introduce a Python policy.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


# The only live stride strategy. There is no live switch: Goal profiles,
# machine defaults and benchmark arms cannot select "off" for new decisions.
LIVE_STRIDE_STRATEGY = "evidence_conditioned_v1"


@lru_cache(maxsize=1)
def stride_strategy_options() -> tuple[str, ...]:
    """Historically recorded strategy values, accepted only when decoding stored data."""
    contract = json.loads(files("loopx.control_plane").joinpath("stride_strategy_schema.json").read_text(encoding="utf-8"))
    return tuple(contract["enum"])


def normalize_stride_strategy(value: Any) -> str:
    """Validate a historically recorded strategy; live decisions use LIVE_STRIDE_STRATEGY."""
    if not isinstance(value, str) or value not in stride_strategy_options():
        raise ValueError("stride_strategy must be off or evidence_conditioned_v1")
    return value
