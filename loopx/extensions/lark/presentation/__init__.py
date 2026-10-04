"""Lark/Feishu collaboration surface capability."""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "periodic_report_lark_sink_adapter",
    "periodic_report_miaoda_html_sink_adapter",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".periodic_report", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
