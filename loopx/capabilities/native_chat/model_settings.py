"""Provider-neutral model values persisted by native Chat Sessions.

These are bounded visible provider values, not LoopX entity or file names.
"""
from __future__ import annotations

from typing import Any


_AGENT_MODEL_ID_MAX_LENGTH = 160
MODEL_SELECTION_UNSET = object()


def normalize_agent_model_id(value: Any) -> str:
    """Keep a provider model value without applying file-name rules.

    ACP option values are protocol strings rather than LoopX entity IDs. They
    are persisted in JSON only, so retain a bounded printable value while
    rejecting controls that would make logs or projections unsafe.
    """
    token = str(value or "")
    if not token.strip() or len(token) > _AGENT_MODEL_ID_MAX_LENGTH or any(ord(character) < 32 for character in token):
        raise ValueError("agent_model_id must be a bounded model value")
    return token


def normalize_agent_reasoning_effort(value: Any) -> str:
    """Preserve an exact provider option; admission still belongs to the host."""
    if not isinstance(value, str):
        raise ValueError("agent_reasoning_effort must be a bounded provider value")
    try:
        return normalize_agent_model_id(value)
    except ValueError as exc:
        raise ValueError("agent_reasoning_effort must be a bounded provider value") from exc


def normalize_session_model_settings(
    *, model: Any, effort: Any, tier: Any,
    requested_model: Any = MODEL_SELECTION_UNSET,
    requested_effort: Any = MODEL_SELECTION_UNSET,
) -> dict[str, Any]:
    """Normalize before persistence; absent requests must not become user pins."""
    fields = {"agent_model_id": normalize_agent_model_id(model) if model else None,
              "agent_reasoning_effort": normalize_agent_model_id(effort) if effort else None,
              "agent_service_tier": normalize_agent_model_id(tier) if tier else None}
    if requested_model is not MODEL_SELECTION_UNSET:
        fields["requested_agent_model_id"] = normalize_agent_model_id(requested_model) if requested_model else None
    if requested_effort is not MODEL_SELECTION_UNSET:
        fields["requested_agent_reasoning_effort"] = normalize_agent_reasoning_effort(requested_effort) if requested_effort is not None else None
    return fields
