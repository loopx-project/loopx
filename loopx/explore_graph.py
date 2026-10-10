"""Compatibility adapters for the typed Explore Harness mode owner."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRejected


@lru_cache(maxsize=4)
def _resolve(evidence: bool, planning: bool) -> dict[str, Any]:
    return effect_runtime_result(
        "explore.configuration.resolve",
        {
            "evidence_enabled": evidence,
            "planning_enabled": planning,
        },
    )


def explore_configuration(graph: Any, harness: Any = None) -> dict[str, Any]:
    graph = graph if isinstance(graph, Mapping) else {}
    harness = harness if isinstance(harness, Mapping) else {}
    return dict(_resolve(graph.get("enabled") is True, harness.get("enabled") is True))


def compact_explore_graph_policy(value: Any, harness: Any = None) -> dict[str, bool]:
    """Graph is the evidence layer, including for legacy planning-only policies."""
    return {"enabled": explore_configuration(value, harness)["evidence_enabled"]}


def plan_explore_configuration(
    current: Mapping[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        return effect_runtime_result(
            "explore.configuration.plan",
            {"current": dict(current), "changes": dict(changes)},
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
