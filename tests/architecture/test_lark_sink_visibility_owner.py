"""Guard the single owner of the Lark sink visibility pair.

``owner-only`` / ``shared`` decide one thing: whether a Lark projection sink may
redact and then write rows that leave the owner's machine.  On the base revision
four modules answered it independently.  ``extensions/lark/presentation/kanban.py``
and ``extensions/lark/presentation/explore_results.py`` each bound all three names
with their own module-level assignments, and the two CLI parsers that feed those
sinks -- ``cli_commands/lark_kanban.py`` and ``cli_commands/explore_feishu_commands.py``
-- spelled the accept set out a second time as an inline ``choices`` list plus a
``default`` string.  The inline spelling is why this guard reads literals by
value: a name-based scan never sees ``choices=["owner-only", "shared"]``.

Decisions are taken on the folded value, not the spelling.  A restated name, a
restated set, a ``frozenset`` variant and an inline ``choices`` list are all
reported, and a construction this scan cannot fold has to be declared with its
file and count.

Neighbour this guard deliberately does not merge, and pins as a probe:
``extensions/manifest.py`` carries ``_PRESENTATION_SURFACE_VISIBILITIES`` as
``{"public-safe", "owner-only"}``.  It reuses the word ``owner-only`` to answer a
different question -- whether an advertised extension surface is public-safe --
so collapsing the two would widen one accept set on the strength of a shared
spelling.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import pathlib
from types import ModuleType

import pytest

from loopx.capabilities.explore.result_log import EXPLORE_RESULT_PROJECTION_VERSION
from loopx.cli_commands.explore_feishu_commands import register_explore_feishu_commands
from loopx.cli_commands.lark_kanban import register_lark_kanban_commands
from loopx.extensions import manifest as extension_manifest
from loopx.extensions.lark.presentation import explore_results, kanban
from loopx.extensions.lark.presentation import sink_visibility as owner

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/extensions/lark/presentation/sink_visibility.py"

OWNER_ONLY = "owner-only"
SHARED = "shared"
GUARDED_NAMES = frozenset(
    {
        "SINK_VISIBILITY_OWNER_ONLY",
        "SINK_VISIBILITY_SHARED",
        "SINK_VISIBILITIES",
    }
)
PAIR_VALUES = frozenset({OWNER_ONLY, SHARED})
# Both CLI parsers render ``sorted(SINK_VISIBILITIES)`` in their help text, so the
# rendered order is part of the contract even though the carrier is a set.
RENDERED_PAIR = [OWNER_ONLY, SHARED]

# A file speaks about this decision when it carries the operator-facing flag or
# the keyword argument.  Without this gate the bare word ``shared`` -- which the
# tree uses for unrelated state -- would flood the census.
PRESCREEN_TOKENS = ("sink_visibility", "sink-visibility")

# Each entry is the module, its public sink entry point, and the smallest
# projection that reaches the visibility gate instead of failing an earlier one.
SINK_CONSUMERS: dict[str, tuple[ModuleType, str, dict]] = {
    "loopx/extensions/lark/presentation/kanban.py": (
        kanban,
        "sync_loopx_projection_to_lark_kanban",
        {},
    ),
    "loopx/extensions/lark/presentation/explore_results.py": (
        explore_results,
        "sync_explore_results_to_lark",
        {"schema_version": EXPLORE_RESULT_PROJECTION_VERSION},
    ),
}
CLI_CONSUMERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "loopx/cli_commands/lark_kanban.py": (
        "lark-kanban sync-projection",
        ("lark-kanban", "sync-projection"),
    ),
    "loopx/cli_commands/explore_feishu_commands.py": ("feishu-sync", ("feishu-sync",)),
}


def module_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def module_level_binds(source: str) -> list[tuple[int, str]]:
    """Name the module-level assignments that restate a guarded name."""

    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in GUARDED_NAMES:
                found.append((node.lineno, target.id))
    return found


def inline_pair_sites(source: str) -> list[tuple[int, str]]:
    """Name every string literal that carries one half of the pair."""

    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in PAIR_VALUES:
            assert isinstance(node.value, str)
            found.append((node.lineno, node.value))
    return found


def scan_tree() -> tuple[list[str], list[str]]:
    """Return (restated-name findings, inline-literal findings) across ``loopx/``."""

    name_findings: list[str] = []
    literal_findings: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == OWNER_PATH:
            continue
        source = module_source(path)
        for lineno, name in module_level_binds(source):
            name_findings.append(f"{rel}:{lineno} rebinds {name}")
        if any(token in source for token in PRESCREEN_TOKENS):
            for lineno, value in inline_pair_sites(source):
                literal_findings.append(f"{rel}:{lineno} spells out {value!r}")
    return name_findings, literal_findings


def _no_op(parser: argparse.ArgumentParser) -> None:
    """Stand in for the shared ``--format`` / config-path adders."""


def build_parser(route: tuple[str, ...]) -> argparse.ArgumentParser:
    """Build the real CLI tree and return the parser that carries the flag."""

    root = argparse.ArgumentParser(prog="loopx")
    subparsers = root.add_subparsers(dest="cmd")
    if route[0] == "lark-kanban":
        register_lark_kanban_commands(subparsers, _no_op)
    else:
        register_explore_feishu_commands(subparsers, _no_op, _no_op, _no_op)
    parser: argparse.ArgumentParser = subparsers.choices[route[0]]
    for name in route[1:]:
        nested = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        assert nested, f"{parser.prog} has no subcommands to reach {name!r}"
        parser = nested[0].choices[name]
    return parser


def sink_visibility_action(parser: argparse.ArgumentParser) -> argparse.Action:
    for action in parser._actions:
        if "--sink-visibility" in action.option_strings:
            return action
    raise AssertionError(f"{parser.prog} no longer parses --sink-visibility")


def test_only_the_owner_module_binds_the_guarded_names() -> None:
    name_findings, _ = scan_tree()
    assert name_findings == [], "\n".join(name_findings)


def test_no_sink_visibility_module_spells_the_pair_out_again() -> None:
    _, literal_findings = scan_tree()
    assert literal_findings == [], "\n".join(literal_findings)


def test_the_owner_states_the_pair_as_the_recorded_literals() -> None:
    assert owner.SINK_VISIBILITY_OWNER_ONLY == OWNER_ONLY
    assert owner.SINK_VISIBILITY_SHARED == SHARED
    assert owner.SINK_VISIBILITIES == {OWNER_ONLY, SHARED}
    assert sorted(owner.SINK_VISIBILITIES) == RENDERED_PAIR


def test_the_owner_is_a_leaf_that_exports_only_the_pair() -> None:
    tree = ast.parse(module_source(REPO_ROOT / OWNER_PATH))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(f"relative={node.level}:{node.module}")
        elif isinstance(node, ast.Import):
            imports.extend(f"absolute:{alias.name}" for alias in node.names)
    assert imports == ["relative=0:__future__"], imports

    exported = {name for name in dir(owner) if name.startswith("SINK_VISIBILIT")}
    assert exported == set(GUARDED_NAMES), sorted(exported)


@pytest.mark.parametrize(
    ("path", "entry"), sorted(SINK_CONSUMERS.items()), ids=sorted(SINK_CONSUMERS)
)
def test_each_sink_holds_the_owner_object_and_applies_it(
    path: str, entry: tuple[ModuleType, str, dict]
) -> None:
    module, function_name, projection = entry
    for name in sorted(GUARDED_NAMES):
        assert getattr(module, name) is getattr(owner, name), f"{path} {name}"
    bound_names = {name for _, name in module_level_binds(module_source(REPO_ROOT / path))}
    assert bound_names == set(), f"{path} defines {sorted(bound_names)} itself"

    sink = getattr(module, function_name)
    with pytest.raises(ValueError) as refused:
        sink(None, projection=projection, sink_visibility="public-safe")
    assert str(refused.value) == (
        f"sink_visibility must be one of {sorted(owner.SINK_VISIBILITIES)}"
    ), path


def test_the_shared_value_still_unlocks_the_projection_path() -> None:
    """``shared`` must stay accepted at both sinks, not merely parse.

    The kanban sink answers with the dry-run packet it would write; the explore
    sink has already passed the visibility gate by the time it notices the
    projection carries no goal id.  Either way the failure asserted against is a
    *later* rule, which is what proves the gate itself opened.
    """

    packet = kanban.sync_loopx_projection_to_lark_kanban(None, projection={}, sink_visibility=SHARED)
    assert packet["sink_visibility"] == SHARED
    assert packet["execute"] is False

    with pytest.raises(ValueError) as later:
        explore_results.sync_explore_results_to_lark(
            None,
            projection={"schema_version": EXPLORE_RESULT_PROJECTION_VERSION},
            sink_visibility=SHARED,
        )
    assert "sink_visibility" not in str(later.value)
    assert str(later.value) == "projection is missing goal_id"


@pytest.mark.parametrize(
    ("path", "entry"), sorted(CLI_CONSUMERS.items()), ids=sorted(CLI_CONSUMERS)
)
def test_each_cli_parser_builds_its_choices_from_the_owner(
    path: str, entry: tuple[str, tuple[str, ...]]
) -> None:
    command, route = entry
    action = sink_visibility_action(build_parser(route))
    assert list(action.choices) == RENDERED_PAIR, path
    assert action.default is owner.SINK_VISIBILITY_OWNER_ONLY, path
    assert command, path


@pytest.mark.parametrize(
    "route", sorted({entry[1] for entry in CLI_CONSUMERS.values()})
)
def test_both_parsers_still_refuse_a_value_outside_the_pair(route: tuple[str, ...]) -> None:
    parser = build_parser(route)
    with pytest.raises(SystemExit):
        parser.parse_args(["--sink-visibility", "public-safe"])
    assert sink_visibility_action(parser).default == OWNER_ONLY


@pytest.mark.parametrize(
    ("path", "entry"), sorted(SINK_CONSUMERS.items()), ids=sorted(SINK_CONSUMERS)
)
def test_the_private_branch_is_still_the_default(
    path: str, entry: tuple[ModuleType, str, dict]
) -> None:
    """Nothing may widen the default onto the redacted-but-shared path."""

    module, function_name, _ = entry
    default = inspect.signature(getattr(module, function_name)).parameters["sink_visibility"].default
    assert default is owner.SINK_VISIBILITY_OWNER_ONLY, path


def test_the_manifest_surface_vocabulary_is_a_different_decision() -> None:
    surface = extension_manifest._PRESENTATION_SURFACE_VISIBILITIES
    assert surface == {"public-safe", OWNER_ONLY}
    assert surface != owner.SINK_VISIBILITIES
    assert SHARED not in surface
    assert "public-safe" not in owner.SINK_VISIBILITIES


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("restated pair as a set", 'SINK_VISIBILITIES = {"owner-only", "shared"}\n'),
        ("restated default", 'SINK_VISIBILITY_OWNER_ONLY = "owner-only"\n'),
        (
            "inline argparse choices",
            "def add(parser):\n"
            '    parser.add_argument("--sink-visibility", choices=["owner-only", "shared"])\n',
        ),
        ("inline argparse default", 'def add(parser):\n    parser.add_argument(default="shared")\n'),
        (
            "frozenset variant",
            'SHARED = "shared"\n'
            'SINK_VISIBILITY_OWNER_ONLY = "owner-only"\n'
            "SINK_VISIBILITIES = frozenset([SINK_VISIBILITY_OWNER_ONLY, SHARED])\n",
        ),
    ],
    ids=["set", "default", "choices", "choices-default", "frozenset"],
)
def test_the_scan_reports_a_restated_pair(label: str, source: str) -> None:
    assert module_level_binds(source) or inline_pair_sites(source), label


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("importing the owner", "from .sink_visibility import SINK_VISIBILITIES\n"),
        (
            "values arrive folded",
            "def add(parser, choices):\n"
            '    parser.add_argument("--sink-visibility", choices=choices)\n',
        ),
        (
            "the manifest decision elsewhere",
            '_PRESENTATION_SURFACE_VISIBILITIES = {"public-safe", "owner-only"}\n',
        ),
    ],
    ids=["import", "folded", "other-domain"],
)
def test_the_scan_leaves_a_different_decision_alone(label: str, source: str) -> None:
    assert module_level_binds(source) == [], label
    if any(token in source for token in PRESCREEN_TOKENS):
        assert inline_pair_sites(source) == [], label


def test_the_relevance_gate_does_not_blind_the_name_layer() -> None:
    """A file that never mentions the flag is still caught by the name scan."""

    source = 'SINK_VISIBILITY_SHARED = "shared"\n'
    assert not any(token in source for token in PRESCREEN_TOKENS)
    assert module_level_binds(source) == [(1, "SINK_VISIBILITY_SHARED")]
