#!/usr/bin/env python3
"""Smoke-test parseable Agent Todo durability and completed-work archives."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.status import parse_active_state_todos  # noqa: E402
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.control_plane.testing.canary_harness import import_fixture_authority, run_json_cli  # noqa: E402
from loopx.rollout_event_log import (  # noqa: E402
    append_rollout_event,
    build_rollout_event,
    rollout_event_log_path,
)


GOAL_ID = "todo-durability-fixture-goal"
FIRST_OPEN_TODO = (
    "[P1] Add a parseable todo fixture when discovered planning work cannot be "
    "represented by the current Markdown parser."
)
SECOND_OPEN_TODO = "[P2] Keep the todo archive warning visible in quota should-run."
ACTIVE_DONE_TODO = "[P2] Prior completed implementation remains in active Agent Todo until archived."
DEFERRED_TODO = "[P1] Resume the issue surface fixture after CLI extraction stabilizes."
PR_DEFERRED_TODO = "[P1] Resume the Lark Kanban follow-up after PR #532 merges."
ARCHIVED_DONE_TODOS = 25


def run_cli(*args: str, registry_path: Path, runtime: Path) -> dict:
    return run_json_cli(*args, registry_path=registry_path, runtime_root=runtime,
                        cwd=REPO_ROOT, include_returncode=False)


def state_text() -> str:
    archived_lines = "".join(
        f"- [x] [P1] Archived completed work item {index}.\n"
        f"  <!-- loopx:todo todo_id=todo_archive_fixture_{index} role=agent status=done task_class=advancement_task -->\n"
        for index in range(1, ARCHIVED_DONE_TODOS + 1)
    )
    return (
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Todo Durability Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Add a parseable todo fixture when discovered planning work\n"
        "  cannot be represented by the current Markdown parser.\n"
        f"- [x] {ACTIVE_DONE_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_done_cli status=done task_class=advancement_task -->\n"
        f"- [-] {DEFERRED_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_deferred_surface status=deferred task_class=advancement_task claimed_by=codex-product-capability resume_when=todo_done:todo_done_cli -->\n"
        f"- [-] {PR_DEFERRED_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_pr_deferred status=deferred task_class=advancement_task claimed_by=codex-side-bypass task_repository=git:github.com/huangruiteng/loopx resume_when=pr_merged:#532 -->\n"
        f"- [ ] {SECOND_OPEN_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_handoff_review status=open task_class=advancement_task action_kind=review_pr continuation_policy=independent_handoff claimed_by=codex-reviewer excluded_agents=codex-builder evidence=smoke_handoff_note -->\n\n"
        "## Completed Work Archive\n\n"
        f"{archived_lines}"
    )


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state_text(), encoding="utf-8")
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    append_rollout_event(
        rollout_event_log_path(runtime, GOAL_ID),
        pr_merged_event(),
    )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "todo-durability-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 5,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime


def assert_parseable_agent_todos(agent_todos: dict, *, hot_path: bool = False) -> None:
    assert agent_todos["schema_version"] == "todo_summary_v0", agent_todos
    assert agent_todos["total_count"] == 5, agent_todos
    assert agent_todos["open_count"] == 2, agent_todos
    assert agent_todos["done_count"] == 3, agent_todos
    assert agent_todos["deferred_count"] == 2, agent_todos
    item_lane = "first_executable_items" if hot_path else "first_open_items"
    visible_items = agent_todos[item_lane]
    assert [item["index"] for item in visible_items] == [1, 5], agent_todos

    first_open = visible_items[0]
    assert first_open["schema_version"] == "todo_item_v0", first_open
    assert first_open["status"] == "open", first_open
    assert first_open["priority"] == "P1", first_open
    if not hot_path:
        assert first_open["role"] == "agent", first_open
        assert first_open["archive_state"] == "active", first_open
        assert first_open["source_section"] == "Agent Todo", first_open
    assert first_open["text"] == FIRST_OPEN_TODO, first_open
    if not hot_path:
        assert (
            first_open["title"]
            == "Add a parseable todo fixture when discovered planning work cannot be represented by the current Markdown parser."
        ), first_open
    assert str(first_open["todo_id"]).startswith("todo_"), first_open

    second_open = visible_items[1]
    assert second_open["priority"] == "P2", second_open
    assert second_open["status"] == "open", second_open
    assert second_open["text"] == SECOND_OPEN_TODO, second_open
    if not hot_path:
        handoff_note = second_open["handoff_note"]
        assert handoff_note["schema_version"] == "handoff_note_v0", handoff_note
        assert handoff_note["todo_id"] == "todo_handoff_review", handoff_note
        assert handoff_note["to_agent"] == "codex-reviewer", handoff_note
        assert handoff_note["excluded_agents"] == ["codex-builder"], handoff_note
        assert handoff_note["intent"] == "review_pr", handoff_note
        assert handoff_note["evidence_refs"] == ["todo:todo_handoff_review:evidence"], handoff_note

    if hot_path:
        assert "first_open_items" not in agent_todos, agent_todos
        assert "deferred_items" not in agent_todos, agent_todos
        assert agent_todos["payload_compaction"]["schema_version"] == (
            "quota_cli_todo_summary_compaction_v0"
        ), agent_todos
        return

    deferred_by_id = {item["todo_id"]: item for item in agent_todos["deferred_items"]}
    assert set(deferred_by_id) == {"todo_deferred_surface", "todo_pr_deferred"}, agent_todos
    deferred = deferred_by_id["todo_deferred_surface"]
    assert deferred["status"] == "deferred", deferred
    assert deferred["resume_when"] == "todo_done:todo_done_cli", deferred
    assert deferred["resume_ready"] is True, deferred
    assert deferred["resume_condition"]["target_status"] == "done", deferred
    pr_deferred = deferred_by_id["todo_pr_deferred"]
    assert pr_deferred["resume_when"] == "pr_merged:#532", pr_deferred
    assert pr_deferred["resume_ready"] is True, pr_deferred
    assert pr_deferred["resume_condition"]["kind"] == "pr_merged", pr_deferred
    assert pr_deferred["resume_condition"]["pr_number"] == 532, pr_deferred
    assert pr_deferred["resume_condition"]["pr_repo"] == "huangruiteng/loopx", pr_deferred
    assert pr_deferred["resume_condition"]["repository_binding_source"] == (
        "task_repository"
    ), pr_deferred
    assert pr_deferred["resume_condition"]["matched_pr_ref"] == "huangruiteng/loopx#532", pr_deferred
    resume_candidate_ids = {item["todo_id"] for item in agent_todos["deferred_resume_candidates"]}
    assert {"todo_deferred_surface", "todo_pr_deferred"} <= resume_candidate_ids, agent_todos
    if "items" in agent_todos:
        statuses = [item["status"] for item in agent_todos["items"][:3]]
        assert statuses == ["open", "open", "deferred"], agent_todos


def attention_item(status_payload: dict) -> dict:
    items = status_payload.get("attention_queue", {}).get("items") or []
    assert len(items) == 1, status_payload
    return items[0]


def pr_merged_event() -> dict:
    return build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="pr_merge",
        pr_ref="huangruiteng/loopx#532",
        status="ready",
        summary="PR #532 merged and can resume dependent deferred todos.",
    )


def assert_pr_resume_repository_binding() -> None:
    events = [
        pr_merged_event(),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="pr_merge",
            pr_ref="example/another-repo#532",
            status="ready",
            summary="A different repository merged the same PR number.",
        ),
    ]
    parsed = parse_active_state_todos(
        "## Agent Todo\n\n"
        "- [-] Bind an unqualified PR to the task repository.\n"
        "  <!-- loopx:todo todo_id=todo_bound status=deferred "
        "task_class=advancement_task task_repository=git:github.com/huangruiteng/loopx "
        "resume_when=pr_merged:#532 -->\n"
        "- [-] Honor an explicitly qualified cross-repository dependency.\n"
        "  <!-- loopx:todo todo_id=todo_qualified status=deferred "
        "task_class=advancement_task task_repository=git:github.com/huangruiteng/loopx "
        "resume_when=pr_merged:example/another-repo#532 -->\n"
        "- [-] Fail closed when an unqualified dependency has no repository binding.\n"
        "  <!-- loopx:todo todo_id=todo_ambiguous status=deferred "
        "task_class=advancement_task resume_when=pr_merged:#532 -->\n",
        rollout_events=events,
    )
    items = {
        item["todo_id"]: item for item in parsed["agent_todos"]["deferred_items"]
    }

    bound = items["todo_bound"]
    assert bound["resume_ready"] is True, bound
    assert bound["resume_condition"]["matched_pr_ref"] == "huangruiteng/loopx#532", bound
    assert bound["resume_condition"]["repository_binding_source"] == "task_repository", bound

    qualified = items["todo_qualified"]
    assert qualified["resume_ready"] is True, qualified
    assert qualified["resume_condition"]["matched_pr_ref"] == "example/another-repo#532", qualified
    assert qualified["resume_condition"]["repository_binding_source"] == (
        "qualified_resume_when"
    ), qualified

    ambiguous = items["todo_ambiguous"]
    assert ambiguous["resume_ready"] is False, ambiguous
    assert ambiguous["resume_condition"]["repository_binding_state"] == "ambiguous", ambiguous
    assert ambiguous["resume_condition"]["repository_binding_reason"] == (
        "task_repository_missing"
    ), ambiguous
    assert ambiguous["resume_condition"]["candidate_pr_refs"] == [
        "example/another-repo#532",
        "huangruiteng/loopx#532",
    ], ambiguous


def assert_lifecycle(provider: str) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-durability-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp))
        status_payload = run_cli("status", registry_path=registry_path, runtime=runtime)
        item = attention_item(status_payload)

        assert_parseable_agent_todos(item["agent_todos"])
        assert "completed_todo_archive_warning" not in item, item
        assert "completed_todo_archive_warning" not in item["project_asset"], item
        assert item["project_asset"]["agent_todos"]["open"] == 2, item
        assert item["project_asset"]["agent_todos"]["done"] == 3, item
        assert item["project_asset"]["agent_todos"]["deferred_count"] == 2, item
        assert (
            {
                candidate["todo_id"]
                for candidate in item["project_asset"]["agent_todos"]["deferred_resume_candidates"]
            }
            >= {"todo_deferred_surface", "todo_pr_deferred"}
        ), item
        assert item["project_asset"]["agent_todos"]["next"] == FIRST_OPEN_TODO, item
        assert item["project_asset"]["agent_todos"]["next_index"] == 1, item
        todo_list_payload = run_cli(
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            "todo_pr_deferred",
            registry_path=registry_path,
            runtime=runtime,
        )
        listed_pr_deferred = todo_list_payload["todo"]
        assert listed_pr_deferred["resume_ready"] is True, todo_list_payload
        assert listed_pr_deferred["resume_condition"]["matched_pr_ref"] == (
            "huangruiteng/loopx#532"
        ), todo_list_payload
        indexed_handoff = next(
            row
            for row in status_payload["todo_index"]["items"]
            if row.get("todo_id") == "todo_handoff_review"
        )
        assert indexed_handoff["handoff_note"]["goal_id"] == GOAL_ID, indexed_handoff
        assert indexed_handoff["handoff_note"]["to_agent"] == "codex-reviewer", indexed_handoff

        guard = run_cli("quota", "should-run", "--goal-id", GOAL_ID, registry_path=registry_path, runtime=runtime)
        assert guard["should_run"] is True, guard
        assert_parseable_agent_todos(guard["agent_todo_summary"], hot_path=True)
        assert "completed_todo_archive_warning" not in guard, guard

        first_todo_id = guard["agent_todo_summary"]["first_executable_items"][0]["todo_id"]
        # Historical inspection above is read-only. Declare the fixture's
        # existing claim owners before the reviewed import and normal writes.
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        registry['goals'][0]['coordination'] = {
            'agent_model': 'peer_v1',
            'registered_agents': ['fixture-agent', 'codex-product-capability',
                'codex-side-bypass', 'codex-reviewer', 'codex-builder'],
        }
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        import_fixture_authority(registry_path, GOAL_ID, provider=provider)
        original = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert original['source_authority'] == provider + '_v0'
        assert original['decision_read_from_provider'] is True and original['legacy_fallback_used'] is False
        assert len(original['todos']) == 5 + ARCHIVED_DONE_TODOS
        assert sum(row['archive_state'] == 'archive' for row in original['todos']) == ARCHIVED_DONE_TODOS
        native_guard = run_cli('quota', 'should-run', '--goal-id', GOAL_ID,
            '--agent-id', 'fixture-agent', '--host-surface', 'generic_cli',
            '--scheduler-owner', 'none', '--execution-mode', 'interactive',
            registry_path=registry_path, runtime=runtime)
        assert native_guard['selected_todo']['todo_id'] == first_todo_id
        lifecycle_result = run_cli(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            first_todo_id,
            '--agent-id', 'fixture-agent',
            "--evidence",
            "fixture completed",
            "--next-agent-todo",
            "Continue lifecycle follow-up without an explicit priority prefix.",
            "--next-task-class",
            "advancement_task",
            "--next-action-kind",
            "fixture_follow_up",
            registry_path=registry_path,
            runtime=runtime,
        )
        next_todo = lifecycle_result["next_todos"][0]
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        rows = {row['todo_id']: row for row in canonical['todos']}
        assert rows[next_todo['todo_id']]['text'].startswith('[P1] Continue lifecycle')
        assert rows[first_todo_id]['status'] == 'done'
        assert rows[first_todo_id]['successor_todo_ids'] == [next_todo['todo_id']]
        assert len(canonical['todos']) == len(original['todos']) + 1
        for row in original['todos']:
            if row['todo_id'] != first_todo_id:
                assert rows[row['todo_id']] == row

        post_lifecycle_status = run_cli("status", registry_path=registry_path, runtime=runtime)
        post_lifecycle_item = attention_item(post_lifecycle_status)
        post_agent_todos = post_lifecycle_item["agent_todos"]
        assert post_agent_todos["open_count"] == 2, post_agent_todos
        # The summary retains its documented non-open count, including deferred.
        assert post_agent_todos["done_count"] == 4, post_agent_todos
        assert post_agent_todos["deferred_count"] == 2, post_agent_todos
        assert post_agent_todos["first_open_items"][0]["priority"] == "P1", post_agent_todos
        assert post_agent_todos["first_open_items"][0]["todo_id"] == next_todo["todo_id"], post_agent_todos
        assert post_agent_todos["first_open_items"][0]["action_kind"] == "fixture_follow_up", post_agent_todos



def main() -> int:
    assert_pr_resume_repository_binding()
    parsed = parse_active_state_todos(state_text(), rollout_events=[pr_merged_event()])
    assert_parseable_agent_todos(parsed["agent_todos"])
    for provider in ('file', 'sqlite'):
        assert_lifecycle(provider)
    print("todo-durability-fixture-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
