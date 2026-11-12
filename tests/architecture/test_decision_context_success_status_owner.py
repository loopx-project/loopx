"""Guard one owner for the decision-context successful-read status set (#4447).

``_SUCCESS_STATUSES`` in ``freshness.py`` decides which scan/attempt statuses
count as a successful read (``completed`` / ``no_change``). The same two-member
set was restated across the package: ``capture.py`` bound it under a different
name (``_READ_SUCCESS_STATUSES``) *and* inlined it once, while ``assembler.py``
inlined it four times with no name at all -- seven sites folding to one value.
Both consumers *already import* from ``freshness``, so borrowing the owner object
adds no new import edge and cannot introduce a cycle. Every site now references
the single owner and the restated name is retired.

The by-name semantic inventory cannot see this: the owner and the retired
capture name are private ``_``-bindings (the inventory only catalogues
``UPPER_NAME`` carriers), they differ by name, and four of the seven sites had no
name to key on. This guard keys on the **folded value** instead, so a renamed or
anonymous inline restatement is caught, while the genuinely different neighbours
-- the four-member ``_SCAN_STATUSES`` vocabulary and the ordered four-status
report tuple -- are never swept.

Two layers: a whole-tree folded-value census (only the owner states the set), and
behavioural parity through the owner's own ``source_freshness_row`` gate, so a
migration that silently changed which statuses count as success fails here.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone

import pytest

from loopx.capabilities.decision_context import (
    assembler as assembler_mod,
    capture as capture_mod,
    freshness as owner,
    sources as sources_mod,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/capabilities/decision_context/freshness.py"
CAPTURE_PATH = "loopx/capabilities/decision_context/capture.py"
ASSEMBLER_PATH = "loopx/capabilities/decision_context/assembler.py"

SUCCESS = frozenset({"completed", "no_change"})
FAILURE = frozenset({"provider_failed", "failed", "unavailable"})
SCAN_VOCABULARY = frozenset({"completed", "failed", "no_change", "unavailable"})


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


def _status_literal_sites(source: str) -> list[int]:
    """Every collection literal in the source folding to SUCCESS, counted once.

    ``frozenset({...})`` is walked as both the call and its inner set; the inner
    node is skipped so one restatement is one site, not two.
    """

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
        if id(node) not in wrapped and _members(node) == SUCCESS
    ]


@pytest.fixture(scope="module")
def tree_offenders() -> dict[str, list[int]]:
    offenders: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER_PATH:
            continue
        sites = _status_literal_sites(path.read_text(encoding="utf-8"))
        if sites:
            offenders[relative] = sites
    return offenders


def test_only_the_owner_states_the_success_set(tree_offenders) -> None:
    assert tree_offenders == {}, (
        f"a restatement of the successful-read statuses remains: {tree_offenders}"
    )


def test_the_owner_states_the_recorded_members_once() -> None:
    assert frozenset(owner._SUCCESS_STATUSES) == SUCCESS
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_status_literal_sites(source)) == 1


def test_capture_borrows_the_owner_object() -> None:
    assert capture_mod._SUCCESS_STATUSES is owner._SUCCESS_STATUSES


def test_assembler_borrows_the_owner_object() -> None:
    assert assembler_mod._SUCCESS_STATUSES is owner._SUCCESS_STATUSES


def test_capture_retired_its_restated_name() -> None:
    assert not hasattr(capture_mod, "_READ_SUCCESS_STATUSES")
    source = (REPO_ROOT / CAPTURE_PATH).read_text(encoding="utf-8")
    assert _status_literal_sites(source) == []
    assert "_SUCCESS_STATUSES" in source


def test_assembler_has_no_inline_restatement() -> None:
    source = (REPO_ROOT / ASSEMBLER_PATH).read_text(encoding="utf-8")
    assert _status_literal_sites(source) == []
    assert "_SUCCESS_STATUSES" in source


def test_the_failure_neighbour_is_a_different_set() -> None:
    # capture keeps a distinct failure set; single-sourcing success must not
    # collapse or delete it.
    assert frozenset(capture_mod._READ_FAILURE_STATUSES) == FAILURE
    assert SUCCESS.isdisjoint(FAILURE)


def test_the_scan_vocabulary_is_a_different_set() -> None:
    # The full status vocabulary is a superset, not the success set; it stays
    # its own owner in sources.py and is deliberately left untouched.
    assert frozenset(sources_mod._SCAN_STATUSES) == SCAN_VOCABULARY
    assert SUCCESS < SCAN_VOCABULARY
    assert SUCCESS != SCAN_VOCABULARY


# --- behavioural parity through the owner's own freshness gate --------------------------

OBSERVED_AT = datetime(2026, 7, 25, 17, 30, tzinfo=timezone.utc)


def _spec() -> sources_mod.DecisionSourceSpec:
    return sources_mod.DecisionSourceSpec(
        source_id="source:owner",
        provider_id="source-provider",
        source_kind="person",
        priority="p0",
        evidence_level="s2",
        objectives=("adoption",),
        scan_mode="incremental",
        exact_read_policy="material_change",
        freshness_seconds=3600,
        private_locator="private-owner-locator",
    )


@pytest.mark.parametrize("status", sorted(SUCCESS))
def test_a_successful_attempt_does_not_alert(status: str) -> None:
    row = owner.source_freshness_row(
        source=_spec(),
        observed_at=OBSERVED_AT,
        last_read_at=OBSERVED_AT.isoformat(),
        last_attempt_status=status,
        failure_streak=0,
    )
    assert row["status"] == "fresh"
    assert row["alert"] is False
    assert row["alert_reasons"] == []


@pytest.mark.parametrize("status", sorted(capture_mod._READ_FAILURE_STATUSES))
def test_a_failed_attempt_alerts_with_the_status(status: str) -> None:
    # The read time is fresh, yet a non-success attempt still raises an alert:
    # this is exactly the owner gate the consumers now share by identity.
    row = owner.source_freshness_row(
        source=_spec(),
        observed_at=OBSERVED_AT,
        last_read_at=OBSERVED_AT.isoformat(),
        last_attempt_status=status,
        failure_streak=0,
    )
    assert row["status"] == "fresh"
    assert row["alert"] is True
    assert row["alert_reasons"] == [f"last_attempt_{status}"]


# --- the census bites every restatement form and leaves the others ----------------------

POISON_CORPUS = (
    ("bare set restatement", 'X = {"completed", "no_change"}\n', 1),
    ("frozenset call", 'X = frozenset({"completed", "no_change"})\n', 1),
    ("set call over a list", 'X = set(["completed", "no_change"])\n', 1),
    ("list literal", 'X = ["completed", "no_change"]\n', 1),
    ("tuple literal", 'X = ("completed", "no_change")\n', 1),
    ("reordered members fold the same", 'X = {"no_change", "completed"}\n', 1),
    ("renamed binding is still caught", '_ANY = {"completed", "no_change"}\n', 1),
    ("nested inside a function", 'def f():\n    return {"completed", "no_change"}\n', 1),
    ("importing the owner is not a restatement", "from .m import _SUCCESS_STATUSES\n", 0),
    ("alias rebind is not a restatement", "_READ_SUCCESS_STATUSES = _SUCCESS_STATUSES\n", 0),
    ("the full scan vocabulary is not this set", '_SCAN = {"completed", "failed", "no_change", "unavailable"}\n', 0),
    ("the ordered report tuple is not this set", 'X = ("completed", "no_change", "failed", "unavailable")\n', 0),
    ("a superset is not this set", 'X = {"completed", "no_change", "failed"}\n', 0),
    ("a single status is not this set", 'X = {"completed"}\n', 0),
)


@pytest.mark.parametrize(
    "label,source,expected", POISON_CORPUS, ids=[case[0] for case in POISON_CORPUS]
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_status_literal_sites(source)) == expected, label