"""Todo examples and narrative never become canonical work or writable regions."""
from copy import deepcopy
import json
import re
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.active_state_editing import section_bounds, archive_section_bounds
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections, inspect_todo_section_projection
from test_todo_machine_section_projection import _records


EXAMPLE = "## Agent Todo\n- [ ] Example task\n  <!-- loopx:todo todo_id=todo_example status=open -->\n\n## Completed Work Archive\n- [x] Example decision\n  <!-- loopx:todo todo_id=todo_example_decision role=user status=done -->\n"
REAL = "## Agent Todo\n- [ ] Actual task\n  <!-- loopx:todo todo_id=todo_real status=open claimed_by=agent-a -->\n"


def document(example, marked):
    real = REAL
    if marked:
        real = real.replace("## Agent Todo\n", "## Agent Todo\n<!-- loopx:todo-region-v0 role=agent begin -->\n")
        real += "<!-- loopx:todo-region-v0 role=agent end -->\n"
    return "# Synthetic goal\n\n" + example + "\n" + real


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("wrapper", ["```markdown\n{}\n```\n", "~~~~\n{}\n~~~~\n", "<!-- example\n{}\n-->\n"])
def test_readers_and_editors_share_visible_region_ownership(marked, wrapper):
    example = re.sub(r"  <!--.*?-->\n", "", EXAMPLE) if wrapper.startswith("<!--") else EXAMPLE
    source = document(wrapper.format(example), marked)
    active, archived, _ = parse_todo_source(source)
    assert [row.get("todo_id") for row in active["agent"]] == ["todo_real"]
    assert archived == []
    bounds = section_bounds(source.splitlines(), "agent")
    assert bounds is not None and bounds[0] == max(i for i, line in enumerate(source.splitlines()) if line == "## Agent Todo")
    assert archive_section_bounds(source.splitlines()) is None


def test_marked_archive_ends_before_unmanaged_narrative_tasks():
    source = document("", True) + "\n## Completed Work Archive\n<!-- loopx:todo-region-v0 role=archive begin -->\n- [x] Actual historical task\n  <!-- loopx:todo todo_id=todo_history role=user status=done -->\n<!-- loopx:todo-region-v0 role=archive end -->\n\n- [x] Narrative checklist\n  <!-- loopx:todo todo_id=todo_not_history role=user status=done -->\n"
    _, archive, _ = parse_todo_source(source)
    assert [row["todo_id"] for row in archive] == ["todo_history"]


def test_native_projection_preserves_the_deterministic_id_order_contract():
    records = []
    for todo_id in ["todo_zulu", "todo_alpha", "todo_middle"]:
        row = deepcopy(_records()[0])
        row.update(schema_version="todo_domain_record_v0", todo_id=todo_id)
        row.pop("index")
        row.pop("source_section")
        records.append(row)
    source = deepcopy(records)
    rendered = render_canonical_todo_sections("## Agent Todo\n", records, provider_revision="test:1")
    active, _, _ = parse_todo_source(rendered.markdown)
    assert [row["todo_id"] for row in active["agent"]] == ["todo_alpha", "todo_middle", "todo_zulu"]
    assert records == source


def test_projection_diagnostics_ignore_markers_inside_examples():
    marker = "<!-- loopx:todo-section-projection-v0 role=agent provider_revision=fake:1 records_sha256=" + "a" * 64 + " -->"
    source = "```markdown\n" + marker + "\n```\n\n## Agent Todo\n"
    rendered = render_canonical_todo_sections(source, _records(), provider_revision="actual:1")
    inspected = inspect_todo_section_projection(rendered.markdown)
    assert inspected["section_count"] == 2
    assert {row["revision"] for row in inspected["sections"]} == {"actual:1"}


def test_canonical_update_projects_real_section_and_preserves_example(tmp_path):
    state, registry = tmp_path / "state.md", tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    example = "```markdown\n" + EXAMPLE + "```\n"
    state.write_text(document(example, False))
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "document-goal", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a"]},
    }]}))
    active, _, _ = parse_todo_source(state.read_text(encoding="utf-8"))
    initialize_canonical_authority(
        runtime,
        "document-goal",
        build_todo_runtime_shadow_projection(
            goal_id="document-goal",
            todos=[
                {
                    "schema_version": "todo_item_v0",
                    "task_class": "advancement_task",
                    **record,
                }
                for record in active["agent"]
            ],
            handoff_mode="soft_claim",
        ),
        state_path=state,
    )

    def cli(*arguments):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--registry",
                str(registry),
                "--format",
                "json",
                "todo",
                *arguments,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )

    before = state.read_bytes()
    rejected = cli(
        "update",
        "--goal-id",
        "document-goal",
        "--role",
        "agent",
        "--agent-id",
        "agent-a",
        "--todo-id",
        "todo_example",
        "--text",
        "Updated actual task",
    )
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert state.read_bytes() == before
    updated = cli(
        "update",
        "--goal-id",
        "document-goal",
        "--role",
        "agent",
        "--agent-id",
        "agent-a",
        "--todo-id",
        "todo_real",
        "--text",
        "Updated actual task",
    )
    assert updated.returncode == 0, updated.stdout + updated.stderr
    projection = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id="document-goal",
    )
    assert projection is not None
    assert projection["todos"][0]["text"] == "Updated actual task"
    assert example in state.read_text()
    assert "Updated actual task" in state.read_text()


def test_archived_priority_and_sparse_imported_indexes_roundtrip_without_mutating_authority():
    records = []
    for todo_id, index in [("todo_later", 47), ("todo_earlier", 23)]:
        row = deepcopy(_records()[0])
        row.update(todo_id=todo_id, status="done", done=True, archive_state="archive",
                   source_section="Completed Work Archive", index=index)
        records.append(row)
    before = deepcopy(records)
    rendered = render_canonical_todo_sections("## Agent Todo\n", records, provider_revision="history:1")
    _, archive, _ = parse_todo_source(rendered.markdown)
    assert [row["todo_id"] for row in archive] == ["todo_earlier", "todo_later"]
    assert [row["index"] for row in archive] == [1, 2]
    assert all(row["text"].startswith("[P0]") for row in archive)
    assert records == before
    assert not render_canonical_todo_sections(rendered.markdown, records, provider_revision="history:1").changed
