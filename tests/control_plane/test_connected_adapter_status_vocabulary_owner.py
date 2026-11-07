"""Refs #4447: one definition for the connected-adapter status vocabulary.

`CONNECTED_ADAPTER_STATUSES` was defined three times with identical values — in both
status projections and in `loopx.status` — and `CONNECTED_DELIVERY_ADAPTER_STATUSES`
twice. Both sets are the admitted values for the same injected parameters of the
attention and lifecycle read models (`attention_routing` and `work_items.lifecycle`
take them as arguments), so every production caller was hand-copying one vocabulary
and a fourth caller had to invent a literal to call either function.

The `status` package now owns both sets and every other module imports them. These two
sets are *not* registered vocabularies in `loopx/semantics/vocabulary_v0.json`, so the
registry's `owner_exclusivity` policy is not what enforces the single owner here: the
registry's `value_shape` admits `^[a-z][a-z0-9_]*$` and these values carry hyphens. The
guarantee comes from this module's own tests, which is why the owner docstring says the
same thing. `loopx.status` keeps exporting both names because
`examples/control_plane/goal-attention-readmodel-smoke.py` reads them as
`status_module.CONNECTED_ADAPTER_STATUSES`.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from loopx import status
from loopx.control_plane.status import (
    adapter_status_vocabulary,
    goal_attention_projection,
    lifecycle_projection,
)

EXPECTED_CONNECTED_STATUSES = {"connected", "connected-read-only", "pre-tick-runnable"}
EXPECTED_CONNECTED_DELIVERY_STATUSES = {"connected-delivery"}
DEFINING_SOURCE_MARKER = "pre-tick-runnable"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPOSITORY_ROOT / "loopx" / "semantics" / "vocabulary_v0.json"
# The registry's own `value_shape`, restated here so this test pins the same rule the
# registry enforces rather than a second opinion about it.
REGISTRY_VALUE_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def test_vocabulary_is_unchanged() -> None:
    """Merging the fork must not change the admitted values."""
    assert set(adapter_status_vocabulary.CONNECTED_ADAPTER_STATUSES) == (
        EXPECTED_CONNECTED_STATUSES
    )
    assert set(adapter_status_vocabulary.CONNECTED_DELIVERY_ADAPTER_STATUSES) == (
        EXPECTED_CONNECTED_DELIVERY_STATUSES
    )


def test_every_importer_shares_the_single_definition() -> None:
    """Identity, not equality: a re-typed literal would fork the vocabulary again."""
    for module in (
        status,
        goal_attention_projection,
        lifecycle_projection,
    ):
        assert module.CONNECTED_ADAPTER_STATUSES is (
            adapter_status_vocabulary.CONNECTED_ADAPTER_STATUSES
        ), module.__name__
    for module in (status, goal_attention_projection):
        assert module.CONNECTED_DELIVERY_ADAPTER_STATUSES is (
            adapter_status_vocabulary.CONNECTED_DELIVERY_ADAPTER_STATUSES
        ), module.__name__


def test_the_owner_is_the_only_definition_left() -> None:
    """No active module restates the values; only the owner declares them."""
    for module in (
        status,
        goal_attention_projection,
        lifecycle_projection,
    ):
        assert DEFINING_SOURCE_MARKER not in inspect.getsource(module), module.__name__


def test_the_owner_is_not_a_registered_vocabulary_owner() -> None:
    """The owner docstring claims these sets are not in the registry; pin that claim.

    The docstring was corrected to stop claiming the registry's `owner_exclusivity`
    policy enforces the single owner here. A claim like that decays silently, so the
    claim itself is checked: no registered vocabulary lists these values, no
    registered owner points at this module, and the hyphenated values would fail the
    registry's `value_shape` anyway.
    """
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    registered_values = {
        value
        for vocabulary in registry["vocabularies"].values()
        for value in vocabulary["values"]
    }
    owned_values = set(adapter_status_vocabulary.CONNECTED_ADAPTER_STATUSES) | set(
        adapter_status_vocabulary.CONNECTED_DELIVERY_ADAPTER_STATUSES
    )
    assert not owned_values & registered_values, sorted(
        owned_values & registered_values
    )

    registered_owners = [
        owner
        for vocabulary in registry["vocabularies"].values()
        for owner in vocabulary["owners"].values()
        if owner
    ]
    assert not [
        owner for owner in registered_owners if "adapter_status_vocabulary" in owner
    ], registered_owners

    hyphenated = {value for value in owned_values if "-" in value}
    assert hyphenated == {
        "connected-read-only",
        "pre-tick-runnable",
        "connected-delivery",
    }
    assert all(
        REGISTRY_VALUE_SHAPE.match(value) is None for value in hyphenated
    ), hyphenated
