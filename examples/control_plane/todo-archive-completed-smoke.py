#!/usr/bin/env python3
"""Smoke-test completed Agent Todo archive CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.todos.completed_archive import (  # noqa: E402
    completed_todo_archive_warning,
    completed_todo_count,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.status import parse_active_state_todos  # noqa: E402

GOAL_ID = "todo-archive-completed-goal"


def state_text() -> str:
    done_lines = []
    for index in range(1, 15):
        done_lines.append(f"- [x] [P1] Completed implementation lane {index}.\n")
        if index == 1:
            done_lines.append("  Continuation detail must move with the archived item.\n")
            done_lines.append(
                "  <!-- loopx:todo todo_id=todo_completed_lane_1 status=done "
                "task_class=advancement_task excluded_agents=codex-reviewer -->\n"
            )
    return (
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Todo Archive Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Keep current open benchmark validation work visible.\n"
        + "".join(done_lines)
        + "- [-] [P0] Resume deferred acceptance after its dependency.\n"
        + "  <!-- loopx:todo todo_id=todo_deferred_acceptance status=deferred "
        + "task_class=advancement_task resume_when=todo_done:todo_dependency -->\n"
        + "- [ ] [P2] Keep monitor work after completed items visible.\n\n"
        "## Completed Work Archive\n\n"
        "- [x] [P1] Previously archived completed work.\n"
        "  <!-- loopx:todo todo_id=todo_previously_archived role=agent status=done task_class=advancement_task -->\n"
    )


def user_role_state_text() -> str:
    """A user-role section where one done gate is a durable standing receipt."""

    return (
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Todo Archive User Role Fixture\n\n"
        "## User Todo\n\n"
        "- [x] [P0] Approve validated benchmark PR self-merge for this agent.\n"
        "  <!-- loopx:todo todo_id=todo_user_standing_approve status=done "
        "task_class=user_gate decision_scope=write_scope:goal:benchmark_pr_self_merge "
        "decision_outcome=approve blocks_agent=codex-reviewer -->\n"
        "- [x] [P1] Ordinary completed reminder one.\n"
        "  <!-- loopx:todo todo_id=todo_user_ordinary_1 status=done "
        "task_class=user_action bound_agent=codex-reviewer -->\n"
        "- [x] [P1] Ordinary completed reminder two.\n"
        "  <!-- loopx:todo todo_id=todo_user_ordinary_2 status=done "
        "task_class=user_action bound_agent=codex-reviewer -->\n\n"
        "## Agent Todo\n\n"
        "- [x] [P1] Completed agent lane outside the user archive scope.\n"
        "  <!-- loopx:todo todo_id=todo_agent_completed status=done "
        "task_class=advancement_task claimed_by=codex-reviewer -->\n"
        "- [ ] [P2] Keep current open agent work visible.\n"
        "  <!-- loopx:todo todo_id=todo_agent_open status=open "
        "task_class=advancement_task claimed_by=codex-reviewer -->\n"
    )


def write_fixture(root: Path, *, text: str | None = None, import_authority: bool = True) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = ".codex/goals/todo-archive-completed-goal/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state_text() if text is None else text, encoding="utf-8")
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "todo-archive-completed-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": ["codex-reviewer"],
                        },
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if import_authority:
        import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, runtime, state_path


def run_cli(registry_path: Path, runtime: Path, *args: str, check: bool = True) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def assert_user_role_archive_retains_standing_decision() -> None:
    """`--role user` archives ordinary done gates but never a standing receipt.

    The shipped CLI accepts `--role`, yet the user-role archive path was only
    covered at the helper level. This pins the two invariants that only the
    command can prove: a durable standing decision survives compaction, and the
    Agent Todo section is not touched while compacting the user section.
    """

    with tempfile.TemporaryDirectory(prefix="loopx-todo-archive-user-role-") as tmp:
        registry_path, runtime, state_path = write_fixture(
            Path(tmp), text=user_role_state_text()
        )
        original = state_path.read_text(encoding="utf-8")
        parsed = parse_active_state_todos(original)
        assert parsed["user_todos"]["done_count"] == 3, parsed
        assert parsed["agent_todos"]["done_count"] == 1, parsed

        dry_run = run_cli(
            registry_path,
            runtime,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--max-active-done",
            "0",
        )
        assert dry_run["dry_run"] is True, dry_run
        assert dry_run["role"] == "user", dry_run
        assert dry_run["active_done_before"] == 3, dry_run
        assert dry_run["active_done_after"] == 1, dry_run
        assert dry_run["moved_count"] == 2, dry_run
        assert dry_run["retained_standing_decision_count"] == 1, dry_run
        assert state_path.read_text(encoding="utf-8") == original

        execute = run_cli(
            registry_path,
            runtime,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--max-active-done",
            "0",
            "--execute",
        )
        assert execute["dry_run"] is False, execute
        assert execute["moved_count"] == 2, execute
        assert execute["active_done_before"] == 3, execute
        assert execute["active_done_after"] == 1, execute
        assert execute["retained_standing_decision_count"] == 1, execute

        updated = state_path.read_text(encoding="utf-8")
        parsed_after = parse_active_state_todos(updated)
        user_ids = [item["todo_id"] for item in parsed_after["user_todos"]["items"]]
        assert user_ids == ["todo_user_standing_approve"], parsed_after
        assert updated.index("## Completed Work Archive") < updated.index(
            "todo_user_ordinary_1"
        ), updated
        assert updated.count("todo_user_ordinary_2") == 1, updated
        authority = parsed_after.get("standing_decision_authority")
        assert authority is not None, parsed_after
        assert authority["active_count"] == 1, authority
        assert authority["entries"][0]["source_todo_id"] == (
            "todo_user_standing_approve"
        ), authority

        # Role scoping: compacting the user section must not move Agent Todo work.
        agent_ids = [item["todo_id"] for item in parsed_after["agent_todos"]["items"]]
        assert "todo_agent_completed" in agent_ids, parsed_after
        assert parsed_after["agent_todos"]["done_count"] == 1, parsed_after
        assert updated.index("todo_agent_completed") < updated.index(
            "## Completed Work Archive"
        ), updated

        status_after = run_cli(registry_path, runtime, "status")
        assert status_after["contract_errors"] == [], status_after


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-archive-completed-") as tmp:
        registry_path, runtime, state_path = write_fixture(Path(tmp))
        original = state_path.read_text(encoding="utf-8")
        parsed = parse_active_state_todos(original)
        assert parsed["agent_todos"]["done_count"] == 15, parsed
        assert parsed["agent_todos"]["deferred_count"] == 1, parsed
        assert parsed["agent_todos"]["open_count"] == 2, parsed
        warning = completed_todo_archive_warning(parsed["agent_todos"])
        assert warning is not None, parsed
        assert warning["kind"] == "completed_agent_todo_archive_required", warning
        assert warning["active_done_count"] == 14, warning
        assert warning["active_open_count"] == 2, warning
        assert warning["max_active_done_count"] == 12, warning
        assert warning["default_archive_keep_count"] == 10, warning
        assert warning["archive_command_template"] == (
            "loopx todo archive-completed --goal-id <goal-id> --max-active-done 10 --execute"
        ), warning

        default_dry_run = run_cli(
            registry_path,
            runtime,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
        )
        assert default_dry_run["ok"] is True, default_dry_run
        assert default_dry_run["dry_run"] is True, default_dry_run
        assert default_dry_run["changed"] is True, default_dry_run
        assert default_dry_run["active_done_after"] == warning["default_archive_keep_count"], (
            warning,
            default_dry_run,
        )
        assert default_dry_run["moved_count"] == 4, default_dry_run
        assert state_path.read_text(encoding="utf-8") == original

        dry_run = run_cli(
            registry_path,
            runtime,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--max-active-done",
            "12",
        )
        assert dry_run["ok"] is True, dry_run
        assert dry_run["dry_run"] is True, dry_run
        assert dry_run["changed"] is True, dry_run
        assert dry_run["moved_count"] == 2, dry_run
        assert state_path.read_text(encoding="utf-8") == original

        execute = run_cli(
            registry_path,
            runtime,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--max-active-done",
            "12",
            "--execute",
        )
        assert execute["ok"] is True, execute
        assert execute["dry_run"] is False, execute
        assert execute["changed"] is True, execute
        assert execute["active_done_before"] == 14, execute
        assert execute["active_done_after"] == 12, execute
        assert execute["moved_count"] == 2, execute

        updated = state_path.read_text(encoding="utf-8")
        parsed_after = parse_active_state_todos(updated)
        assert parsed_after["agent_todos"]["done_count"] == 13, parsed_after
        assert parsed_after["agent_todos"]["advancement_done_count"] == 15, parsed_after
        assert parsed_after["agent_todos"]["archived_advancement_done_count"] == 3, parsed_after
        assert parsed_after["agent_todos"]["deferred_count"] == 1, parsed_after
        assert parsed_after["agent_todos"]["open_count"] == 2, parsed_after
        assert completed_todo_archive_warning(parsed_after["agent_todos"]) is None, parsed_after
        assert updated.index("## Completed Work Archive") < updated.index("[P1] Completed implementation lane 1.")
        assert "Continuation detail must move with the archived item." in updated
        assert updated.count("[P1] Completed implementation lane 1.") == 1
        assert updated.count("[P1] Completed implementation lane 2.") == 1
        assert "excluded_agents=codex-reviewer" in updated
        assert "updated_at: 2026-01-01T00:00:00+00:00" in updated

        status_after = run_cli(registry_path, runtime, "status")
        assert status_after["ok"] is True, status_after
        assert status_after["contract_errors"] == [], status_after

        done_before_completion = completed_todo_count(parsed_after["agent_todos"])
        lineage_source_text = "Complete work whose successor lineage must survive archiving."
        lineage_successor_text = "Continue the public archive interplay validation."
        lineage_source = run_cli(
            registry_path,
            runtime,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            lineage_source_text,
            "--claimed-by",
            "codex-reviewer",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "archive_interplay",
        )
        completed_with_successor = run_cli(
            registry_path,
            runtime,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            lineage_source["todo_id"],
            "--claimed-by",
            "codex-reviewer",
            "--agent-id",
            "codex-reviewer",
            "--evidence",
            "focused archive interplay smoke passed",
            "--next-agent-todo",
            lineage_successor_text,
            "--next-claimed-by",
            "codex-reviewer",
        )
        successor_id = completed_with_successor["next_todos"][0]["todo_id"]
        assert completed_with_successor["successor_todo_ids"] == [successor_id], (
            completed_with_successor
        )

        completed_projection = parse_active_state_todos(state_path.read_text(encoding="utf-8"))
        completion_warning = completed_todo_archive_warning(
            completed_projection["agent_todos"]
        )
        assert completion_warning is not None, completed_projection
        assert completion_warning["active_done_count"] == done_before_completion + 1, (
            completion_warning
        )

        archive_completed = run_cli(
            registry_path,
            runtime,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--max-active-done",
            "0",
            "--execute",
        )
        assert archive_completed["active_done_before"] == completion_warning["active_done_count"], (
            archive_completed
        )
        assert archive_completed["active_done_after"] == 0, archive_completed
        assert archive_completed["moved_count"] == completion_warning["active_done_count"], (
            archive_completed
        )

        archived_lineage = state_path.read_text(encoding="utf-8")
        assert archived_lineage.index("## Completed Work Archive") < archived_lineage.index(
            lineage_source_text
        )
        assert archived_lineage.count(lineage_source_text) == 1, archived_lineage
        assert archived_lineage.count(lineage_successor_text) == 1, archived_lineage
        assert f"successor_todo_ids={successor_id}" in archived_lineage, archived_lineage
        active_after_archive = parse_active_state_todos(archived_lineage)["agent_todos"]
        assert all(
            item["todo_id"] != lineage_source["todo_id"]
            for item in active_after_archive["items"]
        ), active_after_archive
        successor_item = next(
            item for item in active_after_archive["items"] if item["todo_id"] == successor_id
        )
        assert successor_item["status"] == "open", successor_item
        assert successor_item["claimed_by"] == "codex-reviewer", successor_item
        deferred_item = next(
            item
            for item in active_after_archive["items"]
            if item["todo_id"] == "todo_deferred_acceptance"
        )
        assert deferred_item["status"] == "deferred", deferred_item
        assert deferred_item["archive_state"] == "active", deferred_item
        deferred_update = run_cli(
            registry_path,
            runtime,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            "todo_deferred_acceptance",
            "--agent-id",
            "codex-reviewer",
            "--note",
            "Deferred acceptance remains lifecycle-addressable after archive compaction.",
        )
        assert deferred_update["ok"] is True, deferred_update
        assert deferred_update["changed"] is True, deferred_update
        assert completed_todo_archive_warning(active_after_archive) is None, active_after_archive

        lineage_status = run_cli(registry_path, runtime, "status")
        assert lineage_status["ok"] is True, lineage_status
        assert lineage_status["contract_errors"] == [], lineage_status

        before_authority = run_cli(registry_path, runtime, "todo", "list", "--goal-id", GOAL_ID)
        invalid_rows = (
            ""
            + "- [ ] [P1] Contradictory archived work item.\n"
            + "  <!-- loopx:todo todo_id=todo_archived_open status=done "
            + "task_class=advancement_task excluded_agents=codex-reviewer -->\n"
            + "- [x] [P1] Malformed archived work item.\n"
            + "  <!-- loopx:todo todo_id=todo_archived_malformed status=<bad> "
            + "task_class=advancement_task excluded_agents=codex-reviewer -->\n"
            + "- [x] [P1] Empty-status archived work item.\n"
            + "  <!-- loopx:todo todo_id=todo_archived_empty status= "
            + "task_class=advancement_task excluded_agents=codex-reviewer -->\n"
        )
        state_path.write_text(archived_lineage + invalid_rows, encoding="utf-8")
        invalid_status = run_cli(registry_path, runtime, "status", check=False)
        assert invalid_status["ok"] is True, invalid_status
        after_authority = run_cli(registry_path, runtime, "todo", "list", "--goal-id", GOAL_ID)
        assert after_authority["todos"] == before_authority["todos"]
        assert after_authority["authority_read"]["provider_revision"] == before_authority["authority_read"]["provider_revision"]
        # These diagnostics still belong to explicit legacy source inspection,
        # before import. Display text outside canonical regions grants no work.
        source_registry, source_runtime, source_state = write_fixture(
            Path(tmp) / "invalid-source", text=state_text() + invalid_rows,
            import_authority=False,
        )
        before_source = source_state.read_bytes()
        invalid_status = run_cli(source_registry, source_runtime, "status", check=False)
        assert invalid_status["ok"] is False, invalid_status
        assert source_state.read_bytes() == before_source
        assert any(
            "todo_archived_open" in item and "executor exclusions" in item
            for item in invalid_status["contract_errors"]
        ), invalid_status
        assert any(
            "todo_archived_malformed" in item and "malformed status metadata" in item
            for item in invalid_status["contract_errors"]
        ), invalid_status
        assert any(
            "todo_archived_empty" in item and "malformed status metadata" in item
            for item in invalid_status["contract_errors"]
        ), invalid_status

    assert_user_role_archive_retains_standing_decision()

    print("todo-archive-completed-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
