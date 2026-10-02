"""Guard the single owner of the lowercase public-safe slug shape.

The shape ``^[a-z][a-z0-9_.-]{0,127}$`` answers one question -- is this
identifier a lowercase public-safe slug -- and on the base revision seven modules
compiled it themselves: ``periodic_report/core.py``, ``issue_fix/periodic_report.py``
and ``control_plane/handoff/review_batch.py``, plus four files inside
``capabilities/periodic_report`` that this slice declares instead of converting.

Decisions are made on the folded value, not the spelling: a regex built at module
level, inside a function, or by concatenating same-file string constants is
reported, and a construction whose value cannot be folded has to be declared with
its file and count.

The four declared sites are ``adapters.py``, ``archive.py``, ``audience.py`` and
``bindings.py``.  They are not converted here because their import blocks are
being rewritten by an open branch of the same author, so a seventh edit would
only create a conflict between two pending pull requests.  Converting one means
deleting its entry, and a declared file whose count moves fails the guard either
way.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import pytest

from loopx import public_safe_text
from loopx.capabilities.issue_fix import periodic_report as issue_fix_report
from loopx.capabilities.periodic_report import core
from loopx.control_plane.handoff import review_batch

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/public_safe_text.py"
SLUG_BODY = r"^[a-z][a-z0-9_.-]{0,127}$"
SLUG_INNER = SLUG_BODY[1:-1]

DECLARED_INDIVIDUAL_SITES: dict[str, int] = {
    "loopx/capabilities/periodic_report/adapters.py": 1,
    "loopx/capabilities/periodic_report/archive.py": 1,
    "loopx/capabilities/periodic_report/audience.py": 1,
    "loopx/capabilities/periodic_report/bindings.py": 1,
}
# A construction whose value this scan cannot fold, per file.  None today.
DECLARED_UNFOLDABLE_SITES: dict[str, int] = {}

CONSUMERS: dict[str, tuple[ModuleType, str]] = {
    "loopx/capabilities/periodic_report/core.py": (core, "PUBLIC_SAFE_SLUG_PATTERN"),
    "loopx/capabilities/issue_fix/periodic_report.py": (
        issue_fix_report,
        "PUBLIC_SAFE_SLUG_PATTERN",
    ),
    "loopx/control_plane/handoff/review_batch.py": (
        review_batch,
        "PUBLIC_SAFE_SLUG_PATTERN",
    ),
}
PRESCREEN_TOKENS = ("import re", "from re import")
REGEX_METHODS = {
    "compile",
    "fullmatch",
    "match",
    "search",
    "findall",
    "finditer",
    "sub",
    "subn",
    "split",
}
FOLD_DEPTH_LIMIT = 4


def fold_string(node: ast.AST, constants: dict[str, str], depth: int = 0) -> str | None:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = fold_string(node.left, constants, depth)
        right = fold_string(node.right, constants, depth)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Name) and depth < FOLD_DEPTH_LIMIT:
        return constants.get(node.id)
    return None


def untrusted_names(tree: ast.Module) -> set[str]:
    module_targets: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                module_targets[name] = module_targets.get(name, 0) + 1
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_targets[node.target.id] = module_targets.get(node.target.id, 0) + 1
    untrusted = {name for name, count in module_targets.items() if count > 1}
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            untrusted.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            untrusted.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            untrusted.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id not in module_targets:
                untrusted.add(node.id)
    return untrusted


def module_level_string_constants(tree: ast.Module) -> dict[str, str]:
    trusted = untrusted_names(tree)
    pending: list[tuple[str, ast.AST]] = []
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id not in trusted:
                pending.append((target.id, node.value))
    for _ in range(FOLD_DEPTH_LIMIT):
        unresolved: list[tuple[str, ast.AST]] = []
        for name, value in pending:
            scope = {k: v for k, v in constants.items() if k != name}
            folded = fold_string(value, scope)
            if folded is None:
                unresolved.append((name, value))
            else:
                constants[name] = folded
        pending = unresolved
        if not pending:
            break
    return constants


def regex_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            holder = func.value
            if isinstance(holder, ast.Name) and holder.id == "re" and (
                func.attr in REGEX_METHODS
            ):
                calls.append(node)
        elif isinstance(func, ast.Name) and func.id in REGEX_METHODS:
            calls.append(node)
    return calls


def shape_rows(source: str, name: str) -> list[dict[str, object]]:
    if not any(token in source for token in PRESCREEN_TOKENS):
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    rows: list[dict[str, object]] = []
    for node in regex_calls(tree):
        folded = fold_string(node.args[0], constants)
        if folded is None:
            continue
        # Anchors are normalized away: under fullmatch they are redundant, so a
        # half-anchored restatement is the same decision, not a new one.
        if folded.removeprefix("^").removesuffix("$") == SLUG_INNER:
            rows.append({"file": name, "line": node.lineno, "kind": "slug_shape"})
    return rows


def unfoldable_rows(source: str, name: str) -> list[dict[str, object]]:
    if not any(token in source for token in PRESCREEN_TOKENS):
        return []
    if "[a-z" not in source:
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    return [
        {"file": name, "line": node.lineno, "kind": "unfoldable_regex"}
        for node in regex_calls(tree)
        if fold_string(node.args[0], constants) is None
        and "[a-z" in ast.unparse(node.args[0])
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
        relative = path.relative_to(root).as_posix()
        rows.extend(shape_rows(source, relative))
    return rows


def offender_rows(
    rows: list[dict[str, object]],
    *,
    declared: dict[str, int] | None = None,
    owner: str = OWNER_PATH,
) -> list[str]:
    budget = DECLARED_INDIVIDUAL_SITES if declared is None else declared
    offenders: list[str] = []
    counted: dict[str, int] = {}
    for row in rows:
        file = str(row["file"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        if file == owner:
            continue
        offenders.append(f"{file}:{row['line']} restates the public-safe slug shape")
    for file, allowed in budget.items():
        found = counted.get(file, 0)
        if found != allowed:
            offenders.append(f"{file} declares {allowed} site(s), found {found}")
    return sorted(set(offenders))


def unfoldable_offender_rows(
    rows: list[dict[str, object]], *, declared: dict[str, int] | None = None
) -> list[str]:
    budget = DECLARED_UNFOLDABLE_SITES if declared is None else declared
    offenders: list[str] = []
    counted: dict[str, int] = {}
    for row in rows:
        file = str(row["file"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        offenders.append(
            f"{file}:{row['line']} builds a slug-shaped regex from a value this "
            "scan cannot fold; declare it or route the site to the owner"
        )
    for file, allowed in budget.items():
        found = counted.get(file, 0)
        if found != allowed:
            offenders.append(f"{file} declares {allowed} unfoldable site(s), found {found}")
    return sorted(set(offenders))


def module_level_bindings(tree: ast.Module) -> set[str]:
    """Module-level names, including ``def`` so a shadowing copy is visible."""

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
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


# --------------------------------------------------------------------------
# 1. one owner, four declared sites
# --------------------------------------------------------------------------


def test_no_other_module_compiles_the_slug_shape() -> None:
    assert offender_rows(collect(REPO_ROOT)) == []


def test_declared_sites_are_exactly_the_four_in_flight_files() -> None:
    rows = collect(REPO_ROOT)
    declared_found = sorted(
        str(row["file"]) for row in rows if str(row["file"]) in DECLARED_INDIVIDUAL_SITES
    )
    assert declared_found == sorted(DECLARED_INDIVIDUAL_SITES)


def test_owner_states_the_shape_as_the_recorded_literal() -> None:
    assert public_safe_text.PUBLIC_SAFE_SLUG_PATTERN.pattern == SLUG_BODY
    assert (
        len([row for row in collect(REPO_ROOT) if str(row["file"]) == OWNER_PATH]) == 1
    )


def test_no_module_hides_an_unfoldable_slug_construction() -> None:
    rows: list[dict[str, object]] = []
    for path in sorted((REPO_ROOT / "loopx").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rows.extend(unfoldable_rows(source, path.relative_to(REPO_ROOT).as_posix()))
    assert unfoldable_offender_rows(rows) == []


# --------------------------------------------------------------------------
# 2. consumers are wired and actually decide with the owner's object
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "entry"), sorted(CONSUMERS.items()), ids=sorted(CONSUMERS)
)
def test_consumer_holds_the_owner_object_and_applies_it(path: str, entry: tuple) -> None:
    module, name = entry
    assert getattr(module, name) is public_safe_text.PUBLIC_SAFE_SLUG_PATTERN
    tree = parse(module)
    assert referenced(tree, name), f"{path} imports the shape but never applies it"
    assert name not in module_level_bindings(tree), f"{path} binds {name} locally"
    assert "_TOKEN_RE" not in module_level_bindings(tree), f"{path} keeps a local copy"


def test_a_removed_import_cannot_stay_behind_as_a_local_compile() -> None:
    for path, (module, _) in sorted(CONSUMERS.items()):
        source = pathlib.Path(str(module.__file__)).resolve().read_text("utf-8")
        assert shape_rows(source, path) == []


# --------------------------------------------------------------------------
# 3. the owner's answer
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["a", "report", "cadence_digest", "p0", "a.b-c_9", "a" * 128, "issue_fix.v1"],
)
def test_owner_accepts_a_slug(value: str) -> None:
    assert public_safe_text.PUBLIC_SAFE_SLUG_PATTERN.fullmatch(value)


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("", "empty"),
        ("A", "must start lowercase"),
        ("9lives", "must start with a letter"),
        ("a b", "space"),
        ("a/b", "slash"),
        ("a:b", "colon"),
        ("a@b", "at sign"),
        ("a+b", "plus"),
        ("a,b", "comma"),
        ("a" * 129, "one over the bound"),
        ("caf\u00e9", "non-ascii"),
        ("_leading", "must start with a letter"),
        ("-leading", "must start with a letter"),
        (".leading", "must start with a letter"),
    ],
)
def test_owner_rejects_a_non_slug(value: str, why: str) -> None:
    assert public_safe_text.PUBLIC_SAFE_SLUG_PATTERN.fullmatch(value) is None, why


def test_the_128_character_bound_is_the_owners_answer_at_the_edge() -> None:
    assert public_safe_text.PUBLIC_SAFE_SLUG_PATTERN.fullmatch("a" * 128)
    assert public_safe_text.PUBLIC_SAFE_SLUG_PATTERN.fullmatch("a" * 129) is None


# --------------------------------------------------------------------------
# 4. the scan sees the spellings a future edit will use
# --------------------------------------------------------------------------

OFFENDING: tuple[tuple[str, str], ...] = (
    (
        "module level compile",
        'import re\n\nTOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")\n',
    ),
    (
        "unanchored spelling, same answer under fullmatch",
        'import re\n\nTOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}")\n',
    ),
    (
        "inside a function body",
        'import re\n\n\ndef check(value):\n'
        '    return re.fullmatch(r"^[a-z][a-z0-9_.-]{0,127}$", value)\n',
    ),
    (
        "concatenated from same-file constants",
        'import re\n\nHEAD = "^[a-z]"\nTAIL = "[a-z0-9_.-]{0,127}$"\n'
        "TOKEN_RE = re.compile(HEAD + TAIL)\n",
    ),
    (
        "regex function imported from re",
        'from re import compile\n\nX = compile(r"^[a-z][a-z0-9_.-]{0,127}$")\n',
    ),
    (
        "restated as a shadowing local helper",
        'import re\n\nTOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")\n\n\n'
        "def _token(value):\n    return TOKEN.fullmatch(value)\n",
    ),
)
NON_OFFENDING: tuple[tuple[str, str], ...] = (
    (
        "a different bound is a different decision",
        'import re\n\nIDENTITY = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")\n',
    ),
    (
        "a different character class",
        'import re\n\nSLASHY = re.compile(r"^[a-z][a-z0-9_.:/#-]{0,199}$")\n',
    ),
    (
        "the shape used as data in an error message",
        'import re\n\nMESSAGE = "expected ^[a-z][a-z0-9_.-]{0,127}$"\n'
        "OK = re.compile(r\"^x$\")\n",
    ),
)


@pytest.mark.parametrize(("label", "source"), OFFENDING, ids=[r[0] for r in OFFENDING])
def test_the_scan_reports_a_restated_slug(label: str, source: str) -> None:
    rows = shape_rows(source, "loopx/capabilities/probe.py")
    assert rows, label
    assert offender_rows(rows, declared={}) != [], label


@pytest.mark.parametrize(
    ("label", "source"), NON_OFFENDING, ids=[r[0] for r in NON_OFFENDING]
)
def test_the_scan_leaves_a_different_decision_alone(label: str, source: str) -> None:
    assert shape_rows(source, "loopx/capabilities/probe.py") == [], label


def test_unfoldable_probe_is_forced_into_the_open() -> None:
    source = 'import re\n\n\ndef build(prefix):\n'
    source += '    return re.compile(prefix + "[a-z0-9_.-]{0,127}$")\n'
    name = "loopx/capabilities/probe.py"
    assert shape_rows(source, name) == []
    rows = unfoldable_rows(source, name)
    assert rows, "the unfoldable probe must be seen"
    assert unfoldable_offender_rows(rows, declared={}) != []


def test_declared_counts_are_enforced_in_both_directions() -> None:
    rows = collect(REPO_ROOT)
    assert offender_rows(rows) == []
    retired = [row for row in rows if str(row["file"]) !=
               "loopx/capabilities/periodic_report/audience.py"]
    assert offender_rows(retired) == [
        "loopx/capabilities/periodic_report/audience.py declares 1 site(s), found 0"
    ]
    doubled = rows + [
        {
            "file": "loopx/capabilities/periodic_report/audience.py",
            "line": 99,
            "kind": "slug_shape",
        }
    ]
    assert offender_rows(doubled) == [
        "loopx/capabilities/periodic_report/audience.py declares 1 site(s), found 2"
    ]
