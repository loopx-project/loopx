"""Guard one owner for the decision-context successful-read status set (#4447).

``_SUCCESS_STATUSES`` in ``freshness.py`` decides which scan/attempt statuses
count as a successful read (``completed`` / ``no_change``). The same two-member
set was restated across the package: ``capture.py`` bound it under a different
name (``_READ_SUCCESS_STATUSES``) *and* inlined it once, while ``assembler.py``
inlined it four times with no name at all -- seven sites folding to one value.
Both consumers *already import* from ``freshness``, so borrowing the owner object
adds no new import edge and cannot introduce a cycle. Every site now references
the single owner and the restated name is retired.

Blocking checks are bound to the two known consumers and their status-decision
slots: a named binding of the folded set, or an ``in`` / ``not in`` membership
comparand stating it inline. Equal value alone is not proof of one concept
(semantic-vocabulary-convergence-v0): the ordered four-status report tuple in
``_source_coverage`` is display data, and regrouping it as
``(*("completed", "no_change"), "failed", "unavailable")`` preserves every
observable output. The whole-tree folded-value census still collects such
same-value candidates, but only as advisory output -- never a failure.

Behavioural parity runs through the owner's own ``source_freshness_row`` gate
and the real ``_source_coverage`` report, so a migration that silently changed
which statuses count as success -- or reordered the report keys -- fails here.
"""

from __future__ import annotations

import ast
import pathlib
import warnings
from dataclasses import replace
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


def _wrapped_inner_ids(tree: ast.AST) -> set[int]:
    """Inner literals of a ``set(...)`` / ``frozenset(...)`` call.

    The call and its inner collection fold to the same members; skipping the
    inner node keeps one restatement one site, not two.
    """

    return {
        id(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
        in {"frozenset", "set"}
        and len(node.args) == 1
    }


def _status_literal_sites(source: str) -> list[int]:
    """Every collection literal in the source folding to SUCCESS, counted once.

    This is the advisory census: a same-value site is a merge candidate to
    investigate, not proof of a second success decision.
    """

    tree = ast.parse(source)
    wrapped = _wrapped_inner_ids(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if id(node) not in wrapped and _members(node) == SUCCESS
    ]


def _decision_slot_sites(source: str) -> list[int]:
    """SUCCESS-folding literals in status-decision slots, counted once.

    A decision slot is a named binding (an ``Assign`` / ``AnnAssign`` value) or
    a membership comparand (``in`` / ``not in``). Iteration and display order
    -- the ``status_counts`` report tuple, including a starred regrouping of
    it -- are not decisions and are left to the advisory census.
    """

    tree = ast.parse(source)
    wrapped = _wrapped_inner_ids(tree)
    slots: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            slots.add(id(node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            slots.add(id(node.value))
        elif isinstance(node, ast.Compare):
            slots.update(
                id(comparator)
                for op, comparator in zip(node.ops, node.comparators)
                if isinstance(op, (ast.In, ast.NotIn))
            )
    return [
        node.lineno
        for node in ast.walk(tree)
        if id(node) in slots and id(node) not in wrapped and _members(node) == SUCCESS
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


def test_whole_tree_same_value_candidates_are_advisory(tree_offenders) -> None:
    # The RFC keeps equal-value findings advisory: outside the known decision
    # slots a same-value site is a merge candidate, never a failure.
    if tree_offenders:
        warnings.warn(
            f"advisory same-value candidates (not a failure): {tree_offenders}",
            stacklevel=1,
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
    assert _decision_slot_sites(source) == []
    assert "_SUCCESS_STATUSES" in source


def test_assembler_has_no_restated_success_decision() -> None:
    source = (REPO_ROOT / ASSEMBLER_PATH).read_text(encoding="utf-8")
    assert _decision_slot_sites(source) == []
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


def test_successful_read_consumers_keep_observable_semantics() -> None:
    # The report side of the consumers: the four display keys keep their
    # recorded order and counts while only the success decision is
    # single-sourced. Reordering or dropping a key fails here; a regrouping
    # that preserves order and counts (the starred form) changes nothing.
    collected = tuple(
        assembler_mod._CollectedSource(
            source=replace(_spec(), source_id=f"source:{index}"),
            scan=sources_mod.DecisionSourceScan(
                provider_id="source-provider",
                source_id=f"source:{index}",
                status=status,
                observed_at=OBSERVED_AT.isoformat(),
                requested_limit=1,
            ),
            exact_reads=(),
            exact_read_failed=exact_read_failed,
            receipt={},
        )
        for index, (status, exact_read_failed) in enumerate(
            (
                ("completed", False),
                ("no_change", False),
                ("failed", False),
                ("unavailable", False),
                ("completed", True),
            )
        )
    )
    report = assembler_mod._source_coverage(collected)
    p0 = report["priority_rows"][0]
    assert p0["priority"] == "p0"
    assert list(p0["status_counts"]) == [
        "completed",
        "no_change",
        "failed",
        "unavailable",
    ]
    assert p0["status_counts"] == {
        "completed": 2,
        "no_change": 1,
        "failed": 1,
        "unavailable": 1,
    }
    assert p0["complete_count"] == 2
    assert p0["exact_read_incomplete_count"] == 1
    assert report["incomplete_required_source_ids"] == [
        "source:2",
        "source:3",
        "source:4",
    ]
    assert report["status"] == "incomplete"
    assert report["complete"] is False


def test_same_value_without_same_slot_remains_advisory() -> None:
    # The reviewer counterexample: the starred regrouping of the ordered
    # report keys folds to SUCCESS inside a display tuple, so the advisory
    # census sees it -- but it decides nothing, so the hard check stays silent.
    starred_report = (
        'for status in (*("completed", "no_change"), "failed", "unavailable"):\n'
        "    pass\n"
    )
    assert _status_literal_sites(starred_report) == [1]
    assert _decision_slot_sites(starred_report) == []


# --- the census bites every restatement form; the hard check bites decisions only -------

POISON_CORPUS = (
    ("bare set restatement", 'X = {"completed", "no_change"}\n', 1, 1),
    ("frozenset call", 'X = frozenset({"completed", "no_change"})\n', 1, 1),
    ("set call over a list", 'X = set(["completed", "no_change"])\n', 1, 1),
    ("list literal", 'X = ["completed", "no_change"]\n', 1, 1),
    ("tuple literal", 'X = ("completed", "no_change")\n', 1, 1),
    ("reordered members fold the same", 'X = {"no_change", "completed"}\n', 1, 1),
    ("renamed binding is still caught", '_ANY = {"completed", "no_change"}\n', 1, 1),
    ("annotated binding is still caught", '_ANY: frozenset = frozenset({"completed", "no_change"})\n', 1, 1),
    ("membership inside a function", 'def f(status):\n    return status in {"completed", "no_change"}\n', 1, 1),
    ("negative membership is a decision too", 'def f(status):\n    return status not in {"completed", "no_change"}\n', 1, 1),
    ("importing the owner is not a restatement", "from .m import _SUCCESS_STATUSES\n", 0, 0),
    ("alias rebind is not a restatement", "_READ_SUCCESS_STATUSES = _SUCCESS_STATUSES\n", 0, 0),
    ("the full scan vocabulary is not this set", '_SCAN = {"completed", "failed", "no_change", "unavailable"}\n', 0, 0),
    ("the ordered report tuple is not this set", 'X = ("completed", "no_change", "failed", "unavailable")\n', 0, 0),
    ("a superset is not this set", 'X = {"completed", "no_change", "failed"}\n', 0, 0),
    ("a single status is not this set", 'X = {"completed"}\n', 0, 0),
    ("the starred report regrouping is advisory only", 'for status in (*("completed", "no_change"), "failed", "unavailable"):\n    pass\n', 1, 0),
    ("iterating the success pair for display is advisory only", 'for status in ("completed", "no_change"):\n    pass\n', 1, 0),
)


@pytest.mark.parametrize(
    "label,source,census_expected,slots_expected",
    POISON_CORPUS,
    ids=[case[0] for case in POISON_CORPUS],
)
def test_census_and_decision_slots_classify_every_form(
    label: str, source: str, census_expected: int, slots_expected: int
) -> None:
    assert len(_status_literal_sites(source)) == census_expected, label
    assert len(_decision_slot_sites(source)) == slots_expected, label