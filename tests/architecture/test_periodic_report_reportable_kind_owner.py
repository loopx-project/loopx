"""Guard one owner for the periodic-report reportable trigger kinds (#4447).

``_REPORTABLE_TRIGGER_KINDS`` in ``periodic_report/core.py`` decides which
trigger kinds may produce a periodic report. ``triggers.py`` restated the same
eight kinds as ``_REPORTABLE_KINDS`` while *already importing* eleven names from
``core`` -- the strongest form of this repository's duplicate-knowledge case: a
module restating a constant whose owner it already imports from. It now borrows
the owner object and the restated name is retired.

The neighbouring ``_NON_REPORTABLE_KINDS`` (five different kinds) is a separate
vocabulary and is deliberately left alone. The targeted literal checks key on
the **folded value**, so that neighbour is never swept: its members differ. The
by-name semantic inventory cannot see this pair either, because the two sides
use different names and both are module-private.

Hard checks are pinned to the sites that share one semantic slot, scope and
contract: the owner (``core``) and the validation entry
(``triggers._normalize_policy``), with behavioural parity through that entry.
A profile's ``enabled_kinds`` is a different slot -- a per-profile choice of
which kinds *that profile* enables -- so two profiles may legitimately select
the same eight kinds with different intervals; those selections are verified
through the real decision entry, not by counting literals. Whole-package
same-value matching stays in the existing advisory lane (the semantic
inventory's merge candidates): the convergence RFC makes merge candidates
advisory because an equal value set is not proof of one concept, and
``reuse_existing`` requires the same slot, compatible scope and an equivalent
contract.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from loopx.capabilities.periodic_report import core, triggers

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/capabilities/periodic_report/core.py"
CONSUMER_PATH = "loopx/capabilities/periodic_report/triggers.py"

KINDS = frozenset(
    {
        "bounded_segment_milestone",
        "cadence_due",
        "manual",
        "material_blocker",
        "material_decision",
        "material_recovery",
        "primary_goal_outcome",
        "vision_closed",
    }
)
NON_REPORTABLE = frozenset(
    {
        "monitor_unchanged",
        "state_refreshed",
        "surface_only",
        "todo_completed",
        "vision_checkpoint",
    }
)


def _members(node: ast.expr) -> frozenset[str] | None:
    """Fold a set / frozenset / list / tuple of plain string literals to members."""

    if isinstance(node, ast.Call):
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname in {"frozenset", "set"} and len(node.args) == 1:
            return _members(node.args[0])
        return None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(values) == len(node.elts) and values:
            return frozenset(values)
    return None


def _kind_literal_sites(source: str) -> list[int]:
    """Every collection literal folding to KINDS, counted once (call+set is one)."""

    tree = ast.parse(source)
    wrapped = {
        id(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
        in {"frozenset", "set"}
        and len(node.args) == 1
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if id(node) not in wrapped and _members(node) == KINDS
    ]


def test_the_owner_states_the_recorded_members_once() -> None:
    assert frozenset(core._REPORTABLE_TRIGGER_KINDS) == KINDS
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_kind_literal_sites(source)) == 1


def test_the_consumer_borrows_the_owner_object() -> None:
    assert triggers._REPORTABLE_TRIGGER_KINDS is core._REPORTABLE_TRIGGER_KINDS


def test_the_consumer_retired_its_restated_name() -> None:
    assert not hasattr(triggers, "_REPORTABLE_KINDS")
    source = (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    assert _kind_literal_sites(source) == []
    assert "_REPORTABLE_TRIGGER_KINDS" in source


def test_the_non_reportable_neighbour_is_a_different_set() -> None:
    # The five non-reportable kinds stay where they are; the value census must
    # not sweep them, and they must not overlap the reportable set.
    assert frozenset(triggers._NON_REPORTABLE_KINDS) == NON_REPORTABLE
    assert NON_REPORTABLE.isdisjoint(KINDS)
    source = (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    assert _kind_literal_sites(source) == []


def test_the_preset_enabled_kinds_is_a_configuration_not_the_vocabulary() -> None:
    from loopx.capabilities.periodic_report import presets

    # A preset's ``enabled_kinds`` is a configuration choice -- which kinds
    # *this preset* enables -- not the vocabulary of which kinds are
    # reportable. Borrowing the owner here would auto-enable any future
    # reportable kind in the weekly preset, a domain decision rather than a
    # deduplication. Two presets, two different selections, so the list is a
    # per-preset policy; it is verified behaviourally below, not pinned by a
    # literal-site count that a second legitimate profile would break.
    weekly = presets._WEEKLY_PROGRESS_PROFILE["trigger_policy"]["enabled_kinds"]
    assert frozenset(weekly) == KINDS
    portfolio = presets.resolve_periodic_report_profile_preset("portfolio-progress")
    assert portfolio["trigger_policy"]["enabled_kinds"] == ["cadence_due"]


# --- profile selections are configuration, verified through the real decision entry ---


def _decide(
    profile_id: str, policy: dict[str, Any], kind: str, facts: dict[str, Any]
) -> dict[str, Any]:
    return triggers.build_periodic_report_trigger_decision(
        {
            "schema_version": triggers.TRIGGER_REQUEST_SCHEMA,
            "evaluated_at": "2026-11-12T00:00:00Z",
            "profile": {"profile_id": profile_id, "profile_version": "v1"},
            "trigger_policy": policy,
            "candidates": [
                {
                    "trigger_kind": kind,
                    "observed_at": "2026-11-12T00:00:00Z",
                    "source_ref": f"{profile_id}-{kind}",
                    "evidence_digest": f"{profile_id}-{kind}-evidence",
                    "facts": facts,
                }
            ],
        }
    )


def test_each_profile_selection_drives_the_real_decision() -> None:
    from loopx.capabilities.periodic_report import presets

    weekly = presets.resolve_periodic_report_profile_preset("weekly-progress")
    portfolio = presets.resolve_periodic_report_profile_preset("portfolio-progress")
    # The same authorized manual candidate: weekly enables it, portfolio does not.
    selected = _decide(
        "weekly_progress", weekly["trigger_policy"], "manual", {"authorized": True}
    )
    assert selected["selected_trigger_kind"] == "manual"
    suppressed = _decide(
        "portfolio_progress",
        portfolio["trigger_policy"],
        "manual",
        {"authorized": True},
    )
    assert suppressed["selected_trigger_kind"] is None
    assert suppressed["suppressed_triggers"][0]["reason"] == "disabled_by_profile"


def test_a_second_profile_may_select_the_same_kinds_with_its_own_interval() -> None:
    # Two profiles may legitimately select the same eight kinds with different
    # intervals: the selection belongs to each profile, so the real decision
    # entry must accept this one rather than treat it as a duplicate vocabulary.
    policy = {"enabled_kinds": sorted(KINDS), "minimum_interval_seconds": 21_600}
    decision = _decide("six_hour_digest", policy, "cadence_due", {"due": True})
    assert decision["ok"] is True
    assert decision["selected_trigger_kind"] == "cadence_due"
    assert decision["trigger_policy"]["enabled_kinds"] == sorted(KINDS)
    assert decision["trigger_policy"]["minimum_interval_seconds"] == 21_600


# --- behavioural parity through the consumer's own policy normalizer ----------------------


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_reportable_kind_is_accepted(kind: str) -> None:
    normalized = triggers._normalize_policy({"enabled_kinds": [kind]})
    assert normalized["enabled_kinds"] == [kind]


@pytest.mark.parametrize("kind", sorted(NON_REPORTABLE | {"not_a_kind"}))
def test_a_non_reportable_kind_is_rejected(kind: str) -> None:
    with pytest.raises(ValueError, match="is not reportable"):
        triggers._normalize_policy({"enabled_kinds": [kind]})


def test_the_default_enabled_kinds_is_the_owner_set() -> None:
    # With no explicit enabled_kinds the policy defaults to the full owner set,
    # so the consumer's default is pinned to the borrowed object, not a copy.
    normalized = triggers._normalize_policy({})
    assert normalized["enabled_kinds"] == sorted(KINDS)


# --- the folded-value scan bites every restatement form and leaves the others -----------

_MEMBER_TEXT = ", ".join(repr(kind) for kind in sorted(KINDS))
_NON_MEMBER_TEXT = ", ".join(repr(kind) for kind in sorted(NON_REPORTABLE))

POISON_CORPUS = (
    ("bare set restatement", f"X = {{{_MEMBER_TEXT}}}\n", 1),
    ("frozenset call", f"X = frozenset({{{_MEMBER_TEXT}}})\n", 1),
    ("set call over a list", f"X = set([{_MEMBER_TEXT}])\n", 1),
    ("list literal", f"X = [{_MEMBER_TEXT}]\n", 1),
    ("tuple literal", f"X = ({_MEMBER_TEXT})\n", 1),
    ("renamed binding is still caught", f"_ANY = {{{_MEMBER_TEXT}}}\n", 1),
    ("nested inside a function", f"def f():\n    return {{{_MEMBER_TEXT}}}\n", 1),
    ("importing the owner is not a restatement", "from .core import _REPORTABLE_TRIGGER_KINDS\n", 0),
    ("alias rebind is not a restatement", "_REPORTABLE_KINDS = _REPORTABLE_TRIGGER_KINDS\n", 0),
    ("the non-reportable neighbour is a different set", f"_NON_REPORTABLE_KINDS = {{{_NON_MEMBER_TEXT}}}\n", 0),
    ("a single kind is not the whole set", 'X = {"manual"}\n', 0),
)


@pytest.mark.parametrize(
    "label,source,expected", POISON_CORPUS, ids=[case[0] for case in POISON_CORPUS]
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_kind_literal_sites(source)) == expected, label
