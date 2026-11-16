"""Context-provider exports resolve without selecting unrelated implementations."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import (
        ContextProviderItem as ContextProviderItem,
        ContextProviderRetrieval as ContextProviderRetrieval,
        ContextProviderSync as ContextProviderSync,
        canonical_context_lines as canonical_context_lines,
        canonical_context_matches as canonical_context_matches,
        canonical_context_text as canonical_context_text,
    )
    from .bm25 import BM25ContextProvider as BM25ContextProvider
    from .bm25 import build_bm25_context_provider as build_bm25_context_provider
    from .factory import build_context_provider as build_context_provider
    from .factory import register_context_provider as register_context_provider
    from .openviking import OpenVikingContextProvider as OpenVikingContextProvider
    from .openviking import build_openviking_context_provider as build_openviking_context_provider
    from .service_ownership import context_provider_service_restarted as context_provider_service_restarted
    from .service_ownership import load_context_provider_service_ownership as load_context_provider_service_ownership

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
