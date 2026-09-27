"""Original configuration IO adapter for the shared TS checkpoint projection."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ...control_plane.effect_runtime import effect_runtime_result
from .experiment import resolve_reward_memory_surface_config


def build_reward_memory_surface_read_authority_checkpoints(
    config: Mapping[str, Any], surface_id: str, *, verified: bool, source_ref: str,
) -> dict[str, dict[str, Any]]:
    """Project only the configured surface; the caller supplies existing read proof.

    Enabled configuration is not proof. False remains false. This does not call
    a provider, select a policy source, or verify/expand the caller's authority.
    """
    route = resolve_reward_memory_surface_config(config, surface_id)
    result = effect_runtime_result("reward_memory.read_authority.surface_checkpoints", {
        "surface_id": surface_id, "verified": verified, "source_ref": source_ref,
        "corpora": [{"corpus_id": item["corpus"]["corpus_id"],
            "read_authority": item["corpus"]["read_authority"],
            "scope": {key: item["corpus"]["scope"].get(key) for key in (
                "workspace_ref", "project_ref", "user_ref", "peer_ref", "session_ref",
            )}} for item in route["recall_corpora"]],
    })
    return cast(dict[str, dict[str, Any]], result["checkpoints"])
