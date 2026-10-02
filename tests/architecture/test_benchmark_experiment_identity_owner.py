"""Guard the single owner of the benchmark experiment identity vocabulary.

An arm role and a public-safe experiment token are decided once, in
``loopx/capabilities/benchmark_toolkit/experiment_identity.py``.  Before that
module existed, the token shape and its rejection helper were restated in five
toolkit modules, the four-name arm vocabulary in three of them plus the CLI that
admits a case slot -- nine places, two of which no name-keyed scan can see.

The guard decides on folded values, not spellings: a literal set, tuple, list or
``frozenset(...)`` of the four arm names is an offender wherever it appears
(including an ``argparse`` ``choices=`` argument), and so is a regex whose folded
value is the token shape, whether it is built at module level, inside a function,
or from same-file string constants.

What is deliberately *not* collapsed, and asserted here so nobody "finishes" the
job by merging a real distinction: subsets of the arm vocabulary (an anchor may
not be an ``explore`` arm), and token shapes that differ by an allowed character
or a bound -- those are other decisions with other rejection texts.
"""

from __future__ import annotations

import ast
import pathlib
from types import ModuleType

import pytest

from loopx.capabilities.benchmark_toolkit import experiment_identity as owner
from loopx.capabilities.benchmark_toolkit import (
    concurrency_envelope,
    experiment_board,
    factorial_contrast,
    study_projection,
    traex_evidence,
)
from loopx.cli_commands import benchmark_concurrency

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/capabilities/benchmark_toolkit/experiment_identity.py"
OWNER_MODULE = "loopx.capabilities.benchmark_toolkit.experiment_identity"

ARM_NAMES = {"baseline", "control", "treatment", "explore"}
TOKEN_BODY = r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$"

# Nothing outside the owner may decide either vocabulary.  A future site has to
# be converted or declared here with a reason, and a stale entry fails too.
DECLARED_INDIVIDUAL_SITES: dict[str, int] = {}

TOKEN_CONSUMERS: dict[str, ModuleType] = {
    "loopx/capabilities/benchmark_toolkit/study_projection.py": study_projection,
    "loopx/capabilities/benchmark_toolkit/experiment_board.py": experiment_board,
    "loopx/capabilities/benchmark_toolkit/concurrency_envelope.py": (
        concurrency_envelope
    ),
    "loopx/capabilities/benchmark_toolkit/factorial_contrast.py": factorial_contrast,
    "loopx/capabilities/benchmark_toolkit/traex_evidence.py": traex_evidence,
}
ARM_CONSUMERS: dict[str, ModuleType] = {
    "loopx/capabilities/benchmark_toolkit/study_projection.py": study_projection,
    "loopx/capabilities/benchmark_toolkit/experiment_board.py": experiment_board,
    "loopx/capabilities/benchmark_toolkit/concurrency_envelope.py": (
        concurrency_envelope
    ),
}

PRESCREEN_TOKENS = ("import re", "from re import")
ARM_PRESCREEN = "baseline"
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
    """Names the scan will not fold: re-bound, shadowed or duplicated anywhere."""

    module_targets: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                module_targets[name] = module_targets.get(name, 0) + 1
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
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


def regex_pattern_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        holder = func.value
        return (
            isinstance(holder, ast.Name)
            and holder.id == "re"
            and func.attr in REGEX_METHODS
        )
    return isinstance(func, ast.Name) and func.id in REGEX_METHODS


def string_elements(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"frozenset", "set", "tuple", "list", "sorted"} and node.args:
            return string_elements(node.args[0])
        return None
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        values = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        return values if len(values) == len(node.elts) else None
    return None


def identity_rows(source: str, name: str) -> list[dict[str, object]]:
    """Rows for every arm-vocabulary or token-shape decision inside ``source``."""

    rows: list[dict[str, object]] = []
    tree = ast.parse(source)
    if ARM_PRESCREEN in source:
        for node in ast.walk(tree):
            elements = string_elements(node)
            if elements is None or set(elements) != ARM_NAMES:
                continue
            if len(elements) != len(ARM_NAMES):
                continue
            rows.append(
                {
                    "file": name,
                    "line": getattr(node, "lineno", 0),
                    "kind": "arm_vocabulary",
                    "spelling": type(node).__name__,
                }
            )
    if any(token in source for token in PRESCREEN_TOKENS):
        constants = module_level_string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not regex_pattern_call(node):
                continue
            folded = fold_string(node.args[0], constants)
            if folded is None:
                continue
            if folded == TOKEN_BODY:
                rows.append(
                    {
                        "file": name,
                        "line": getattr(node, "lineno", 0),
                        "kind": "token_shape",
                        "spelling": "regex",
                    }
                )
    unique: dict[tuple[str, int, str], dict[str, object]] = {}
    for row in rows:
        unique.setdefault((str(row["file"]), int(row["line"]), str(row["kind"])), row)
    return list(unique.values())


def unfoldable_rows(source: str, name: str) -> list[dict[str, object]]:
    if not any(token in source for token in PRESCREEN_TOKENS):
        return []
    if "A-Za-z0-9" not in source:
        return []
    tree = ast.parse(source)
    constants = module_level_string_constants(tree)
    return [
        {"file": name, "line": node.lineno, "kind": "unfoldable_regex"}
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and regex_pattern_call(node)
        and fold_string(node.args[0], constants) is None
        and "A-Za-z" in ast.unparse(node.args[0])
    ]


def collect(root: pathlib.Path, scan_dir: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((root / scan_dir).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rows.extend(identity_rows(source, path.relative_to(root).as_posix()))
    return rows


def offender_rows(
    rows: list[dict[str, object]], *, declared: dict[str, int] | None = None
) -> list[str]:
    budget = DECLARED_INDIVIDUAL_SITES if declared is None else declared
    offenders: list[str] = []
    counted: dict[str, int] = {}
    for row in rows:
        file = str(row["file"])
        if file in budget:
            counted[file] = counted.get(file, 0) + 1
            continue
        if file == OWNER_PATH:
            continue
        offenders.append(f"{file}:{row['line']} restates the {row['kind']}")
    for file, allowed in budget.items():
        found = counted.get(file, 0)
        if found != allowed:
            offenders.append(
                f"{file} declares {allowed} site(s), found {found}"
            )
    return sorted(set(offenders))


# --------------------------------------------------------------------------
# 1. nothing outside the owner decides either vocabulary
# --------------------------------------------------------------------------


def test_no_product_module_restates_either_vocabulary() -> None:
    assert offender_rows(collect(REPO_ROOT, "loopx")) == []


def test_owner_source_is_scanned_at_all() -> None:
    # The scan reads the owner too; if it ever reports nothing there, the
    # prescreens are blind and every other row in this file is vacuous.
    assert collect(REPO_ROOT, "loopx")


def test_owner_states_both_vocabularies_once_each() -> None:
    rows = identity_rows((REPO_ROOT / OWNER_PATH).read_text("utf-8"), OWNER_PATH)
    assert sorted(str(row["kind"]) for row in rows) == [
        "arm_vocabulary",
        "arm_vocabulary",
        "token_shape",
    ]


# --------------------------------------------------------------------------
# 2. consumers hold the owner's objects and actually use them
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "module"), sorted(TOKEN_CONSUMERS.items()), ids=sorted(TOKEN_CONSUMERS)
)
def test_token_consumer_holds_the_owner_helper(path: str, module: ModuleType) -> None:
    assert module._token is owner.experiment_token_text
    tree = parse(module)
    assert referenced(tree, "_token"), f"{path} binds _token but never calls it"
    assert "_token" not in module_level_bindings(tree), f"{path} still defines _token"


@pytest.mark.parametrize(
    ("path", "module"), sorted(ARM_CONSUMERS.items()), ids=sorted(ARM_CONSUMERS)
)
def test_arm_consumer_holds_the_owner_set_and_applies_it(
    path: str, module: ModuleType
) -> None:
    assert module.ARM_ROLES is owner.ARM_ROLES
    tree = parse(module)
    assert referenced(tree, "ARM_ROLES"), f"{path} imports the set but does not use it"
    assert "ARM_ROLES" not in module_level_bindings(tree), f"{path} redefines the set"


def test_cli_admits_slots_with_the_owner_vocabulary_in_its_order() -> None:
    assert benchmark_concurrency.ARM_ROLE_CHOICES is owner.ARM_ROLE_CHOICES
    choices = parser_arm_choices()
    assert choices == list(owner.ARM_ROLE_CHOICES)
    assert sorted(choices) == sorted(ARM_NAMES)


def test_rejection_message_and_text_are_the_owners_not_a_copy() -> None:
    for module in TOKEN_CONSUMERS.values():
        with pytest.raises(ValueError) as caught:
            module._token("not a token", field="run_id")
        assert caught.value.args[0] == "run_id must be a compact public-safe token"


# --------------------------------------------------------------------------
# 3. the owner's decision, on the boundary a caller can reach
# --------------------------------------------------------------------------


def test_owner_states_the_two_shapes_as_recorded_literals() -> None:
    assert owner.ARM_ROLES == frozenset(ARM_NAMES)
    assert owner.ARM_ROLE_CHOICES == ("baseline", "control", "treatment", "explore")
    assert owner.EXPERIMENT_TOKEN_PATTERN.pattern == TOKEN_BODY


@pytest.mark.parametrize(
    "value",
    [
        "a",
        "run-7",
        "study:2026.09",
        "arm+baseline",
        "ns.name_at:sub+1-x",
        "A" * 128,
        "  padded_run  ",
    ],
)
def test_token_accepts_allowed_characters_and_the_128_bound(value: str) -> None:
    assert owner.experiment_token_text(value, field="case_id") == value.strip()


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "empty"),
        ("   ", "blank"),
        ("A" * 129, "one over the bound"),
        ("a#hash", "hash is not allowed"),
        ("a/b", "slash is not allowed"),
        ("a b", "inner space"),
        ("a\tb", "tab"),
        ("-leading", "must start alphanumeric"),
        (".leading", "must start alphanumeric"),
        ("caf\u00e9", "non-ascii"),
    ],
)
def test_token_rejects_outside_the_shape(value: str, reason: str) -> None:
    with pytest.raises(ValueError) as caught:
        owner.experiment_token_text(value, field="case_id")
    assert caught.value.args[0] == "case_id must be a compact public-safe token"


def test_owner_set_rejects_a_role_that_is_not_one_of_the_four() -> None:
    for role in ("baseline", "control", "treatment", "explore"):
        assert role in owner.ARM_ROLES
    for role in ("anchor", "", "Explore", "explor"):
        assert role not in owner.ARM_ROLES


# --------------------------------------------------------------------------
# 4. the scan sees the spellings a future edit will use
# --------------------------------------------------------------------------

ARM_OFFENDERS: tuple[tuple[str, str], ...] = (
    (
        "module level set literal",
        'ARM = {"baseline", "control", "treatment", "explore"}\n',
    ),
    (
        "frozenset call",
        "ARM = frozenset({\n    'baseline',\n    'control',\n    'treatment',\n"
        "    'explore',\n})\n",
    ),
    (
        "argparse choices list, the site this PR converted",
        "import argparse\n\np = argparse.ArgumentParser()\n"
        'p.add_argument("--arm-role", choices=["baseline", "control", '
        '"treatment", "explore"])\n',
    ),
    (
        "tuple inside a function body",
        "def build():\n"
        '    allowed = ("baseline", "control", "treatment", "explore")\n'
        "    return allowed\n",
    ),
    (
        "reordered set, same decision",
        'ARM = {"explore", "treatment", "control", "baseline"}\n',
    ),
    (
        "list passed to a validator",
        'check(values=["baseline", "control", "treatment", "explore"])\n',
    ),
)
ARM_ALLOWED: tuple[tuple[str, str], ...] = (
    (
        "an anchor subset is a different decision",
        'def anchor_ok(role):\n    return role not in {"baseline", "control", "treatment"}\n',
    ),
    (
        "derivation from the owner, not a restatement",
        "from .experiment_identity import ARM_ROLES\n\n"
        'OTHER_ROLES = ARM_ROLES - {"baseline"}\n',
    ),
    (
        "two of four names in prose, not a vocabulary",
        'NOTE = "baseline and control arms share the prompt"\n',
    ),
)
TOKEN_OFFENDERS: tuple[tuple[str, str], ...] = (
    (
        "module level compile",
        'import re\n\nTOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")\n',
    ),
    (
        "inside a function",
        'import re\n\n\ndef check(value):\n'
        '    return bool(re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$", value))\n',
    ),
    (
        "concatenated from same-file constants",
        'import re\n\nHEAD = "^[A-Za-z0-9]"\nTAIL = "[A-Za-z0-9_.:@+-]{0,127}$"\n'
        "TOKEN = re.compile(HEAD + TAIL)\n",
    ),
    (
        "imported regex name",
        'from re import compile\n\nTOKEN = compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")\n',
    ),
)
TOKEN_ALLOWED: tuple[tuple[str, str], ...] = (
    (
        "a shape that differs by one allowed character",
        'import re\n\nSLASH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$")\n',
    ),
    (
        "the same class with another bound",
        'import re\n\nSHORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,63}$")\n',
    ),
)


@pytest.mark.parametrize(
    ("label", "source"), ARM_OFFENDERS, ids=[row[0] for row in ARM_OFFENDERS]
)
def test_the_scan_reports_a_restated_arm_vocabulary(label: str, source: str) -> None:
    rows = identity_rows(source, "loopx/capabilities/benchmark_toolkit/probe.py")
    assert [row["kind"] for row in rows] == ["arm_vocabulary"], label


@pytest.mark.parametrize(
    ("label", "source"), TOKEN_OFFENDERS, ids=[row[0] for row in TOKEN_OFFENDERS]
)
def test_the_scan_reports_a_restated_token_shape(label: str, source: str) -> None:
    rows = identity_rows(source, "loopx/capabilities/benchmark_toolkit/probe.py")
    assert [row["kind"] for row in rows] == ["token_shape"], label


@pytest.mark.parametrize(
    ("label", "source"), ARM_ALLOWED + TOKEN_ALLOWED, ids=[r[0] for r in ARM_ALLOWED + TOKEN_ALLOWED]
)
def test_the_scan_leaves_a_different_decision_alone(label: str, source: str) -> None:
    rows = identity_rows(source, "loopx/capabilities/benchmark_toolkit/probe.py")
    assert rows == [], label


def test_a_declared_site_is_required_to_exist() -> None:
    rows = identity_rows('ARM = {"baseline", "control", "treatment", "explore"}\n',
                         "loopx/capabilities/benchmark_toolkit/probe.py")
    assert offender_rows(rows, declared={"loopx/capabilities/benchmark_toolkit/probe.py": 1}) == []
    assert offender_rows(rows, declared={"loopx/capabilities/benchmark_toolkit/probe.py": 2}) == [
        "loopx/capabilities/benchmark_toolkit/probe.py declares 2 site(s), found 1"
    ]


def test_unfoldable_token_construction_cannot_hide() -> None:
    source = (
        "import re\n\n\ndef build(prefix):\n"
        '    return re.compile(prefix + "[A-Za-z0-9_.:@+-]{0,127}$")\n'
    )
    assert identity_rows(source, "loopx/capabilities/benchmark_toolkit/probe.py") == []
    rows = unfoldable_rows(source, "loopx/capabilities/benchmark_toolkit/probe.py")
    assert [row["kind"] for row in rows] == ["unfoldable_regex"]
    assert offender_rows(rows, declared={}) != []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def parse(module: ModuleType) -> ast.Module:
    path = pathlib.Path(str(module.__file__)).resolve()
    return ast.parse(path.read_text(encoding="utf-8"))


def module_level_bindings(tree: ast.Module) -> set[str]:
    """Names bound at module level, including ``def`` and ``class``.

    A consumer can shadow the imported owner helper with a local ``def _token``
    placed above the import; behaviourally that copy is dead code, structurally
    it is the duplicate the guard exists to prevent, so a function name counts as
    a binding here.
    """

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


def parser_arm_choices() -> list[str]:
    """Read the ``choices`` the CLI actually hands argparse."""

    tree = parse(benchmark_concurrency)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.keywords:
            continue
        if getattr(node.func, "attr", "") != "add_argument":
            continue
        for keyword in node.keywords:
            if keyword.arg == "choices":
                if isinstance(keyword.value, ast.Name):
                    assert keyword.value.id == "ARM_ROLE_CHOICES"
                    return list(benchmark_concurrency.ARM_ROLE_CHOICES)
    raise AssertionError("no argparse choices= argument found")
