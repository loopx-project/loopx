"""Reusable bounded context-provider integrations for LoopX capabilities."""

from .base import (
    canonical_context_text,
    canonical_context_lines,
    canonical_context_matches,
    ContextProviderItem,
    ContextProviderRetrieval,
    ContextProviderSync,
)
from .factory import build_context_provider, register_context_provider
from .bm25 import BM25ContextProvider, build_bm25_context_provider
from .openviking import (
    OpenVikingContextProvider,
    build_openviking_context_provider,
)
from .service_ownership import (
    context_provider_service_restarted,
    load_context_provider_service_ownership,
)


register_context_provider("openviking", build_openviking_context_provider)
register_context_provider("bm25", build_bm25_context_provider)

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
