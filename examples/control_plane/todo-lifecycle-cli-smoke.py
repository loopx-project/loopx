#!/usr/bin/env python3
"""Smoke-test structured todo lifecycle transitions by todo_id."""

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

from loopx.status import parse_active_state_todos  # noqa: E402
from todo_lifecycle_fixtures import (  # noqa: E402
    GOAL_ID,
    HANDOFF_SOURCE_TODO,
    HANDOFF_SUCCESSOR_TODO,
    REBUILD_TODO,
    REVIEW_TODO,
    RUN_TODO,
    SIDE_CONTINUATION_TODO,
    SIDE_REVIEW_TODO,
    SIDE_TODO,
    UNCLAIMED_SOURCE_TODO,
    UNCLAIMED_SUCCESSOR_TODO,
    VALIDATE_TODO,
    assert_complete_links_existing_successor,
    assert_configured_peer_handoff,
    assert_no_followup_cli_metadata,
    assert_same_title_completion_creates_fresh_successor,
    parsed_items,
    run_cli,
    run_cli_error,
    write_fixture,
)


SUCCESSOR_REPOSITORY = "git:github.com/huangruiteng/loopx"
SUCCESSOR_CAPABILITIES = ["network", "external_evidence_poll"]


def assert_peer_monitor_no_followup() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-monitor-no-followup-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))
        side_monitor_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Watch one public-safe signal until the bounded window closes.",
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "continuous_monitor",
            "--action-kind",
            "public_signal_monitor",
            "--monitor-target-key",
            "public-signal-window",
            "--cadence",
            "1d",
            "--next-due-at",
            "2026-07-11T00:00:00Z",
            "--expires-at",
            "2026-07-12T00:00:00Z",
        )
        side_monitor_id = side_monitor_added["todo_id"]
        monitor_update_bypass = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_monitor_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--status",
            "done",
            "--no-follow-up",
            "--evidence",
            "bounded public watch ended without material change",
        )
        assert "agent todo completion must use" in monitor_update_bypass["error"], (
            monitor_update_bypass
        )

        side_monitor_completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_monitor_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "bounded public watch ended without material change",
            "--no-follow-up",
        )
        assert side_monitor_completed["changed"] is True, side_monitor_completed
        assert side_monitor_completed["completion_policy"]["self_merged"] is False, (
            side_monitor_completed
        )
        assert side_monitor_completed["completion_continuation"] == "no_followup", side_monitor_completed
        monitor_item = next(
            item for item in parsed_items(state_file) if item["todo_id"] == side_monitor_id
        )
        assert monitor_item["status"] == "done", monitor_item
        assert monitor_item["no_followup"] is True, monitor_item

        write_monitor_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Watch a signal whose closeout includes repository delivery.",
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "continuous_monitor",
            "--action-kind",
            "delivery_monitor",
            "--required-write-scope",
            "loopx/**",
            "--monitor-target-key",
            "delivery-monitor-window",
            "--cadence",
            "1d",
            "--next-due-at",
            "2026-07-11T00:00:00Z",
            "--expires-at",
            "2026-07-12T00:00:00Z",
        )
        write_monitor_completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            write_monitor_added["todo_id"],
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "monitor closeout included repository delivery",
            "--no-follow-up",
        )
        assert write_monitor_completed["changed"] is True, write_monitor_completed
        assert write_monitor_completed["completion_continuation"] == "no_followup", write_monitor_completed
        write_monitor_item = next(
            item
            for item in parsed_items(state_file)
            if item["todo_id"] == write_monitor_added["todo_id"]
        )
        assert write_monitor_item["status"] == "done", write_monitor_item
        assert write_monitor_item["no_followup"] is True, write_monitor_item


def assert_open_parent_successor_advisory() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-open-parent-successor-advisory-") as tmp:
        registry_path, _state_file = write_fixture(Path(tmp))
        parent = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Split a bounded aggregate into explicit successor work.",
            "--task-class",
            "advancement_task",
            "--claimed-by",
            "codex-main-control",
        )
        successor = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Execute the first explicit successor slice.",
            "--task-class",
            "advancement_task",
            "--claimed-by",
            "codex-side-bypass",
        )

        linked = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            parent["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--successor-todo-id",
            successor["todo_id"],
        )
        assert linked["status"] == "applied", linked
        parent_read = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID,
                              "--todo-id", parent["todo_id"])["todo"]
        assert parent_read["status"] == "open", parent_read
        assert parent_read["successor_todo_ids"] == [successor["todo_id"]], parent_read
        quota = run_cli(registry_path, "quota", "should-run", "--goal-id", GOAL_ID,
                        "--agent-id", "codex-main-control")
        # Linking records lineage; only an explicit lifecycle command changes
        # the parent's availability in the actual canonical quota consumer.
        assert quota["agent_lane_next_action"]["todo_id"] == parent["todo_id"], quota

        explicitly_deferred = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            parent["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--status",
            "deferred",
            "--resume-when",
            f"todo_done:{successor['todo_id']}",
            "--successor-todo-id",
            successor["todo_id"],
        )
        assert explicitly_deferred["status"] == "applied", explicitly_deferred
        deferred = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID,
                           "--todo-id", parent["todo_id"])["todo"]
        assert deferred["status"] == "deferred", deferred
        assert deferred["resume_when"] == f"todo_done:{successor['todo_id']}", deferred



def assert_nonblocking_review_continuation() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-nonblocking-review-continuation-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp))
        source = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Publish one validated feature PR to the experiment branch.",
            "--claimed-by",
            "codex-main-control",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "experiment_feature_delivery",
        )
        completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            source["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
            "--evidence",
            "feature PR is validated and ready for review",
            "--next-user-todo",
            "[P1] Review the validated experiment feature PR.",
            "--next-user-task-class",
            "user_action",
            "--next-agent-todo",
            "[P1] Continue the next independent experiment feature.",
            "--next-claimed-by",
            "codex-main-control",
            "--next-action-kind",
            "experiment_feature_delivery",
        )
        assert completed["changed"] is True, completed
        fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        review = next(
            item
            for item in fields["user_todos"]["items"]
            if item["text"] == "[P1] Review the validated experiment feature PR."
        )
        assert review["task_class"] == "user_action", review
        assert review["bound_agent"] == "codex-main-control", review
        assert not review.get("blocks_agent"), review
        assert not review.get("global_gate"), review

        continuation = next(
            item
            for item in fields["agent_todos"]["items"]
            if item["text"] == "[P1] Continue the next independent experiment feature."
        )
        assert continuation["status"] == "open", continuation
        assert continuation["claimed_by"] == "codex-main-control", continuation

        guard = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            "codex-main-control",
            "--available-capability",
            "shell",
            "--available-capability",
            "filesystem_write",
        )
        assert guard["state"] == "eligible", guard
        assert guard["should_run"] is True, guard
        assert guard["interaction_contract"]["mode"] == "bounded_delivery", guard
        assert guard["requires_user_action"] is False, guard

        missing_class = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            continuation["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
            "--evidence",
            "missing successor classification probe",
            "--next-user-todo",
            "Review an explicitly classified successor.",
        )
        assert (
            "--next-user-todo requires explicit --next-user-task-class "
            "user_action|user_gate"
        ) in missing_class["error"], missing_class

        missing_todo = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            continuation["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
            "--evidence",
            "invalid successor classification probe",
            "--next-user-task-class",
            "user_action",
        )
        assert (
            "--next-user-task-class requires --next-user-todo"
            in missing_todo["error"]
        ), missing_todo


def main() -> int:
    assert_configured_peer_handoff()
    assert_no_followup_cli_metadata()
    assert_complete_links_existing_successor()
    assert_same_title_completion_creates_fresh_successor()
    assert_peer_monitor_no_followup()
    assert_open_parent_successor_advisory()
    assert_nonblocking_review_continuation()

    with tempfile.TemporaryDirectory(prefix="loopx-todo-lifecycle-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)

        added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            RUN_TODO,
            "--task-class",
            "advancement_task",
            "--action-kind",
            "run_eval",
        )
        assert added["added"] is True, added
        run_todo_id = added["todo_id"]
        items = parsed_items(state_file)
        run_item = next(item for item in items if item["todo_id"] == run_todo_id)
        assert run_item["task_class"] == "advancement_task", run_item
        assert run_item["action_kind"] == "run_eval", run_item

        archive_open = run_cli(
            registry_path,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--execute",
            "--max-active-done",
            "0",
        )
        assert archive_open["moved_count"] == 0, archive_open
        assert any(item["todo_id"] == run_todo_id and not item["done"] for item in parsed_items(state_file))

        completion_args = (
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            run_todo_id,
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
            "--evidence",
            "fresh-repeat-result-ready",
            "--next-agent-todo",
            REBUILD_TODO,
            "--next-claimed-by",
            "codex-main-control",
            "--next-action-kind",
            "rebuild_score",
        )
        completed = run_cli(registry_path, *completion_args)
        assert completed["changed"] is True, completed
        assert completed["next_todos"][0]["status"] == "open", completed
        rebuild_todo_id = completed["next_todos"][0]["todo_id"]
        assert completed["successor_todo_ids"] == [rebuild_todo_id], completed
        items = parsed_items(state_file)
        completed_item = next(item for item in items if item["todo_id"] == run_todo_id)
        rebuild_item = next(item for item in items if item["todo_id"] == rebuild_todo_id)
        assert completed_item["done"] is True and completed_item["status"] == "done", completed_item
        assert completed_item["evidence"] == "fresh-repeat-result-ready", completed_item
        assert completed_item["claimed_by"] == "codex-main-control", completed_item
        assert completed_item["successor_todo_ids"] == [rebuild_todo_id], completed_item
        assert rebuild_item["done"] is False and rebuild_item["task_class"] == "advancement_task", rebuild_item
        assert rebuild_item["action_kind"] == "rebuild_score", rebuild_item
        assert rebuild_item["claimed_by"] == "codex-main-control", rebuild_item
        assert rebuild_item.get("updated_at"), rebuild_item
        assert completed["next_todos"][0].get("updated_at") == rebuild_item["updated_at"], completed

        side_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_TODO,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        assert side_added["added"] is True, side_added
        side_todo_id = side_added["todo_id"]
        side_item = next(item for item in parsed_items(state_file) if item["todo_id"] == side_todo_id)
        assert side_item.get("updated_at"), side_item
        missing_evidence = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_todo_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--self-merged",
        )
        assert "--self-merged requires --evidence" in missing_evidence["error"], missing_evidence

        side_self_merged = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_todo_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "self-merged commit abc123 after focused validation",
            "--self-merged",
        )
        assert side_self_merged["changed"] is True, side_self_merged
        assert side_self_merged["completion_policy"]["self_merged"] is True, side_self_merged
        assert side_self_merged["next_todos"] == [], side_self_merged

        side_continuation_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Prepare another small side-agent doc slice.",
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        side_continuation_todo_id = side_continuation_added["todo_id"]
        side_continuation_completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_continuation_todo_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "self-merged commit def456 after focused validation",
            "--self-merged",
            "--next-agent-todo",
            SIDE_CONTINUATION_TODO,
            "--next-claimed-by",
            "codex-side-bypass",
            "--next-action-kind",
            "contract_refine",
        )
        assert side_continuation_completed["changed"] is True, side_continuation_completed
        assert side_continuation_completed["completion_policy"]["self_merged"] is True, side_continuation_completed
        assert side_continuation_completed["next_todos"][0]["claimed_by"] == "codex-side-bypass", (
            side_continuation_completed
        )
        side_successor_id = side_continuation_completed["next_todos"][0]["todo_id"]
        assert side_continuation_completed["successor_todo_ids"] == [side_successor_id], (
            side_continuation_completed
        )
        side_successor_item = next(item for item in parsed_items(state_file) if item["todo_id"] == side_successor_id)
        assert side_successor_item["done"] is False, side_successor_item
        assert side_successor_item["claimed_by"] == "codex-side-bypass", side_successor_item

        side_review_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_REVIEW_TODO,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        assert side_review_added["added"] is True, side_review_added
        side_review_todo_id = side_review_added["todo_id"]

        missing_successor = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_review_todo_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "side-worktree-contract-diff",
            "--next-task-repository",
            SUCCESSOR_REPOSITORY,
        )
        assert "--next-task-repository requires --next-agent-todo" in (
            missing_successor["error"]
        ), missing_successor

        side_review_claim_error = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_review_todo_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "side-worktree-contract-diff",
            "--next-agent-todo",
            REVIEW_TODO,
            "--next-claimed-by",
            "codex-side-bypass",
            "--next-continuation-policy",
            "independent_handoff",
            "--next-excluded-agent",
            "codex-side-bypass",
        )
        assert "cannot also appear in excluded_agents" in (
            side_review_claim_error["error"]
        ), side_review_claim_error

        side_completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_review_todo_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "side-worktree-contract-diff",
            "--next-agent-todo",
            REVIEW_TODO,
            "--next-claimed-by",
            "codex-main-control",
            "--next-continuation-policy",
            "independent_handoff",
            "--next-excluded-agent",
            "codex-side-bypass",
            "--next-action-kind",
            "review",
            "--next-task-repository",
            SUCCESSOR_REPOSITORY,
            "--next-required-capability",
            "network",
            "--next-required-capability",
            "external_evidence_poll",
        )
        assert side_completed["changed"] is True, side_completed
        review_todo_id = side_completed["next_todos"][0]["todo_id"]
        assert side_completed["next_todos"][0]["claimed_by"] == "codex-main-control", side_completed
        assert side_completed["next_todos"][0].get("blocks_agent") is None, side_completed
        assert side_completed["next_todos"][0]["excluded_agents"] == ["codex-side-bypass"], side_completed
        assert side_completed["next_todos"][0]["task_repository"] == (
            SUCCESSOR_REPOSITORY
        ), side_completed
        assert side_completed["next_todos"][0]["required_capabilities"] == (
            SUCCESSOR_CAPABILITIES
        ), side_completed
        assert side_completed["next_todos"][0]["unblocks_todo_id"] == side_review_todo_id, side_completed
        assert side_completed["successor_todo_ids"] == [review_todo_id], side_completed
        items = parsed_items(state_file)
        side_item = next(item for item in items if item["todo_id"] == side_todo_id)
        side_review_item = next(item for item in items if item["todo_id"] == side_review_todo_id)
        review_item = next(item for item in items if item["todo_id"] == review_todo_id)
        assert side_item["done"] is True and side_item["claimed_by"] == "codex-side-bypass", side_item
        assert side_review_item["done"] is True and side_review_item["claimed_by"] == "codex-side-bypass", (
            side_review_item
        )
        assert side_review_item["successor_todo_ids"] == [review_todo_id], side_review_item
        assert review_item["done"] is False and review_item["claimed_by"] == "codex-main-control", review_item
        assert review_item["action_kind"] == "review", review_item
        assert review_item.get("blocks_agent") is None, review_item
        assert review_item["excluded_agents"] == ["codex-side-bypass"], review_item
        assert review_item["task_repository"] == SUCCESSOR_REPOSITORY, review_item
        assert review_item["required_capabilities"] == SUCCESSOR_CAPABILITIES, review_item
        assert review_item["unblocks_todo_id"] == side_review_todo_id, review_item
        assert review_item.get("updated_at"), review_item
        assert side_completed["next_todos"][0].get("updated_at") == review_item["updated_at"], side_completed

        before_retry = state_file.read_bytes()
        conflicting_retry = run_cli_error(
            registry_path, *completion_args, "--next-action-kind", "different_action",
        )
        assert conflicting_retry["error_code"] == "coordination_operation_identity_mismatch", conflicting_retry
        assert state_file.read_bytes() == before_retry
        repeated = run_cli(registry_path, *completion_args)
        assert repeated["changed"] is False, repeated
        assert state_file.read_bytes() == before_retry
        assert state_file.read_text(encoding="utf-8").count(REBUILD_TODO) == 1

        supersede_claimed_by_error = run_cli_error(
            registry_path,
            "todo",
            "supersede",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            rebuild_todo_id,
            "--claimed-by",
            "codex-main-control",
            "--next-agent-todo",
            VALIDATE_TODO,
        )
        assert "todo supersede does not support --claimed-by" in supersede_claimed_by_error["error"], (
            supersede_claimed_by_error
        )
        assert "--next-claimed-by" in supersede_claimed_by_error["error"], supersede_claimed_by_error
        assert "inherit the superseded todo owner when present" in supersede_claimed_by_error["error"], (
            supersede_claimed_by_error
        )

        superseded = run_cli(
            registry_path,
            "todo",
            "supersede",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            rebuild_todo_id,
            "--agent-id",
            "codex-main-control",
            "--reason",
            "folded-into-validation-step",
            "--next-agent-todo",
            VALIDATE_TODO,
            "--next-claimed-by",
            "codex-main-control",
            "--next-action-kind",
            "validate",
        )
        assert superseded["changed"] is True, superseded
        validate_todo_id = superseded["next_todos"][0]["todo_id"]
        assert superseded["successor_todo_ids"] == [validate_todo_id], superseded
        items = parsed_items(state_file)
        rebuild_item = next(item for item in items if item["todo_id"] == rebuild_todo_id)
        validate_item = next(item for item in items if item["todo_id"] == validate_todo_id)
        assert rebuild_item["done"] is True and rebuild_item["superseded_by"] == validate_todo_id, rebuild_item
        assert rebuild_item["successor_todo_ids"] == [validate_todo_id], rebuild_item
        assert validate_item["done"] is False and validate_item["task_class"] == "advancement_task", validate_item
        assert validate_item["claimed_by"] == "codex-main-control", validate_item

        handoff_source = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            HANDOFF_SOURCE_TODO,
            "--claimed-by",
            "codex-main-control",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "handoff_route",
        )
        handoff = run_cli(
            registry_path,
            "todo",
            "supersede",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            handoff_source["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--reason",
            "split-to-side-agent-successor",
            "--next-agent-todo",
            HANDOFF_SUCCESSOR_TODO,
            "--next-claimed-by",
            "codex-side-bypass",
            "--next-action-kind",
            "handoff_route",
        )
        assert handoff["changed"] is True, handoff
        assert handoff["next_todos"][0]["claimed_by"] == "codex-side-bypass", handoff
        handoff_successor_id = handoff["next_todos"][0]["todo_id"]
        assert handoff["successor_todo_ids"] == [handoff_successor_id], handoff
        handoff_items = parsed_items(state_file)
        handoff_source_item = next(item for item in handoff_items if item["todo_id"] == handoff_source["todo_id"])
        handoff_successor_item = next(item for item in handoff_items if item["todo_id"] == handoff_successor_id)
        assert handoff_source_item["superseded_by"] == handoff_successor_id, handoff_source_item
        assert handoff_source_item["successor_todo_ids"] == [handoff_successor_id], handoff_source_item
        assert handoff_successor_item["claimed_by"] == "codex-side-bypass", handoff_successor_item

        unclaimed_source = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            UNCLAIMED_SOURCE_TODO,
            "--task-class",
            "advancement_task",
            "--action-kind",
            "unclaimed_route",
        )
        unclaimed = run_cli(
            registry_path,
            "todo",
            "supersede",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            unclaimed_source["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--reason",
            "keep-successor-unclaimed",
            "--next-agent-todo",
            UNCLAIMED_SUCCESSOR_TODO,
            "--next-action-kind",
            "unclaimed_route",
        )
        assert unclaimed["changed"] is True, unclaimed
        assert not unclaimed["next_todos"][0].get("claimed_by"), unclaimed
        unclaimed_successor_id = unclaimed["next_todos"][0]["todo_id"]
        unclaimed_successor_item = next(
            item for item in parsed_items(state_file) if item["todo_id"] == unclaimed_successor_id
        )
        assert not unclaimed_successor_item.get("claimed_by"), unclaimed_successor_item

        blocked = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            validate_todo_id,
            "--agent-id",
            "codex-main-control",
            "--status",
            "blocked",
            "--reason",
            "waiting-on-public-scorer-source",
            "--task-class",
            "blocker",
        )
        assert blocked["changed"] is True, blocked
        validate_item = next(item for item in parsed_items(state_file) if item["todo_id"] == validate_todo_id)
        assert validate_item["status"] == "blocked", validate_item
        assert validate_item["done"] is False, validate_item
        assert validate_item["task_class"] == "blocker", validate_item
        fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        executable_ids = {
            item["todo_id"]
            for item in fields["agent_todos"].get("first_executable_items", [])
        }
        item_ids = {
            item["todo_id"]
            for item in fields["agent_todos"].get("items", [])
        }
        assert validate_todo_id in item_ids, fields
        assert validate_todo_id not in executable_ids, fields

    print("todo-lifecycle-cli-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
