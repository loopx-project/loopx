from __future__ import annotations

from pathlib import Path

import pytest

from loopx.bootstrap import render_state_markdown
from loopx.control_plane.goals.active_state_metadata import (
    active_state_section_text,
    markdown_blockquote,
)
from loopx.control_plane.projects.registration_state import render_registration_state
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source


BOUNDARY_OBJECTIVES = [
    # A start-goal-collapsed closed fence becomes one opening-fence line.
    "```text Implement and validate the task. ```",
    "~~~text Implement and validate the task. ~~~",
    # An unclosed comment example would hide every following section.
    "<!-- unclosed objective example comment",
    # An unfenced heading example must not become a Todo source region.
    "## Agent Todo example",
    # A task-shaped example must not be adopted as a Todo.
    "- [ ] fake objective todo",
]

PLAIN_OBJECTIVE = "Implement and validate the task."
CLOSED_MULTILINE_FENCE_OBJECTIVE = "```text\nImplement and validate the task.\n```"


def _bootstrap_state(tmp_path: Path, objective: str) -> str:
    state = render_state_markdown(
        project=tmp_path,
        goal_id="objective-example",
        adapter_kind="read_only_project_map_v0",
        objective=objective,
        updated_at="2026-09-15T00:00:00+00:00",
        goal_doc=None,
        execution_profile=None,
    )
    # Bootstrap now leaves authoring to the canonical Todo owner. Supply one
    # independent legitimate Todo to prove objective prose cannot hide it.
    return state.replace(
        "\n## Agent Todo\n",
        "\n## Agent Todo\n\n- [ ] Real planned work.\n",
        1,
    )


def test_objective_markdown_quotes_every_line() -> None:
    assert markdown_blockquote("") == ""
    assert markdown_blockquote(PLAIN_OBJECTIVE) == f"> {PLAIN_OBJECTIVE}"
    assert (
        markdown_blockquote("first\n\nsecond")
        == "> first\n> \n> second"
    )


@pytest.mark.parametrize("objective", BOUNDARY_OBJECTIVES)
def test_bootstrap_state_keeps_generated_todos_readable_beyond_objective_examples(
    tmp_path: Path, objective: str
) -> None:
    state = _bootstrap_state(tmp_path, objective)
    items, _archive, sources = parse_todo_source(state)

    assert sources["user"] == "User Todo / Owner Review Reading Queue"
    assert sources["agent"] == "Agent Todo"
    # The legitimate work stays readable and the objective creates no work.
    assert len(items["agent"]) == 1
    assert "fake objective todo" not in str(items["agent"][0].get("text") or "")


@pytest.mark.parametrize(
    "objective",
    [PLAIN_OBJECTIVE, CLOSED_MULTILINE_FENCE_OBJECTIVE],
)
def test_bootstrap_state_keeps_plain_objective_controls_readable(
    tmp_path: Path, objective: str
) -> None:
    state = _bootstrap_state(tmp_path, objective)
    items, _archive, sources = parse_todo_source(state)

    assert sources["user"] is not None
    assert sources["agent"] is not None
    assert len(items["agent"]) == 1


def test_bootstrap_state_preserves_objective_text_in_frontmatter_and_body(
    tmp_path: Path,
) -> None:
    objective = BOUNDARY_OBJECTIVES[0]
    state = _bootstrap_state(tmp_path, objective)

    # The frontmatter keeps the raw objective for exact readback.
    assert f'objective: "{objective}"' in state
    # The body is quote-isolated: no unquoted machine-grammar line remains.
    objective_lines = [f"> {line}" for line in objective.splitlines()]
    assert "\n".join(objective_lines) in state


@pytest.mark.parametrize("objective", BOUNDARY_OBJECTIVES)
def test_chat_context_readback_strips_objective_quote_isolation(
    tmp_path: Path, objective: str
) -> None:
    state = _bootstrap_state(tmp_path, objective)

    assert active_state_section_text(state, "Objective") == objective


def test_registry_state_markdown_keeps_todo_sections_readable_beyond_fenced_objective() -> None:
    state = render_registration_state(
        project_id="project-example",
        goal_id="goal-example",
        objective="```text Implement the pipeline. ```",
        non_goals=[],
        acceptance=["Pipeline works."],
        unknowns=[],
        next_effect="Register the project.",
        stop_condition="Pipeline accepted.",
        updated_at="2026-09-15T00:00:00+00:00",
    )
    items, _archive, sources = parse_todo_source(state)

    assert sources["user"] == "User Todo / Owner Review Reading Queue"
    assert sources["agent"] == "Agent Todo"
    assert items["user"] == []
    assert items["agent"] == []
    assert 'objective: "```text Implement the pipeline. ```"' in state
