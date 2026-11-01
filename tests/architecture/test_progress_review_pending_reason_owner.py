"""One owner decides the reason a scoped progress-review receipt carries no verdict.

``loopx/control_plane/work_items/progress_review_policy.py`` owns the
progress-review vocabulary that both the capability receipt and the control-plane
consumer read, and both already imported ``PROGRESS_REVIEW_SIGNALS`` from it. The
pending reason was the exception: each side declared ``"pending_evaluation"``
under its own name, so a rename on one side would leave the other silently
comparing against a reason that no longer exists.
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


def owner_import_levels(source: str) -> dict[str, int]:
    """Local names bound by an import from the owner module, with their levels."""
    bindings: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(OWNER_MODULE):
            for alias in node.names:
                bindings[alias.asname or alias.name] = node.level
    return bindings


def test_the_policy_owner_states_the_reason() -> None:
    assert progress_review_policy.PROGRESS_REVIEW_PENDING_REASON == REASON_VALUE


@pytest.mark.parametrize(
    ("module", "local_name"),
    [
        (receipt, "PROGRESS_REVIEW_PENDING_REASON"),
        (external_progress_review, "EXTERNAL_PROGRESS_REVIEW_PENDING_REASON"),
    ],
    ids=["capability-receipt", "external-review"],
)
def test_each_consumer_imports_the_owner_name_and_carries_its_value(
    module: object, local_name: str
) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]

    assert owner_import_levels(source).get(local_name), (
        f"{local_name} must be imported from {OWNER_MODULE}, not re-declared locally"
    )
    assert getattr(module, local_name) == REASON_VALUE


def test_no_module_outside_the_owner_holds_the_reason() -> None:
    assert modules_holding_the_reason() == [OWNER_PATH]


def test_the_reason_stays_distinct_from_the_unevaluated_vocabulary() -> None:
    # The receipt reason is input; the classifier projects it onto its own
    # `"pending"` outcome reason, so the two vocabularies must not merge.
    assert REASON_VALUE not in (
        external_progress_review.EXTERNAL_PROGRESS_REVIEW_UNEVALUATED_REASONS
    )
