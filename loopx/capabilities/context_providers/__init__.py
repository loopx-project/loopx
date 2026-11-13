"""Context-provider exports resolve without selecting unrelated implementations."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "canonical_context_text": "base",
    "canonical_context_lines": "base",
    "canonical_context_matches": "base",
    "ContextProviderItem": "base",
    "ContextProviderRetrieval": "base",
    "ContextProviderSync": "base",
    "build_context_provider": "factory",
    "register_context_provider": "factory",
    "BM25ContextProvider": "bm25",
    "build_bm25_context_provider": "bm25",
    "OpenVikingContextProvider": "openviking",
    "build_openviking_context_provider": "openviking",
    "context_provider_service_restarted": "service_ownership",
    "load_context_provider_service_ownership": "service_ownership",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    "ContextProviderItem",
    "ContextProviderRetrieval",
    "ContextProviderSync",
    "OpenVikingContextProvider",
    "BM25ContextProvider",
    "build_context_provider",
    "register_context_provider",
    "context_provider_service_restarted",
    "load_context_provider_service_ownership",
    "canonical_context_text",
    "canonical_context_lines",
    "canonical_context_matches",
]
