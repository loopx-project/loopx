"""Guard one owner for the issue-fix close event-type set (#4447).

``_ISSUE_CLOSE_EVENT_TYPES`` in ``metrics_supplement.py`` decides which event
types count as issue-close activity (``issue_close_recommended`` /
``issue_close_request_published`` / ``issue_closed_observed`` /
``issue_reopened_observed``). The supplement builds and filters the activity by
this set; ``metrics_projection.py`` only validates the compacted rows it receives
against the *same* four types, yet restated them inline as a function-local
``known_types`` set. The projection *already imports* private names from the
supplement (``_SUPPLEMENT_FIELDS``), so borrowing one more owner object adds no
new import edge and cannot introduce a cycle. The inline set is retired and the
validation now references the single owner.

The by-name semantic inventory cannot see this: the owner is a private
``_``-binding (the inventory only catalogues ``UPPER_NAME`` carriers) and the
restatement was an anonymous function-local under a different name. This guard
keys on the **folded value** instead, so a renamed or anonymous inline
restatement is caught, while the genuinely different neighbour -- the nine-member
``_EVENT_TYPES`` vocabulary that supersedes this set -- is never swept.

Two layers: a whole-tree folded-value census (only the owner states the set), and
behavioural parity through the projection's own ``_issue_close_activity`` gate,
parametrised over the owner's actual membership, so a migration that silently
changed which event types count as close activity fails here.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from loopx.capabilities.issue_fix import (
    metrics_projection as projection_mod,
    metrics_supplement as owner,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/capabilities/issue_fix/metrics_supplement.py"
PROJECTION_PATH = "loopx/capabilities/issue_fix/metrics_projection.py"

ISSUE_CLOSE = frozenset(
    {
        "issue_close_recommended",
        "issue_close_request_published",
        "issue_closed_observed",
        "issue_reopened_observed",
    }
)
EVENT_VOCABULARY = frozenset(
    {
        "capability_gap",
        "duplicate_external_write",
        "first_push_ci",
        "human_intervention",
        "issue_close_recommended",
        "issue_close_request_published",
        "issue_closed_observed",
        "issue_reopened_observed",
        "useful_public_comment",
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


def _close_type_literal_sites(source: str) -> list[int]:
    """Every collection literal in the source folding to ISSUE_CLOSE, counted once.

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
        if id(node) not in wrapped and _members(node) == ISSUE_CLOSE
    ]


@pytest.fixture(scope="module")
def tree_offenders() -> dict[str, list[int]]:
    offenders: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER_PATH:
            continue
        sites = _close_type_literal_sites(path.read_text(encoding="utf-8"))
        if sites:
            offenders[relative] = sites
    return offenders


def test_only_the_owner_states_the_close_set(tree_offenders) -> None:
    assert tree_offenders == {}, (
        f"a restatement of the issue-close event types remains: {tree_offenders}"
    )


def test_the_owner_states_the_recorded_members_once() -> None:
    assert frozenset(owner._ISSUE_CLOSE_EVENT_TYPES) == ISSUE_CLOSE
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_close_type_literal_sites(source)) == 1


def test_projection_borrows_the_owner_object() -> None:
    assert projection_mod._ISSUE_CLOSE_EVENT_TYPES is owner._ISSUE_CLOSE_EVENT_TYPES


def test_projection_retired_its_inline_set() -> None:
    source = (REPO_ROOT / PROJECTION_PATH).read_text(encoding="utf-8")
    assert _close_type_literal_sites(source) == []
    assert "_ISSUE_CLOSE_EVENT_TYPES" in source
    assert "known_types" not in source


def test_the_event_vocabulary_is_a_strict_superset() -> None:
    # The full event vocabulary is a superset, not the close set; it stays its
    # own owner in metrics_supplement.py and is deliberately left untouched.
    assert frozenset(owner._EVENT_TYPES) == EVENT_VOCABULARY
    assert ISSUE_CLOSE < EVENT_VOCABULARY
    assert ISSUE_CLOSE != EVENT_VOCABULARY


# --- behavioural parity through the projection's own close-activity gate ----------------


def _activity_value(event_type: str, *, evidence_url: str | None = None) -> dict:
    event = {
        "event_id": "evt-1",
        "event_type": event_type,
        "occurred_at": "2026-07-25T17:30:00+00:00",
    }
    if evidence_url is not None:
        event["evidence_url"] = evidence_url
    return {
        "issue_close_activity": [
            {
                "schema_version": "issue_fix_issue_close_activity_v0",
                "issue_ref": "owner/repo#1",
                "events": [event],
            }
        ]
    }


@pytest.mark.parametrize("event_type", sorted(owner._ISSUE_CLOSE_EVENT_TYPES))
def test_projection_accepts_every_owner_close_type(event_type: str) -> None:
    url = (
        None
        if event_type == "issue_close_recommended"
        else "https://example.com/evidence"
    )
    activity, _ = projection_mod._issue_close_activity(
        _activity_value(event_type, evidence_url=url)
    )
    assert len(activity) == 1
    assert activity[0]["events"][0]["event_type"] == event_type


@pytest.mark.parametrize(
    "event_type", sorted(owner._EVENT_TYPES - owner._ISSUE_CLOSE_EVENT_TYPES)
)
def test_projection_rejects_types_outside_the_close_set(event_type: str) -> None:
    # These are valid events in the broader vocabulary but not close activity:
    # the projection's accept/reject boundary is exactly the owner's set, which
    # it now shares by identity rather than restating.
    with pytest.raises(ValueError):
        projection_mod._issue_close_activity(
            _activity_value(event_type, evidence_url="https://example.com/evidence")
        )


# --- the census bites every restatement form and leaves the others ----------------------

POISON_CORPUS = (
    ("bare set restatement", 'X = {"issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed"}\n', 1),
    ("frozenset call", 'X = frozenset({"issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed"})\n', 1),
    ("set call over a list", 'X = set(["issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed"])\n', 1),
    ("list literal", 'X = ["issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed"]\n', 1),
    ("tuple literal", 'X = ("issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed")\n', 1),
    ("reordered members fold the same", 'X = {"issue_reopened_observed", "issue_closed_observed", "issue_close_request_published", "issue_close_recommended"}\n', 1),
    ("renamed binding is still caught", '_ANY = {"issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed"}\n', 1),
    ("nested inside a function", 'def f():\n    return {"issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed"}\n', 1),
    ("importing the owner is not a restatement", "from .m import _ISSUE_CLOSE_EVENT_TYPES\n", 0),
    ("alias rebind is not a restatement", "known_types = _ISSUE_CLOSE_EVENT_TYPES\n", 0),
    ("the broader event vocabulary is not this set", 'X = {"capability_gap", "duplicate_external_write", "first_push_ci", "human_intervention", "issue_close_recommended", "issue_close_request_published", "issue_closed_observed", "issue_reopened_observed", "useful_public_comment"}\n', 0),
    ("the count-field pair is not this set", 'X = {"issue_close_recommendations", "issue_close_requests_published"}\n', 0),
    ("a subset is not this set", 'X = {"issue_close_recommended", "issue_closed_observed"}\n', 0),
    ("a single event type is not this set", 'X = {"issue_close_recommended"}\n', 0),
)


@pytest.mark.parametrize(
    "label,source,expected", POISON_CORPUS, ids=[case[0] for case in POISON_CORPUS]
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_close_type_literal_sites(source)) == expected, label