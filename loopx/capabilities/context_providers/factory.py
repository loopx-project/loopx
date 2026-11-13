from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .base import ContextProvider


ContextProviderBuilder = Callable[[Mapping[str, Any]], ContextProvider]


def _build_openviking(config: Mapping[str, Any]) -> ContextProvider:
    from .openviking import build_openviking_context_provider

    return build_openviking_context_provider(config)


def _build_bm25(config: Mapping[str, Any]) -> ContextProvider:
    from .bm25 import build_bm25_context_provider

    return build_bm25_context_provider(config)


# Builtin ids are already registered, so extensions retain the same duplicate
# rejection. Resolving that registry does not select either provider's code.
_CONTEXT_PROVIDER_BUILDERS: dict[str, ContextProviderBuilder] = {
    "openviking": _build_openviking,
    "bm25": _build_bm25,
}


def register_context_provider(
    provider_id: str,
    builder: ContextProviderBuilder,
) -> None:
    normalized = str(provider_id or "").strip()
    if not normalized:
        raise ValueError("context provider id is required")
    if normalized in _CONTEXT_PROVIDER_BUILDERS:
        raise ValueError(f"context provider already registered: {normalized}")
    _CONTEXT_PROVIDER_BUILDERS[normalized] = builder


def build_context_provider(config: Mapping[str, Any]) -> ContextProvider:
    """Resolve one configured provider without capability-specific branching."""

    provider_id = str(config.get("provider") or "").strip()
    builder = _CONTEXT_PROVIDER_BUILDERS.get(provider_id)
    if builder is None:
        raise ValueError(f"unsupported context provider: {provider_id or '<missing>'}")
    return builder(config)
