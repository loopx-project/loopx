#!/usr/bin/env python3
"""Bounded canary for the status -> quota -> review-packet Todo read path."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    read_run_index,
    run_json_cli,
    write_fixture_registry,
)


GOAL_ID = "control-plane-integrated-canary"
MONITOR_GOAL_ID = "control-plane-integrated-monitor-canary"
MARKDOWN_GOAL_ID = "control-plane-markdown-continuation-canary"
AGENT_ID = "codex-product-capability"
PRIMARY_AGENT_ID = "codex-main-control"
CANARY_TODO_ID = "todo_integrated_canary"
CANARY_TODO_TITLE = "Design bounded status/quota/review-packet/Todo/read-path canary"
SUCCESSOR_TODO_TITLE = "Continue integrated Markdown successor routing canary"
MARKDOWN_CONTINUATION_TITLE = "Continue same-agent markdown continuation through status and quota."
MONITOR_TODO_ID = "todo_integrated_due_monitor"
MONITOR_TARGET_KEY = "integrated-due-monitor-watch"
VALIDATED_PROGRESS_CLASSIFICATION = "integrated_canary_validated_progress"
DEFAULT_MAX_SECONDS = 60.0
STATUS_BACKED_COMMANDS = {"quota", "review-packet", "status"}


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-06-27T00:00:00+00:00\n"
        "---\n\n"
        "# Control Plane Integrated Canary\n\n"
        "## Next Action\n\n"
        "- Keep the fixture-only integrated canary under two minutes.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P2] Markdown fallback todo that should lose to the event log.\n"
        "  <!-- loopx:todo todo_id=todo_markdown_fallback status=open "
        "task_class=advancement_task action_kind=stale_markdown -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="control-plane-canary",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[PRIMARY_AGENT_ID, AGENT_ID],
        quota_allowed_slots=10,
        peer_independent_worktree_required=False,
    )
    return registry_path, state_file, runtime


def write_monitor_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "monitor-project"
    runtime = root / "monitor-runtime"
    state_file = project / ".codex" / "goals" / MONITOR_GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-06-27T00:00:00+00:00\n"
        "---\n\n"
        "# Control Plane Monitor Canary\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1-monitor] Monitor integrated state-machine transition and write back only material transitions.\n"
        f"  <!-- loopx:todo todo_id={MONITOR_TODO_ID} status=open "
        "task_class=continuous_monitor action_kind=monitor "
        f"claimed_by={AGENT_ID} target_key={MONITOR_TARGET_KEY} "
        "cadence=5m next_due_at=2000-01-01T00:00:00+00:00 -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=MONITOR_GOAL_ID,
        domain="control-plane-canary",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[AGENT_ID],
        quota_allowed_slots=10,
    )
    return registry_path, runtime


def write_markdown_continuation_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "markdown-project"
    runtime = root / "markdown-runtime"
    state_file = project / ".codex" / "goals" / MARKDOWN_GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-06-27T00:00:00+00:00\n"
        "---\n\n"
        "# Control Plane Markdown Continuation Canary\n\n"
        "## Next Action\n\n"
        "- Preserve same-agent markdown successor routing across status and quota.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P2] Markdown placeholder that should lose to the claimed P1 todo.\n"
        "  <!-- loopx:todo todo_id=todo_markdown_placeholder status=open "
        "task_class=advancement_task action_kind=stale_markdown -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=MARKDOWN_GOAL_ID,
        domain="control-plane-canary",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[PRIMARY_AGENT_ID, AGENT_ID],
        quota_allowed_slots=10,
        peer_independent_worktree_required=False,
    )
    return registry_path, runtime, project


def write_canary_todos(state_file: Path) -> None:
    state_file.write_text(state_file.read_text().split("## Agent Todo")[0] +
        "## Agent Todo\n\n" + f"- [ ] [P1] {CANARY_TODO_TITLE}\n" +
        f"  <!-- loopx:todo todo_id={CANARY_TODO_ID} status=open task_class=advancement_task "
        f"action_kind=integrated_canary_design claimed_by={AGENT_ID} -->\n" +
        "\n## User Todo\n\n- [x] [P2] Prior canary scope approval\n"
        "  <!-- loopx:todo todo_id=todo_user_prior_approval status=done task_class=user_gate -->\n")


def run_cli(registry_path: Path, runtime_root: Path, *args: str) -> dict[str, Any]:
    cli_args = list(args)
    if (
        cli_args
        and cli_args[0] in STATUS_BACKED_COMMANDS
        and "--scan-root" not in cli_args
        and "--scan-path" not in cli_args
    ):
        # Keep public-boundary validation on the fixture project, not this source checkout.
        cli_args.extend(["--scan-root", str(registry_path.parent.parent)])
    return run_json_cli(
        *cli_args,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=REPO_ROOT,
        include_returncode=False,
    )


def find_queue_item(status_payload: dict[str, Any], *, goal_id: str = GOAL_ID) -> dict[str, Any]:
    queue = status_payload.get("attention_queue")
    assert isinstance(queue, dict), status_payload
    for item in queue.get("items") or []:
        if isinstance(item, dict) and item.get("goal_id") == goal_id:
            return item
    raise AssertionError(f"{goal_id} missing from attention queue: {status_payload}")


def assert_selected_agent_todo(
    summary: dict[str, Any],
    *,
    compact_quota: bool = False,
) -> None:
    item_lane = "first_executable_items" if compact_quota else "first_open_items"
    items = summary.get(item_lane) if isinstance(summary.get(item_lane), list) else []
    if not items and not compact_quota:
        items = summary.get("items") if isinstance(summary.get("items"), list) else []
    assert [item.get("todo_id") for item in items] == [CANARY_TODO_ID], summary
    item = items[0]
    if compact_quota:
        assert summary["payload_compaction"]["schema_version"] == (
            "quota_cli_todo_summary_compaction_v0"
        ), summary
        assert "title" not in item, summary
        assert item["text"] == f"[P1] {CANARY_TODO_TITLE}", summary
    else:
        assert item["title"] == CANARY_TODO_TITLE, summary
    assert item["claimed_by"] == AGENT_ID, summary
    assert item["task_class"] == "advancement_task", summary
    assert "todo_markdown_fallback" not in json.dumps(summary, sort_keys=True), summary


def assert_bounded_delivery_state_machine_bundle(quota_payload: dict[str, Any]) -> None:
    """Assert that the major hot-path state machines agree on active work."""

    contract = quota_payload["interaction_contract"]
    assert contract["mode"] == "bounded_delivery", quota_payload
    assert contract["user_channel"] == {
        "action_required": False,
        "notify": "DONT_NOTIFY",
    }, contract
    agent_channel = contract["agent_channel"]
    assert agent_channel["must_attempt"] is True, contract
    assert agent_channel["delivery_allowed"] is True, contract
    assert agent_channel["quiet_noop_allowed"] is False, contract
    assert agent_channel["primary_action"].startswith(f"{CANARY_TODO_ID}: [P1]"), contract
    cli_channel = contract["cli_channel"]
    assert cli_channel["spend_allowed_now"] is False, contract
    assert cli_channel["spend_after_validation"] is True, contract
    assert cli_channel["spend_policy"] == "spend once after validated writeback", contract
    assert any("refresh-state" in action for action in cli_channel["next_cli_actions"]), contract
    assert any("spend-slot" in action for action in cli_channel["next_cli_actions"]), contract

    lane = quota_payload["work_lane_contract"]
    assert lane["schema_version"] == "work_lane_contract_v1", lane
    assert lane["lane"] == "advancement_task", lane
    assert lane["next_lane"] == "advancement_task", lane
    assert lane["obligation"] == "advance_one_bounded_segment", lane
    assert lane["must_attempt_work"] is True, lane
    assert lane["reason_codes"] == ["open_agent_todo"], lane

    scheduler = quota_payload["scheduler_hint"]
    assert scheduler["action"] == "run_now", scheduler
    assert scheduler["cadence_class"] == "active_work", scheduler
    codex_app = scheduler["codex_app"]
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler
    assert codex_app["no_spend_for_cadence_change"] is True, scheduler
    assert codex_app["stateful_backoff"]["apply_needed"] is True, scheduler

    frontier = quota_payload["goal_frontier_projection"]
    assert frontier["remaining_advancement_frontier"] == {
        "current_agent_claimed_advancement_count": 1,
        "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 0,
    }, frontier
    assert frontier["monitor_only_lanes"]["present"] is False, frontier
    assert frontier["replan_required"] is False, frontier

    liveness = quota_payload["automation_liveness"]
    assert liveness["keep_active"] is True, liveness
    assert liveness["pause_allowed"] is False, liveness
    assert liveness["automation_action"] == "execute_bounded_work", liveness

    identity = quota_payload["agent_identity"]
    assert identity["agent_model"] == "peer_v1", identity
    assert "role" not in identity, identity
    assert "primary_agent" not in identity, identity
    assert quota_payload["agent_lane_next_action"]["preserves_goal_next_action"] is True, quota_payload


def assert_scheduler_ack_state_machine(
    registry_path: Path,
    runtime_root: Path,
    quota_payload: dict[str, Any],
) -> dict[str, Any]:
    codex_app = quota_payload["scheduler_hint"]["codex_app"]
    backoff = codex_app["stateful_backoff"]
    ack_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "scheduler-ack",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
        "--surface",
        "codex_app",
        "--state-key",
        backoff["state_key"],
        "--applied-rrule",
        codex_app["recommended_rrule"],
        "--reset-token",
        backoff["reset_token"],
        "--identity-signature",
        backoff["identity_signature"],
        "--execute",
    )
    assert ack_payload["ok"] is True, ack_payload
    assert ack_payload["mode"] == "scheduler-ack", ack_payload
    assert ack_payload["dry_run"] is False, ack_payload

    steady_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    steady_codex_app = steady_payload["scheduler_hint"]["codex_app"]
    assert steady_codex_app["stateful_backoff"]["apply_needed"] is False, steady_payload
    assert steady_codex_app["stateful_backoff"]["state_status"] == "same_identity", steady_payload
    assert steady_codex_app["host_action"] == "none", steady_payload
    assert "recommended_rrule" not in steady_codex_app, steady_payload
    return steady_payload


def assert_todo_completion_successor_state_machine(
    registry_path: Path,
    runtime_root: Path,
) -> str:
    completed = run_cli(
        registry_path,
        runtime_root,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--todo-id",
        CANARY_TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
        "--self-merged",
        "--evidence",
        "fixture Todo completion passed",
        "--next-agent-todo",
        SUCCESSOR_TODO_TITLE,
        "--next-claimed-by",
        AGENT_ID,
        "--next-task-class",
        "advancement_task",
        "--next-action-kind",
        "state_machine_canary_refactor",
    )
    assert completed["ok"] is True, completed
    assert completed["completed"] is True, completed
    assert completed["self_merged"] is True, completed
    assert completed["todo_id"] == CANARY_TODO_ID, completed
    assert completed["status"] == "done", completed
    assert len(completed["next_todos"]) == 1, completed
    successor = completed["next_todos"][0]
    successor_id = successor["todo_id"]
    assert completed["successor_todo_ids"] == [successor_id], completed
    assert successor["claimed_by"] == AGENT_ID, completed
    assert successor["task_class"] == "advancement_task", completed
    assert successor["action_kind"] == "state_machine_canary_refactor", completed

    listed = run_cli(
        registry_path,
        runtime_root,
        "todo",
        "list",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--agent-id",
        AGENT_ID,
    )
    assert listed["source"] == "markdown_active_state", listed
    by_id = {item["todo_id"]: item for item in listed["todos"]}
    assert by_id[CANARY_TODO_ID]["status"] == "done", listed
    assert by_id[CANARY_TODO_ID]["evidence"] == "fixture Todo completion passed", listed
    assert by_id[CANARY_TODO_ID]["successor_todo_ids"] == [successor_id], listed
    assert by_id[successor_id]["status"] == "open", listed
    assert by_id[successor_id]["claimed_by"] == AGENT_ID, listed
    assert by_id[successor_id]["unblocks_todo_id"] == CANARY_TODO_ID, listed
    assert "todo_succession_warning" not in listed["agent_todos"], listed

    routed = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert routed["decision"] == "run", routed
    assert routed["effective_action"] == "normal_run", routed
    assert routed["agent_lane_next_action"]["todo_id"] == successor_id, routed
    assert routed["agent_lane_next_action"]["text"] == f"[P1] {SUCCESSOR_TODO_TITLE}", routed
    assert "title" not in routed["agent_lane_next_action"], routed
    assert routed["agent_lane_next_action"]["preserves_goal_next_action"] is True, routed
    assert "active_state_next_action" not in routed, routed
    assert routed["goal_route_hint"]["selected_action_differs_from_durable"] is True, routed
    assert routed["goal_route_hint"]["goal_next_action_mutation"] == "none", routed
    assert routed["interaction_contract"]["mode"] == "bounded_delivery", routed
    assert routed["scheduler_hint"]["action"] == "run_now", routed
    return successor_id


def assert_refresh_and_spend_state_machine(
    registry_path: Path,
    runtime_root: Path,
    successor_id: str,
) -> None:
    refresh_payload = run_cli(
        registry_path,
        runtime_root,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--agent-lane",
        "product_capability",
        "--progress-scope",
        "agent_lane",
        "--classification",
        VALIDATED_PROGRESS_CLASSIFICATION,
        "--delivery-batch-scale",
        "multi_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--recommended-action",
        "Integrated canary fixture validated the active transition bundle.",
    )
    assert refresh_payload["ok"] is True, refresh_payload
    assert refresh_payload["appended"] is True, refresh_payload
    assert refresh_payload["classification"] == VALIDATED_PROGRESS_CLASSIFICATION, refresh_payload
    assert refresh_payload["delivery_batch_scale"] == "multi_surface", refresh_payload
    assert refresh_payload["delivery_outcome"] == "outcome_progress", refresh_payload

    matching_runs = [
        run for run in read_run_index(runtime_root, GOAL_ID)
        if run.get("classification") == VALIDATED_PROGRESS_CLASSIFICATION
    ]
    assert len(matching_runs) == 1, matching_runs
    assert matching_runs[0]["progress_scope"] == "agent_lane", matching_runs
    assert matching_runs[0]["agent_id"] == AGENT_ID, matching_runs

    spend_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        successor_id,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
    )
    assert spend_payload["ok"] is True, spend_payload
    assert spend_payload["appended"] is True, spend_payload
    assert spend_payload["dry_run"] is False, spend_payload

    spent_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert spent_payload["quota"]["spent_slots"] == 1, spent_payload
    assert spent_payload["quota"]["state"] == "eligible", spent_payload


def assert_due_monitor_poll_state_machine(root: Path) -> None:
    registry_path, runtime_root = write_monitor_fixture(root)

    quota_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        MONITOR_GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert quota_payload["ok"] is True, quota_payload
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["decision"] == "run", quota_payload
    assert quota_payload["effective_action"] == "normal_run", quota_payload
    lane = quota_payload["work_lane_contract"]
    assert lane["monitor_kind"] == "todo_monitor_due", lane
    assert lane["obligation"] == "attempt_due_monitor", lane
    assert lane["selected_todo_id"] == MONITOR_TODO_ID, lane
    contract = quota_payload["interaction_contract"]
    assert contract["mode"] == "bounded_delivery", contract
    assert contract["agent_channel"]["must_attempt"] is True, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is False, contract
    assert contract["cli_channel"]["spend_allowed_now"] is False, contract
    assert quota_payload["goal_frontier_projection"]["replan_required"] is False, quota_payload
    assert quota_payload.get("autonomous_replan_obligation") is None, quota_payload

    monitor_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "monitor-poll",
        "--goal-id",
        MONITOR_GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        MONITOR_TODO_ID,
        "--result-hash",
        "unchanged-integrated-monitor",
        "--cadence",
        "5m",
        "--execute",
    )
    assert monitor_payload["ok"] is True, monitor_payload
    assert monitor_payload["appended"] is True, monitor_payload
    assert monitor_payload["dry_run"] is False, monitor_payload
    assert monitor_payload["delivery_outcome"] == "surface_only", monitor_payload
    assert monitor_payload["material_change"] is False, monitor_payload
    assert monitor_payload["monitor_event"]["material_change"] is False, monitor_payload
    assert monitor_payload["todo_writeback"]["todo_id"] == MONITOR_TODO_ID, monitor_payload
    assert monitor_payload["todo_writeback"]["next_due_at"], monitor_payload
    assert monitor_payload["todo_writeback"]["consecutive_no_change"] == 1, monitor_payload

    quiet_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        MONITOR_GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert quiet_payload["decision"] == "skip", quiet_payload
    assert quiet_payload["effective_action"] == "monitor_quiet_skip", quiet_payload
    assert quiet_payload["should_run"] is False, quiet_payload
    assert quiet_payload["execution_obligation"]["must_attempt_work"] is False, quiet_payload
    assert quiet_payload["interaction_contract"]["mode"] == "monitor_quiet_skip", quiet_payload
    assert quiet_payload["work_lane_contract"]["obligation"] == (
        "quiet_until_material_monitor_transition"
    ), quiet_payload
    assert quiet_payload["work_lane_contract"]["must_attempt_work"] is False, quiet_payload
    assert quiet_payload["goal_frontier_projection"]["monitor_only_lanes"]["present"] is True, quiet_payload
    assert quiet_payload["goal_frontier_projection"]["replan_required"] is False, quiet_payload
    assert quiet_payload.get("autonomous_replan_obligation") is None, quiet_payload
    assert quiet_payload["agent_todo_summary"]["monitor_due_count"] == 0, quiet_payload
    assert quiet_payload["scheduler_hint"]["action"] != "run_now", quiet_payload


def assert_markdown_same_agent_continuation_read_path(root: Path) -> None:
    registry_path, runtime_root, project = write_markdown_continuation_fixture(root)
    added = run_cli(
        registry_path,
        runtime_root,
        "todo",
        "add",
        "--goal-id",
        MARKDOWN_GOAL_ID,
        "--role",
        "agent",
        "--text",
        MARKDOWN_CONTINUATION_TITLE,
        "--claimed-by",
        AGENT_ID,
        "--task-class",
        "advancement_task",
        "--action-kind",
        "state_machine_canary_refactor",
    )
    assert added["ok"] is True, added
    assert added["added"] is True, added
    source_todo_id = added["todo_id"]

    status_payload = run_cli(
        registry_path,
        runtime_root,
        "status",
        "--scan-root",
        str(project),
        "--agent-id",
        AGENT_ID,
        "--limit",
        "3",
    )
    queue_item = find_queue_item(status_payload, goal_id=MARKDOWN_GOAL_ID)
    assert queue_item["status"] in {"active_state_agent_todo", "connected_without_run"}, queue_item
    assert queue_item["waiting_on"] == "codex", queue_item
    assert MARKDOWN_CONTINUATION_TITLE in queue_item["recommended_action"], queue_item

    source_quota = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        MARKDOWN_GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert source_quota["decision"] == "run", source_quota
    assert source_quota["agent_lane_next_action"]["todo_id"] == source_todo_id, source_quota
    assert source_quota["interaction_contract"]["mode"] == "bounded_delivery", source_quota
    assert source_quota["interaction_contract"]["agent_channel"]["must_attempt"] is True, source_quota

    completed = run_cli(
        registry_path,
        runtime_root,
        "todo",
        "complete",
        "--goal-id",
        MARKDOWN_GOAL_ID,
        "--role",
        "agent",
        "--todo-id",
        source_todo_id,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
        "--self-merged",
        "--evidence",
        "fixture same-agent markdown continuation passed",
        "--next-agent-todo",
        MARKDOWN_CONTINUATION_TITLE,
        "--next-claimed-by",
        AGENT_ID,
        "--next-task-class",
        "advancement_task",
        "--next-action-kind",
        "state_machine_canary_refactor",
    )
    assert completed["ok"] is True, completed
    assert completed["completed"] is True, completed
    assert completed["self_merged"] is True, completed
    assert len(completed["next_todos"]) == 1, completed
    successor = completed["next_todos"][0]
    successor_todo_id = successor["todo_id"]
    assert successor["added"] is True, completed
    assert successor["already_exists"] is False, completed
    assert successor_todo_id != source_todo_id, completed
    assert successor["unblocks_todo_id"] == source_todo_id, completed
    assert successor["claimed_by"] == AGENT_ID, completed

    listed = run_cli(
        registry_path,
        runtime_root,
        "todo",
        "list",
        "--goal-id",
        MARKDOWN_GOAL_ID,
        "--role",
        "agent",
        "--agent-id",
        AGENT_ID,
    )
    assert listed["source"] == "markdown_active_state", listed
    by_id = {item["todo_id"]: item for item in listed["todos"]}
    assert by_id[source_todo_id]["status"] == "done", listed
    assert by_id[successor_todo_id]["status"] == "open", listed
    assert by_id[successor_todo_id]["unblocks_todo_id"] == source_todo_id, listed
    assert "todo_succession_warning" not in listed["agent_todos"], listed

    successor_quota = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        MARKDOWN_GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert successor_quota["decision"] == "run", successor_quota
    assert successor_quota["effective_action"] == "normal_run", successor_quota
    assert successor_quota["agent_lane_next_action"]["todo_id"] == successor_todo_id, successor_quota
    assert successor_quota["agent_lane_next_action"]["text"] == MARKDOWN_CONTINUATION_TITLE, successor_quota
    assert successor_quota["agent_lane_next_action"]["unblocks_todo_id"] == source_todo_id, successor_quota
    assert successor_quota["interaction_contract"]["user_channel"]["action_required"] is False, successor_quota
    assert successor_quota["interaction_contract"]["agent_channel"]["must_attempt"] is True, successor_quota
    assert successor_quota["scheduler_hint"]["action"] == "run_now", successor_quota

    refreshed_status = run_cli(
        registry_path,
        runtime_root,
        "status",
        "--scan-root",
        str(project),
        "--agent-id",
        AGENT_ID,
        "--limit",
        "3",
    )
    refreshed_item = find_queue_item(refreshed_status, goal_id=MARKDOWN_GOAL_ID)
    assert refreshed_item["waiting_on"] == "codex", refreshed_item
    assert refreshed_item["project_asset"]["agent_lane_next_action"]["todo_id"] == (
        successor_todo_id
    ), refreshed_item
    assert refreshed_item["project_asset"]["agent_todos"]["payload_reference"][
        "canonical_path"
    ] == "attention_queue.items[].agent_todos", refreshed_item
    assert source_todo_id not in refreshed_item["recommended_action"], refreshed_item

    packet_payload = run_cli(
        registry_path,
        runtime_root,
        "review-packet",
        "--goal-id",
        MARKDOWN_GOAL_ID,
        "--format",
        "json",
        "--agent-id",
        AGENT_ID,
    )
    assert packet_payload["ok"] is True, packet_payload
    assert packet_payload["handoff_interface_budget"]["within_budget"] is True, packet_payload
    assert MARKDOWN_CONTINUATION_TITLE in packet_payload["agent_todo_text"], packet_payload
    assert successor_todo_id in packet_payload["project_agent_handoff"], packet_payload
    assert source_todo_id not in packet_payload["project_agent_handoff"], packet_payload


def run_fixture_canary(root: Path) -> None:
    registry_path, state_file, runtime_root = write_fixture(root)
    runtime_root = root / "runtime"
    write_canary_todos(state_file)

    status_payload = run_cli(
        registry_path,
        runtime_root,
        "status",
        "--scan-root",
        str(root / "project"),
        "--agent-id",
        AGENT_ID,
        "--limit",
        "3",
    )
    assert status_payload["ok"] is True, status_payload
    queue_item = find_queue_item(status_payload)
    allowed_status = {"active_state_agent_todo", "connected_without_run"}
    assert queue_item["status"] in allowed_status, queue_item
    assert queue_item["waiting_on"] == "codex", queue_item
    assert CANARY_TODO_TITLE in queue_item["recommended_action"], queue_item
    assert_selected_agent_todo(queue_item["agent_todos"])
    assert queue_item["project_asset"]["agent_todos"]["payload_reference"][
        "canonical_path"
    ] == "attention_queue.items[].agent_todos", queue_item

    quota_payload = run_cli(
        registry_path,
        runtime_root,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
    )
    assert quota_payload["ok"] is True, quota_payload
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["effective_action"] == "normal_run", quota_payload
    assert quota_payload["interaction_contract"]["agent_channel"]["must_attempt"] is True, quota_payload
    assert CANARY_TODO_TITLE in quota_payload["recommended_action"], quota_payload
    assert_selected_agent_todo(
        quota_payload["agent_todo_summary"],
        compact_quota=True,
    )
    assert_bounded_delivery_state_machine_bundle(quota_payload)
    assert_scheduler_ack_state_machine(registry_path, runtime_root, quota_payload)
    successor_id = assert_todo_completion_successor_state_machine(registry_path, runtime_root)
    assert_refresh_and_spend_state_machine(registry_path, runtime_root, successor_id)

    packet_payload = run_cli(
        registry_path,
        runtime_root,
        "review-packet",
        "--goal-id",
        GOAL_ID,
        "--format",
        "json",
    )
    assert packet_payload["ok"] is True, packet_payload
    assert packet_payload["status"] in allowed_status, packet_payload
    assert packet_payload["project_asset_source"] == "project_asset", packet_payload
    assert SUCCESSOR_TODO_TITLE in packet_payload["project_agent_handoff"], packet_payload
    assert CANARY_TODO_TITLE not in packet_payload["project_agent_handoff"], packet_payload
    assert packet_payload["handoff_interface_budget"]["within_budget"] is True, packet_payload
    assert_markdown_same_agent_continuation_read_path(root)
    assert_due_monitor_poll_state_machine(root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deep-run", action="store_true", help="Reserved for explicit heavier checks.")
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.deep_run:
        raise SystemExit("--deep-run is intentionally not implemented for the fixture canary yet")
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="loopx-control-plane-canary-") as tmp:
        run_fixture_canary(Path(tmp))
    elapsed = time.monotonic() - start
    assert elapsed <= args.max_seconds, {
        "elapsed_seconds": elapsed,
        "max_seconds": args.max_seconds,
    }
    print(f"control-plane-integrated-canary-smoke ok elapsed={elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
