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
    DEFAULT_SQLITE_PROJECTED_ARCHIVE_LIMIT,
    render_canonical_todo_sections,
)
from loopx.control_plane.todos.provider_terminal_lifecycle import (
    terminal_canonical_todo_if_promoted,
)
from loopx.global_registry import sanitize_goal_for_global
from loopx.paths import global_registry_path


def test_sqlite_projection_caps_markdown_archive_and_preserves_active_dependencies() -> None:
    source = (
        "# Goal\n\n"
        "Human narrative stays intact.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "## Completed Work Archive\n\n"
        "## Next Action\n\n"
        "- Continue.\n"
    )
    records: list[dict[str, object]] = [
        {
            "schema_version": "todo_domain_record_v0",
            "todo_id": "todo_waiting_on_oldest",
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "Follow up after oldest archived prerequisite.",
            "archive_state": "active",
            "task_class": "advancement_task",
            "resume_when": "todo_done:todo_arch_000",
        }
    ]
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
            }
        )

    projected = render_canonical_todo_sections(
        source,
        records,
        provider_revision="sqlite:cap:1",
        max_projected_archive_records=DEFAULT_SQLITE_PROJECTED_ARCHIVE_LIMIT,
    )
    assert projected.changed is True
    assert projected.todo_count == 31
    _, archived_rows, _ = parse_todo_source(projected.markdown)
    archived_ids = [row["todo_id"] for row in archived_rows]
    # 20 tail archived items + 1 dependency-referenced oldest item (todo_arch_000)
    assert len(archived_ids) == DEFAULT_SQLITE_PROJECTED_ARCHIVE_LIMIT + 1
    assert "todo_arch_000" in archived_ids
    assert "todo_arch_001" not in archived_ids
    assert "todo_arch_029" in archived_ids

    replay = render_canonical_todo_sections(
        projected.markdown,
        records,
        provider_revision="sqlite:cap:1",
        max_projected_archive_records=DEFAULT_SQLITE_PROJECTED_ARCHIVE_LIMIT,
    )
    assert replay.changed is False
    assert replay.markdown == projected.markdown


def test_provider_projection_uses_archive_cap_only_for_sqlite_v0(
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
            "todo_id": f"todo_arch_{index:03d}",
            "role": "agent",
            "status": "done",
            "done": True,
            "text": f"Archived item {index}.",
            "archive_state": "archive",
            "task_class": "advancement_task",
        }
        for index in range(25)
    ]

    def fake_read(**kwargs):
        result = {
            "status": "loaded",
            "source_authority": "sqlite_v0",
            "provider_revision": "sqlite:25:abc",
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
    assert delivered["todo_count"] == 25
    _, archived_rows, _ = parse_todo_source(state_file.read_text(encoding="utf-8"))
    assert len(archived_rows) == DEFAULT_SQLITE_PROJECTED_ARCHIVE_LIMIT


def test_completing_13th_todo_in_sqlite_auto_archives_to_10_active_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
                "text": "Complete the 13th step.",
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
    initialize_canonical_authority(
        runtime_root, "goal-a", projection, state_path=state_file, provider="sqlite"
    )

    result = terminal_canonical_todo_if_promoted(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-a",
        command="complete",
        todo_id="todo_13th",
        role="agent",
        actor_agent_id="agent-a",
        authority_reason=None,
        decision_outcome=None,
        evidence="verified 13th step",
        completion_result_file=None,
        note="completed",
        reason=None,
        completion_turn_key="turn-13",
        completion_identity_source=None,
        completion_delivery_workspace=None,
        completion_validation_workspace_path=None,
        claim_operation_id=None,
        task_lease_idempotency_key=None,
        task_lease_expected_version=None,
        no_followup=False,
        successor_todo_ids=["todo_successor"],
        claimed_by="agent-a",
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


def test_multi_goal_source_registry_only_flags_goals_whose_entry_drifted(
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
    new_synced_at = "2026-11-01T12:00:00+00:00"
    global_goals = [
        sanitize_goal_for_global(
            goals_in_source[0],
            source_registry=source_registry_path,
            synced_at=new_synced_at,
        ),
        sanitize_goal_for_global(
            goals_in_source[1],
            source_registry=source_registry_path,
            synced_at=old_synced_at,
        ),
        sanitize_goal_for_global(
            goals_in_source[2],
            source_registry=source_registry_path,
            synced_at=old_synced_at,
        ),
    ]
    # Mutate goal-drifted in source_registry without syncing it to global
    goals_in_source[2] = {**goals_in_source[2], "status": "paused"}
    source_registry = {"schema_version": 1, "goals": goals_in_source}
    source_registry_path.write_text(
        json.dumps(source_registry, indent=2) + "\n", encoding="utf-8"
    )
    now = time.time()
    os.utime(source_registry_path, (now, now))

    global_path = global_registry_path(runtime)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        json.dumps({"schema_version": 1, "goals": global_goals}, indent=2) + "\n",
        encoding="utf-8",
    )

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
