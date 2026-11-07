"""One owner decides the reviewer-notification queue receipt schema version.

``issue_fix_reviewer_notification_queue_receipt_v1`` names one wire contract, and
two modules stated it: ``loopx/domain_packs/issue_fix.py``, which the capability
surface already imports for the local-time shape, and
``loopx/capabilities/issue_fix/reviewer_notification.py``, which restated it under
an ``ISSUE_FIX_`` prefix. A version bump in one file would have left the other
accepting the previous contract, which is the failure the local-time owner test
was written to prevent.

Layer 1 judges folded values rather than spellings, so a copy written across
lines, built with ``+``, interpolated into an f-string, or declared with a type
annotation is reported too.
Layer 2 pins the consuming module to the owner's object and checks that it keeps
no local binding. Layer 3 records that the current and legacy receipts stay
distinct at the boundary that reads a queued receipt.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

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


def stated_bindings(tree: ast.Module) -> list[tuple[str, int, ast.expr]]:
    """One extraction rule for module-level string bindings.

    A plain assignment and an annotated assignment with a value state the same
    thing before anything runs, so both feed the fold and both are census
    candidates; reading only ``ast.Assign`` leaves ``COPY: Final[str] = ...`` a
    restatement this guard never sees.
    """
    stated: list[tuple[str, int, ast.expr]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            if node.value is None:
                continue
            target = node.target
        else:
            continue
        if isinstance(target, ast.Name):
            stated.append((target.id, node.lineno, node.value))
    return stated


def module_constants(tree: ast.Module) -> dict[str, str]:
    pending: list[tuple[str, ast.AST]] = [
        (bound, value) for bound, _line, value in stated_bindings(tree)
    ]
    constants: dict[str, str] = {}
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
    tree = ast.parse(source)
    constants = module_constants(tree)
    rows: list[dict[str, object]] = []
    for _bound, lineno, value in stated_bindings(tree):
        if fold_string(value, constants) == QUEUE_RECEIPT_VALUE:
            rows.append({"file": name, "line": lineno, "kind": "queue_receipt_version"})
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


OFFENDING = (
    (
        "split literal addition",
        'COPY = "issue_fix_reviewer_notification_" + "queue_" + "receipt_v1"\n',
    ),
    (
        "adjacent literals across lines",
        'COPY = (\n    "issue_fix_reviewer_notification_queue_" "receipt_v1"\n)\n',
    ),
    (
        "same-file constants concatenated",
        'HEAD = "issue_fix_reviewer_notification_queue_"\nTAIL = "receipt_v1"\n'
        "COPY = HEAD + TAIL\n",
    ),
    (
        "constant pieces inside adjacent f-strings",
        'COPY = f"issue_fix_reviewer_notification_queue_rece" f"ipt_v1"\n',
    ),
    (
        "typed annotation with the full literal",
        'from typing import Final\n\nCOPY: Final[str] = "issue_fix_reviewer_notification_'
        'queue_receipt_v1"\n',
    ),
    (
        "typed annotation built with addition",
        'COPY: str = "issue_fix_reviewer_notification_" + "queue_" + "receipt_v1"\n',
    ),
    (
        "typed annotation over a typed same-file constant",
        'from typing import Final\nHEAD: Final[str] = "issue_fix_reviewer_notification_queue_"\n'
        'COPY: Final[str] = HEAD + "receipt_v1"\n',
    ),
)
NON_OFFENDING = (
    (
        "the legacy receipt is a different decision",
        'COPY = "issue_fix_reviewer_notification_queue_receipt_v0"\n',
    ),
    (
        "an unrelated contract name is unrelated",
        'COPY = "issue_fix_reviewer_notification_queue_projection_v1"\n',
    ),
    (
        "a typed legacy receipt is a different decision",
        'from typing import Final\n\nCOPY: Final[str] = "issue_fix_reviewer_notification_'
        'queue_receipt_v0"\n',
    ),
)


@pytest.mark.parametrize(
    ("label", "source"), OFFENDING, ids=[row[0] for row in OFFENDING]
)
def test_the_scan_reports_a_restated_queue_receipt_version(label: str, source: str) -> None:
    rows = version_rows(source, "loopx/capabilities/probe.py")
    assert rows, label
    assert offenders(rows) != [], label


@pytest.mark.parametrize(
    ("label", "source"), NON_OFFENDING, ids=[row[0] for row in NON_OFFENDING]
)
def test_the_scan_leaves_a_different_contract_alone(label: str, source: str) -> None:
    assert version_rows(source, "loopx/capabilities/probe.py") == [], label


def test_a_split_copy_is_rejected_through_the_repository_scan(
    tmp_path: pathlib.Path,
) -> None:
    source = 'COPY = "issue_fix_reviewer_notification_" + "queue_" + "receipt_v1"\n'
    probe_root = tmp_path / "loopx" / "capabilities"
    probe_root.mkdir(parents=True)
    (probe_root / "probe.py").write_text(source, encoding="utf-8")
    rows = collect(tmp_path)
    assert [row["file"] for row in rows] == ["loopx/capabilities/probe.py"]
    assert offenders(rows)[0].startswith("loopx/capabilities/probe.py:")


def test_the_census_flags_a_typed_copy_beside_the_real_owner(
    tmp_path: pathlib.Path,
) -> None:
    owner_dir = tmp_path / "loopx" / "domain_packs"
    owner_dir.mkdir(parents=True)
    (owner_dir / "issue_fix.py").write_text(
        (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8"), encoding="utf-8"
    )
    copy_dir = tmp_path / "loopx" / "capabilities"
    copy_dir.mkdir(parents=True)
    (copy_dir / "copy.py").write_text(
        f"from typing import Final\n\nCOPY: Final[str] = \"{QUEUE_RECEIPT_VALUE}\"\n",
        encoding="utf-8",
    )
    rows = collect(tmp_path)
    assert sorted(str(row["file"]) for row in rows) == [
        "loopx/capabilities/copy.py",
        OWNER_PATH,
    ]
    assert offenders(rows) != [], offenders(rows)


def test_the_census_leaves_a_typed_legacy_receipt_alone(
    tmp_path: pathlib.Path,
) -> None:
    copy_dir = tmp_path / "loopx" / "capabilities"
    copy_dir.mkdir(parents=True)
    (copy_dir / "legacy.py").write_text(
        'from typing import Final\n\nCOPY: Final[str] = '
        '"issue_fix_reviewer_notification_queue_receipt_v0"\n',
        encoding="utf-8",
    )
    assert collect(tmp_path) == []
