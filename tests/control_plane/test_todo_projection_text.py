"""Source text is lossless; attention summaries retain their display budget."""
from copy import deepcopy

import pytest

from loopx.control_plane.todos.active_state_todo_parser import (
    parse_active_state_todos,
    parse_todo_source,
)
from loopx.control_plane.todos.machine_section_projection import (
    TodoSectionProjectionError,
    render_canonical_todo_sections,
)
from loopx.control_plane.todos.todo_summary import normalize_todo_text


SOURCE = "# Goal\n\nRetain this narrative.\n\n## Agent Todo\n"


@pytest.mark.parametrize("schema", ["todo_item_v0", "todo_domain_record_v0"])
@pytest.mark.parametrize("role,archive", [("agent", False), ("user", False), ("agent", True), ("user", True)])
@pytest.mark.parametrize("length", [500, 501, 505, 1001])
@pytest.mark.parametrize("priority", [False, True])
def test_accepted_text_and_title_roundtrip_without_a_display_cutoff(schema, role, archive, length, priority):
    prefix = "[P0] " if priority else ""
    title = ("审阅🙂A" * length)[:length - len(prefix) - 1] + "!"
    text = prefix + title
    heading = "Completed Work Archive" if archive else (
        "Agent Todo" if role == "agent" else "User Todo / Owner Review Reading Queue"
    )
    record = {
        "schema_version": schema, "todo_id": "todo_long", "role": role,
        "status": "done" if archive else "open", "done": archive,
        "text": text, "archive_state": "archive" if archive else "active",
        "task_class": "advancement_task",
    }
    if schema == "todo_item_v0":
        record.update(index=1, source_section=heading)
    if priority:
        record.update(priority="P0", title=title)
    original = deepcopy(record)

    result = render_canonical_todo_sections(SOURCE, [record], provider_revision="text-revision")
    assert text in result.markdown
    active, archived, _ = parse_todo_source(result.markdown)
    parsed = archived if archive else active[role]
    assert parsed[0]["text"] == text
    assert parsed[0]["todo_id"] == "todo_long"
    replay = render_canonical_todo_sections(result.markdown, [record], provider_revision="text-revision")
    assert replay.changed is False
    assert replay.markdown == result.markdown
    assert record == original
    assert "Retain this narrative." in result.markdown


def test_source_continuations_do_not_drop_the_suffix_before_compaction():
    first = "[P1] " + "Evidence " * 80
    continuation = "独立反证🙂 " * 80 + "last obligation"
    source = SOURCE + (
        f"- [ ] {first}\n  {continuation}\n"
        "  <!-- loopx:todo todo_id=todo_wrapped status=open -->\n"
    )
    expected = " ".join((first + continuation).split())
    active, _, _ = parse_todo_source(source)
    assert active["agent"][0]["text"] == expected
    summary = parse_active_state_todos(source)["agent_todos"]
    assert summary["items"][0]["text"] == normalize_todo_text(expected)
    assert len(summary["items"][0]["text"]) == 500
    assert summary["items"][0]["text"].endswith("…")
    assert normalize_todo_text(expected, limit=220).endswith("…")


def test_legacy_archive_summary_does_not_reclassify_from_long_source_tail():
    text = "Completed " + "retained " * 70 + "dependency monitor"
    source = SOURCE + (
        "\n## Completed Work Archive\n"
        f"- [x] {text}\n"
        "  <!-- loopx:todo todo_id=todo_old role=agent status=done -->\n"
    )
    summary = parse_active_state_todos(source)["agent_todos"]
    assert summary["archived_advancement_done_count"] == 1
    assert summary["advancement_done_count"] == 1


def test_explicit_title_disagreement_after_the_display_prefix_is_still_rejected():
    title = "Same prefix " * 60 + "original tail"
    record = {
        "schema_version": "todo_domain_record_v0", "todo_id": "todo_long",
        "role": "agent", "status": "open", "done": False,
        "text": "[P0] " + title, "title": title[:-13] + "different tail",
        "priority": "P0", "archive_state": "active", "task_class": "advancement_task",
    }
    original = deepcopy(record)
    with pytest.raises(TodoSectionProjectionError, match="parity mismatch"):
        render_canonical_todo_sections(SOURCE, [record], provider_revision="conflicting-title")
    assert record == original
