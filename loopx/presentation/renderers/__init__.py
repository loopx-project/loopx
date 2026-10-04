"""Render-only helpers for LoopX presentation surfaces."""
from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "periodic_report_html_renderer_adapter": "periodic_report_html",
    "render_periodic_report_html": "periodic_report_html",
    "periodic_report_markdown_renderer_adapter": "periodic_report_markdown",
    "render_periodic_report_markdown": "periodic_report_markdown",
}

__all__ = [
    "periodic_report_html_renderer_adapter",
    "periodic_report_markdown_renderer_adapter",
    "render_periodic_report_html",
    "render_periodic_report_markdown",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
