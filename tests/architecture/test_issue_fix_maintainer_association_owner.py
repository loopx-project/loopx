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

The guard mirrors this repository's other single-owner censuses, scoped to the
capability that owns the contract:

1. a **capability-scoped member census**: no Python module under
   ``loopx/capabilities/issue_fix`` may state a collection literal whose members
   are exactly this set, except the owner. It folds set / frozenset / list / tuple
   literals, so a renamed restatement and an inline ``x in {...}`` are the same
   offender as the original constant. The scope is the capability, not the whole
   package: these three words are also ordinary ordered display labels elsewhere
   in ``loopx``, and a package-wide scan would sweep those unrelated tuples;
2. **identity**: both consumers hold the owner's object, not a copy;
3. an **AST reader check**: ``candidate_evidence`` loads the borrowed name and
   binds neither the public nor the retired private name;
4. **behavioural truth tables through both real gates**. The candidate builder
   ``candidate_evidence.build_public_github_candidate_preflight_input`` is driven
   with synthetic provider comments: only the three members produce maintainer
   evidence, a non-member (notably ``CONTRIBUTOR``) produces none, and a member
   comment missing its ``updatedAt`` revision is rejected. This is the gate a
   deliberate ``or association == "CONTRIBUTOR"`` expansion would slip past a
   static-only check. The ``github_public._normalise_comment_signal`` table holds
   timing fixed, so association membership -- not the clock -- flips the signal,
   and a maintainer reply before the anchor still is not a signal.

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
ISSUE_FIX_ROOT = PACKAGE_ROOT / "capabilities" / "issue_fix"
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
    # Scoped to the issue-fix capability that owns the contract (owner +
    # candidate_evidence + pr_lifecycle). A package-wide scan would also sweep
    # unrelated ordered display labels elsewhere in loopx that list these words.
    offenders: dict[str, list[int]] = {}
    for path in sorted(ISSUE_FIX_ROOT.rglob("*.py")):
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


# --- behavioural truth table through the REAL candidate builder -------------------------
#
# build_public_github_candidate_preflight_input is the gate that decides which
# provider comments become maintainer evidence. Driving it directly -- rather than
# an isolated copy -- means a deliberate expansion of the predicate at the
# candidate gate (e.g. `or association == "CONTRIBUTOR"`) is caught here even though
# every static census and the owner truth table still pass.

CANDIDATE_REPO = "volcengine/OpenViking"
CANDIDATE_ISSUE = "#3305"
CANDIDATE_GENERATED_AT = "2026-07-30T12:00:00Z"
CANDIDATE_COMMENT_URL = (
    "https://github.com/volcengine/OpenViking/issues/3305#issuecomment-1"
)


def _candidate_source_receipts() -> dict[str, dict[str, object]]:
    return {
        field: {
            "provider": "github_graphql",
            "observed_at": CANDIDATE_GENERATED_AT,
            "query_fingerprint": f"sha256:{field}",
            "page_count": 1,
            "result_count": 0,
            "complete": True,
            "truncated": False,
            "raw_provider_payload_captured": False,
        }
        for field in (
            "numeric_pr_evidence",
            "semantic_pr_evidence",
            "maintainer_comment_evidence",
        )
    }


def _candidate_comment_rows(
    association: str,
    *,
    updated_at: str | None = CANDIDATE_GENERATED_AT,
) -> list[dict]:
    comment: dict[str, object] = {
        "authorAssociation": association,
        "url": CANDIDATE_COMMENT_URL,
    }
    if updated_at is not None:
        comment["updatedAt"] = updated_at
    payload = candidate_evidence.build_public_github_candidate_preflight_input(
        repo=CANDIDATE_REPO,
        issue_ref=CANDIDATE_ISSUE,
        issue_state="OPEN",
        closing_pull_requests=[],
        cross_referenced_pull_requests=[],
        maintainer_comments=[comment],
        generated_at=CANDIDATE_GENERATED_AT,
        source_receipts=_candidate_source_receipts(),
    )
    return payload["maintainer_comment_evidence"]["rows"]


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_candidate_builder_admits_a_maintainer_comment_at_its_current_revision(
    association: str,
) -> None:
    assert _candidate_comment_rows(association) == [
        {"ref": CANDIDATE_COMMENT_URL, "revision": CANDIDATE_GENERATED_AT}
    ]


@pytest.mark.parametrize("association", ["CONTRIBUTOR", "NONE", "FIRST_TIME_CONTRIBUTOR"])
def test_candidate_builder_rejects_a_non_maintainer_comment(association: str) -> None:
    # Through the real builder: an `or association == "CONTRIBUTOR"` expansion at
    # the candidate gate would turn this empty result into a row.
    assert _candidate_comment_rows(association) == []


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
@pytest.mark.parametrize("updated_at", [None, ""], ids=["missing", "empty"])
def test_candidate_builder_requires_the_current_comment_revision(
    association: str, updated_at: str | None
) -> None:
    with pytest.raises(ValueError, match="updatedAt is required"):
        _candidate_comment_rows(association, updated_at=updated_at)


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