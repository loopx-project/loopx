"""One owner decides which progress-review signals exist.

``("noul", "choice")`` answers one question -- which typed signals a progress
review receipt and the autonomous replan obligation may name -- and three modules
answered it: the control-plane policy vocabulary, the receipt's drift-signal key
set, and the external-review trigger. The policy module already declares itself
the owner and the capability package re-exports it, so the other two now import
the owner's object instead of restating the value.

The delegating modules keep their public names (``PROGRESS_REVIEW_SIGNAL_KEYS``
and ``EXTERNAL_PROGRESS_REVIEW_SIGNALS``) through an aliased import, so no public
attribute is removed. Layer 1 scans for the folded value, layer 2 pins the
delegating modules to the owner's object and reconciles the importer set, and
layer 3 records the owner's verdict.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
from types import ModuleType

import pytest

from loopx.capabilities import configuration_ui
from loopx.capabilities.progress_review import policy as capability_policy
from loopx.capabilities.progress_review import receipt
from loopx.cli_commands import registry_admin_configure
from loopx.control_plane.work_items import (
    external_progress_review,
    progress_review_policy,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/control_plane/work_items/progress_review_policy.py"
OWNER_NAME = "PROGRESS_REVIEW_SIGNALS"
SIGNAL_VALUE = ("noul", "choice")

DECLARED_INDIVIDUAL_SITES: dict[str, int] = {}

CONSUMERS: dict[str, tuple[ModuleType, str]] = {
    "loopx/capabilities/configuration_ui.py": (configuration_ui, OWNER_NAME),
    "loopx/capabilities/progress_review/receipt.py": (
        receipt,
        "PROGRESS_REVIEW_SIGNAL_KEYS",
    ),
    "loopx/capabilities/progress_review/policy.py": (capability_policy, OWNER_NAME),
    "loopx/cli_commands/registry_admin_configure.py": (
        registry_admin_configure,
        OWNER_NAME,
    ),
    "loopx/control_plane/work_items/external_progress_review.py": (
        external_progress_review,
        "EXTERNAL_PROGRESS_REVIEW_SIGNALS",
    ),
}


def literal_value(node: ast.AST) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    values: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append(element.value)
    return tuple(values)


def signal_rows(source: str, name: str) -> list[dict[str, object]]:
    if "noul" not in source:
        return []
    tree = ast.parse(source)
    return [
        {"file": name, "line": node.lineno, "kind": "signal_vocabulary"}
        for node in ast.walk(tree)
        if literal_value(node) == SIGNAL_VALUE
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
        rows.extend(signal_rows(source, relative))
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
        found.append(f"{file}:{row['line']} restates the progress-review signal set")
    for file, allowed in budget.items():
        if counted.get(file, 0) != allowed:
            found.append(
                f"{file} declares {allowed} site(s), found {counted.get(file, 0)}"
            )
    return sorted(set(found))


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


def referenced(tree: ast.Module, name: str) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == name for node in ast.walk(tree)
    )


def parse(module: ModuleType) -> ast.Module:
    path = pathlib.Path(str(module.__file__)).resolve()
    return ast.parse(path.read_text(encoding="utf-8"))


def test_no_other_module_states_the_signal_set() -> None:
    assert offenders(collect(REPO_ROOT)) == []


def test_every_module_that_imports_the_owner_is_a_declared_consumer() -> None:
    importing = modules_importing_the_owner() - {OWNER_PATH}
    assert sorted(importing) == sorted(CONSUMERS)


@pytest.mark.parametrize("path", sorted(CONSUMERS), ids=sorted(CONSUMERS))
def test_consumer_holds_the_owner_object_and_applies_it(path: str) -> None:
    module, local_name = CONSUMERS[path]
    assert getattr(module, local_name) is progress_review_policy.PROGRESS_REVIEW_SIGNALS
    applies = referenced(parse(module), local_name) or local_name in getattr(
        module, "__all__", ()
    )
    assert applies, f"{path} imports the set but never applies it"


def test_the_owner_states_the_recorded_signal_set() -> None:
    assert progress_review_policy.PROGRESS_REVIEW_SIGNALS == SIGNAL_VALUE


@pytest.mark.parametrize("value", SIGNAL_VALUE)
def test_owner_accepts_a_known_signal(value: str) -> None:
    assert progress_review_policy.normalize_progress_review_signal(value) == value


@pytest.mark.parametrize("value", ["Noul", "drift", "", "choice,noul"])
def test_owner_rejects_an_unknown_signal(value: str) -> None:
    with pytest.raises(ValueError):
        progress_review_policy.normalize_progress_review_signal(value)


def configure_goal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    registry_admin_configure.register_configure_goal_command(subparsers)
    return subparsers.choices["configure-goal"]


def parser_choices(parser: argparse.ArgumentParser, option: str) -> tuple[str, ...]:
    for action in parser._actions:
        if option in action.option_strings:
            choices = action.choices or ()
            return tuple(choices)
    raise AssertionError(f"{option} is not registered")


def test_cli_signal_choices_follow_the_owner() -> None:
    parser = configure_goal_parser()
    assert parser_choices(parser, "--progress-review-signal") == SIGNAL_VALUE
    assert (
        parser_choices(parser, "--progress-review-mode")
        == progress_review_policy.PROGRESS_REVIEW_MODES
    )


def test_cli_rejects_a_signal_the_owner_does_not_declare() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    registry_admin_configure.register_configure_goal_command(subparsers)
    with pytest.raises(SystemExit):
        parser.parse_args(["configure-goal", "--progress-review-signal", "drift"])


OFFENDING = (
    ("tuple literal", 'SIGNALS = ("noul", "choice")\n'),
    ("list literal", 'SIGNALS = ["noul", "choice"]\n'),
    (
        "inside a function",
        'def allowed(value):\n    return value in ("noul", "choice")\n',
    ),
)
NON_OFFENDING = (
    ("a subset is a different decision", 'SIGNALS = ("noul",)\n'),
    (
        "an extended set is a different decision",
        'SIGNALS = ("noul", "choice", "drift")\n',
    ),
)


@pytest.mark.parametrize(
    ("label", "source"), OFFENDING, ids=[row[0] for row in OFFENDING]
)
def test_the_scan_reports_a_restated_signal_set(label: str, source: str) -> None:
    rows = signal_rows(source, "loopx/capabilities/probe.py")
    assert rows, label
    assert offenders(rows, declared={}) != [], label


@pytest.mark.parametrize(
    ("label", "source"), NON_OFFENDING, ids=[row[0] for row in NON_OFFENDING]
)
def test_the_scan_leaves_a_different_decision_alone(label: str, source: str) -> None:
    assert signal_rows(source, "loopx/capabilities/probe.py") == [], label


def test_declared_counts_are_enforced_in_both_directions() -> None:
    declared = {"loopx/capabilities/probe.py": 1}
    assert offenders([], declared=declared) == [
        "loopx/capabilities/probe.py declares 1 site(s), found 0"
    ]
