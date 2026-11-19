"""Guard one owner for the backlog-hygiene bullet grammar (#4447).

``BACKLOG_HYGIENE_BULLET_PATTERN`` in ``control_plane/status/active_state_projection.py``
is the compiled grammar for a Next Action / Operating Lessons bullet
(``^\\s*(?:[-*]|\\d+[.)])\\s+(.+?)\\s*$``). ``state_projection.py`` restated the *same*
compiled regex under a different name (``BULLET_PATTERN``) to split its Next Action
section into entries. ``state_projection`` *already imports* ``SECTION_HEADING_PATTERN``
from that very owner module, so borrowing one more compiled pattern adds no new
import edge and cannot introduce a cycle. The restated regex is retired and
``_section_entries`` now matches against the single owner.

Two existing machines miss this. The by-name semantic inventory only catalogues
``UPPER_NAME`` carriers and would not pair two differently named bindings; the
status-facade vocabulary-owners machine is also name-keyed, so a copy renamed to
``BULLET_PATTERN`` slips past it (that machine is extended separately to record the
consumer as an importer of the owner name). This guard keys on the **compiled
pattern literal** instead, so a renamed restatement is caught, while the genuinely
different neighbours -- the owner's own ``SECTION_HEADING_PATTERN`` and the
consumer's ``PRIORITY_PATTERN`` -- are never swept.

Two layers: a whole-tree compiled-pattern census (only the owner compiles it), and
behavioural parity through the consumer's public ``active_state_next_action_entries``
gate -- marker stripping, non-bullet passthrough and continuation grouping -- so a
migration that silently changed which lines count as bullets fails here.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from loopx import state_projection as consumer
from loopx.control_plane.status import active_state_projection as owner
from loopx.control_plane.todos.contract import compact_todo_text

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "loopx"
OWNER_PATH = "loopx/control_plane/status/active_state_projection.py"
CONSUMER_PATH = "loopx/state_projection.py"

PATTERN = r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$"
SECTION_HEADING_PATTERN = r"^##+\s+(.+?)\s*$"
PRIORITY_PATTERN = r"^\[(P[0-4])\]\s+(.+)$"


def _compiled_pattern_sites(source: str, pattern: str) -> list[int]:
    """Every ``re.compile(<literal>)`` call in the source whose pattern matches.

    Keys on the pattern literal, so a restatement under any name (or with extra
    flags) is still one site; a bare pattern string that is never compiled is not
    a compiled-regex restatement and is left alone.
    """

    tree = ast.parse(source)
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname != "compile":
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == pattern
        ):
            sites.append(node.lineno)
    return sites


@pytest.fixture(scope="module")
def tree_offenders() -> dict[str, list[int]]:
    offenders: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER_PATH:
            continue
        sites = _compiled_pattern_sites(path.read_text(encoding="utf-8"), PATTERN)
        if sites:
            offenders[relative] = sites
    return offenders


def test_only_the_owner_compiles_the_bullet_pattern(tree_offenders) -> None:
    assert tree_offenders == {}, (
        f"a restatement of the backlog-hygiene bullet grammar remains: {tree_offenders}"
    )


def test_the_owner_compiles_the_recorded_pattern_once() -> None:
    assert owner.BACKLOG_HYGIENE_BULLET_PATTERN.pattern == PATTERN
    source = (REPO_ROOT / OWNER_PATH).read_text(encoding="utf-8")
    assert len(_compiled_pattern_sites(source, PATTERN)) == 1


def test_state_projection_borrows_the_owner_object() -> None:
    assert (
        consumer.BACKLOG_HYGIENE_BULLET_PATTERN is owner.BACKLOG_HYGIENE_BULLET_PATTERN
    )


def test_state_projection_retired_its_restated_regex() -> None:
    source = (REPO_ROOT / CONSUMER_PATH).read_text(encoding="utf-8")
    assert _compiled_pattern_sites(source, PATTERN) == []
    # The retired binding was the standalone name BULLET_PATTERN; the borrowed
    # owner name BACKLOG_HYGIENE_BULLET_PATTERN contains it as a substring, so a
    # word boundary is needed to avoid matching the import.
    assert re.search(r"\bBULLET_PATTERN\b", source) is None
    assert "BACKLOG_HYGIENE_BULLET_PATTERN" in source


def test_the_section_heading_neighbour_is_a_different_pattern() -> None:
    # SECTION_HEADING_PATTERN shares the owner module and is also borrowed, but it
    # is a different grammar; the bullet census must not sweep it.
    assert owner.SECTION_HEADING_PATTERN.pattern == SECTION_HEADING_PATTERN
    assert SECTION_HEADING_PATTERN != PATTERN


def test_the_consumer_keeps_its_own_priority_neighbour() -> None:
    assert consumer.PRIORITY_PATTERN.pattern == PRIORITY_PATTERN
    assert PRIORITY_PATTERN != PATTERN


# --- behavioural parity through the consumer's public Next Action gate ------------------

BULLET_LINES = (
    "- foo",
    "* foo",
    "1. foo",
    "1) foo",
    "12) multi word task",
    "  - leading spaces",
    "- trailing spaces  ",
    "\t- tabbed",
    "- [P1] do thing",
    "- 日本語のタスク",
)

NON_BULLET_LINES = (
    "plain prose line",
    "-foo",
    "-",
    "1.foo",
    "> a quote",
    "a - b",
    "   ",
)


def _next_action(line: str) -> list[str]:
    state = "## Next Action\n" + line + "\n"
    return consumer.active_state_next_action_entries(state, limit=None, text_limit=None)


@pytest.mark.parametrize("line", BULLET_LINES)
def test_bullet_marker_is_stripped_exactly_as_the_owner(line: str) -> None:
    match = owner.BACKLOG_HYGIENE_BULLET_PATTERN.match(line)
    assert match is not None
    expected = compact_todo_text(match.group(1))
    assert _next_action(line) == ([expected] if expected else [])


@pytest.mark.parametrize("line", NON_BULLET_LINES)
def test_non_bullet_line_keeps_its_text(line: str) -> None:
    assert owner.BACKLOG_HYGIENE_BULLET_PATTERN.match(line) is None
    stripped = line.strip()
    expected = compact_todo_text(stripped) if stripped else ""
    assert _next_action(line) == ([expected] if expected else [])


def test_bullet_groups_an_indented_continuation() -> None:
    state = "## Next Action\n- first task\n  continued detail\n"
    got = consumer.active_state_next_action_entries(state, limit=None, text_limit=None)
    match = owner.BACKLOG_HYGIENE_BULLET_PATTERN.match("- first task")
    assert match is not None
    expected = compact_todo_text(match.group(1) + " continued detail")
    assert got == [expected]


# --- the census bites every restatement form and leaves the others ----------------------

POISON_CORPUS = (
    ("re.compile of the pattern", "X = re.compile(" + repr(PATTERN) + ")\n", 1),
    (
        "bare compile from re",
        "from re import compile\nX = compile(" + repr(PATTERN) + ")\n",
        1,
    ),
    (
        "nested in a function",
        "def f():\n    return re.compile(" + repr(PATTERN) + ")\n",
        1,
    ),
    (
        "flags do not hide the restated pattern",
        "X = re.compile(" + repr(PATTERN) + ", re.IGNORECASE)\n",
        1,
    ),
    (
        "the section-heading grammar is different",
        "X = re.compile(" + repr(SECTION_HEADING_PATTERN) + ")\n",
        0,
    ),
    (
        "the priority grammar is different",
        "X = re.compile(" + repr(PRIORITY_PATTERN) + ")\n",
        0,
    ),
    ("a bare pattern string is not a compiled regex", "X = " + repr(PATTERN) + "\n", 0),
    (
        "importing the owner is not a restatement",
        "from .control_plane.status.active_state_projection import "
        "BACKLOG_HYGIENE_BULLET_PATTERN\n",
        0,
    ),
    ("alias rebind is not a restatement", "BULLET_PATTERN = BACKLOG_HYGIENE_BULLET_PATTERN\n", 0),
)


@pytest.mark.parametrize(
    "label,source,expected", POISON_CORPUS, ids=[case[0] for case in POISON_CORPUS]
)
def test_the_census_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_compiled_pattern_sites(source, PATTERN)) == expected, label