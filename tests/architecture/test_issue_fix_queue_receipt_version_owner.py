"""One owner decides the reviewer-notification queue receipt schema version.

``issue_fix_reviewer_notification_queue_receipt_v1`` names one wire contract, and
two modules stated it: ``loopx/domain_packs/issue_fix.py``, which the capability
surface already imports for the local-time shape, and
``loopx/capabilities/issue_fix/reviewer_notification.py``, which restated it under
an ``ISSUE_FIX_`` prefix. A version bump in one file would have left the other
accepting the previous contract, which is the failure the local-time owner test
was written to prevent.

Layer 1 judges folded values rather than spellings, so a copy written across
lines, built with ``+``, or interpolated into an f-string is reported too.
Layer 2 pins the consuming module to the owner's object and checks that it keeps
no local binding. Layer 3 records that the current and legacy receipts stay
distinct at the boundary that reads a queued receipt.
"""

from __future__ import annotations

import ast
import pathlib

from loopx.capabilities.issue_fix import reviewer_notification
from loopx.domain_packs import issue_fix as issue_fix_pack

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/domain_packs/issue_fix.py"
OWNER_NAME = "REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION"
CONSUMER_PATH = "loopx/capabilities/issue_fix/reviewer_notification.py"
CONSUMER_NAME = "ISSUE_FIX_REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION"
QUEUE_RECEIPT_VALUE = "issue_fix_reviewer_notification_queue_receipt_v1"

DECLARED_UNFOLDABLE_SITES: dict[str, int] = {}


def fold_string(node: ast.AST, constants: dict[str, str], depth: int = 0) -> str | None:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        return "".join(parts) if len(parts) == len(node.values) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = fold_string(node.left, constants, depth + 1)
        right = fold_string(node.right, constants, depth + 1)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name) and depth < 4:
        return constants.get(node.id)
    return None


def module_constants(tree: ast.Module) -> dict[str, str]:
    pending: list[tuple[str, ast.AST]] = []
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                pending.append((target.id, node.value))
    for _ in range(4):
        unresolved: list[tuple[str, ast.AST]] = []
        for name, value in pending:
            folded = fold_string(value, {k: v for k, v in constants.items() if k != name})
            if folded is None:
                unresolved.append((name, value))
            else:
                constants[name] = folded
        pending = unresolved
        if not pending:
            break
    return constants


def version_rows(source: str, name: str) -> list[dict[str, object]]:
    if "queue_receipt" not in source:
        return []
    tree = ast.parse(source)
    constants = module_constants(tree)
    rows: list[dict[str, object]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if fold_string(node.value, constants) == QUEUE_RECEIPT_VALUE:
            rows.append(
                {"file": name, "line": node.lineno, "kind": "queue_receipt_version"}
            )
    return rows


def collect(root: pathlib.Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((root / "loopx").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rows.extend(version_rows(source, path.relative_to(root).as_posix()))
    return rows


def offenders(rows: list[dict[str, object]]) -> list[str]:
    found = [
        f"{row['file']}:{row['line']} restates the queue receipt schema version"
        for row in rows
        if row["file"] != OWNER_PATH
    ]
    for file, allowed in DECLARED_UNFOLDABLE_SITES.items():
        count = sum(1 for row in rows if row["file"] == file)
        if count != allowed:
            found.append(f"{file} declares {allowed} site(s), found {count}")
    return sorted(set(found))


def test_only_the_domain_pack_states_the_queue_receipt_version() -> None:
    rows = collect(REPO_ROOT)
    reported = offenders(rows)
    assert reported == [], reported
    assert any(row["file"] == OWNER_PATH for row in rows), rows


def test_the_consumer_binds_the_owner_object_without_a_local_copy() -> None:
    owner_value = getattr(issue_fix_pack, OWNER_NAME)
    assert owner_value == QUEUE_RECEIPT_VALUE, owner_value
    assert getattr(reviewer_notification, CONSUMER_NAME) is owner_value
    source = (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    assert f"{CONSUMER_NAME} = (" not in source, "consumer re-declared the version"


def test_current_and_legacy_receipt_versions_stay_distinct_at_the_boundary() -> None:
    owner_value = getattr(issue_fix_pack, OWNER_NAME)
    legacy = getattr(
        reviewer_notification,
        "ISSUE_FIX_REVIEWER_NOTIFICATION_LEGACY_QUEUE_RECEIPT_SCHEMA_VERSION",
    )
    assert owner_value != legacy, (owner_value, legacy)
    assert owner_value.endswith("_v1") and legacy.endswith("_v0")
