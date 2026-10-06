"""Guard the single owner of the agent-lane progress scope.  Refs #4447 Track A.

``progress_scope`` answers one question: does this recorded run belong to one agent's
own lane, or to the goal everyone shares?  The agent-lane answer was stated in ten
places on the base revision.  Four modules bound ``AGENT_LANE_PROGRESS_SCOPE`` to their
own literal -- ``control_plane/agents/agent_lane_recommendation.py``,
``control_plane/status/run_projection.py``, ``history.py`` and ``state_refresh.py`` --
and production modules also skipped the name and spelled the value inline where a CLI
command is rendered or a payload is built.  A name-keyed inventory reports the first
four as one fork and is structurally blind to the inline spellings, which is why this
guard reads module-level bindings and value-position literals as two separate layers.

Decisions are taken on a value in context, never on the bare word.  ``"agent_lane"`` is
also a *record field name* in five modules and an ``authority`` spelling in the
TypeScript recommendation vocabulary; those answer different questions, so the census
only counts a line where the field or flag that consumes it sits beside it.

Two production inline sites are recorded rather than converted, because open pull
requests are editing those files (#4023 and #3200 on ``bootstrap_command_pack.py``,
#5280 on ``cli_commands/status.py``).  A recorded site is a debt with a name on it:
gaining a second occurrence fails, and so does fixing one without retiring its entry.
"""

from __future__ import annotations

import ast
import functools
import inspect
import pathlib
from types import ModuleType

import pytest

from loopx import history, project_prompt, state_refresh, status
from loopx.control_plane import progress_scope as owner
from loopx.control_plane.agents import agent_lane_recommendation
from loopx.control_plane.status import run_projection

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "loopx"
OWNER_PATH = "loopx/control_plane/progress_scope.py"

GUARDED_NAME = "AGENT_LANE_PROGRESS_SCOPE"
SCOPE_VALUE = "agent_lane"
OTHER_SCOPE_VALUE = "goal"

# A literal answers *this* question only in a position that consumes it as a scope: a
# ``progress_scope=`` keyword, a ``"progress_scope": value`` pair, or a comparison
# against something that names the field or the flag. Line proximity is not enough --
# ``state_refresh.py`` carries ``"agent_lane"`` in a tuple of *field* names next to
# ``"progress_scope"``, and reading that as a restatement would make this guard's
# coverage claim false.
VALUE_KEYWORDS = frozenset({"progress_scope", "agent_lane_progress_scope"})
SCOPE_FIELD_NAMES = ("progress_scope", "--progress-scope")

# In-flight pull requests own these files, so the sites are named rather than moved:
# #4023 and #3200 on ``bootstrap_command_pack.py``, #5280 on ``cli_commands/status.py``.
DEFERRED_INLINE_VALUES: dict[str, int] = {
    "loopx/bootstrap_command_pack.py": 1,
    "loopx/cli_commands/status.py": 1,
}

# Shipped behaviour specs keep their own expected value on purpose. Borrowing the owner
# here would let a changed owner move the fixture with it, so the scenario would keep
# passing for a wrong string -- the same reason tests keep an independent literal.
INDEPENDENT_EXPECTATION_SITES: dict[str, int] = {
    "loopx/control_plane/testing/control_plane_composition_scenarios.py": 1,
    "loopx/control_plane/testing/replan_semantic_action_behavior.py": 1,
    "loopx/control_plane/testing/selected_todo_tool_behavior.py": 1,
}

CONSUMERS: dict[str, ModuleType] = {
    "loopx/control_plane/agents/agent_lane_recommendation.py": agent_lane_recommendation,
    "loopx/control_plane/status/run_projection.py": run_projection,
    "loopx/history.py": history,
    "loopx/project_prompt.py": project_prompt,
    "loopx/state_refresh.py": state_refresh,
    # The public facade borrows the value through its projection, as every carrier in
    # this family has since #5195.
    "loopx/status.py": status,
}

# Carries the word as a record field name, i.e. a different decision.
FIELD_NAME_NEIGHBOURS = (
    "loopx/control_plane/runtime/run_compaction.py",
    "loopx/control_plane/runtime/agent_evidence_history.py",
    "loopx/state_refresh.py",
)


def _label(path: pathlib.Path, tree: pathlib.Path) -> str:
    return path.relative_to(tree).as_posix()


def _python_sources(tree: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in (tree / "loopx").rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _module_level_bound_names(source: str) -> list[str]:
    names: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            names.extend(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def declaring_modules(tree: pathlib.Path) -> list[str]:
    """Every module under ``tree`` that re-binds the guarded name at module level."""
    return sorted(
        _label(path, tree)
        for path in _python_sources(tree)
        if GUARDED_NAME in _module_level_bound_names(path.read_text(encoding="utf-8"))
    )


def _scope_literal_nodes(node: ast.expr | None) -> list[ast.Constant]:
    """The value itself, folded through the forms that only choose between values.

    ``progress_scope="agent_lane" if agent_id else None`` restates the value as surely
    as the bare literal does, so a ternary is folded rather than declared unfoldable.
    """
    if node is None:
        return []
    if isinstance(node, ast.Constant):
        return [node] if node.value == SCOPE_VALUE else []
    if isinstance(node, ast.IfExp):
        return _scope_literal_nodes(node.body) + _scope_literal_nodes(node.orelse)
    return []


def _mentions_scope_field(node: ast.expr, source: str) -> bool:
    segment = ast.get_source_segment(source, node) or ""
    return any(field in segment for field in SCOPE_FIELD_NAMES)


def _value_positions(tree: ast.Module, source: str) -> list[int]:
    """Lines where a literal sits in a position that consumes it as a progress scope."""
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in VALUE_KEYWORDS:
            found.extend(lineno.lineno for lineno in _scope_literal_nodes(node.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "progress_scope":
                    found.extend(
                        lineno.lineno for lineno in _scope_literal_nodes(value)
                    )
        elif isinstance(node, ast.Compare):
            sides = (node.left, *node.comparators)
            literals = [
                literal for side in sides for literal in _scope_literal_nodes(side)
            ]
            if not literals:
                continue
            # The other side has to name the field or the flag being compared, e.g.
            # ``argument_value(tokens, "--progress-scope") != "agent_lane"``.
            if any(
                not _scope_literal_nodes(side) and _mentions_scope_field(side, source)
                for side in sides
            ):
                found.extend(literal.lineno for literal in literals)
    return found


def inline_value_sites(tree: pathlib.Path) -> dict[str, int]:
    """Where the value is written out again in a position that consumes it as a scope."""
    sites: dict[str, int] = {}
    for path in _python_sources(tree):
        source = path.read_text(encoding="utf-8")
        hits = _value_positions(ast.parse(source), source)
        if hits:
            sites[_label(path, tree)] = len(hits)
    return sites


@functools.lru_cache(maxsize=1)
def _repo_declarations() -> tuple[str, ...]:
    return tuple(declaring_modules(REPOSITORY_ROOT))


@functools.lru_cache(maxsize=1)
def _repo_inline_values() -> tuple[tuple[str, int], ...]:
    return tuple(sorted(inline_value_sites(REPOSITORY_ROOT).items()))


def test_the_owner_holds_the_only_declaration() -> None:
    assert list(_repo_declarations()) == [OWNER_PATH]


def test_the_owner_is_a_leaf() -> None:
    """A leaf keeps no projection a de facto authority for the others."""
    tree = ast.parse((REPOSITORY_ROOT / OWNER_PATH).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert [name for name in imported if name.split(".")[0] != "__future__"] == []


def test_every_consumer_borrows_the_owner_object_and_references_it() -> None:
    for path, module in CONSUMERS.items():
        assert getattr(module, GUARDED_NAME) is owner.AGENT_LANE_PROGRESS_SCOPE, path
        assert inspect.getsource(module).count(GUARDED_NAME) >= 2, (
            f"{path} imports the owner but never uses it"
        )


def test_inline_value_sites_are_exactly_the_recorded_ones() -> None:
    measured = {
        path: count for path, count in _repo_inline_values() if path != OWNER_PATH
    }
    recorded = {**DEFERRED_INLINE_VALUES, **INDEPENDENT_EXPECTATION_SITES}
    assert measured == recorded, (
        "a production module restates the value outside the owner; either borrow it "
        "or record the site with its file and count and the reason it stays"
    )


def test_the_field_name_neighbour_is_not_counted_as_a_restatement() -> None:
    """Proves the context gate does its job rather than matching nothing at all."""
    measured = dict(_repo_inline_values())
    for path in FIELD_NAME_NEIGHBOURS:
        source = (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
        assert f'"{SCOPE_VALUE}"' in source, path
        assert path not in measured, path


def test_the_lane_filter_still_uses_the_owners_value() -> None:
    lane_run = {"classification": "progress", "progress_scope": SCOPE_VALUE}
    goal_run = {"classification": "progress", "progress_scope": OTHER_SCOPE_VALUE}
    assert history.latest_status_run([lane_run, goal_run]) is goal_run
    assert run_projection.is_status_neutral_run(lane_run) is True
    assert run_projection.is_status_neutral_run(goal_run) is False
    assert (
        agent_lane_recommendation.latest_agent_lane_run(
            {"latest_runs": [goal_run, lane_run]},
            agent_lane_progress_scope=owner.AGENT_LANE_PROGRESS_SCOPE,
        )
        is lane_run
    )


def test_the_rendered_operator_command_is_unchanged_by_the_migration() -> None:
    rendered = project_prompt.render_refresh_state_command(
        "goal-a", agent_id="agent-1", progress_scope=owner.AGENT_LANE_PROGRESS_SCOPE
    )
    assert f"--progress-scope {SCOPE_VALUE}" in rendered
    assert rendered == project_prompt.render_refresh_state_command(
        "goal-a", agent_id="agent-1", progress_scope=SCOPE_VALUE
    )


def test_the_goal_side_stays_where_it_is_validated() -> None:
    """The pair and its goal value exist once, beside the validator; not duplicated here."""
    assert not hasattr(owner, "GOAL_PROGRESS_SCOPE")
    assert not hasattr(owner, "PROGRESS_SCOPE_CHOICES")
    assert state_refresh.PROGRESS_SCOPE_CHOICES == (OTHER_SCOPE_VALUE, SCOPE_VALUE)
    assert state_refresh.normalize_progress_scope(SCOPE_VALUE) == SCOPE_VALUE
    assert state_refresh.normalize_progress_scope(None) == ""
    with pytest.raises(ValueError, match="--progress-scope must be one of"):
        state_refresh.normalize_progress_scope("not-a-scope")


def test_a_seeded_second_declaration_and_copy_are_both_reported(
    tmp_path: pathlib.Path,
) -> None:
    """Without this the two scans above could pass by finding nothing."""
    loopx = tmp_path / "loopx"
    (loopx / "control_plane").mkdir(parents=True)
    (loopx / "owner.py").write_text(
        f'{GUARDED_NAME} = "{SCOPE_VALUE}"\n', encoding="utf-8"
    )
    (loopx / "restated.py").write_text(
        f'{GUARDED_NAME} = "{SCOPE_VALUE}"  # noqa\n', encoding="utf-8"
    )
    (loopx / "inlined.py").write_text(
        f'render(refresh_command, progress_scope="{SCOPE_VALUE}")\n', encoding="utf-8"
    )
    # The shape this guard originally missed: the value inside a ternary is still the
    # value handed to the same field.
    (loopx / "ternary.py").write_text(
        f'render(refresh_command, progress_scope="{SCOPE_VALUE}" if agent else None)\n',
        encoding="utf-8",
    )
    (loopx / "compared.py").write_text(
        f'if flag_value(tokens, "--progress-scope") != "{SCOPE_VALUE}":\n'
        "    raise ValueError('wrong lane')\n",
        encoding="utf-8",
    )
    (loopx / "fieldname.py").write_text(
        f'record["{SCOPE_VALUE}"] = agent_lane\n'
        f"for field in ('progress_scope', '{SCOPE_VALUE}'):\n    pass\n",
        encoding="utf-8",
    )
    assert declaring_modules(tmp_path) == ["loopx/owner.py", "loopx/restated.py"]
    assert inline_value_sites(tmp_path) == {
        "loopx/inlined.py": 1,
        "loopx/ternary.py": 1,
        "loopx/compared.py": 1,
    }
