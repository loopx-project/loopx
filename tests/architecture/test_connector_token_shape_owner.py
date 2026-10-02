"""Guard the single owner of the connector token shape.

``[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`` decides whether a value may be carried as a
connector token -- a capability id, an inbox id, an effect id or an app id -- and
on the base revision three extension modules compiled it themselves:
``external_connector_runtime.py``, ``external_connector_provider.py`` (which
already imports eleven other names from the runtime module) and
``lark/document_comment_provider.py``.

The runtime module is the owner because the provider already asks it for the
other connector contracts; that direction of import is the evidence, not a
preference.

Neighbours this guard deliberately does not merge, and pins as probes: the same
character class carries other bounds in the tree (``{0,127}``, ``{0,159}``,
``{0,255}``), and ``SAFE_SCOPE_PATTERN`` adds ``/`` while ``SAFE_PROFILE_PATTERN``
drops ``:``.  A bound or a character class is a different answer to a different
question, so collapsing them would be a product change.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import pytest

from loopx.extensions import external_connector_provider as provider
from loopx.extensions import external_connector_runtime as owner
from loopx.extensions.lark import document_comment_provider as lark_document

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/extensions/external_connector_runtime.py"
TOKEN_BODY = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}"
MARKER = "[A-Za-z0-9._:-]"

CONSUMERS: dict[str, tuple[ModuleType, str]] = {
    "loopx/extensions/external_connector_provider.py": (
        provider,
        "SAFE_TOKEN_PATTERN",
    ),
    "loopx/extensions/lark/document_comment_provider.py": (
        lark_document,
        "SAFE_TOKEN_PATTERN",
    ),
}
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
PRESCREEN_TOKENS = ("import re", "from re import")
FOLD_DEPTH_LIMIT = 4
# A regex construction whose value cannot be folded must be declared here.
DECLARED_UNFOLDABLE_SITES: dict[str, int] = {}


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
            folded = fold_string(value, {k: v for k, v in constants.items() if k != name})
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
            if (
                isinstance(holder, ast.Name)
                and holder.id == "re"
                and func.attr in REGEX_METHODS
            ):
                calls.append(node)
        elif isinstance(func, ast.Name) and func.id in REGEX_METHODS:
            calls.append(node)
    return calls


def shape_rows(source: str, name: str) -> list[dict[str, object]]:
    if not any(token in source for token in PRESCREEN_TOKENS) or MARKER not in source:
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    rows: list[dict[str, object]] = []
    for node in regex_calls(tree):
        folded = fold_string(node.args[0], constants)
        if folded is None:
            continue
        if folded.removeprefix("^").removesuffix("$") == TOKEN_BODY:
            rows.append({"file": name, "line": node.lineno, "kind": "token_shape"})
    return rows


def unfoldable_rows(source: str, name: str) -> list[dict[str, object]]:
    if not any(token in source for token in PRESCREEN_TOKENS) or MARKER not in source:
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    return [
        {"file": name, "line": node.lineno, "kind": "unfoldable"}
        for node in regex_calls(tree)
        if fold_string(node.args[0], constants) is None
        and MARKER in ast.unparse(node.args[0])
    ]


def collect() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    shapes: list[dict[str, object]] = []
    unfoldable: list[dict[str, object]] = []
    for path in sorted((REPO_ROOT / "loopx").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        shapes.extend(shape_rows(source, relative))
        unfoldable.extend(unfoldable_rows(source, relative))
    return shapes, unfoldable


def module_level_bindings(tree: ast.Module) -> set[str]:
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
# 1. one owner for this shape
# --------------------------------------------------------------------------


def test_only_the_owner_module_compiles_the_connector_token_shape() -> None:
    shapes, _ = collect()
    assert {str(row["file"]) for row in shapes} == {OWNER_PATH}


def test_owner_states_the_shape_as_the_recorded_literal() -> None:
    assert owner.SAFE_TOKEN_PATTERN.pattern == TOKEN_BODY


def test_no_module_hides_an_unfoldable_token_construction() -> None:
    _, unfoldable = collect()
    offenders = [
        f"{row['file']}:{row['line']}"
        for row in unfoldable
        if str(row["file"]) not in DECLARED_UNFOLDABLE_SITES
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# 2. the two consumers are wired, and still use it
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "entry"), sorted(CONSUMERS.items()), ids=sorted(CONSUMERS)
)
def test_consumer_holds_the_owner_object_and_applies_it(path: str, entry: tuple) -> None:
    module, name = entry
    assert getattr(module, name) is owner.SAFE_TOKEN_PATTERN
    tree = parse(module)
    assert referenced(tree, name), f"{path} imports the shape but never applies it"
    assert name not in module_level_bindings(tree), f"{path} compiles its own copy"
    source = pathlib.Path(str(module.__file__)).resolve().read_text("utf-8")
    assert shape_rows(source, path) == [], f"{path} restates the shape"


def test_the_provider_still_asks_the_runtime_for_its_other_contracts() -> None:
    # Ownership follows the direction the tree already imports in; if that
    # direction ever reverses, this guard's premise has to be re-checked.
    tree = parse(provider)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
    }
    assert "external_connector_runtime" in imports


# --------------------------------------------------------------------------
# 3. the owner's answer
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "a",
        "connector-1",
        "app.id:2",
        "ns_name.sub-doma.in",
        "a" * 200,
        "A1",
    ],
)
def test_owner_accepts_a_connector_token(value: str) -> None:
    assert owner.SAFE_TOKEN_PATTERN.fullmatch(value)


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("", "empty"),
        ("-lead", "must start alphanumeric"),
        (".lead", "must start alphanumeric"),
        (":lead", "must start alphanumeric"),
        ("a b", "space"),
        ("a/b", "slash belongs to the scope shape"),
        ("a@b", "at sign"),
        ("a\tb", "tab"),
        ("a" * 201, "one over the bound"),
        ("caf\u00e9", "non-ascii"),
    ],
)
def test_owner_rejects_outside_the_shape(value: str, why: str) -> None:
    assert owner.SAFE_TOKEN_PATTERN.fullmatch(value) is None, why


def test_the_200_character_bound_is_measured_at_the_edge() -> None:
    assert owner.SAFE_TOKEN_PATTERN.fullmatch("a" * 200)
    assert owner.SAFE_TOKEN_PATTERN.fullmatch("a" * 201) is None


def test_neighbouring_shapes_are_still_their_own_decisions() -> None:
    # Not merged on purpose: a different character class or bound is a
    # different answer, so this PR must not quietly absorb them.
    assert provider.SAFE_SCOPE_PATTERN.pattern != owner.SAFE_TOKEN_PATTERN.pattern
    assert lark_document.SAFE_PROFILE_PATTERN.pattern != (
        owner.SAFE_TOKEN_PATTERN.pattern
    )


# --------------------------------------------------------------------------
# 4. the scan sees the spellings a future edit will use
# --------------------------------------------------------------------------

OFFENDING: tuple[tuple[str, str], ...] = (
    (
        "module level compile",
        'import re\n\nTOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")\n',
    ),
    (
        "anchored spelling, same answer under fullmatch",
        'import re\n\nTOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")\n',
    ),
    (
        "inside a function",
        'import re\n\n\ndef check(value):\n'
        '    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value)\n',
    ),
    (
        "assembled from same-file constants",
        'import re\n\nHEAD = "[A-Za-z0-9]"\nTAIL = "[A-Za-z0-9._:-]{0,199}"\n'
        "TOKEN = re.compile(HEAD + TAIL)\n",
    ),
    (
        "regex imported straight from re",
        'from re import compile\n\nX = compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")\n',
    ),
)
NON_OFFENDING: tuple[tuple[str, str], ...] = (
    (
        "the scope shape, which allows a slash",
        'import re\n\nSCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")\n',
    ),
    (
        "the profile shape, which drops the colon",
        'import re\n\nPROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")\n',
    ),
    (
        "the same class at a different bound",
        'import re\n\nSHORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")\n',
    ),
    (
        "the class as data inside a longer grammar",
        'import re\n\nCOMPOUND = re.compile(r"^[a-z][a-z0-9._-]{0,30}:[A-Za-z0-9._:-]{1,200}$")\n',
    ),
)


@pytest.mark.parametrize(
    ("label", "source"), OFFENDING, ids=[row[0] for row in OFFENDING]
)
def test_the_scan_reports_a_restated_token_shape(label: str, source: str) -> None:
    rows = shape_rows(source, "loopx/extensions/probe.py")
    assert rows, label
    assert {str(row["file"]) for row in rows} != {OWNER_PATH}, label


@pytest.mark.parametrize(
    ("label", "source"), NON_OFFENDING, ids=[row[0] for row in NON_OFFENDING]
)
def test_the_scan_leaves_a_different_decision_alone(label: str, source: str) -> None:
    assert shape_rows(source, "loopx/extensions/probe.py") == [], label


def test_an_unfoldable_construction_is_forced_into_the_open() -> None:
    source = (
        'import re\n\n\ndef build(part):\n'
        '    return re.compile("[A-Za-z0-9]" + part + "[A-Za-z0-9._:-]{0,199}")\n'
    )
    name = "loopx/extensions/probe.py"
    assert shape_rows(source, name) == []
    rows = unfoldable_rows(source, name)
    assert rows
    offenders = [
        str(row["file"])
        for row in rows
        if str(row["file"]) not in DECLARED_UNFOLDABLE_SITES
    ]
    assert offenders == [name]
