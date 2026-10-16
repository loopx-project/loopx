from __future__ import annotations

import pytest

from loopx.control_plane.todos.active_state_editing import (
    insert_into_existing_section,
    section_bounds,
    todo_blocks,
)
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.todos.machine_region import find_todo_regions, todo_region_marker
from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections


def _source() -> str:
    return "## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n\n"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("tail", [
    "# Next Action\n\n- [ ] Narrative checkbox, not a Todo.\n",
    "Next Action\n===========\n\n- [ ] Narrative checkbox, not a Todo.\n",
    "  ## Next Action\n\n- [ ] Narrative checkbox, not a Todo.\n",
    "This paragraph is not generated Todo data.\n\n- [ ] Narrative checkbox.\n",
])
def test_legacy_adoption_preserves_non_generated_suffix(tail: str, newline: str) -> None:
    source = (_source() + tail).replace("\n", newline)
    projected = render_canonical_todo_sections(source, [], provider_revision="rev-1")
    suffix = tail.replace("\n", newline)
    assert projected.markdown.endswith(suffix)
    assert todo_region_marker("agent", "end") in projected.markdown
    parsed = parse_active_state_todos(projected.markdown, item_limit=None)
    assert parsed["agent_todos"]["items"] == []
    assert render_canonical_todo_sections(
        projected.markdown, [], provider_revision="rev-1"
    ).markdown == projected.markdown


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("indent", ["  ", "\t"])
def test_legacy_folded_task_adoption_replaces_its_complete_block(newline, indent):
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source

    task = ("- [ ] A folded task with\n" + indent + "a retained identity.\n" +
            "  <!-- loopx:todo todo_id=todo_folded status=open task_class=advancement_task -->\n")
    tail = "  Indented narrative after the task block.\n\n```markdown\n## Agent Todo\n- [ ] Example only.\n```\n"
    source = (_source() + task + "\n" + tail).replace("\n", newline)
    record = {"schema_version": "todo_item_v0", "role": "agent", "index": 1,
              "source_section": "Agent Todo", "archive_state": "active",
              "status": "open", "done": False, "task_class": "advancement_task",
              "todo_id": "todo_folded", "text": "A folded task with a retained identity."}
    projected = render_canonical_todo_sections(source, [record], provider_revision="rev-1")
    assert projected.markdown.endswith(tail.replace("\n", newline))
    assert projected.markdown.count("todo_id=todo_folded") == 1
    assert projected.markdown.count("A folded task with a retained identity.") == 1
    readback, _, _ = parse_todo_source(projected.markdown)
    assert readback["agent"][0]["text"] == "A folded task with a retained identity."
    assert readback["agent"][0]["todo_id"] == "todo_folded"
    assert render_canonical_todo_sections(
        projected.markdown, [record], provider_revision="rev-1"
    ).markdown == projected.markdown


@pytest.mark.parametrize("example", [
    "```markdown\n## Agent Todo\n- [ ] Example only.\n```\n",
    "~~~~markdown\n## Agent Todo\n- [ ] Example only.\n~~~~\n",
    "<!-- example\n## Agent Todo\n- [ ] Example only.\n-->\n",
])
def test_code_and_comment_examples_are_not_adopted(example: str) -> None:
    source = example + "\n" + _source()
    projected = render_canonical_todo_sections(source, [], provider_revision="rev-1")
    assert projected.markdown.startswith(example + "\n")
    parsed = parse_active_state_todos(projected.markdown, item_limit=None)
    assert parsed["agent_todos"]["items"] == []


def test_unclosed_code_example_cannot_supply_todo_regions() -> None:
    with pytest.raises(ValueError, match="required Todo sections"):
        render_canonical_todo_sections("```markdown\n" + _source(), [], provider_revision="rev-1")


@pytest.mark.parametrize("replacement", [
    "",
    "<!-- loopx:todo-region-v0 role=user end -->",
    "<!-- loopx:todo-region-v0 role=agent begin -->",
    "<!-- loopx:todo-region-v0 role=agent finish -->",
    "Human narrative unexpectedly placed inside.\n<!-- loopx:todo-region-v0 role=agent end -->",
])
def test_malformed_region_is_never_reinterpreted_as_legacy(replacement: str) -> None:
    projected = render_canonical_todo_sections(_source(), [], provider_revision="rev-1")
    malformed = projected.markdown.replace(todo_region_marker("agent", "end"), replacement)
    with pytest.raises(ValueError, match="Todo region"):
        render_canonical_todo_sections(malformed, [], provider_revision="rev-1")


def test_section_editor_and_reader_share_generated_bounds() -> None:
    projected = render_canonical_todo_sections(_source(), [], provider_revision="rev-1")
    tail = "Narrative outside the paired marker.\n- [ ] Not an active Todo.\n"
    lines = (projected.markdown + tail).splitlines()
    start, end, heading = section_bounds(lines, "agent")
    assert heading == "Agent Todo"
    assert lines[end] == todo_region_marker("agent", "end")
    insert_into_existing_section(lines, start, end, "- [ ] A generated Todo.")
    start, end, heading = section_bounds(lines, "agent")
    blocks = todo_blocks(lines, start, end, role="agent", source_section=heading)
    assert [block["text"] for block in blocks] == ["A generated Todo."]
    parsed = parse_active_state_todos("\n".join(lines), item_limit=None)
    assert [item["text"] for item in parsed["agent_todos"]["items"]] == ["A generated Todo."]
    assert lines[end + 1:] == tail.splitlines()
    assert len(find_todo_regions(lines)) == 2


def test_mixed_legacy_boundary_does_not_skip_next_heading() -> None:
    source = "\n".join([
        "## User Todo", "- [ ] User task.", "## Agent Todo",
        todo_region_marker("agent", "begin"), "- [ ] Agent task.",
        todo_region_marker("agent", "end"),
    ])
    parsed = parse_active_state_todos(source, item_limit=None)
    assert [item["text"] for item in parsed["user_todos"]["items"]] == ["User task."]
    assert [item["text"] for item in parsed["agent_todos"]["items"]] == ["Agent task."]


def test_state_counts_classify_headings_exactly_as_the_region_writer() -> None:
    """The open counts and the Todo region writer must read one marker set.

    ``loopx/state_projection.py`` carried its own copy of the marker tuples and
    they had drifted three ways: a bare ``owner`` marker made a prose section
    named ``Ownership`` count as user Todos, ``codex todo`` was missing although
    the writer creates it, and there was no archive guard so an archived section
    counted as live work. The counts feed ``state_projection_gap_warning``, so an
    inflated agent count suppresses the warning that a Next Action is executable
    with no agent Todo behind it -- the failure is silence, not a wrong number on
    a screen.
    """
    from loopx.control_plane.goals.active_state_metadata import todo_role_for_heading
    from loopx.state_projection import summarize_state_todo_open_counts

    state_text = "\n".join([
        "# Goal state",
        "",
        "## Agent Todo",
        "",
        "- [ ] [P1] implement the reader metric",
        "- [ ] [P2] classify the candidate groups",
        "",
        "## Agent Todo Archive",
        "",
        "- [ ] [P1] archived one",
        "- [ ] [P2] archived two",
        "",
        "## Codex Todo",
        "",
        "- [ ] [P1] a heading the region writer creates",
        "",
        "## Ownership",
        "",
        "- [ ] prose about who owns what, not a Todo",
        "",
    ])

    counts = summarize_state_todo_open_counts(state_text)
    assert counts == {"user": 0, "agent": 3}, (
        "archived Todos must not count as live, a Codex Todo heading must count, "
        "and an Ownership prose section must not count as user Todos"
    )

    for heading, expected in [
        ("Agent Todo Archive", None),
        ("Codex Todo", "agent"),
        ("Ownership", None),
        ("Agent Todo", "agent"),
        ("Owner Reading Queue", "user"),
    ]:
        assert todo_role_for_heading(heading) == expected, heading
