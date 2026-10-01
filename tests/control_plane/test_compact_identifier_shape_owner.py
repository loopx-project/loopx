"""Shared compact identifier shapes have one compiled owner."""

from __future__ import annotations

from pathlib import Path

from loopx.capabilities.decision_context import packets, profile, sources
from loopx.capabilities.material_lifecycle import _validation
from loopx.capabilities.reward_memory import (
    application,
    candidate_review,
    dogfood,
    experience_quality,
    ingestion,
    memory_utility,
    registry,
)
from loopx.capabilities.semantic_preference import contract
from loopx.public_safe_text import (
    COMPACT_TOKEN_PATTERN,
    MODULE_QUALIFIED_SURFACE_PATTERN,
    PUBLIC_SAFE_REFERENCE_PATTERN,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/public_safe_text.py"
REFERENCE_SHAPE = "^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$"
COMPACT_SHAPE = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
SURFACE_SHAPE = "^[a-z][a-z0-9_-]*(?:\\.[a-z][a-z0-9_-]*)+$"


SHAPE_CONSUMERS = {
    REFERENCE_SHAPE: (
        "loopx/capabilities/reward_memory/application.py",
        "loopx/capabilities/reward_memory/candidate_review.py",
        "loopx/capabilities/reward_memory/dogfood.py",
        "loopx/capabilities/reward_memory/experience_quality.py",
        "loopx/capabilities/reward_memory/ingestion.py",
        "loopx/capabilities/reward_memory/memory_utility.py",
        "loopx/capabilities/reward_memory/registry.py",
        "loopx/capabilities/semantic_preference/contract.py",
    ),
    COMPACT_SHAPE: (
        "loopx/capabilities/decision_context/packets.py",
        "loopx/capabilities/decision_context/profile.py",
        "loopx/capabilities/decision_context/sources.py",
        "loopx/capabilities/material_lifecycle/_validation.py",
    ),
    SURFACE_SHAPE: (
        "loopx/capabilities/reward_memory/application.py",
        "loopx/capabilities/reward_memory/candidate_review.py",
        "loopx/capabilities/reward_memory/ingestion.py",
        "loopx/capabilities/reward_memory/registry.py",
        "loopx/capabilities/semantic_preference/contract.py",
    ),
}


def test_compact_identifier_shapes_are_declared_once() -> None:
    for shape, consumers in SHAPE_CONSUMERS.items():
        assert shape in (REPOSITORY_ROOT / OWNER_MODULE).read_text(encoding="utf-8")
        for consumer in consumers:
            assert shape not in (REPOSITORY_ROOT / consumer).read_text(encoding="utf-8")


def test_reward_memory_and_semantic_preference_share_reference_and_surface_shapes() -> None:
    for module in (application, candidate_review, ingestion, registry, contract):
        assert module.TOKEN_RE is PUBLIC_SAFE_REFERENCE_PATTERN
        assert module.SURFACE_RE is MODULE_QUALIFIED_SURFACE_PATTERN
    assert dogfood.TOKEN_RE is PUBLIC_SAFE_REFERENCE_PATTERN
    assert experience_quality.OPAQUE_REF_RE is PUBLIC_SAFE_REFERENCE_PATTERN
    assert memory_utility._EXISTING_TOKEN_RE is PUBLIC_SAFE_REFERENCE_PATTERN


def test_decision_and_material_contracts_share_the_compact_token_shape() -> None:
    for module in (packets, profile, sources, _validation):
        assert module._TOKEN_RE is COMPACT_TOKEN_PATTERN
