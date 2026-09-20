"""Executable counterexamples for the Python production scan's soundness.

The property under test is not "these shapes must stay unknown" but the
weaker, load-bearing one:

    every value the source can actually put in the field is either contained
    in the reported set, or the report is marked ``unresolved``.

Reporting a *smaller* set while claiming it is complete is what lets an
unregistered value pass a gate that exists to reject it. Asserting the
property this way keeps a future precision improvement legal: a scan that
learns to resolve one of these shapes exactly still satisfies it.

Each case runs the snippet for real and compares the observed field values
against what the scan reports for the same text.
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest

from loopx.semantics.inventory import SourceFile
from loopx.semantics.python_production import scan_python_production

FIELD = "effective_action"


def _observed(source: str, arguments: list[Any]) -> set[str]:
    """Values ``emit`` actually places in the field for these arguments."""
    namespace: dict[str, Any] = {}
    exec(compile(textwrap.dedent(source), "<counterexample>", "exec"), namespace)
    emit = namespace["emit"]
    seen: set[str] = set()
    for argument in arguments:
        result = emit() if argument is _NO_ARGUMENT else emit(argument)
        value = result.get(FIELD)
        if isinstance(value, str):
            seen.add(value)
    return seen


def _reported(source: str) -> tuple[set[str], bool]:
    rows = scan_python_production(
        SourceFile(path="counterexample.py", suffix=".py", text=textwrap.dedent(source)),
        field=FIELD,
        enums={},
    )
    if not rows:
        return set(), True
    values: set[str] = set()
    for row in rows:
        values |= set(row.values)
    return values, any(row.unresolved for row in rows)


class _NoArgument:
    pass


_NO_ARGUMENT = _NoArgument()


MATCH_CAPTURE = '''
def emit(value):
    action = "run"
    match value:
        case action:
            pass
    return {"effective_action": action}
'''

MATCH_STAR = '''
def emit(values):
    action = "run"
    match values:
        case [*action]:
            pass
    return {"effective_action": str(action)}
'''

MATCH_MAPPING_REST = '''
def emit(mapping):
    action = "run"
    match mapping:
        case {"k": 1, **action}:
            pass
    return {"effective_action": str(action)}
'''

ASSIGNED_THEN_IMPORTED = '''
def emit():
    action = "run"
    import os as action
    return {"effective_action": str(action)}
'''

# The same rebinding at module scope, where the scan has a second reason to
# look at imports: an import is how a producer names the owner module it
# qualifies against, so an import may not shadow that qualified binding. It
# must still take the name away from a plain assignment to the same name, and
# for a while it did not -- the scan reported ``["run"]`` as a complete set
# while CPython bound the module object.
MODULE_ASSIGNED_THEN_IMPORTED = '''
action = "run"
import os as action
RESULT = {"effective_action": str(action)}

def emit():
    return RESULT
'''

# ``except ... as action`` rebinds the name to the exception and unbinds it at
# block exit, so the initializer above is not what the field can carry.
ASSIGNED_THEN_CAUGHT = '''
def emit(flag):
    action = "run"
    try:
        raise ValueError("boom")
    except ValueError as action:
        return {"effective_action": str(action)}
    return {"effective_action": action}
'''

NESTED_NONLOCAL_REBIND = '''
def emit(value):
    action = "run"
    def inner():
        nonlocal action
        action = value
    inner()
    return {"effective_action": action}
'''

CONTAINER_ALIAS_WRITE = '''
def emit(value):
    row = {"effective_action": "run"}
    box = [row]
    box[0]["effective_action"] = value
    return row
'''

ESCAPES_INTO_CALL = '''
def _mutate(rows, value):
    rows[0]["effective_action"] = value

def emit(value):
    row = {"effective_action": "run"}
    _mutate([row], value)
    return row
'''

CONVERTS_NONE = '''
def emit():
    return {"effective_action": str(None)}
'''

CONVERTS_INT = '''
def emit():
    return {"effective_action": str(42)}
'''


@pytest.mark.parametrize(
    ("source", "arguments"),
    [
        pytest.param(MATCH_CAPTURE, ["drop", "other"], id="match-capture-rebinds"),
        pytest.param(MATCH_STAR, [["a"]], id="match-star-rebinds"),
        pytest.param(MATCH_MAPPING_REST, [{"k": 1, "z": 2}], id="match-mapping-rest-rebinds"),
        pytest.param(ASSIGNED_THEN_IMPORTED, [_NO_ARGUMENT], id="import-takes-the-name"),
        pytest.param(MODULE_ASSIGNED_THEN_IMPORTED, [_NO_ARGUMENT], id="module-import-takes-the-name"),
        pytest.param(ASSIGNED_THEN_CAUGHT, [True], id="except-takes-the-name"),
        pytest.param(NESTED_NONLOCAL_REBIND, ["drop"], id="nested-scope-rebinds"),
        pytest.param(CONTAINER_ALIAS_WRITE, ["drop"], id="alias-writes-the-container"),
        pytest.param(ESCAPES_INTO_CALL, ["drop"], id="container-escapes-into-a-call"),
        pytest.param(CONVERTS_NONE, [_NO_ARGUMENT], id="str-of-none-is-text"),
        pytest.param(CONVERTS_INT, [_NO_ARGUMENT], id="str-of-int-is-text"),
    ],
)
def test_reported_values_never_omit_an_observed_value(source: str, arguments: list[Any]) -> None:
    observed = _observed(source, arguments)
    reported, unresolved = _reported(source)
    assert unresolved or observed <= reported, (
        f"scan claimed a complete value set {sorted(reported)} while the source "
        f"can produce {sorted(observed)}"
    )


def test_str_of_a_literal_reports_the_converted_text() -> None:
    """Precision, not only soundness: a provable conversion keeps its value."""
    reported, unresolved = _reported(CONVERTS_NONE)
    assert reported == {"None"} and not unresolved
    reported, unresolved = _reported(CONVERTS_INT)
    assert reported == {"42"} and not unresolved


def test_str_of_an_enum_member_object_is_not_its_value() -> None:
    """``str(Member)`` is the qualified name, so it cannot claim the value."""
    source = '''
from loopx.control_plane.quota.effective_action import EffectiveAction

def emit():
    return {"effective_action": str(EffectiveAction.NORMAL_RUN)}
'''
    from loopx.control_plane.quota.effective_action import EffectiveAction

    assert str(EffectiveAction.NORMAL_RUN) != EffectiveAction.NORMAL_RUN.value
    reported, unresolved = _reported(source)
    assert unresolved or str(EffectiveAction.NORMAL_RUN) in reported


def test_str_of_a_value_read_keeps_its_evidence() -> None:
    """``str(Member.value)`` is identity, so the value stays provable."""
    source = '''
def emit(override):
    fallback = "control_plane_repair"
    return {"effective_action": str(override or fallback)}
'''
    reported, unresolved = _reported(source)
    assert "control_plane_repair" in reported or unresolved
