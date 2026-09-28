"""Refs #4447: one definition for the periodic-report sink and source vocabularies.

`_SINK_STATUSES` was defined three times and `_SINK_ROLES` three times inside the
`periodic_report` package — in `core`, `adapters` and `bindings` — while
`_SOURCE_STATUSES` was defined twice. Every copy validated the same wire field
against its own literal list, so a value added for one entry point would be rejected
by the other two, and each module's check looked independently authoritative.

`core` already owns the four sets beside these (`_ARTIFACT_STATUSES` and the schema
names) and both other modules already import its private validators, so `core` is
the owner and the consumers import. The admitted values did not change.
"""

from __future__ import annotations

import ast
import inspect

from loopx.capabilities.periodic_report import adapters, bindings, core

EXPECTED_SOURCE_STATUSES = {"complete", "partial", "failed", "unknown"}
EXPECTED_SINK_STATUSES = {"pending", "sent", "failed", "skipped", "unknown"}
EXPECTED_SINK_ROLES = {"archive", "delivery"}
SHARED_NAMES = frozenset({"_SOURCE_STATUSES", "_SINK_STATUSES", "_SINK_ROLES"})


def test_vocabulary_is_unchanged() -> None:
    """Merging the copies must not change the admitted values."""
    assert set(core._SOURCE_STATUSES) == EXPECTED_SOURCE_STATUSES
    assert set(core._SINK_STATUSES) == EXPECTED_SINK_STATUSES
    assert set(core._SINK_ROLES) == EXPECTED_SINK_ROLES


def test_every_consumer_shares_the_single_definition() -> None:
    """Identity, not equality: a re-typed literal would fork the vocabulary again."""
    for module in (adapters, bindings):
        assert module._SINK_STATUSES is core._SINK_STATUSES, module.__name__
        assert module._SINK_ROLES is core._SINK_ROLES, module.__name__
    # `bindings` never read a source status, so only the module that does has to
    # name the shared set; asserting it on both would pass on a vacuous hasattr.
    assert adapters._SOURCE_STATUSES is core._SOURCE_STATUSES
    assert not hasattr(bindings, "_SOURCE_STATUSES")


def test_the_consumers_define_no_second_copy() -> None:
    """A module-level re-assignment of one of these names would fork it again.

    Matching the value strings is not usable here: the consumers legitimately assign
    a single status such as ``status = "skipped"`` while building a result. Only a
    module-level binding of the vocabulary name itself re-opens the fork, so that is
    what this reads.
    """
    restated = {
        module.__name__: _module_level_names(module)
        for module in (adapters, bindings)
        if _module_level_names(module) & SHARED_NAMES
    }

    assert restated == {}


def _module_level_names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
