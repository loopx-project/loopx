"""One owner decides the reason a scoped progress-review receipt carries no verdict.

``loopx/control_plane/work_items/progress_review_policy.py`` owns the
progress-review vocabulary that both the capability receipt and the control-plane
consumer read, and both already imported ``PROGRESS_REVIEW_SIGNALS`` from it. The
pending reason was the exception: each side declared ``"pending_evaluation"``
under its own name, so a rename on one side would leave the other silently
comparing against a reason that no longer exists.

A consumer must import the owner's name and then leave it alone. Checking only
that the import exists, or that the value still compares equal, is not enough:
rebinding the name below the import with a computed but equal string keeps the
import, keeps the value and still installs a second owner, and an identity check
cannot see it either because CPython folds and interns such a string. The guard
therefore reads the import line and rejects any later module-level assignment to
that name, whatever value shape it uses.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from loopx.capabilities.progress_review import receipt
from loopx.control_plane.work_items import (
    external_progress_review,
    progress_review_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/control_plane/work_items/progress_review_policy.py"
OWNER_NAME = "PROGRESS_REVIEW_PENDING_REASON"
OWNER_MODULE = "progress_review_policy"
REASON_VALUE = "pending_evaluation"


def reason_assignments(source: str) -> list[str]:
    """Names bound to the pending-reason literal anywhere in a module."""
    tree = ast.parse(source)
    held: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if node.target is not None
                else []
            )
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and node.value.value == REASON_VALUE
                ):
                    held.append(target.id)
    return held


def modules_holding_the_reason() -> list[str]:
    holders: list[str] = []
    for path in sorted(REPO_ROOT.glob("loopx/**/*.py")):
        try:
            held = reason_assignments(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - guarded tree
            continue
        if held:
            holders.append(path.relative_to(REPO_ROOT).as_posix())
    return holders


def _first_line(lines: dict[str, int], name: str, lineno: int) -> dict[str, int]:
    lines[name] = min(lineno, lines.get(name, lineno))
    return lines


def owner_import_lines(source: str) -> dict[str, int]:
    """Local names bound by an import from the owner module, with their lines."""
    bindings: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(OWNER_MODULE):
            for alias in node.names:
                bindings = _first_line(bindings, alias.asname or alias.name, node.lineno)
    return bindings


def module_assignment_lines(source: str) -> dict[str, int]:
    """Module-level names assigned a value, whatever shape that value has."""
    assignments: dict[str, int] = {}
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign):
            targets, value = list(statement.targets), statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets, value = [statement.target], statement.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assignments = _first_line(assignments, target.id, statement.lineno)
    return assignments


def test_the_policy_owner_states_the_reason() -> None:
    assert progress_review_policy.PROGRESS_REVIEW_PENDING_REASON == REASON_VALUE


@pytest.mark.parametrize(
    ("module", "local_name"),
    [
        (receipt, OWNER_NAME),
        (external_progress_review, "EXTERNAL_PROGRESS_REVIEW_PENDING_REASON"),
    ],
    ids=["capability-receipt", "external-review"],
)
def test_each_consumer_imports_the_owner_name_and_never_rebinds_it(
    module: object, local_name: str
) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]

    assert local_name in owner_import_lines(source), (
        f"{local_name} must be imported from {OWNER_MODULE}, not re-declared locally"
    )
    assert local_name not in module_assignment_lines(source), (
        f"{local_name} is assigned again in this module; an equal local value is a"
        " second owner even while the owner import stays in place"
    )
    assert getattr(module, local_name) == REASON_VALUE


def test_the_census_flags_a_rebind_that_keeps_the_import_and_the_value() -> None:
    # The counterexample review raised: the import survives, the value still
    # compares equal, and a literal-keyed census sees nothing because the copy is
    # built by concatenation.
    source = (
        f"from loopx.control_plane.work_items.{OWNER_MODULE} import {OWNER_NAME}\n"
        f'{OWNER_NAME} = "pending_" + "evaluation"\n'
    )

    assert owner_import_lines(source) == {OWNER_NAME: 1}
    assert module_assignment_lines(source) == {OWNER_NAME: 2}


def test_no_module_outside_the_owner_holds_the_reason() -> None:
    assert modules_holding_the_reason() == [OWNER_PATH]


def test_the_reason_stays_distinct_from_the_unevaluated_vocabulary() -> None:
    # The receipt reason is input; the classifier projects it onto its own
    # `"pending"` outcome reason, so the two vocabularies must not merge.
    assert REASON_VALUE not in (
        external_progress_review.EXTERNAL_PROGRESS_REVIEW_UNEVALUATED_REASONS
    )
