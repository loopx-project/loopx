"""Guard one owner for the model-behavior actor-result field set (#4447).

``_ACTOR_RESULT_FIELDS`` in ``model_behavior_qualification.py`` decides which
keys an actor result may carry. ``onboarding_model_behavior_qualification.py``
restated the same four keys under a different name (``_RESULT_FIELDS``) while
*already importing* three private helpers from that owner -- the strongest form
of this repository's duplicate-knowledge case: a module restating a constant
whose owner it already imports from. It now borrows the owner object and the
restated name is retired.

The by-name semantic inventory cannot see this pair: ``_RESULT_FIELDS`` is also
bound in ``issue_fix/repository_memory.py`` and ``extensions/capability_admission.py``
to *different* value sets, so by name it is a fork, not a twin, and the equal
value set shared with ``_ACTOR_RESULT_FIELDS`` is invisible to a name-keyed scan.
This guard keys on the **folded value** instead, so a renamed restatement is
caught and a same-name-different-value neighbour is never swept.

Enforcement is scoped to the two actual validation entry points: the owner
states the set exactly once and the consumer borrows it. A whole-package
folded-value census is kept only as **advisory** (RFC
``semantic-vocabulary-convergence-v0`` ``same_value_candidates_advisory``):
independent same-value data -- e.g. an ordered ``preview_columns()`` presentation
tuple -- is reported for review, never an enforcing failure, because reuse
requires the same semantic slot, scope and contract, not merely the same
members. Behavioural parity through each module's own normalizer still pins the
accepted keys, so a migration that silently changed them fails here.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

from loopx.control_plane.testing import (
    model_behavior_qualification as owner,
    onboarding_model_behavior_qualification as consumer,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/control_plane/testing/model_behavior_qualification.py"
CONSUMER_PATH = "loopx/control_plane/testing/onboarding_model_behavior_qualification.py"

FIELDS = frozenset({"schema_version", "actor_ref", "decision", "tool_calls"})


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


def _field_literal_sites(source: str) -> list[int]:
    """Every collection literal in the source folding to FIELDS, counted once.

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
        if id(node) not in wrapped and _members(node) == FIELDS
    ]


def _entry_point_offenders() -> dict[str, list[int]]:
    """Enforce ownership only at the two actual validation entry points.

    The owner may state the set exactly once; the consumer must borrow it and
    state it nowhere. Same-value data in any other module is advisory, never an
    enforcing offender -- an ordered presentation tuple is not a field set.
    """

    offenders: dict[str, list[int]] = {}
    owner_sites = _field_literal_sites((REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8"))
    if len(owner_sites) != 1:
        offenders[OWNER_PATH] = owner_sites
    consumer_sites = _field_literal_sites(
        (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    )
    if consumer_sites:
        offenders[CONSUMER_PATH] = consumer_sites
    return offenders


def _package_same_value_candidates() -> dict[str, list[int]]:
    """Advisory whole-package census: report same-value sites for review only."""

    candidates: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER_PATH:
            continue
        sites = _field_literal_sites(path.read_text(encoding="utf-8"))
        if sites:
            candidates[relative] = sites
    return candidates


def test_only_the_two_entry_points_are_enforced() -> None:
    assert _entry_point_offenders() == {}, (
        f"a restatement remains at an enforced validation entry point: {_entry_point_offenders()}"
    )


def test_same_value_candidates_outside_the_entry_points_are_advisory() -> None:
    # RFC same_value_candidates_advisory: independent same-value data (e.g. an
    # ordered preview_columns() presentation tuple) is reported for review, not
    # an enforcing failure. Only the consumer entry point is enforced here; the
    # rest of the package is deliberately NOT asserted empty.
    candidates = _package_same_value_candidates()
    assert CONSUMER_PATH not in candidates, (
        f"the consumer entry point must borrow, not restate: {candidates.get(CONSUMER_PATH)}"
    )


def test_the_owner_states_the_recorded_members_once() -> None:
    assert frozenset(owner._ACTOR_RESULT_FIELDS) == FIELDS
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_field_literal_sites(source)) == 1


def test_the_consumer_borrows_the_owner_object() -> None:
    assert consumer._ACTOR_RESULT_FIELDS is owner._ACTOR_RESULT_FIELDS


def test_the_consumer_retired_its_restated_name() -> None:
    assert not hasattr(consumer, "_RESULT_FIELDS")
    source = (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    assert _field_literal_sites(source) == []
    assert "_ACTOR_RESULT_FIELDS" in source


# --- behavioural parity through each module's own normalizer ----------------------------


def test_both_normalizers_reject_the_same_unknown_fields() -> None:
    raw = {key: None for key in FIELDS}
    raw.update({"bogus": 1, "extra": 2})
    with pytest.raises(ValueError) as owner_error:
        owner.normalize_model_behavior_actor_result(raw)
    with pytest.raises(ValueError) as consumer_error:
        consumer.normalize_onboarding_model_behavior_actor_result(raw, phase="entry")
    expected = "unknown actor result field(s): bogus, extra"
    assert str(owner_error.value) == expected
    assert str(consumer_error.value) == expected


def test_the_four_owner_fields_are_not_unknown_to_either() -> None:
    # Positive control: with exactly the four keys present, neither normalizer
    # reports an unknown field (each fails later on schema_version instead), so
    # the accepted-key set is pinned identical, not just the rejection path.
    raw = {key: None for key in FIELDS}
    with pytest.raises(ValueError) as owner_error:
        owner.normalize_model_behavior_actor_result(raw)
    assert "unknown actor result field" not in str(owner_error.value)
    with pytest.raises(ValueError) as consumer_error:
        consumer.normalize_onboarding_model_behavior_actor_result(raw, phase="entry")
    assert "unknown actor result field" not in str(consumer_error.value)


# --- the census bites every restatement form and leaves the others ----------------------

POISON_CORPUS = (
    ("bare set restatement", 'X = {"schema_version", "actor_ref", "decision", "tool_calls"}\n', 1),
    ("frozenset call", 'X = frozenset({"schema_version", "actor_ref", "decision", "tool_calls"})\n', 1),
    ("set call over a list", 'X = set(["schema_version", "actor_ref", "decision", "tool_calls"])\n', 1),
    ("list literal", 'X = ["schema_version", "actor_ref", "decision", "tool_calls"]\n', 1),
    ("tuple literal", 'X = ("schema_version", "actor_ref", "decision", "tool_calls")\n', 1),
    ("renamed binding is still caught", '_ANY = {"schema_version", "actor_ref", "decision", "tool_calls"}\n', 1),
    ("nested inside a function", 'def f():\n    return {"schema_version", "actor_ref", "decision", "tool_calls"}\n', 1),
    ("importing the owner is not a restatement", "from .m import _ACTOR_RESULT_FIELDS\n", 0),
    ("alias rebind is not a restatement", "_RESULT_FIELDS = _ACTOR_RESULT_FIELDS\n", 0),
    ("a different field set is not this one", '_DECISION_FIELDS = {"schema_version", "phase", "next_action"}\n', 0),
    ("a superset is not this set", 'X = {"schema_version", "actor_ref", "decision", "tool_calls", "extra"}\n', 0),
)


@pytest.mark.parametrize(
    "label,source,expected", POISON_CORPUS, ids=[case[0] for case in POISON_CORPUS]
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_field_literal_sites(source)) == expected, label


# --- enforcement distinguishes a real consumer restatement from independent data --------


def test_enforcement_bites_a_consumer_restatement_not_independent_presentation(
    tmp_path, monkeypatch
) -> None:
    """The reviewer's preview_columns() case: an ordered presentation tuple of the
    same four strings is legitimate independent data, not a field-set definition.
    Enforcement must catch a consumer restatement yet never fail on that tuple;
    the advisory census reports it for review without enforcing.
    """

    module = sys.modules[__name__]
    owner_rel = "loopx/control_plane/testing/model_behavior_qualification.py"
    consumer_rel = "loopx/control_plane/testing/onboarding_model_behavior_qualification.py"
    presentation_rel = "loopx/control_plane/testing/preview.py"
    owner_file = tmp_path / owner_rel
    consumer_file = tmp_path / consumer_rel
    presentation_file = tmp_path / presentation_rel
    for file in (owner_file, consumer_file, presentation_file):
        file.parent.mkdir(parents=True, exist_ok=True)
    owner_file.write_text(
        '_ACTOR_RESULT_FIELDS = {"schema_version", "actor_ref", "decision", "tool_calls"}\n',
        encoding="utf-8",
    )
    presentation_file.write_text(
        "def preview_columns():\n"
        '    return ("schema_version", "actor_ref", "decision", "tool_calls")\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PACKAGE_ROOT", tmp_path / "loopx")
    monkeypatch.setattr(module, "OWNER_PATH", owner_rel)
    monkeypatch.setattr(module, "CONSUMER_PATH", consumer_rel)

    # A real consumer restatement is enforced; the independent presentation tuple
    # is present in the same package but is never an enforcing offender.
    consumer_file.write_text(
        '_RESULT_FIELDS = {"schema_version", "actor_ref", "decision", "tool_calls"}\n',
        encoding="utf-8",
    )
    assert _entry_point_offenders() == {consumer_rel: [1]}
    assert presentation_rel not in _entry_point_offenders()
    candidates = _package_same_value_candidates()
    assert candidates.get(consumer_rel) == [1]
    assert candidates.get(presentation_rel) == [2]  # advisory only, non-failing

    # Once the consumer borrows the owner object, enforcement is clean while the
    # independent presentation tuple stays an advisory candidate, not a failure.
    consumer_file.write_text(
        "from .model_behavior_qualification import _ACTOR_RESULT_FIELDS\n",
        encoding="utf-8",
    )
    assert _entry_point_offenders() == {}
    assert presentation_rel not in _entry_point_offenders()
    assert _package_same_value_candidates().get(presentation_rel) == [2]