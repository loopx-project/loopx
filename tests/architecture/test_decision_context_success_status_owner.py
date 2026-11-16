"""Guard one owner for the decision-context successful-read status set (#4447).

``_SUCCESS_STATUSES`` in ``freshness.py`` decides which scan/attempt statuses
count as a successful read (``completed`` / ``no_change``). The same two-member
set was restated across the package: ``capture.py`` bound it under a different
name (``_READ_SUCCESS_STATUSES``) *and* inlined it once, while ``assembler.py``
inlined it four times with no name at all -- seven sites folding to one value.
Both consumers *already import* from ``freshness``, so borrowing the owner object
adds no new import edge and cannot introduce a cycle. Every site now references
the single owner and the restated name is retired.

Blocking checks are behavioural rather than shape-based. Both consumers import
the owner object by identity, and every real decision site is exercised against
an owner that accepts one extra status: the coverage counts, the freshness
``last_read_at`` projection, the legacy spool status projection, and a full
capture tick. A restated literal anywhere on those paths freezes at the stock
two members, so the widened status falls back to "not success" (1 vs 0) and the
probe fails. That is also why display-only memberships, locals rebound later in
the same function, and bindings whose provenance cannot be proven are safe
here: they decide nothing and stay with the whole-tree folded-value census as
advisory output (equal value is not proof of one concept --
semantic-vocabulary-convergence-v0).

Behavioural parity runs through the owner's own ``source_freshness_row`` gate
and the real ``_source_coverage`` report, so a migration that silently changed
which statuses count as success -- or reordered the report keys -- fails here.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sqlite3
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
from loopx.capabilities.decision_context.capture import capture_profile_sources
from loopx.capabilities.decision_context.providers import (
    LocalFileDecisionSourceProvider,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/capabilities/decision_context/freshness.py"
CAPTURE_PATH = "loopx/capabilities/decision_context/capture.py"

SUCCESS = frozenset({"completed", "no_change"})
FAILURE = frozenset({"provider_failed", "failed", "unavailable"})
SCAN_VOCABULARY = frozenset({"completed", "failed", "no_change", "unavailable"})

# One vocabulary status the stock owner rejects. The probes widen the owner by
# exactly this member: borrowers follow, a frozen copy keeps reporting 0.
WIDENED_STATUS = "unavailable"


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


# --- the widened-owner probes: every real decision site must borrow, not copy ----------


@pytest.fixture
def widened_owner(monkeypatch) -> frozenset[str]:
    """Model the owner accepting one more status; only borrowers can follow.

    Rebinding each module attribute is the runtime equivalent of editing the
    owner definition: consumers hold the same object in production, so a new
    literal copy at a decision site is the only thing that stays behind.
    """

    widened = frozenset({*SUCCESS, WIDENED_STATUS})
    assert capture_mod._SUCCESS_STATUSES is owner._SUCCESS_STATUSES
    assert assembler_mod._SUCCESS_STATUSES is owner._SUCCESS_STATUSES
    for module in (owner, capture_mod, assembler_mod):
        monkeypatch.setattr(module, "_SUCCESS_STATUSES", widened)
    return widened


def _collected(status: str):
    return assembler_mod._CollectedSource(
        source=_spec(),
        scan=sources_mod.DecisionSourceScan(
            provider_id="source-provider",
            source_id="source:owner",
            status=status,
            observed_at=OBSERVED_AT.isoformat(),
            requested_limit=1,
        ),
        exact_reads=(),
        exact_read_failed=False,
        receipt={},
    )


def test_the_widened_status_is_not_success_for_the_stock_owner() -> None:
    # The premise of the probe: the stock owner rejects this status, so a
    # borrower reports 1 after widening while a frozen copy keeps reporting 0.
    report = assembler_mod._source_coverage((_collected(WIDENED_STATUS),))
    assert report["priority_rows"][0]["complete_count"] == 0
    assert report["incomplete_required_source_ids"] == ["source:owner"]
    assert report["status"] == "incomplete"


def test_coverage_follows_the_owner_when_it_widens(widened_owner) -> None:
    report = assembler_mod._source_coverage((_collected(WIDENED_STATUS),))
    assert report["priority_rows"][0]["complete_count"] == 1
    assert report["incomplete_required_source_ids"] == []
    assert report["status"] == "complete"


def test_freshness_follows_the_owner_when_it_widens(widened_owner) -> None:
    report = assembler_mod._assembly_source_freshness(
        collected_sources=(_collected(WIDENED_STATUS),),
        coverage_sources=(_spec(),),
        assembly_time=OBSERVED_AT,
    )
    row = report["sources"][0]
    assert row["last_attempt_status"] == WIDENED_STATUS
    assert row["last_read_at"] == OBSERVED_AT.isoformat()
    assert row["alert"] is False


def _legacy_spool(status: str) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY,
            checked_at TEXT,
            status TEXT,
            cursor TEXT,
            binding_digest TEXT
        );
        CREATE TABLE batches (id INTEGER PRIMARY KEY, source_id TEXT);
        """
    )
    db.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
        ("source:owner", OBSERVED_AT.isoformat(), status, None, None),
    )
    return db


def test_legacy_spool_projection_follows_the_owner_when_it_widens(
    widened_owner,
) -> None:
    projection = capture_mod._status(
        _legacy_spool(WIDENED_STATUS), (_spec(),), now=OBSERVED_AT, capacity=10
    )
    row = projection["sources"][0]
    assert row["status"] == WIDENED_STATUS
    assert row["last_read_at"] == OBSERVED_AT.isoformat()
    assert row["freshness"] == "fresh"


def _capture_profile(authority: pathlib.Path) -> dict[str, object]:
    return {
        "schema_version": "decision_context_profile_v0",
        "goal_id": "example-decision-goal",
        "enabled": True,
        "enabled_agents": ["example-agent"],
        "source_provider_bindings": [
            {
                "provider_id": "local-authority",
                "adapter": "local-file",
                "config": {"max_bytes": 4096},
            }
        ],
        "sources": [
            {
                "source_id": "source:authority:baseline",
                "provider_id": "local-authority",
                "source_kind": "artifact",
                "priority": "p0",
                "evidence_level": "s3",
                "objectives": ["delivery-quality", "product-adoption"],
                "scan_mode": "incremental",
                "exact_read_policy": "material_change",
                "freshness_seconds": 3 * 24 * 60 * 60,
                "private_locator": str(authority),
                "max_changes": 8,
                "enabled": True,
            }
        ],
        "context_provider": None,
        "automation": {
            "automatic_capture": True,
            "fail_open": True,
            "source_ids": ["source:authority:baseline"],
        },
    }


class _WidenedStatusProvider(LocalFileDecisionSourceProvider):
    """Report the widened status on a scan the capture tick otherwise accepts."""

    def scan(self, **kwargs):
        base = super().scan(**kwargs)
        return replace(
            base,
            status=WIDENED_STATUS,
            reason_code="owner_widened_probe",
            items=(),
            cursor_after=f"{base.cursor_after}:owner-probe",
        )


def test_capture_tick_follows_the_owner_when_it_widens(
    widened_owner, tmp_path
) -> None:
    authority = tmp_path / "authority.txt"
    authority.write_text("private-body-not-for-spool")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_capture_profile(authority)))
    spool_path = tmp_path / "spool.sqlite"

    result = capture_profile_sources(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile_path,
        spool_path=spool_path,
        cursor_path=tmp_path / "reviewed.json",
        execute=True,
        source_provider_overrides={
            "local-authority": _WidenedStatusProvider(
                provider_id="local-authority", max_bytes=4096
            )
        },
    )
    # A frozen copy of the stock set advances neither the cursor nor the
    # success time: the batch count and last_read_at both fall to 0/None.
    assert result["sources"][0]["status"] == WIDENED_STATUS
    assert result["pending_batch_count"] == 1
    assert result["sources"][0]["last_read_at"] is not None
    with sqlite3.connect(spool_path) as db:
        cursor = db.execute("SELECT cursor FROM sources").fetchone()[0]
    assert cursor.endswith(":owner-probe")


# --- the advisory census folds every same-value form -----------------------------------

POISON_CORPUS = (
    ("bare set restatement", 'X = {"completed", "no_change"}\n', 1),
    ("frozenset call", 'X = frozenset({"completed", "no_change"})\n', 1),
    ("set call over a list", 'X = set(["completed", "no_change"])\n', 1),
    ("list literal", 'X = ["completed", "no_change"]\n', 1),
    ("tuple literal", 'X = ("completed", "no_change")\n', 1),
    ("reordered members fold the same", 'X = {"no_change", "completed"}\n', 1),
    ("nested inside a function", 'def f():\n    return {"completed", "no_change"}\n', 1),
    ("display filter over column names", 'cols = [s for s in all_statuses if s in {"completed", "no_change"}]\n', 1),
    ("named display columns expanded into the report", 'success_columns = ("completed", "no_change")\nfor status in (*success_columns, "failed", "unavailable"):\n    pass\n', 1),
    ("starred inline report", 'for status in (*("completed", "no_change"), "failed", "unavailable"):\n    pass\n', 1),
    ("importing the owner is not a restatement", "from .m import _SUCCESS_STATUSES\n", 0),
    ("alias rebind of the owner is not a restatement", "_READ_SUCCESS_STATUSES = _SUCCESS_STATUSES\n", 0),
    ("the full scan vocabulary is not this set", '_SCAN = {"completed", "failed", "no_change", "unavailable"}\n', 0),
    ("the ordered report tuple is not this set", 'X = ("completed", "no_change", "failed", "unavailable")\n', 0),
    ("a superset is not this set", 'X = {"completed", "no_change", "failed"}\n', 0),
    ("a single status is not this set", 'X = {"completed"}\n', 0),
)


@pytest.mark.parametrize(
    "label,source,expected",
    POISON_CORPUS,
    ids=[case[0] for case in POISON_CORPUS],
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_status_literal_sites(source)) == expected, label
