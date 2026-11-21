"""The mapping-read accessor set has one owner across the semantic scans.

``field_use`` (RFC B3) and ``consumer_report`` (RFC B5) both classify syntactic
reads of a named payload field, and both used to decide independently which
method names count as an unambiguous mapping read: ``get`` and ``pop``.  The
two lists agreed, but a new accessor -- or a dropped one -- had two places to
land, and the retirement metric and the consumer evidence would disagree about
the same ``payload.get(...)`` call if only one side moved.

``consumer_report`` keeps the binding private on purpose: its module-level
comment says a public ``NAME = (...)`` of string literals is itself an
inventory carrier, and the module must not add a closed-set carrier to the tree
it measures.  The fold therefore imports the owner's object *as* the private
name instead of renaming the binding, and the inventory sees no new carrier:
it counts only public uppercase assignments, never imports.

Two layers catch different regressions.  The census compares the listed
*value* at any depth and under any binding name, so a renamed or reordered
local copy fails it.  The wiring cases then prove both scan faces really read
the owner: ``get``/``pop`` are reads on both sides, ``setdefault`` stays a
write rather than becoming a read, an unrelated method is neither, and a
computed key stays an unresolved read on both sides instead of disappearing.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from loopx.semantics import consumer_report, field_use
from loopx.semantics.inventory import SourceFile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "loopx" / "semantics"
OWNER_PATH = "loopx/semantics/field_use.py"
CONSUMER_PATH = "loopx/semantics/consumer_report.py"

FIELD = "example_slot"
FIELDS = frozenset({FIELD})


def _rel(path: pathlib.PurePath) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _package_sources() -> list[pathlib.Path]:
    return sorted(p for p in PACKAGE.glob("*.py") if "__pycache__" not in p.parts)


def _set_members(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Call) and node.args:
        node = node.args[0]
    if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return None
    return tuple(sorted(ast.unparse(element) for element in node.elts))


def _accessor_set_definitions(members: frozenset[str]) -> list[str]:
    """Sites listing these accessors at any depth, in any order, under any name."""

    wanted = tuple(sorted(f"'{value}'" for value in members))
    sites: set[str] = set()
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _set_members(node) == wanted:
                sites.add(_rel(path))
    return sorted(sites)


def _source(text: str) -> SourceFile:
    return SourceFile(path="loopx/control_plane/probe.py", suffix=".py", text=text)


def test_the_owner_states_exactly_the_two_unambiguous_accessors() -> None:
    assert field_use.MAPPING_READ_CALLS == frozenset({"get", "pop"})


def test_the_accessor_set_is_listed_once_in_the_semantics_package() -> None:
    sites = _accessor_set_definitions(field_use.MAPPING_READ_CALLS)
    assert sites == [OWNER_PATH], (
        f"the mapping-read accessors are listed in {sites}; "
        "a renamed or reordered copy counts as a second owner too"
    )


def test_consumer_report_holds_the_owner_object_not_a_copy() -> None:
    assert consumer_report._MAPPING_READS is field_use.MAPPING_READ_CALLS


def test_consumer_report_imports_the_set_instead_of_assigning_it() -> None:
    tree = ast.parse((REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        assert "_MAPPING_READS" not in names, (
            "consumer_report assigns _MAPPING_READS again; import the owner's object"
        )


@pytest.mark.parametrize("accessor", sorted(field_use.MAPPING_READ_CALLS))
def test_field_use_face_reads_the_owner_accessors(accessor: str) -> None:
    found, dynamic = field_use.python_module_scan(
        ast.parse(f'payload.{accessor}("{FIELD}")'), FIELDS
    )
    assert found[FIELD] == {"mapping_call_read"}
    assert dynamic == 0


def test_field_use_face_keeps_writes_and_strangers_out_of_the_read_set() -> None:
    found, dynamic = field_use.python_module_scan(
        ast.parse(f'payload.setdefault("{FIELD}", 1)'), FIELDS
    )
    assert found[FIELD] == {"mapping_call_write"}
    assert dynamic == 0
    found, dynamic = field_use.python_module_scan(
        ast.parse(f'payload.fetch("{FIELD}")'), FIELDS
    )
    assert "mapping_call_read" not in found.get(FIELD, set())
    assert dynamic == 0


@pytest.mark.parametrize("accessor", sorted(field_use.MAPPING_READ_CALLS))
def test_consumer_report_face_reads_the_owner_accessors(accessor: str) -> None:
    rows = consumer_report.scan_python_consumers(
        _source(f'payload.{accessor}("{FIELD}")'),
        vocabulary="example_vocabulary",
        slot=FIELD,
        values=("alpha", "beta"),
    )
    assert [(row.role, row.anchor, row.blocker) for row in rows] == [
        ("read", "slot_read", None)
    ]


@pytest.mark.parametrize("accessor", ["setdefault", "fetch"])
def test_consumer_report_face_keeps_writes_and_strangers_out_of_the_read_set(
    accessor: str,
) -> None:
    rows = consumer_report.scan_python_consumers(
        _source(f'payload.{accessor}("{FIELD}", 1)'),
        vocabulary="example_vocabulary",
        slot=FIELD,
        values=("alpha", "beta"),
    )
    assert [row for row in rows if row.anchor == "slot_read"] == []


def test_a_computed_key_stays_unresolved_in_both_faces() -> None:
    found, dynamic = field_use.python_module_scan(
        ast.parse(f"payload.get({FIELD})"), FIELDS
    )
    assert dynamic == 1
    assert "mapping_call_read" not in found.get(FIELD, set())
    rows = consumer_report.scan_dynamic_reads(_source("payload.get(name)"))
    assert [(row.role, row.anchor, row.blocker) for row in rows] == [
        ("unknown", "dynamic_mapping_read", "dynamic_key")
    ]