#!/usr/bin/env python3
"""Smoke-test deferred todo writes and runtime capacity resume routing."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = Path(__file__).resolve().parent
if str(SMOKE_DIR) not in sys.path:
    sys.path.insert(0, str(SMOKE_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from todo_lifecycle_fixtures import (  # noqa: E402
    GOAL_ID,
    parsed_agent_summary,
    parsed_items,
    run_cli,
    run_cli_error,
    write_fixture,
)


CAPACITY_RESUME = "capacity_available:short_pool"
ACTOR_AGENT_ID = "codex-side-bypass"


def assert_deferred_add_and_capacity_resume() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-deferred-capacity-add-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))
        before = state_file.read_text(encoding="utf-8")

        unsupported = run_cli_error(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Wait for an unregistered external predicate.",
            "--status",
            "deferred",
            "--resume-when",
            "external_signal:short_pool",
        )
        assert "supported condition" in unsupported["error"], unsupported
        assert state_file.read_text(encoding="utf-8") == before

        missing_condition = run_cli_error(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Wait without a resume contract.",
            "--status",
            "deferred",
        )
        assert "requires --resume-when" in missing_condition["error"], missing_condition
        assert state_file.read_text(encoding="utf-8") == before

        added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Run when a short worker is available.",
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--status",
            "deferred",
            "--resume-when",
            CAPACITY_RESUME,
        )
        assert added["todo"]["status"] == "deferred", added
        todo_id = added["todo_id"]
        item = next(item for item in parsed_items(state_file) if item["todo_id"] == todo_id)
        assert item["status"] == "deferred" and item["done"] is True, item
        assert item["resume_when"] == CAPACITY_RESUME, item

        projected = parsed_agent_summary(state_file)
        condition = projected["deferred_items"][0]["resume_condition"]
        assert condition["provider"] == "runtime_available_capabilities", condition
        assert condition["provider_required"] is True, condition
        assert projected["deferred_resume_candidates"] == [], projected

        blocked = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-side-bypass",
        )
        assert blocked["agent_todo_summary"]["current_agent_deferred_resume_count"] == 0, blocked

        state_before_polls = state_file.read_text(encoding="utf-8")
        ready_packets = [
            run_cli(
                registry_path,
                "quota",
                "should-run",
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                "codex-side-bypass",
                "--available-capability",
                "short_pool",
            )
            for _ in range(2)
        ]
        for ready in ready_packets:
            assert ready["effective_action"] == "successor_replan_required", ready
            assert ready["agent_todo_summary"]["current_agent_deferred_resume_count"] == 1, ready
            assert ready["goal_frontier_projection"]["deferred_successors"]["ready_todo_ids"] == [
                todo_id
            ], ready
            assert ready["execution_obligation"]["contract"] == "deferred_resume_projection", ready
            next_action = ready["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
            assert "--clear-resume-when" in next_action, ready
        assert state_file.read_text(encoding="utf-8") == state_before_polls

        conflicting = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--agent-id",
            ACTOR_AGENT_ID,
            "--resume-when",
            CAPACITY_RESUME,
            "--clear-resume-when",
        )
        assert (
            "either --resume-when or --clear-resume-when" in conflicting["error"]
        ), conflicting

        still_deferred = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--agent-id",
            ACTOR_AGENT_ID,
            "--clear-resume-when",
        )
        assert (
            "transition to deferred requires --resume-when" in still_deferred["error"]
        ), still_deferred

        reopened = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--agent-id",
            ACTOR_AGENT_ID,
            "--status",
            "open",
            "--clear-resume-when",
            "--note",
            "capacity condition was satisfied",
        )
        assert (
            reopened["status"] == "applied" and reopened["changed"] is True
        ), reopened
        item = next(item for item in parsed_items(state_file) if item["todo_id"] == todo_id)
        assert item["status"] == "open" and item.get("resume_when") is None, item

        runnable = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            ACTOR_AGENT_ID,
            "--available-capability",
            "short_pool",
        )
        assert runnable["effective_action"] == "normal_run", runnable
        assert runnable["selected_todo"]["todo_id"] == todo_id, runnable


def assert_open_to_deferred_update() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-deferred-capacity-update-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))
        added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Queue work until capacity is declared.",
            "--task-class",
            "advancement_task",
        )
        todo_id = added["todo_id"]
        before = state_file.read_text(encoding="utf-8")
        unsupported = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--agent-id",
            ACTOR_AGENT_ID,
            "--status",
            "deferred",
            "--resume-when",
            "unknown_capacity:short_pool",
        )
        assert "supported condition" in unsupported["error"], unsupported
        assert state_file.read_text(encoding="utf-8") == before

        updated = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--agent-id",
            ACTOR_AGENT_ID,
            "--status",
            "deferred",
            "--resume-when",
            CAPACITY_RESUME,
            "--reason",
            "short worker capacity is not currently available",
        )
        assert updated["status"] == "applied" and updated["changed"] is True, updated
        item = next(item for item in parsed_items(state_file) if item["todo_id"] == todo_id)
        assert item["status"] == "deferred" and item["resume_when"] == CAPACITY_RESUME, item


def main() -> int:
    assert_deferred_add_and_capacity_resume()
    assert_open_to_deferred_update()
    print("todo-deferred-capacity-cli-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
