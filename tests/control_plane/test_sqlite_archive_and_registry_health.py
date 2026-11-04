from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx import status as status_module
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos import provider_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.completed_archive import (
    ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE,
    DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE,
)
from loopx.control_plane.todos.machine_section_projection import (
    BOUNDED_ACTIVE_VIEW_MAX_DONE_ITEMS,
    BOUNDED_ACTIVE_VIEW_TEXT_LIMIT,
    format_sqlite_archive_pointer_comment,
    render_canonical_todo_sections,
)
from loopx.control_plane.todos.provider_terminal_lifecycle import (
    terminal_canonical_todo_if_promoted,
)
from loopx.global_registry import (
    sanitize_goal_for_global,
    sync_project_registry_to_global,
)
from loopx.paths import global_registry_path


def test_sqlite_bounded_active_view_projection_omits_archive_and_large_inline_fields() -> None:
    source = (
        "# Goal\n\n"
        "Human narrative stays intact.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "## Completed Work Archive\n\n"
        "- [x] Legacy archived item that should be cleared.\n\n"
        "## Next Action\n\n"
        "- Continue.\n"
    )
    long_title = "[P0] " + ("Very long active task description segment " * 8).strip()
    records: list[dict[str, object]] = [
        {
            "schema_version": "todo_domain_record_v0",
            "todo_id": "todo_active_compact",
            "role": "agent",
            "status": "open",
            "done": False,
            "text": long_title,
            "archive_state": "active",
            "task_class": "advancement_task",
            "note": "x" * 12000,
            "evidence": "y" * 8000,
            "reason": "z" * 4000,
            "required_write_scopes": [f"loopx/module_{idx}/**/*.py" for idx in range(20)],
        }
    ]
    for index in range(8):
        records.append(
            {
                "schema_version": "todo_domain_record_v0",
                "todo_id": f"todo_active_done_{index:02d}",
                "role": "agent",
                "status": "done",
                "done": True,
                "text": f"Active done step {index}.",
                "archive_state": "active",
                "task_class": "advancement_task",
            }
        )
    for index in range(30):
        records.append(
            {
                "schema_version": "todo_domain_record_v0",
                "todo_id": f"todo_arch_{index:03d}",
                "role": "agent",
                "status": "done",
                "done": True,
                "text": f"Archived work item {index}.",
                "archive_state": "archive",
                "task_class": "advancement_task",
                "note": "archived-note-" * 500,
            }
        )

    projected = render_canonical_todo_sections(
        source,
        records,
        provider_revision="sqlite:bounded:1",
        bounded_active_view=True,
    )
    assert projected.changed is True
    assert projected.todo_count == 39
    assert (
        format_sqlite_archive_pointer_comment(
            archived_count=30,
            active_done_omitted=8 - BOUNDED_ACTIVE_VIEW_MAX_DONE_ITEMS,
        )
        in projected.markdown
    )
    assert "note=" not in projected.markdown
    assert "evidence=" not in projected.markdown
    assert "reason=" not in projected.markdown
    assert "required_write_scopes=" not in projected.markdown
    assert "Archived work item" not in projected.markdown
    assert "Active done step 0." not in projected.markdown
    assert "Active done step 7." in projected.markdown
    active_by_role, archived_rows, _ = parse_todo_source(projected.markdown)
    assert archived_rows == []
    assert len(active_by_role["agent"]) == 1 + BOUNDED_ACTIVE_VIEW_MAX_DONE_ITEMS
    assert len(active_by_role["agent"][0]["text"]) <= BOUNDED_ACTIVE_VIEW_TEXT_LIMIT

    replay = render_canonical_todo_sections(
        projected.markdown,
        records,
        provider_revision="sqlite:bounded:1",
        bounded_active_view=True,
    )
    assert replay.changed is False
    assert replay.markdown == projected.markdown


def test_provider_projection_uses_bounded_active_view_for_sqlite_v0(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    todos = [
        {
            "schema_version": "todo_domain_record_v0",
            "todo_id": "todo_open_1",
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "Active task in SQLite.",
            "archive_state": "active",
            "task_class": "advancement_task",
            "note": "large-note-" * 400,
            "evidence": "large-evidence-" * 400,
        },
        *[
            {
                "schema_version": "todo_domain_record_v0",
                "todo_id": f"todo_arch_{index:03d}",
                "role": "agent",
                "status": "done",
                "done": True,
                "text": f"Archived item {index}.",
                "archive_state": "archive",
                "task_class": "advancement_task",
            }
            for index in range(25)
        ],
    ]

    def fake_read(**kwargs):
        result = {
            "status": "loaded",
            "source_authority": "sqlite_v0",
            "provider_revision": "sqlite:26:abc",
            "todos": todos,
        }
        if "projection_readback" in kwargs:
            witness = kwargs["projection_readback"]
            result["projection_readback"] = {
                "provider_revision": witness["provider_revision"],
                "observed_provider_revision": result["provider_revision"],
                "status": "delivered" if witness["changed"] else "current",
                "next_action": "finish",
            }
        return result

    monkeypatch.setattr(
        provider_projection, "read_canonical_todos_if_promoted", fake_read
    )
    delivered = provider_projection.project_current_canonical_todos(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    assert delivered["status"] == "delivered"
    assert delivered["todo_count"] == 26
    rendered = state_file.read_text(encoding="utf-8")
    assert (
        format_sqlite_archive_pointer_comment(
            archived_count=25, active_done_omitted=0
        )
        in rendered
    )
    assert "note=" not in rendered
    assert "evidence=" not in rendered
    _, archived_rows, _ = parse_todo_source(rendered)
    assert archived_rows == []


@pytest.mark.parametrize("command", ["complete", "supersede"])
def test_terminal_13th_todo_in_sqlite_auto_archives_in_same_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["agent-a"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    initial_todos: list[dict[str, object]] = []
    for index in range(DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE):
        initial_todos.append(
            {
                "schema_version": "todo_item_v0",
                "todo_id": f"todo_done_{index:02d}",
                "role": "agent",
                "status": "done",
                "done": True,
                "text": f"Completed step {index}.",
                "archive_state": "active",
                "source_section": "Agent Todo",
                "index": index + 1,
                "task_class": "advancement_task",
            }
        )
    initial_todos.extend(
        [
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_13th",
                "role": "agent",
                "status": "open",
                "done": False,
                "text": "Complete or supersede the 13th step.",
                "archive_state": "active",
                "source_section": "Agent Todo",
                "index": 13,
                "task_class": "advancement_task",
                "claimed_by": "agent-a",
            },
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_successor",
                "role": "agent",
                "status": "open",
                "done": False,
                "text": "Continue next bounded slice.",
                "archive_state": "active",
                "source_section": "Agent Todo",
                "index": 14,
                "task_class": "advancement_task",
                "claimed_by": "agent-a",
            },
        ]
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=initial_todos, handoff_mode="soft_claim"
    )
    init_result = initialize_canonical_authority(
        runtime_root, "goal-a", projection, state_path=state_file, provider="sqlite"
    )
    assert init_result["cursor"] == "1"

    result = terminal_canonical_todo_if_promoted(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-a",
        command=command,
        todo_id="todo_13th",
        role="agent",
        actor_agent_id="agent-a",
        authority_reason=None,
        decision_outcome=None,
        evidence="verified 13th step" if command == "complete" else None,
        completion_result_file=None,
        note="completed" if command == "complete" else "superseded",
        reason=None if command == "complete" else "replaced by todo_successor",
        completion_turn_key="turn-13" if command == "complete" else None,
        completion_identity_source=None,
        completion_delivery_workspace=None,
        completion_validation_workspace_path=None,
        claim_operation_id=None,
        task_lease_idempotency_key=None,
        task_lease_expected_version=None,
        no_followup=False,
        successor_todo_ids=["todo_successor"],
        claimed_by="agent-a" if command == "complete" else None,
        clear_claim=False,
        next_agent_todo=None,
        next_user_todo=None,
        next_user_task_class=None,
        next_claimed_by=None,
        next_task_class=None,
        next_action_kind=None,
        next_task_repository=None,
        next_required_capabilities=None,
        next_continuation_policy=None,
        next_excluded_agents=None,
        self_merged=False,
        dry_run=False,
        project=project,
        state_file=state_file,
    )
    assert result is not None
    assert result["ok"] is True
    # Single atomic transaction: cursor advances from 1 -> 2 (not 3!)
    assert result["cursor"] == "2"
    assert "auto_archive" in result
    assert result["auto_archive"]["moved_count"] == 3
    assert (
        result["auto_archive"]["active_done_after"]
        == ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE
    )

    after_read = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id="goal-a"
    )
    assert after_read is not None
    assert after_read["cursor"] == "2"
    all_done = [
        item for item in after_read["todos"] if item.get("status") == "done"
    ]
    active_done = [
        item
        for item in all_done
        if item.get("archive_state") != "archive"
    ]
    archived_done = [
        item
        for item in all_done
        if item.get("archive_state") == "archive"
    ]
    assert len(all_done) == 13
    assert len(active_done) == ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE
    assert len(archived_done) == 3

    from loopx.cli import main
    from loopx.control_plane.todos.list_readback import list_goal_todos

    active_list = list_goal_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="goal-a",
        include_archived=False,
    )
    archived_list = list_goal_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id="goal-a",
        include_archived=True,
    )
    active_ids = {item["todo_id"] for item in active_list["todos"]}
    with_archived_ids = {item["todo_id"] for item in archived_list["todos"]}
    assert "todo_done_00" not in active_ids
    assert {"todo_done_00", "todo_done_01", "todo_done_02"} <= with_archived_ids
    assert archived_list.get("include_archived") is True
    rc = main(
        [
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "todo",
            "list",
            "--goal-id",
            "goal-a",
            "--include-archived",
        ]
    )
    assert rc == 0


def test_multi_goal_source_registry_sync_and_health_prevent_sibling_false_positives(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    source_registry_path = project / ".loopx" / "registry.json"
    source_registry_path.parent.mkdir(parents=True)

    goals_in_source = []
    for goal_id in ("goal-synced", "goal-untouched", "goal-drifted"):
        state_rel = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        state_path = project / state_rel
        state_path.parent.mkdir(parents=True)
        state_path.write_text("# Goal\n", encoding="utf-8")
        goals_in_source.append(
            {
                "id": goal_id,
                "status": "active",
                "repo": str(project),
                "state_file": state_rel,
            }
        )

    old_synced_at = "2026-01-01T00:00:00+00:00"
    global_goals = [
        sanitize_goal_for_global(
            goal,
            source_registry=source_registry_path,
            synced_at=old_synced_at,
        )
        for goal in goals_in_source
    ]
    global_path = global_registry_path(runtime)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": global_goals,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Modify goal-synced and goal-drifted in source_registry, then sync ONLY goal-synced
    goals_in_source[0] = {**goals_in_source[0], "domain": "updated-domain"}
    goals_in_source[2] = {**goals_in_source[2], "status": "paused"}
    source_registry = {
        "schema_version": 1,
        "common_runtime_root": str(runtime),
        "goals": goals_in_source,
    }
    source_registry_path.write_text(
        json.dumps(source_registry, indent=2) + "\n", encoding="utf-8"
    )
    sync_receipt = sync_project_registry_to_global(
        registry_path=source_registry_path,
        runtime_root_override=str(runtime),
        goal_id="goal-synced",
    )
    assert sync_receipt["synced_goal_ids"] == ["goal-synced"]
    now = time.time() + 10
    os.utime(source_registry_path, (now, now))

    # Verify that goal-untouched had its synced_at refreshed during the single-goal sync
    # AND that even with future mtime on source_registry_path, only goal-drifted is flagged!
    global_after = json.loads(global_path.read_text(encoding="utf-8"))
    by_id = {g["id"]: g for g in global_after["goals"]}
    assert by_id["goal-untouched"]["synced_at"] == sync_receipt["updated_at"]
    assert by_id["goal-drifted"]["synced_at"] == old_synced_at

    health = status_module.collect_global_registry_health(
        registry_path=source_registry_path,
        runtime_root=runtime,
        current_registry=source_registry,
    )
    stale_goal_ids = [
        finding.get("goal_id")
        for finding in health["findings"]
        if finding.get("kind") == "stale_source_registry"
    ]
    assert stale_goal_ids == ["goal-drifted"]


def test_single_goal_source_registry_unchanged_prevents_false_positive_stale_warning(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    source_registry_path = project / ".loopx" / "registry.json"
    source_registry_path.parent.mkdir(parents=True)

    state_rel = ".codex/goals/goal-single/ACTIVE_GOAL_STATE.md"
    state_path = project / state_rel
    state_path.parent.mkdir(parents=True)
    state_path.write_text("# Goal\n", encoding="utf-8")
    single_goal = {
        "id": "goal-single",
        "status": "active",
        "repo": str(project),
        "state_file": state_rel,
    }

    old_synced_at = "2026-01-01T00:00:00+00:00"
    global_goals = [
        sanitize_goal_for_global(
            single_goal,
            source_registry=source_registry_path,
            synced_at=old_synced_at,
        )
    ]
    global_path = global_registry_path(runtime)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": global_goals,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    source_registry = {
        "schema_version": 1,
        "common_runtime_root": str(runtime),
        "goals": [single_goal],
    }
    source_registry_path.write_text(
        json.dumps(source_registry, indent=2) + "\n", encoding="utf-8"
    )

    now = time.time() + 100
    os.utime(source_registry_path, (now, now))

    health = status_module.collect_global_registry_health(
        registry_path=source_registry_path,
        runtime_root=runtime,
        current_registry=source_registry,
    )
    stale_goal_ids = [
        finding.get("goal_id")
        for finding in health["findings"]
        if finding.get("kind") == "stale_source_registry"
    ]
    assert stale_goal_ids == []

    source_registry["goals"][0]["status"] = "paused"
    source_registry_path.write_text(
        json.dumps(source_registry, indent=2) + "\n", encoding="utf-8"
    )
    os.utime(source_registry_path, (now + 10, now + 10))

    health_mutated = status_module.collect_global_registry_health(
        registry_path=source_registry_path,
        runtime_root=runtime,
        current_registry=source_registry,
    )
    stale_mutated_ids = [
        finding.get("goal_id")
        for finding in health_mutated["findings"]
        if finding.get("kind") == "stale_source_registry"
    ]
    assert stale_mutated_ids == ["goal-single"]


@pytest.mark.parametrize(
    ("scenario", "todo_payload", "expected_omitted_fields", "expect_inline_note"),
    [
        (
            "zero_archive_long_text_and_note",
            {
                "todo_id": "todo_open_both",
                "text": "[P0] " + ("Acceptance requirement and stop condition " * 11)[:454],
                "note": "stop-gate-detail-" * 50 + "end853",
            },
            ["text", "note"],
            False,
        ),
        (
            "zero_archive_long_text_only",
            {
                "todo_id": "todo_open_long_text",
                "text": "[P1] " + ("Long body with acceptance tail " * 12).strip(),
                "note": "Short stop condition retained inline.",
            },
            ["text"],
            True,
        ),
        (
            "zero_archive_long_note_and_scopes_only",
            {
                "todo_id": "todo_open_long_meta",
                "text": "[P1] Compact title preserved in full.",
                "note": "extended-note-stop-condition-" * 30,
                "required_write_scopes": [
                    f"loopx/control_plane/module_{idx}/**/*.py" for idx in range(10)
                ],
            },
            ["note", "required_write_scopes"],
            False,
        ),
    ],
)
def test_sqlite_project_markdown_zero_archive_active_field_omission_and_cold_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    todo_payload: dict[str, object],
    expected_omitted_fields: list[str],
    expect_inline_note: bool,
) -> None:
    from loopx import cli

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    # Intentionally omit "## Completed Work Archive" and keep zero done/archived items.
    state_file.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-a/ACTIVE_GOAL_STATE.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    todo_id = str(todo_payload["todo_id"])
    full_text = str(todo_payload["text"])
    full_note = str(todo_payload["note"])
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "role": "agent",
                "status": "open",
                "done": False,
                "archive_state": "active",
                "source_section": "Agent Todo",
                "index": 1,
                "task_class": "advancement_task",
                **todo_payload,
            }
        ],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime_root, "goal-a", projection, state_path=state_file, provider="sqlite"
    )
    loaded = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id="goal-a")
    assert loaded is not None
    provider_revision = str(loaded["provider_revision"])

    rc = cli.main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "todo",
            "project-markdown",
            "--goal-id",
            "goal-a",
            "--provider-revision",
            provider_revision,
            "--execute",
        ]
    )
    assert rc == 0, scenario
    projection_out = json.loads(capsys.readouterr().out)
    assert projection_out["active_fields_omitted"] == 1
    assert projection_out["omitted_active_records"] == [
        {
            "todo_id": todo_id,
            "role": "agent",
            "omitted_fields": expected_omitted_fields,
            "query": f"loopx todo list --goal-id goal-a --todo-id {todo_id}",
        }
    ]

    rendered = state_file.read_text(encoding="utf-8")
    expected_comment = (
        "<!-- loopx:todo-archive-pointer authority=sqlite_v0 "
        f"scope=active_todo goal_id=goal-a role=agent todo_id={todo_id} "
        f"omitted_fields={','.join(expected_omitted_fields)} "
        f'query="loopx todo list --goal-id goal-a --todo-id {todo_id}" -->'
    )
    assert expected_comment in rendered
    parsed_active, _, _ = parse_todo_source(rendered)
    assert len(parsed_active["agent"]) == 1
    if expect_inline_note:
        assert parsed_active["agent"][0].get("note") == full_note
    else:
        assert "note=" not in rendered
        assert parsed_active["agent"][0].get("note") is None

    # Repeated rendering via project-markdown must be idempotent.
    rc_repeat = cli.main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "todo",
            "project-markdown",
            "--goal-id",
            "goal-a",
            "--provider-revision",
            provider_revision,
            "--execute",
        ]
    )
    assert rc_repeat == 0
    repeat_out = json.loads(capsys.readouterr().out)
    assert repeat_out["changed"] is False
    assert state_file.read_text(encoding="utf-8") == rendered

    # Execute the bound cold-read query from the pointer comment and verify full requirements.
    rc_list = cli.main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "todo",
            "list",
            "--goal-id",
            "goal-a",
            "--todo-id",
            todo_id,
        ]
    )
    assert rc_list == 0
    cold_read = json.loads(capsys.readouterr().out)
    item = cold_read["todo"]
    assert item is not None
    assert item["todo_id"] == todo_id
    assert item["text"] == full_text
    assert item["note"] == full_note
    if "required_write_scopes" in todo_payload:
        assert item["required_write_scopes"] == todo_payload["required_write_scopes"]
