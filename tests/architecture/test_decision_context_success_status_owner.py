"""Guard one owner for the decision-context successful-read status set (#4447).

``_SUCCESS_STATUSES`` in ``freshness.py`` decides which scan/attempt statuses
count as a successful read (``completed`` / ``no_change``). The same two-member
set was restated across the package: ``capture.py`` bound it under a different
name (``_READ_SUCCESS_STATUSES``) *and* inlined it once, while ``assembler.py``
inlined it four times with no name at all -- seven sites folding to one value.
Both consumers *already import* from ``freshness``, so borrowing the owner object
adds no new import edge and cannot introduce a cycle. Every site now references
the single owner and the restated name is retired.

Blocking checks are bound to the two known consumers' membership decisions:
an ``in`` / ``not in`` comparand stating the folded set inline, or a name whose
lexical binding -- resolved from the innermost enclosing scope outward -- is
such a literal. Scope matters: two functions may reuse one spelling, one for
display columns and one for the owner object, and only the binding visible at
the decision counts. A binding alone decides nothing, and a name whose
provenance cannot be resolved locally (an import, a parameter) is not evidence,
so both stay with the whole-tree folded-value census as advisory output, never
a failure (semantic-vocabulary-convergence-v0: equal value is not proof of one
concept).

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


class _Scope:
    """One lexical scope: the module, a function, or a class body."""

    def __init__(self, node: ast.AST, parent: "_Scope | None") -> None:
        self.node = node
        self.parent = parent
        self.bindings: dict[str, ast.expr | None] = {}


def _membership_sites(tree: ast.Module) -> list[tuple[ast.Compare, _Scope]]:
    """Every ``in`` / ``not in`` comparison paired with its enclosing scope."""

    found: list[tuple[ast.Compare, _Scope]] = []

    def record(expr: ast.AST | None, scope: _Scope) -> None:
        if expr is None:
            return
        for node in ast.walk(expr):
            if isinstance(node, ast.Compare):
                found.append((node, scope))

    def bind(target: ast.expr, scope: _Scope, value: ast.expr | None = None) -> None:
        if isinstance(target, ast.Name):
            scope.bindings[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                bind(element, scope)
        elif isinstance(target, ast.Starred):
            bind(target.value, scope)

    def visit(body: list[ast.stmt], scope: _Scope) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope.bindings[stmt.name] = None
                # A class body is not a closure scope for the functions it
                # defines, so skip it when building the lexical chain.
                parent = scope
                while isinstance(parent.node, ast.ClassDef):
                    parent = parent.parent
                inner = _Scope(stmt, parent)
                for arg in (
                    *stmt.args.posonlyargs,
                    *stmt.args.args,
                    *stmt.args.kwonlyargs,
                    stmt.args.vararg,
                    stmt.args.kwarg,
                ):
                    if arg is not None:
                        inner.bindings[arg.arg] = None
                for expr in (*stmt.decorator_list, *stmt.args.defaults):
                    record(expr, scope)
                for expr in stmt.args.kw_defaults:
                    record(expr, scope)
                visit(stmt.body, inner)
            elif isinstance(stmt, ast.ClassDef):
                scope.bindings[stmt.name] = None
                inner = _Scope(stmt, scope)
                for expr in (*stmt.decorator_list, *stmt.bases):
                    record(expr, scope)
                for keyword in stmt.keywords:
                    record(keyword.value, scope)
                visit(stmt.body, inner)
            elif isinstance(stmt, ast.Assign):
                record(stmt.value, scope)
                for target in stmt.targets:
                    bind(target, scope, stmt.value if isinstance(target, ast.Name) else None)
            elif isinstance(stmt, ast.AnnAssign):
                record(stmt.value, scope)
                bind(stmt.target, scope, stmt.value if isinstance(stmt.target, ast.Name) else None)
            elif isinstance(stmt, ast.AugAssign):
                record(stmt.value, scope)
                if isinstance(stmt.target, ast.Name) and stmt.target.id not in scope.bindings:
                    scope.bindings[stmt.target.id] = None
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                record(stmt.iter, scope)
                bind(stmt.target, scope)
                visit(stmt.body, scope)
                visit(stmt.orelse, scope)
            elif isinstance(stmt, ast.While):
                record(stmt.test, scope)
                visit(stmt.body, scope)
                visit(stmt.orelse, scope)
            elif isinstance(stmt, ast.If):
                record(stmt.test, scope)
                visit(stmt.body, scope)
                visit(stmt.orelse, scope)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    record(item.context_expr, scope)
                    if item.optional_vars is not None:
                        bind(item.optional_vars, scope)
                visit(stmt.body, scope)
            elif isinstance(stmt, ast.Try):
                visit(stmt.body, scope)
                for handler in stmt.handlers:
                    record(handler.type, scope)
                    if handler.name:
                        scope.bindings[handler.name] = None
                    visit(handler.body, scope)
                visit(stmt.orelse, scope)
                visit(stmt.finalbody, scope)
            elif isinstance(stmt, ast.Match):
                record(stmt.subject, scope)
                for case in stmt.cases:
                    record(case.guard, scope)
                    visit(case.body, scope)
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    scope.bindings[alias.asname or alias.name.split(".")[0]] = None
            else:
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.expr):
                        record(child, scope)

    module = _Scope(tree, None)
    visit(tree.body, module)
    return found


def _binds_success_literal(name: str, scope: _Scope | None) -> bool:
    """Whether ``name`` resolves, lexically, to a SUCCESS-folding literal.

    The innermost enclosing scope wins, so two functions may reuse one spelling
    for different things and only the binding visible at the decision counts.
    Imports, parameters, and anything else without a literal binding anywhere
    up the chain are unknown provenance: never a hard finding.
    """

    seen: set[tuple[str, int]] = set()
    for _ in range(8):
        holder = scope
        while holder is not None and name not in holder.bindings:
            holder = holder.parent
        if holder is None or (name, id(holder)) in seen:
            return False
        seen.add((name, id(holder)))
        value = holder.bindings[name]
        if value is None:
            return False
        if _members(value) == SUCCESS:
            return True
        if not isinstance(value, ast.Name):
            return False
        name, scope = value.id, holder
    return False


def _decision_slot_sites(source: str) -> list[int]:
    """SUCCESS restatements in status-decision slots, counted once.

    A decision slot is an ``in`` / ``not in`` membership comparand: either a
    collection literal folding to SUCCESS, or a name whose lexical binding is
    such a literal. Bindings alone (display columns), iteration order, and
    memberships over names of unknown provenance decide nothing and are left
    to the advisory census.
    """

    sites: list[int] = []
    for compare, scope in _membership_sites(ast.parse(source)):
        for op, comparand in zip(compare.ops, compare.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if _members(comparand) == SUCCESS:
                sites.append(comparand.lineno)
            elif isinstance(comparand, ast.Name) and _binds_success_literal(
                comparand.id, scope
            ):
                sites.append(comparand.lineno)
    return sorted(sites)


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
    # The reviewer counterexamples: each folds to SUCCESS somewhere in display
    # data, so the advisory census sees the literal -- but no decision is on a
    # restated set, so the hard check stays silent.
    starred_report = (
        'for status in (*("completed", "no_change"), "failed", "unavailable"):\n'
        "    pass\n"
    )
    assert _status_literal_sites(starred_report) == [1]
    assert _decision_slot_sites(starred_report) == []
    named_columns = (
        'success_columns = ("completed", "no_change")\n'
        'for status in (*success_columns, "failed", "unavailable"):\n'
        "    pass\n"
    )
    assert _status_literal_sites(named_columns) == [1]
    assert _decision_slot_sites(named_columns) == []
    # One spelling, two functions, two roles: the report keeps display columns
    # in a local ``statuses`` while freshness aliases the owner under the same
    # name. Resolution is per scope, so neither site is a restatement.
    shared_spelling = (
        "def report():\n"
        '    statuses = ("completed", "no_change")\n'
        '    return (*statuses, "failed", "unavailable")\n'
        "\n"
        "def fresh(attempt):\n"
        "    statuses = _SUCCESS_STATUSES\n"
        "    return attempt in statuses\n"
    )
    assert _status_literal_sites(shared_spelling) == [2]
    assert _decision_slot_sites(shared_spelling) == []
    # A parameter shadows any outer binding of the same spelling: unknown
    # provenance at the decision, so it stays advisory.
    shadowed_parameter = (
        '_statuses = ("completed", "no_change")\n'
        "def f(_statuses):\n"
        "    return status in _statuses\n"
    )
    assert _decision_slot_sites(shadowed_parameter) == []


# --- the census bites every restatement form; the hard check bites decisions only -------

POISON_CORPUS = (
    ("inline membership restatement", 'def f(status):\n    return status in {"completed", "no_change"}\n', 1, 1),
    ("negative membership is a decision too", 'def f(status):\n    return status not in {"completed", "no_change"}\n', 1, 1),
    ("frozenset call in membership", 'def f(status):\n    return status in frozenset({"completed", "no_change"})\n', 1, 1),
    ("set call over a list in membership", 'def f(status):\n    return status in set(["completed", "no_change"])\n', 1, 1),
    ("list comparand", 'def f(status):\n    return status in ["completed", "no_change"]\n', 1, 1),
    ("tuple comparand", 'def f(status):\n    return status in ("completed", "no_change")\n', 1, 1),
    ("reordered members fold the same", 'def f(status):\n    return status in {"no_change", "completed"}\n', 1, 1),
    ("renamed binding decided through is still caught", '_ANY = {"completed", "no_change"}\ndef f(status):\n    return status in _ANY\n', 1, 1),
    ("annotated binding decided through is still caught", '_ANY: frozenset = frozenset({"completed", "no_change"})\ndef f(status):\n    return status in _ANY\n', 1, 1),
    ("the retired capture shape is still caught", '_READ_SUCCESS_STATUSES = frozenset({"completed", "no_change"})\nif source["status"] in _READ_SUCCESS_STATUSES:\n    pass\n', 1, 1),
    ("a display binding alone is advisory only", 'X = {"completed", "no_change"}\n', 1, 0),
    ("a renamed display binding alone is advisory only", '_ANY = {"completed", "no_change"}\n', 1, 0),
    ("the starred report regrouping is advisory only", 'for status in (*("completed", "no_change"), "failed", "unavailable"):\n    pass\n', 1, 0),
    ("named display columns are advisory only", 'success_columns = ("completed", "no_change")\nfor status in (*success_columns, "failed", "unavailable"):\n    pass\n', 1, 0),
    ("iterating the success pair for display is advisory only", 'for status in ("completed", "no_change"):\n    pass\n', 1, 0),
    ("a function-local literal is still a restatement", 'def f(status):\n    _R = ("completed", "no_change")\n    return status in _R\n', 1, 1),
    ("a local alias of a restated literal is still caught", '_ANY = ("completed", "no_change")\ndef f(status):\n    _R = _ANY\n    return status in _R\n', 1, 1),
    ("a closure sees the module restatement", '_ANY = ("completed", "no_change")\ndef outer():\n    def inner(status):\n        return status in _ANY\n', 1, 1),
    ("importing the owner is not a restatement", "from .m import _SUCCESS_STATUSES\n", 0, 0),
    ("alias rebind is not a restatement", "_READ_SUCCESS_STATUSES = _SUCCESS_STATUSES\n", 0, 0),
    ("deciding through an alias of the owner is legal", "_ALIAS = _SUCCESS_STATUSES\ndef f(status):\n    return status in _ALIAS\n", 0, 0),
    ("a local alias of the owner is not a restatement", 'from .freshness import _SUCCESS_STATUSES\ndef f(status):\n    statuses = _SUCCESS_STATUSES\n    return status in statuses\n', 0, 0),
    ("an attribute of the owner is not a restatement", 'import freshness\ndef f(status):\n    return status in freshness._SUCCESS_STATUSES\n', 0, 0),
    ("two functions share one spelling for different roles", 'def report():\n    statuses = ("completed", "no_change")\n    return (*statuses, "failed", "unavailable")\n\ndef fresh(attempt):\n    statuses = _SUCCESS_STATUSES\n    return attempt in statuses\n', 1, 0),
    ("a parameter shadows a module restatement", 'X = ("completed", "no_change")\ndef f(X):\n    return status in X\n', 1, 0),
    ("the full scan vocabulary is not this set", '_SCAN = {"completed", "failed", "no_change", "unavailable"}\n', 0, 0),
    ("the ordered report tuple is not this set", 'X = ("completed", "no_change", "failed", "unavailable")\n', 0, 0),
    ("a superset is not this set", 'X = {"completed", "no_change", "failed"}\n', 0, 0),
    ("a single status is not this set", 'X = {"completed"}\n', 0, 0),
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