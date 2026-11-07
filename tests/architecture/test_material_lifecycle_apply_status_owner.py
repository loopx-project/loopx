"""Guard one owner for the material-lifecycle apply outcome vocabulary.

`build_material_rerank_apply_receipt` records the result of one apply and
`build_material_ranked_entry_rebuild_apply_receipt` replays a whole plan. Both
ask the same question -- is this token a legal apply outcome -- and on the base
revision each stated the answer with its own module-level set, so a status added
on one side stayed illegal on the other while every scan that groups by name
still saw two matching definitions.

Decisions are made on the folded value, not the spelling: a plain set literal, a
reordered literal, a `set(...)` or `frozenset(...)` wrapper, and a one-step
comprehension over the same members are all offenders. Constructions whose
members cannot be folded statically are not reported; widening the census to
them means teaching `_folded_members` to decide them and adding a case here,
rather than declaring a site and trusting it.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from loopx.capabilities.material_lifecycle import _validation
from loopx.capabilities.material_lifecycle._validation import APPLY_STATUSES
from loopx.capabilities.material_lifecycle.ranking import (
    build_material_rerank_apply_receipt,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx" / "capabilities" / "material_lifecycle"
OWNER_MODULE = "loopx/capabilities/material_lifecycle/_validation.py"
CONSUMERS = (
    "loopx/capabilities/material_lifecycle/ranking.py",
    "loopx/capabilities/material_lifecycle/rebuild.py",
)

VOCABULARY = {"applied", "no_change", "rejected", "rolled_back"}


def _folded_comprehension(node: ast.SetComp) -> frozenset[str] | None:
    """Fold the one comprehension shape that cannot change the member set.

    ``{name for name in <foldable literal>}`` with no filter selects exactly the
    iterable's members, so it states the same vocabulary under another spelling.
    Anything with a condition, a transform, or several generators is not decided
    here, and claiming otherwise would put a comment over an unproved match.
    """

    if len(node.generators) != 1 or node.generators[0].ifs:
        return None
    generator = node.generators[0]
    if not isinstance(generator.target, ast.Name) or not isinstance(node.elt, ast.Name):
        return None
    if node.elt.id != generator.target.id:
        return None
    return _folded_members(generator.iter)


def _folded_members(node: ast.AST) -> frozenset[str] | None:
    """Return the member set of a literal set construction, or None."""

    if isinstance(node, ast.SetComp):
        return _folded_comprehension(node)
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        members = node.elts
    elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"set", "frozenset"}:
        if len(node.args) != 1 or node.keywords:
            return None
        inner = node.args[0]
        if isinstance(inner, (ast.Set, ast.List, ast.Tuple, ast.SetComp)):
            return _folded_members(inner)
        return None
    else:
        return None
    values = [
        element.value
        for element in members
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    return frozenset(values) if len(values) == len(members) else None


def set_literals(source: str) -> list[tuple[int, frozenset[str]]]:
    """Every foldable set construction anywhere in the module.

    Module level alone is not enough: a consumer can keep the owner import alive
    and still decide inline with its own literal inside a function body, which is
    the same drift with one more step to find.
    """

    tree = ast.parse(source)
    rows: list[tuple[int, frozenset[str]]] = []
    for node in ast.walk(tree):
        candidates: list[ast.AST] = []
        if isinstance(node, ast.Compare):
            candidates = list(node.comparators)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                [node.target]
                if isinstance(node, ast.AnnAssign)
                else node.targets
            )
            if any(isinstance(target, ast.Name) for target in targets):
                candidates = [node.value]
        for candidate in candidates:
            folded = _folded_members(candidate)
            if folded is not None:
                rows.append((getattr(node, "lineno", 0), folded))
    return rows


def _posix_prefix(prefix: str) -> str:
    """Join census sites with forward slashes whatever the host separator was.

    ``str(Path.relative_to(...))`` yields backslashes on Windows, which made the
    owner comparison fail there even when exactly one correct owner existed.
    """

    return "/".join(part for part in re.split(r"[\\/]+", prefix) if part)


def vocabulary_sites(
    root: pathlib.Path, *, rel_prefix: str = ""
) -> list[str]:
    """Every module-level set in `root` that answers the apply-outcome question."""

    sites: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        prefix = rel_prefix or str(root.relative_to(REPO_ROOT))
        for line, members in set_literals(source):
            if set(members) == VOCABULARY:
                sites.append(f"{_posix_prefix(prefix)}/{path.name}:{line}")
    return sites


def test_only_the_owner_states_the_apply_vocabulary() -> None:
    sites = vocabulary_sites(PACKAGE_ROOT)

    assert [site.split(":")[0] for site in sites] == [OWNER_MODULE], sites
    assert len(sites) == 1, sites


@pytest.mark.parametrize(
    "source",
    [
        # a plain literal is the spelling the base revision used twice
        pytest.param('APPLY = {"applied", "no_change", "rejected", "rolled_back"}\n', id="plain-literal"),
        # reordering the members changes nothing about the decision
        pytest.param('APPLY = {"rolled_back", "rejected", "no_change", "applied"}\n', id="reordered-literal"),
        pytest.param('APPLY = set(["applied", "no_change", "rejected", "rolled_back"])\n', id="set-wrapper"),
        pytest.param('APPLY = frozenset({"applied", "no_change", "rejected", "rolled_back"})\n', id="frozenset-wrapper"),
        pytest.param('APPLY = {status for status in ("applied", "no_change", "rejected", "rolled_back")}\n', id="set-comprehension"),
    ],
)
def test_a_rewritten_copy_is_still_recognised(tmp_path: pathlib.Path, source: str) -> None:
    (tmp_path / "copycat.py").write_text(source, encoding="utf-8")

    assert vocabulary_sites(tmp_path, rel_prefix="copy") == ["copy/copycat.py:1"]


def test_one_member_off_is_not_the_vocabulary(tmp_path: pathlib.Path) -> None:
    """The census is exact, so a widened copy is a review event, not a match."""

    (tmp_path / "widened.py").write_text(
        'APPLY = {"applied", "no_change", "rejected", "rolled_back", "undone"}\n',
        encoding="utf-8",
    )

    assert vocabulary_sites(tmp_path, rel_prefix="copy") == []


@pytest.mark.parametrize("relative", CONSUMERS)
def test_each_consumer_decides_with_the_owner_object(relative: str) -> None:
    module_name = relative.rsplit("/", 1)[-1][:-3]
    module = __import__(
        f"loopx.capabilities.material_lifecycle.{module_name}", fromlist=["APPLY_STATUSES"]
    )

    assert module.APPLY_STATUSES is _validation.APPLY_STATUSES
    source = pathlib.Path(REPO_ROOT / relative).read_text(encoding="utf-8")
    assert source.count("not in APPLY_STATUSES") == 1, (
        f"{relative} must decide membership through the owner exactly once"
    )
    assert "_APPLY_STATUSES" not in source, f"{relative} keeps a private copy"


LEGAL_CASES: dict[str, dict[str, object]] = {
    # each shape is the minimal legal companion for its own status, which is why
    # the vocabulary needs one owner: the accepted tokens and the rules attached
    # to them must not be stated twice, once per consumer.
    "applied": {
        "applied_material_refs": ["material:one"],
        "before_revision": "revision:before",
        "after_revision": "revision:after",
        "rollback_ref": "rollback:before",
        "rejection_reason": None,
    },
    "rolled_back": {
        "applied_material_refs": [],
        "before_revision": "revision:before",
        "after_revision": "revision:after",
        "rollback_ref": "rollback:before",
        "rejection_reason": None,
    },
    "no_change": {
        "applied_material_refs": [],
        "before_revision": "revision:same",
        "after_revision": "revision:same",
        "rollback_ref": None,
        "rejection_reason": None,
    },
    "rejected": {
        "applied_material_refs": [],
        "before_revision": "revision:same",
        "after_revision": "revision:same",
        "rollback_ref": None,
        "rejection_reason": "owner declined the proposal",
    },
}


@pytest.mark.parametrize("status", sorted(LEGAL_CASES))
def test_every_owner_status_is_a_legal_apply_outcome(status: str) -> None:
    receipt = build_material_rerank_apply_receipt(
        goal_id="goal:materials",
        receipt_id=f"receipt:{status}",
        proposal_ref="proposal:one",
        observed_at="2026-01-01T00:00:00Z",
        status=status,
        owner_gate_ref="gate:exact-move",
        validation_ref="validation:readback",
        **LEGAL_CASES[status],
    )

    assert receipt["status"] == status


def test_the_legal_shapes_cover_the_whole_vocabulary() -> None:
    assert sorted(LEGAL_CASES) == sorted(APPLY_STATUSES)


def test_a_status_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported status"):
        build_material_rerank_apply_receipt(
            goal_id="goal:materials",
            receipt_id="receipt:refused",
            proposal_ref="proposal:one",
            observed_at="2026-01-01T00:00:00Z",
            status="accepted",
            before_revision="revision:before",
            after_revision="revision:after",
            owner_gate_ref="gate:exact-move",
            validation_ref="validation:readback",
            rollback_ref=None,
            applied_material_refs=["material:one"],
        )

def test_a_comprehension_over_other_members_is_not_the_vocabulary(
    tmp_path: pathlib.Path,
) -> None:
    """The comprehension fold widens reach, not matching: other members stay clean."""

    (tmp_path / "other.py").write_text(
        'STATUS = {state for state in ("applied", "no_change", "rejected")}\n',
        encoding="utf-8",
    )

    assert vocabulary_sites(tmp_path, rel_prefix="copy") == []


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("loopx\\capabilities\\material_lifecycle", "loopx/capabilities/material_lifecycle"),
        ("loopx/capabilities/material_lifecycle", "loopx/capabilities/material_lifecycle"),
        ("copy", "copy"),
    ],
)
def test_sites_always_join_with_forward_slashes(prefix: str, expected: str) -> None:
    """A Windows-shaped relative prefix must not turn the owner into a mismatch."""

    assert _posix_prefix(prefix) == expected


def test_the_real_scan_reports_one_owner_regardless_of_separator(
    tmp_path: pathlib.Path,
) -> None:
    module = tmp_path / "loopx" / "capabilities" / "material_lifecycle"
    module.mkdir(parents=True)
    (module / "_validation.py").write_text(
        'APPLY_STATUSES = frozenset({"applied", "no_change", "rejected", "rolled_back"})\n',
        encoding="utf-8",
    )

    sites = vocabulary_sites(
        module,
        rel_prefix=str(pathlib.PureWindowsPath("loopx", "capabilities", "material_lifecycle")),
    )

    assert sites == [
        "loopx/capabilities/material_lifecycle/_validation.py:1"
    ], sites
