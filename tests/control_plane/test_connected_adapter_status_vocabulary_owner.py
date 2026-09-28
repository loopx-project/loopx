"""Refs #4447: one definition for the connected-adapter status vocabulary.

`CONNECTED_ADAPTER_STATUSES` was defined three times with identical values — in both
status projections and in `loopx.status` — and `CONNECTED_DELIVERY_ADAPTER_STATUSES`
twice. Both sets are the admitted values for the same injected parameters of the
attention and lifecycle read models (`attention_routing` and `work_items.lifecycle`
take them as arguments), so every production caller was hand-copying one vocabulary
and a fourth caller had to invent a literal to call either function.

The `status` package now owns both sets and every other module imports them, which is
what `owner_exclusivity` in `loopx/semantics/vocabulary_v0.json` requires of a
registered symbol. `loopx.status` keeps exporting both names because
`examples/control_plane/goal-attention-readmodel-smoke.py` reads them as
`status_module.CONNECTED_ADAPTER_STATUSES`.
"""

from __future__ import annotations

import inspect

from loopx import status
from loopx.control_plane.status import (
    adapter_status_vocabulary,
    goal_attention_projection,
    lifecycle_projection,
)

EXPECTED_CONNECTED_STATUSES = {"connected", "connected-read-only", "pre-tick-runnable"}
EXPECTED_CONNECTED_DELIVERY_STATUSES = {"connected-delivery"}
DEFINING_SOURCE_MARKER = "pre-tick-runnable"


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
