"""One owner decides what a reviewer-notification local time looks like.

The shape ``(?:[01]\\d|2[0-3]):[0-5]\\d`` answers one question -- is this an
``HH:MM`` value inside a reviewer-notification delivery window -- and three
modules stated it: the issue-fix domain pack plus two capability surfaces that
validate the same ``allowed_local_time`` window. The two surfaces now import the
domain pack's object instead of compiling the shape themselves.

Layer 1 judges folded values rather than spellings, so a copy built with ``+``,
imported from ``re``, or written inline at a matcher is reported too. Layer 2
pins the delegating modules to the owner's object and checks that they apply it
without keeping a local binding. Layer 3 records the owner's verdict at the
boundaries. A construction whose value this scan cannot fold has to be declared;
the declaration budget is empty today.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import pytest

from loopx.capabilities.issue_fix import reward_memory, reviewer_notification
from loopx.domain_packs import issue_fix as issue_fix_pack

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/domain_packs/issue_fix.py"
OWNER_NAME = "REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN"
LOCAL_TIME_BODY = r"(?:[01]\d|2[0-3]):[0-5]\d"
LOCAL_NAMES = (OWNER_NAME, "LOCAL_TIME_PATTERN", "_LOCAL_TIME_PATTERN")

DECLARED_INDIVIDUAL_SITES: dict[str, int] = {}
DECLARED_UNFOLDABLE_SITES: dict[str, int] = {}

CONSUMERS: dict[str, ModuleType] = {
    "loopx/capabilities/issue_fix/reviewer_notification.py": reviewer_notification,
    "loopx/capabilities/issue_fix/reward_memory.py": reward_memory,
}
MATCHERS = {"compile", "fullmatch", "match", "search"}


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
        left = fold_string(node.left, constants, depth)
        right = fold_string(node.right, constants, depth)
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
            folded = fold_string(
                value, {k: v for k, v in constants.items() if k != name}
            )
            if folded is None:
                unresolved.append((name, value))
            else:
                constants[name] = folded
        pending = unresolved
        if not pending:
            break
    return constants


def matcher_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "re" and func.attr in MATCHERS:
                calls.append(node)
        elif isinstance(func, ast.Name) and func.id in MATCHERS:
            calls.append(node)
    return calls


def time_rows(source: str, name: str) -> list[dict[str, object]]:
    if ":" not in source:
        return []
    tree = ast.parse(source)
    constants = module_constants(tree)
    rows: list[dict[str, object]] = []
    for node in matcher_calls(tree):
        if fold_string(node.args[0], constants) == LOCAL_TIME_BODY:
            rows.append({"file": name, "line": node.lineno, "kind": "local_time"})
    return rows


def unfoldable_rows(source: str, name: str) -> list[dict[str, object]]:
    if ":" not in source or "[0-3]" not in source:
        return []
    tree = ast.parse(source)
    constants = module_constants(tree)
    return [
        {"file": name, "line": node.lineno, "kind": "unfoldable"}
        for node in matcher_calls(tree)
        if fold_string(node.args[0], constants) is None
        and "[0-3]" in ast.unparse(node.args[0])
    ]


def collect(root: pathlib.Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((root / "loopx").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rows.extend(time_rows(source, path.relative_to(root).as_posix()))
    return rows


def collect_unfoldable(root: pathlib.Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((root / "loopx").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rows.extend(unfoldable_rows(source, path.relative_to(root).as_posix()))
    return rows


def offenders(
    rows: list[dict[str, object]],
    *,
    declared: dict[str, int] | None = None,
    owner: str = OWNER_PATH,
) -> list[str]:
    budget = DECLARED_INDIVIDUAL_SITES if declared is None else declared
    found: list[str] = []
    counted: dict[str, int] = {}
    for row in rows:
        file = str(row["file"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        if file == owner:
            continue
        found.append(f"{file}:{row['line']} restates the local-time shape")
    for file, allowed in budget.items():
        if counted.get(file, 0) != allowed:
            found.append(
                f"{file} declares {allowed} site(s), found {counted.get(file, 0)}"
            )
    return sorted(set(found))


def unfoldable_offender_rows(
    rows: list[dict[str, object]], *, declared: dict[str, int] | None = None
) -> list[str]:
    budget = DECLARED_UNFOLDABLE_SITES if declared is None else declared
    found: list[str] = []
    counted: dict[str, int] = {}
    for row in rows:
        file = str(row["file"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        found.append(
            f"{file}:{row['line']} builds a local-time regex from a value this "
            "scan cannot fold; declare it or route the site to the owner"
        )
    for file, allowed in budget.items():
        if counted.get(file, 0) != allowed:
            found.append(
                f"{file} declares {allowed} unfoldable site(s), found {counted.get(file, 0)}"
            )
    return sorted(set(found))


def module_bindings(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def referenced(tree: ast.Module, name: str) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == name for node in ast.walk(tree)
    )


def parse(module: ModuleType) -> ast.Module:
    path = pathlib.Path(str(module.__file__)).resolve()
    return ast.parse(path.read_text(encoding="utf-8"))


def modules_importing_the_owner() -> set[str]:
    importing: set[str] = set()
    for path in sorted((REPO_ROOT / "loopx").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == OWNER_NAME for alias in node.names
            ):
                importing.add(path.relative_to(REPO_ROOT).as_posix())
    return importing


def test_no_other_module_states_the_local_time_shape() -> None:
    assert offenders(collect(REPO_ROOT)) == []


def test_no_module_hides_an_unfoldable_local_time_construction() -> None:
    assert unfoldable_offender_rows(collect_unfoldable(REPO_ROOT)) == []


def test_every_stated_site_is_the_owner_or_a_declared_consumer() -> None:
    rows = collect(REPO_ROOT)
    stray = sorted({str(row["file"]) for row in rows} - {OWNER_PATH, *CONSUMERS})
    assert DECLARED_INDIVIDUAL_SITES == {}
    assert stray == []


def test_every_module_that_imports_the_owner_is_a_declared_consumer() -> None:
    importing = modules_importing_the_owner() - {OWNER_PATH}
    assert sorted(importing) == sorted(CONSUMERS)


@pytest.mark.parametrize("path", sorted(CONSUMERS), ids=sorted(CONSUMERS))
def test_consumer_holds_the_owner_object_and_applies_it(path: str) -> None:
    module = CONSUMERS[path]
    assert (
        getattr(module, OWNER_NAME)
        is issue_fix_pack.REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN
    )
    tree = parse(module)
    assert referenced(tree, OWNER_NAME), (
        f"{path} imports the shape but never applies it"
    )
    local = module_bindings(tree) & set(LOCAL_NAMES) - {OWNER_NAME}
    assert local == set(), f"{path} still binds {sorted(local)}"
    assert time_rows(pathlib.Path(str(module.__file__)).read_text("utf-8"), path) == []


def test_the_owner_states_the_shape_as_the_recorded_literal() -> None:
    assert (
        issue_fix_pack.REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.pattern
        == LOCAL_TIME_BODY
    )


@pytest.mark.parametrize("value", ["00:00", "09:30", "23:59"])
def test_owner_accepts_a_window_time(value: str) -> None:
    assert issue_fix_pack.REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.fullmatch(value)


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("24:00", "hour out of range"),
        ("09:60", "minute out of range"),
        ("9:30", "hour needs two digits"),
        ("09:30:00", "seconds are a different contract"),
        ("09:30 ", "surrounding space"),
        ("", "empty"),
    ],
)
def test_owner_rejects_a_non_window_time(value: str, why: str) -> None:
    assert (
        issue_fix_pack.REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.fullmatch(value) is None
    ), why


OFFENDING = (
    (
        "module level compile",
        'import re\n\nWINDOW = re.compile(r"(?:[01]\\d|2[0-3]):[0-5]\\d")\n',
    ),
    (
        "inline at the matcher",
        'import re\n\n\ndef check(value):\n    return re.fullmatch(r"(?:[01]\\d|2[0-3]):[0-5]\\d", value)\n',
    ),
    (
        "concatenated from same-file constants",
        'import re\n\nHOUR = "(?:[01]\\\\d|2[0-3])"\nMINUTE = ":[0-5]\\\\d"\n'
        "WINDOW = re.compile(HOUR + MINUTE)\n",
    ),
    (
        "imported matcher",
        'from re import compile\n\nWINDOW = compile(r"(?:[01]\\d|2[0-3]):[0-5]\\d")\n',
    ),
)
NON_OFFENDING = (
    (
        "a seconds-aware time is a different decision",
        'import re\n\nCLOCK = re.compile(r"(?:[01]\\d|2[0-3]):[0-5]\\d:[0-5]\\d")\n',
    ),
    (
        "a date shape is unrelated",
        'import re\n\nDATE = re.compile(r"\\d{4}-\\d{2}-\\d{2}")\n',
    ),
)


@pytest.mark.parametrize(
    ("label", "source"), OFFENDING, ids=[row[0] for row in OFFENDING]
)
def test_the_scan_reports_a_restated_local_time(label: str, source: str) -> None:
    rows = time_rows(source, "loopx/capabilities/probe.py")
    assert rows, label
    assert offenders(rows, declared={}) != [], label


@pytest.mark.parametrize(
    ("label", "source"), NON_OFFENDING, ids=[row[0] for row in NON_OFFENDING]
)
def test_the_scan_leaves_a_different_decision_alone(label: str, source: str) -> None:
    assert time_rows(source, "loopx/capabilities/probe.py") == [], label


def test_unfoldable_probe_is_rejected_through_the_repository_scan(
    tmp_path: pathlib.Path,
) -> None:
    source = "import re\n\n\ndef build(prefix):\n"
    source += '    return re.compile(prefix + "(?:[01]\\\\d|2[0-3]):[0-5]\\\\d")\n'
    probe_root = tmp_path / "loopx" / "capabilities"
    probe_root.mkdir(parents=True)
    (probe_root / "probe.py").write_text(source, encoding="utf-8")
    assert time_rows(source, "loopx/capabilities/probe.py") == []
    rows = collect_unfoldable(tmp_path)
    assert [row["file"] for row in rows] == ["loopx/capabilities/probe.py"]
    assert unfoldable_offender_rows(rows)[0].startswith("loopx/capabilities/probe.py:")
    assert DECLARED_UNFOLDABLE_SITES == {}
