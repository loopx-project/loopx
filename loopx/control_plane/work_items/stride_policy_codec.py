"""Configuration transport codec; stride decisions belong to the TS component.

The root package asset is shared with TypeScript and already included in wheels.
Reading these lexical options does not start Node or introduce a Python policy.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def stride_strategy_options() -> tuple[str, ...]:
    contract = json.loads(files("loopx.control_plane").joinpath("stride_strategy_schema.json").read_text())
    return tuple(contract["enum"])


def normalize_stride_strategy(value: Any) -> str:
    if not isinstance(value, str) or value not in stride_strategy_options():
        raise ValueError("stride_strategy must be off or evidence_conditioned_v1")
    return value
