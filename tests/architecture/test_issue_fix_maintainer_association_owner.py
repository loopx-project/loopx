"""One owner for the issue-fix maintainer-association set.

``{"COLLABORATOR", "MEMBER", "OWNER"}`` decides whether a GitHub comment counts
as a maintainer signal. On the base revision it was written out twice inside the
same capability:

===================================  ====================================
site                                 role
===================================  ====================================
``issue_fix/github_public.py:40``    owner: ``MAINTAINER_ASSOCIATIONS``
``issue_fix/candidate_evidence.py``  private ``_MAINTAINER_ASSOCIATIONS`` restatement
===================================  ====================================

``issue_fix/pr_lifecycle.py`` already *imports* the owner's constant, so the
import direction is established and ``candidate_evidence`` is the lone holdout.
``github_public`` is a leaf module -- it imports only the standard library, no
relative imports -- so naming it the authority cannot introduce a cycle.

The guard mirrors this repository's other single-owner censuses:

1. a **whole-tree member census**: no Python module under ``loopx`` may state a
   collection literal whose members are exactly this set, except the owner. It
   folds set / frozenset / list / tuple literals, so a renamed restatement and an
   inline ``x in {...}`` are the same offender as the original constant;
2. **identity**: both consumers hold the owner's object, not a copy;
3. an **AST reader check**: ``candidate_evidence`` loads the borrowed name and
   binds neither the public nor the retired private name;
4. a **behavioural truth table** through ``github_public._normalise_comment_signal``
   that holds timing fixed, so the association membership -- not the clock -- is
   what flips the signal, and a maintainer reply before the anchor still is not a
   signal.

The neighbour this guard deliberately leaves: the TypeScript reviewer set in
``control_plane/capabilities/pr_review_approval_closeout.ts`` adds ``CONTRIBUTOR``
(four members, a different question) and is not Python, so the census never sees
it. The four-member form is pinned as a negative control below.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone

import pytest

from loopx.capabilities.issue_fix import candidate_evidence, github_public, pr_lifecycle

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/capabilities/issue_fix/github_public.py"
CANDIDATE_PATH = "loopx/capabilities/issue_fix/candidate_evidence.py"
MAINTAINER_SET = frozenset({"COLLABORATOR", "MEMBER", "OWNER"})


def module_source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _collection_members(node: ast.expr) -> frozenset[str] | None:
    """Fold a set / frozenset / list / tuple of plain string literals to a member set."""

    if isinstance(node, ast.Call):
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname in {"frozenset", "set"} and len(node.args) == 1:
            return _collection_members(node.args[0])
        return None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(values) == len(node.elts):
            return frozenset(values)
    return None


def _restatement_sites(source: str) -> list[int]:
    """Lines where a collection literal denotes exactly the maintainer set."""

    tree = ast.parse(source)
    # A frozenset(...)/set(...) call and its collection argument are one literal;
    # count the call, not both nodes, so a wrapped set is not reported twice.
    wrapped = {
        id(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
        in {"frozenset", "set"}
        and len(node.args) == 1
    }
    hits: list[int] = []
    for node in ast.walk(tree):
        if id(node) in wrapped:
            continue
        members = _collection_members(node)
        if members is not None and members == MAINTAINER_SET:
            hits.append(node.lineno)
    return hits


def test_only_the_owner_states_the_maintainer_association_set() -> None:
    offenders: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == OWNER_PATH:
            continue
        lines = _restatement_sites(path.read_text(encoding="utf-8"))
        if lines:
            offenders[rel] = lines
    assert not offenders, f"the maintainer association set is restated: {offenders}"


def test_the_owner_file_states_the_set_exactly_once() -> None:
    # Without the path exemption the census would flag the owner itself; this
    # pins that the exemption is load-bearing and that there is one definition.
    assert len(_restatement_sites(module_source(OWNER_PATH))) == 1


def test_both_consumers_borrow_the_owner_object() -> None:
    owner_object = github_public.MAINTAINER_ASSOCIATIONS
    assert candidate_evidence.MAINTAINER_ASSOCIATIONS is owner_object
    assert pr_lifecycle.MAINTAINER_ASSOCIATIONS is owner_object
    assert not hasattr(candidate_evidence, "_MAINTAINER_ASSOCIATIONS")
    assert owner_object == {"COLLABORATOR", "MEMBER", "OWNER"}


def test_the_candidate_reader_loads_the_borrowed_name_and_binds_neither() -> None:
    tree = ast.parse(module_source(CANDIDATE_PATH))
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert "MAINTAINER_ASSOCIATIONS" in loaded
    assert "_MAINTAINER_ASSOCIATIONS" not in loaded
    bound = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "MAINTAINER_ASSOCIATIONS" not in bound
    assert "_MAINTAINER_ASSOCIATIONS" not in bound


# --- behavioural truth table: timing held fixed, association is the deciding factor -----

ANCHOR_URL = "https://github.com/o/r/issues/1#issuecomment-anchor"
REPLY_URL = "https://github.com/o/r/issues/1#issuecomment-reply"
ANCHOR_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signal(
    association: str,
    *,
    created_at: str = "2026-01-02T00:00:00Z",
    url: str = REPLY_URL,
) -> dict:
    return github_public._normalise_comment_signal(
        {"author_association": association, "url": url, "created_at": created_at},
        anchor_url=ANCHOR_URL,
        anchor_created_at=ANCHOR_AT,
    )


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_a_maintainer_reply_after_the_anchor_is_a_signal(association: str) -> None:
    assert _signal(association)["is_maintainer_signal"] is True


@pytest.mark.parametrize("association", ["CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR"])
def test_a_non_maintainer_reply_after_the_anchor_is_not(association: str) -> None:
    assert _signal(association)["is_maintainer_signal"] is False


def test_timing_and_anchor_still_gate_the_signal() -> None:
    # A maintainer association is necessary but not sufficient: before the anchor,
    # or the anchor comment itself, is not a reply signal. This keeps the truth
    # table from degenerating into an always-true assertion on the member set.
    assert _signal("OWNER", created_at="2025-12-31T00:00:00Z")["is_maintainer_signal"] is False
    assert _signal("OWNER", url=ANCHOR_URL)["is_maintainer_signal"] is False


# --- poison controls: the census must bite a restatement and leave the neighbours -------


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("renamed set restatement", 'ALLOWED = {"COLLABORATOR", "MEMBER", "OWNER"}\n', 1),
        (
            "inline membership literal",
            'def f(a):\n    return a in {"COLLABORATOR", "MEMBER", "OWNER"}\n',
            1,
        ),
        ("frozenset call, reordered", 'A = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})\n', 1),
        ("list form", 'A = ["OWNER", "MEMBER", "COLLABORATOR"]\n', 1),
        ("tuple form", 'A = ("OWNER", "MEMBER", "COLLABORATOR")\n', 1),
        (
            "the four-member reviewer set is a different question",
            'A = {"OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"}\n',
            0,
        ),
        ("a two-member subset", 'A = {"OWNER", "MEMBER"}\n', 0),
        ("import from the owner", "from .github_public import MAINTAINER_ASSOCIATIONS\n", 0),
        ("the words inside prose", 'MSG = "MEMBER/OWNER/COLLABORATOR association"\n', 0),
    ],
    ids=[
        "renamed-set",
        "inline-membership",
        "frozenset-call",
        "list-form",
        "tuple-form",
        "four-member-neighbour",
        "two-member-subset",
        "import",
        "prose",
    ],
)
def test_the_census_bites_a_restatement_and_leaves_the_neighbours(
    label: str, source: str, expected: int
) -> None:
    assert len(_restatement_sites(source)) == expected, label