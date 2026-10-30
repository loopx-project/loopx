"""Guard one owner for the material-lifecycle apply outcome vocabulary.

`build_material_rerank_apply_receipt` records the result of one apply and
`build_material_ranked_entry_rebuild_apply_receipt` replays a whole plan. Both
ask the same question -- is this token a legal apply outcome -- and on the base
revision each stated the answer with its own module-level set, so a status added
on one side stayed illegal on the other while every scan that groups by name
still saw two matching definitions.

Decisions are made on the folded value, not the spelling: a plain set literal, a
reordered literal, a `set(...)` or `frozenset(...)` wrapper and a comprehension
over the same members are all offenders, and a construction whose members cannot
be folded has to be declared with its file and count.
"""

from __future__ import annotations

import ast
import pathlib

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


def _folded_members(node: ast.AST) -> frozenset[str] | None:
    """Return the member set of a literal set construction, or None."""

    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        members = node.elts
    elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"set", "frozenset"}:
        if len(node.args) != 1:
            return None
        inner = node.args[0]
        if isinstance(inner, ast.SetComp):
            return None if inner.generators else frozenset(
                _folded_members(ast.Set(elts=inner.elt and [inner.elt])) or ()
            )
        members = inner.elts if isinstance(inner, (ast.Set, ast.List, ast.Tuple)) else None
        if members is None:
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
                sites.append(f"{prefix}/{path.name}:{line}")
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
