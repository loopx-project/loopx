"""One owner for the safe path-segment alphabet."""

from __future__ import annotations

import inspect

from loopx import paths
from loopx.capabilities.agent_turn_recall import runtime as agent_turn_recall
from loopx.capabilities.reward_memory import outcome_lifecycle
from loopx.capabilities.steward_memory import runtime as steward_memory
from loopx.extensions.openviking_semantic_preference import history_export

CONSUMERS = (agent_turn_recall, outcome_lifecycle, steward_memory, history_export)


def test_every_consumer_borrows_the_owner_alphabet() -> None:
    owner = paths.PATH_SEGMENT_UNSAFE_RE
    for module in CONSUMERS:
        assert module.PATH_SEGMENT_UNSAFE_RE is owner, module.__name__


def test_consumers_keep_allowed_punctuation_and_replace_the_rest() -> None:
    value = "a.b_c-d/e f"
    readable = "a.b_c-d-e-f"
    assert agent_turn_recall._safe_token(value, fallback="fallback").startswith(f"{readable}-")
    assert outcome_lifecycle._identity_token(value, fallback="fallback").startswith(f"{readable}-")
    assert steward_memory._safe_event_filename(value) == f"{readable}.md"
    assert history_export._slug(value) == readable


def test_consumers_do_not_restate_the_alphabet() -> None:
    literal = r"[^A-Za-z0-9._-]+"
    for module in CONSUMERS:
        assert literal not in inspect.getsource(module), module.__name__
