"""Decode current and retained check readbacks; decision rules belong to TS."""
from collections.abc import Mapping
from typing import Any


def configured_checks(value: Any) -> dict[str, Any] | None:
    # The old wire flag is decoded here only. New readback carries the actual
    # criteria or no command configuration; default outcome review is separate.
    if isinstance(value, Mapping) and value.get("enabled") is not False and isinstance(value.get("criteria"), list):
        return dict(value)
    return None
