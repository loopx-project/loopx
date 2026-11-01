"""Shared compact identifier shapes have one compiled owner."""

from __future__ import annotations

from pathlib import Path

import pytest

from loopx import chat_action_normalization, chat_actions, chat_action_store, domain_state
from loopx.capabilities.benchmark_toolkit import integrity
from loopx.capabilities.context_providers import service_ownership
from loopx.capabilities.decision_context import packets, profile, sources
from loopx.capabilities.issue_fix import (
    repository_context,
    repository_memory,
    repository_memory_provider,
    reviewer_recommendation,
)
from loopx.capabilities.material_lifecycle import _validation
from loopx.capabilities.reliability_diagnostics import envelope
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
from loopx.control_plane.runtime import public_safety
from loopx.domain_packs import ml_experiment
from loopx.control_plane.goals import (
    botmux_runtime,
    deletion_service,
    goal_ref_validation,
)
from loopx.extensions import presentation
from loopx.extensions.openviking_semantic_preference import history_export
from loopx.public_safe_text import (
    COMPACT_LABEL_PATTERN,
    COMPACT_TOKEN_PATTERN,
    IDENTITY_TOKEN_PATTERN,
    LETTER_LED_COMPACT_TOKEN_PATTERN,
    OPAQUE_ID_PATTERN as PUBLIC_OPAQUE_ID,
    MODULE_QUALIFIED_SURFACE_PATTERN,
    PUBLIC_SAFE_REFERENCE_PATTERN,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/public_safe_text.py"
REFERENCE_SHAPE = "^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$"
COMPACT_SHAPE = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
LETTER_LED_SHAPE = "^[A-Za-z][A-Za-z0-9_.:-]{0,79}$"
LABEL_SHAPE = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
IDENTITY_SHAPE = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$"
SURFACE_SHAPE = "^[a-z][a-z0-9_-]*(?:\\.[a-z][a-z0-9_-]*)+$"
OPAQUE_SHAPE = "^[A-Za-z0-9._:-]{1,200}$"

# Sites still allowed to state each body locally, per file and count. Both are
# empty: every converted module imports the owner instead. A new restatement, or
# an entry whose site has already been converted, fails the census.
OPEN_OPAQUE_SITES: dict[str, int] = {}
OPEN_COMPACT_SITES: dict[str, int] = {}
OPEN_LETTER_LED_SITES: dict[str, int] = {}
OPEN_LABEL_SITES: dict[str, int] = {}
OPEN_IDENTITY_SITES: dict[str, int] = {}


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
        "loopx/capabilities/benchmark_toolkit/integrity.py",
        "loopx/capabilities/decision_context/packets.py",
        "loopx/capabilities/decision_context/profile.py",
        "loopx/capabilities/decision_context/sources.py",
        "loopx/capabilities/material_lifecycle/_validation.py",
        "loopx/extensions/presentation.py",
    ),
    LABEL_SHAPE: (
        "loopx/capabilities/context_providers/service_ownership.py",
        "loopx/capabilities/issue_fix/repository_context.py",
        "loopx/capabilities/issue_fix/repository_memory.py",
        "loopx/capabilities/issue_fix/repository_memory_provider.py",
        "loopx/capabilities/issue_fix/reviewer_recommendation.py",
    ),
    IDENTITY_SHAPE: (
        "loopx/capabilities/reliability_diagnostics/envelope.py",
        "loopx/control_plane/runtime/public_safety.py",
        "loopx/extensions/openviking_semantic_preference/history_export.py",
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


def test_the_compact_token_body_is_compiled_only_by_the_owner_and_declared_sites() -> None:
    found: dict[str, int] = {}
    for path in sorted(REPOSITORY_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = path.read_text(encoding="utf-8").count(COMPACT_SHAPE)
        if count:
            found[relative] = count
    assert found == {OWNER_MODULE: 1, **OPEN_COMPACT_SITES}


def test_the_letter_led_token_body_is_compiled_only_by_the_owner_and_declared_sites() -> None:
    found: dict[str, int] = {}
    for path in sorted(REPOSITORY_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = path.read_text(encoding="utf-8").count(LETTER_LED_SHAPE)
        if count:
            found[relative] = count
    assert found == {OWNER_MODULE: 1, **OPEN_LETTER_LED_SITES}


def test_domain_state_and_ml_experiment_share_the_letter_led_token_shape() -> None:
    assert domain_state._DOMAIN_STATE_TOKEN_RE is LETTER_LED_COMPACT_TOKEN_PATTERN
    assert ml_experiment._TOKEN_RE is LETTER_LED_COMPACT_TOKEN_PATTERN


def test_the_label_body_is_compiled_only_by_the_owner_and_declared_sites() -> None:
    found: dict[str, int] = {}
    for path in sorted(REPOSITORY_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = path.read_text(encoding="utf-8").count(LABEL_SHAPE)
        if count:
            found[relative] = count
    assert found == {OWNER_MODULE: 1, **OPEN_LABEL_SITES}


def test_the_identity_body_is_compiled_only_by_the_owner_and_declared_sites() -> None:
    found: dict[str, int] = {}
    for path in sorted(REPOSITORY_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        count = path.read_text(encoding="utf-8").count(IDENTITY_SHAPE)
        if count:
            found[relative] = count
    assert found == {OWNER_MODULE: 1, **OPEN_IDENTITY_SITES}


def test_labels_and_identities_import_the_owners_object() -> None:
    assert service_ownership._LABEL is COMPACT_LABEL_PATTERN
    assert repository_context._SOURCE_ID_PATTERN is COMPACT_LABEL_PATTERN
    assert repository_memory._LABEL_PATTERN is COMPACT_LABEL_PATTERN
    assert repository_memory_provider._LABEL is COMPACT_LABEL_PATTERN
    assert reviewer_recommendation.REVIEWER_SOURCE_ID_PATTERN is COMPACT_LABEL_PATTERN
    assert envelope.IDENTITY_TOKEN_PATTERN is IDENTITY_TOKEN_PATTERN
    assert public_safety.LOOPX_COMMAND_RECORD_GOAL_ID_PATTERN is IDENTITY_TOKEN_PATTERN
    assert history_export._PUBLIC_GOAL_ID_RE is IDENTITY_TOKEN_PATTERN


def test_the_label_and_identity_bounds_stay_one_character_apart() -> None:
    # These two shapes share their grammar and differ only in the bound each
    # consumer already enforced, so a fold that merges them would silently widen
    # one side. The pair of values that separates them is pinned here.
    longest_label = "a" + "b" * 119
    one_character_longer = "a" + "b" * 120
    assert COMPACT_LABEL_PATTERN.fullmatch(longest_label)
    assert COMPACT_LABEL_PATTERN.fullmatch(one_character_longer) is None
    assert IDENTITY_TOKEN_PATTERN.fullmatch(one_character_longer)


def test_each_contract_holds_the_owners_object_and_reports_its_own_field() -> None:
    assert chat_actions._OPAQUE_ID is PUBLIC_OPAQUE_ID
    assert chat_action_store._OPAQUE_ID is PUBLIC_OPAQUE_ID
    assert deletion_service._OPAQUE_ID is PUBLIC_OPAQUE_ID
    assert goal_ref_validation.GOAL_ID is PUBLIC_OPAQUE_ID
    assert botmux_runtime._SAFE_TOKEN is PUBLIC_OPAQUE_ID
    assert presentation._ID_RE is COMPACT_TOKEN_PATTERN
    assert integrity._PUBLIC_EVIDENCE_ID_PATTERN is COMPACT_TOKEN_PATTERN
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
