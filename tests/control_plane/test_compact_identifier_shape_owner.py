"""Shared compact identifier shapes have one compiled owner."""

from __future__ import annotations

from pathlib import Path

import pytest

from loopx import chat_action_normalization, chat_actions, chat_action_store
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
from loopx.control_plane.goals import botmux_runtime, goal_ref_validation
from loopx.public_safe_text import (
    COMPACT_TOKEN_PATTERN,
    OPAQUE_ID_PATTERN as PUBLIC_OPAQUE_ID,
    MODULE_QUALIFIED_SURFACE_PATTERN,
    PUBLIC_SAFE_REFERENCE_PATTERN,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/public_safe_text.py"
REFERENCE_SHAPE = "^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$"
COMPACT_SHAPE = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
SURFACE_SHAPE = "^[a-z][a-z0-9_-]*(?:\\.[a-z][a-z0-9_-]*)+$"
OPAQUE_SHAPE = "^[A-Za-z0-9._:-]{1,200}$"

# A file that still compiles this body itself, with the number of constructions
# allowed there. The goal-deletion service is being restructured in #5854, so this
# slice declares that site instead of racing it; converting it deletes the entry,
# and leaving an entry whose site is already converted fails too.
OPEN_OPAQUE_SITES = {"loopx/control_plane/goals/deletion_service.py": 1}


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
    OPAQUE_SHAPE: (
        "loopx/chat_actions.py",
        "loopx/chat_action_store.py",
        "loopx/control_plane/goals/goal_ref_validation.py",
        "loopx/control_plane/goals/botmux_runtime.py",
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


def test_the_opaque_id_body_is_compiled_only_by_the_owner_and_declared_sites() -> None:
    # A census, not a roster: any module that starts compiling this body again has
    # to appear here, and a declared site that has been converted fails too.
    found: dict[str, int] = {}
    for path in sorted(REPOSITORY_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = path.read_text(encoding="utf-8").count(OPAQUE_SHAPE)
        if count:
            found[relative] = count
    assert found == {OWNER_MODULE: 1, **OPEN_OPAQUE_SITES}


def test_each_contract_holds_the_owners_object_and_reports_its_own_field() -> None:
    assert chat_actions._OPAQUE_ID is PUBLIC_OPAQUE_ID
    assert chat_action_store._OPAQUE_ID is PUBLIC_OPAQUE_ID
    assert goal_ref_validation.GOAL_ID is PUBLIC_OPAQUE_ID
    assert botmux_runtime._SAFE_TOKEN is PUBLIC_OPAQUE_ID
    for rejected in ("x" * 201, "has space"):
        with pytest.raises(ValueError, match="must be a compact opaque id"):
            chat_action_store._opaque_id(rejected, field="action_id")
        with pytest.raises(ValueError, match="must be a compact opaque id"):
            chat_actions._opaque(rejected, field="action_id")


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("a", True),
        ("goal-1", True),
        ("todo_2.3:4", True),
        ("x" * 200, True),
        ("x" * 201, False),
        ("", False),
        ("/Users/me/goal", False),
        ("goal id", False),
        ("goal\nid", False),
        ("goal\u2028id", False),
    ],
)
def test_the_opaque_id_contract_is_bounded_safe_characters_only(
    value: str, accepted: bool
) -> None:
    assert bool(PUBLIC_OPAQUE_ID.fullmatch(value)) is accepted, value


def test_a_principal_reference_is_a_different_question_and_stays_separate() -> None:
    # ``^[a-z][a-z0-9._-]{0,30}:<opaque>$`` embeds the same body behind a required
    # family prefix. Merging them would either widen the opaque id to accept a
    # prefix it never allowed or narrow a principal to a bare id.
    principal = chat_action_normalization._AUTHORITY_PRINCIPAL
    assert principal.fullmatch("codex:thread-1")
    assert not principal.fullmatch("thread-1")
    assert PUBLIC_OPAQUE_ID.fullmatch("thread-1")
    assert OPAQUE_SHAPE not in (
        REPOSITORY_ROOT / "loopx/chat_action_normalization.py"
    ).read_text(encoding="utf-8")
