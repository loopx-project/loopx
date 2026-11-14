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

Enforcement is keyed on the **field binding**, not on every same-value literal
in the two validation entry-point files: the owner binds ``_ACTOR_RESULT_FIELDS``
exactly once at module level, and the consumer borrows that object (identity) and
binds no field set of its own. The two normalizers' accepted keys are pinned by
behavioural parity, so a migration that silently changed them fails here.

Every other same-value literal -- including independent data in the *same* file,
e.g. an ordered ``preview_columns()`` presentation tuple -- is **advisory** only
(RFC ``semantic-vocabulary-convergence-v0`` ``same_value_candidates_advisory``):
a whole-package folded-value census reports it for review, never an enforcing
failure, because reuse requires the same semantic slot, scope and contract, not
merely the same members. Such data need not be moved or rewritten.
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
OWNER_BINDING = "_ACTOR_RESULT_FIELDS"
CONSUMER_BINDINGS = ("_ACTOR_RESULT_FIELDS", "_RESULT_FIELDS")


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


def _binding_sites(source: str, names: tuple[str, ...]) -> list[int]:
    """Module-level assignments to one of ``names`` whose value folds to FIELDS.

    Only a module-level binding can restate the field set; a literal inside a
    function body (e.g. an ordered ``preview_columns()`` presentation tuple) is
    independent data, not a binding, and is left to the advisory census.
    """

    sites: list[int] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if _members(value) != FIELDS:
            continue
        if any(isinstance(t, ast.Name) and t.id in names for t in targets):
            sites.append(node.lineno)
    return sites


def _entry_point_offenders() -> dict[str, list[int]]:
    """Enforce only the field binding at the two validation entry points.

    The owner binds ``_ACTOR_RESULT_FIELDS`` exactly once; the consumer borrows
    it and binds no field set of its own. Any other same-value literal -- in
    these files or anywhere else -- is advisory, never an enforcing offender.
    """

    offenders: dict[str, list[int]] = {}
    owner_sites = _binding_sites(
        (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8"), (OWNER_BINDING,)
    )
    if len(owner_sites) != 1:
        offenders[OWNER_PATH] = owner_sites
    consumer_sites = _binding_sites(
        (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8"), CONSUMER_BINDINGS
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


def test_the_advisory_census_is_review_only() -> None:
    # RFC same_value_candidates_advisory: the whole-package census reports
    # same-value sites for review and never feeds enforcement. The owner is the
    # legitimate source, so it is excluded; independent same-value data anywhere
    # else -- including the consumer file -- may be reported without failing.
    candidates = _package_same_value_candidates()
    assert OWNER_PATH not in candidates
    assert _entry_point_offenders() == {}


def test_the_owner_states_the_recorded_members_once() -> None:
    assert frozenset(owner._ACTOR_RESULT_FIELDS) == FIELDS
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_binding_sites(source, (OWNER_BINDING,))) == 1


def test_the_consumer_borrows_the_owner_object() -> None:
    assert consumer._ACTOR_RESULT_FIELDS is owner._ACTOR_RESULT_FIELDS


def test_the_consumer_retired_its_restated_name() -> None:
    assert not hasattr(consumer, "_RESULT_FIELDS")
    source = (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    assert _binding_sites(source, CONSUMER_BINDINGS) == []
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


# --- enforcement distinguishes a field-binding restatement from independent data --------

OWNER_BINDING_SRC = (
    '_ACTOR_RESULT_FIELDS = {"schema_version", "actor_ref", "decision", "tool_calls"}\n'
)
CONSUMER_BORROW_SRC = "from .model_behavior_qualification import _ACTOR_RESULT_FIELDS\n"
# The reviewer's counterexample: an ordered presentation tuple of the same four
# strings (in the reviewer's order), used only for table column ordering.
PRESENTATION_SRC = (
    "def preview_columns():\n"
    '    return ("actor_ref", "decision", "tool_calls", "schema_version")\n'
)


def _setup_fixture(tmp_path, monkeypatch):
    module = sys.modules[__name__]
    presentation_rel = "loopx/control_plane/testing/preview.py"
    owner_file = tmp_path / OWNER_PATH
    consumer_file = tmp_path / CONSUMER_PATH
    presentation_file = tmp_path / presentation_rel
    for file in (owner_file, consumer_file, presentation_file):
        file.parent.mkdir(parents=True, exist_ok=True)
    owner_file.write_text(OWNER_BINDING_SRC, encoding="utf-8")
    consumer_file.write_text(CONSUMER_BORROW_SRC, encoding="utf-8")
    presentation_file.write_text(PRESENTATION_SRC, encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "PACKAGE_ROOT", tmp_path / "loopx")
    return owner_file, consumer_file, presentation_file, presentation_rel


def test_same_file_presentation_data_is_advisory_not_enforcing(tmp_path, monkeypatch) -> None:
    """The reviewer's preview_columns() case: the same ordered presentation tuple
    is legitimate independent data whether it lives in a third module or inside
    the owner / consumer file itself. It must never become an enforcing offender;
    the advisory census reports it for review without failing.
    """

    owner_file, consumer_file, _presentation_file, presentation_rel = _setup_fixture(
        tmp_path, monkeypatch
    )
    assert _entry_point_offenders() == {}
    assert _package_same_value_candidates().get(presentation_rel) == [2]

    # The identical function moved INTO the owner file: still exactly one field
    # binding, so enforcement stays clean.
    owner_file.write_text(OWNER_BINDING_SRC + PRESENTATION_SRC, encoding="utf-8")
    assert _entry_point_offenders() == {}
    assert len(_binding_sites(owner_file.read_text(encoding="utf-8"), (OWNER_BINDING,))) == 1

    # ...and into the consumer file: advisory only, reported by the census.
    consumer_file.write_text(CONSUMER_BORROW_SRC + PRESENTATION_SRC, encoding="utf-8")
    assert _entry_point_offenders() == {}
    assert _package_same_value_candidates().get(CONSUMER_PATH) == [3]


def test_enforcement_bites_a_consumer_field_binding_restatement(tmp_path, monkeypatch) -> None:
    _owner_file, consumer_file, _presentation_file, _presentation_rel = _setup_fixture(
        tmp_path, monkeypatch
    )
    consumer_file.write_text(
        '_RESULT_FIELDS = {"schema_version", "actor_ref", "decision", "tool_calls"}\n',
        encoding="utf-8",
    )
    assert _entry_point_offenders() == {CONSUMER_PATH: [1]}
    assert _package_same_value_candidates().get(CONSUMER_PATH) == [1]

    # A same-name restatement bites too (it also breaks object identity).
    consumer_file.write_text(OWNER_BINDING_SRC, encoding="utf-8")
    assert _entry_point_offenders() == {CONSUMER_PATH: [1]}

    # Borrowing the owner object clears enforcement.
    consumer_file.write_text(CONSUMER_BORROW_SRC, encoding="utf-8")
    assert _entry_point_offenders() == {}


def test_renamed_same_value_binding_is_advisory_but_owner_double_binding_bites(
    tmp_path, monkeypatch
) -> None:
    owner_file, consumer_file, _presentation_file, _presentation_rel = _setup_fixture(
        tmp_path, monkeypatch
    )
    # A renamed module-level same-value binding is independent data, not the field
    # binding: advisory only (RFC same_value_candidates_advisory).
    consumer_file.write_text(
        CONSUMER_BORROW_SRC
        + '_PREVIEW_COLUMNS = ("actor_ref", "decision", "tool_calls", "schema_version")\n',
        encoding="utf-8",
    )
    assert _entry_point_offenders() == {}
    assert _package_same_value_candidates().get(CONSUMER_PATH) == [2]

    # The owner must bind the field set exactly once; a second binding bites.
    owner_file.write_text(OWNER_BINDING_SRC + OWNER_BINDING_SRC, encoding="utf-8")
    assert _entry_point_offenders() == {OWNER_PATH: [1, 2]}