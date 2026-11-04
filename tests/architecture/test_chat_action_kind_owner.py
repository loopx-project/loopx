"""The Chat action-kind vocabulary has one owner, so a new kind cannot land twice.

``loopx/chat_action_store.py`` decides which action kinds a proposal may be stored
for, and ``loopx/chat_server.py`` publishes that same set to hosts.
``loopx/chat_actions.py`` kept a byte-identical copy under a different name and
gated dispatch on it, so a kind added to one side stayed invisible to the other
and a proposal could be accepted by the service while the store rejected it.

The census reads annotated module-level assignments as well as plain ones: a
``LOCAL: set[str] = {...}`` restates the same decision as a copy, so a scanner
that only visited ``ast.Assign`` would certify a fold while leaving a typed
second owner in the tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from loopx import chat_action_store, chat_actions

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/chat_action_store.py"
OWNER_NAME = "ACTION_KINDS"
KIND_VALUE = frozenset(
    {
        "agent.bind",
        "gate.resolve",
        "goal.acceptance.check",
        "goal.create",
        "goal.lifecycle",
        "goal.update",
        "heartbeat.bind",
        "monitor.create",
        "monitor.update",
        "operation.execute",
        "run.correct",
        "team.plan",
        "todo.create",
        "todo.update",
    }
)


def set_literal(node: ast.AST) -> frozenset[str] | None:
    """Return the string members of a set literal, including a frozenset call."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "frozenset":
        if not node.args:
            return None
        node = node.args[0]
    if not isinstance(node, ast.Set):
        return None
    members: set[str] = set()
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        members.add(element.value)
    return frozenset(members)


def module_level_bindings(source: str) -> list[tuple[list[ast.expr], ast.expr | None]]:
    """(targets, value) pairs for module-level assignments, typed or untyped."""
    bindings: list[tuple[list[ast.expr], ast.expr | None]] = []
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign):
            bindings.append((list(statement.targets), statement.value))
        elif isinstance(statement, ast.AnnAssign):
            bindings.append(([statement.target], statement.value))
    return bindings


def vocabulary_assignments(source: str) -> list[str]:
    """Names in this source that restate the owner's vocabulary as their own set."""
    held: list[str] = []
    for targets, value in module_level_bindings(source):
        if value is None or set_literal(value) != KIND_VALUE:
            continue
        held.extend(target.id for target in targets if isinstance(target, ast.Name))
    return held


def modules_holding_the_vocabulary() -> list[str]:
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("loopx/**/*.py")):
        try:
            held = vocabulary_assignments(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - guarded tree
            continue
        if held:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    return offenders


def test_the_store_owns_the_declared_action_kind_vocabulary() -> None:
    assert frozenset(chat_action_store.ACTION_KINDS) == KIND_VALUE


def test_the_action_service_applies_the_owner_object() -> None:
    assert chat_actions.SUPPORTED_ACTION_KINDS is chat_action_store.ACTION_KINDS


def test_no_module_outside_the_owner_holds_its_own_copy() -> None:
    assert modules_holding_the_vocabulary() == [OWNER_PATH]


@pytest.mark.parametrize(
    "declaration",
    ["SHADOW = {MEMBERS}", "SHADOW: set[str] = {MEMBERS}", "SHADOW: frozenset[str] = frozenset({MEMBERS})"],
)
def test_the_census_reads_a_reintroduced_copy_whatever_it_is_called_or_annotated(
    declaration: str,
) -> None:
    # Members are written in reverse so a reordered copy is proven to count too.
    members = ", ".join(repr(value) for value in sorted(KIND_VALUE, reverse=True))

    assert vocabulary_assignments(declaration.replace("{MEMBERS}", "{" + members + "}") + "\n") == [
        "SHADOW"
    ]


@pytest.mark.parametrize("action_kind", sorted(KIND_VALUE))
def test_every_declared_kind_is_dispatchable(action_kind: str) -> None:
    assert action_kind in chat_actions.SUPPORTED_ACTION_KINDS
