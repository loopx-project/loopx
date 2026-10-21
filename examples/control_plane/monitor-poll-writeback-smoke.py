#!/usr/bin/env python3
"""Smoke-test monitor-poll todo metadata writeback."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import loopx.cli_commands.quota as quota_command  # noqa: E402
from loopx.quota import record_quota_monitor_poll  # noqa: E402
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path  # noqa: E402
from loopx.control_plane.quota.monitor_poll import (  # noqa: E402
    build_quota_monitor_poll_event,
)
from loopx.control_plane.scheduler.monitor_poll_writeback import (  # noqa: E402
    resolve_monitor_todo_item,
    write_monitor_poll_todo_state,
)
from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    import_fixture_authority,
    read_run_index,
    run_json_cli,
    run_json_cli_result,
    runtime_root_from_registry,
    write_fixture_registry,
)
from loopx.status import (  # noqa: E402
    AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK,
    collect_status,
    parse_active_state_todos,
)


GOAL_ID = "monitor-poll-writeback-fixture"
AGENT_ID = "codex-product-capability"
TODO_ID = "todo_monitorpoll000"
TARGET_KEY = "update-note-draft-pr"
OTHER_TARGET_KEY = "other-monitor-target"
MONITOR_POLL_JSON_BUDGET = 40_000


def assert_compact_monitor_poll_decisions(payload: dict) -> None:
    summary = payload["decision_summary"]
    assert payload["before"] == summary["before"], payload
    assert "work_lane_contract" not in payload["before"], payload
    assert "interaction_contract" not in payload["before"], payload
    if isinstance(payload.get("after"), dict):
        assert payload["after"] != summary["after"], payload
        assert "interaction_contract" not in summary["after"], payload
        contract = payload["after"]["interaction_contract"]
        assert contract["agent_channel"]["must_attempt"] is payload["after"][
            "should_run"
        ], payload
        assert "required_reads" not in contract["agent_channel"], payload
        assert "next_cli_actions" not in contract["cli_channel"], payload
    decisions = payload["payload_compaction"]["decisions"]
    assert decisions["detail_ref"]["request"] == (
        "quota monitor-poll --include-detail decisions"
    ), payload
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    assert len(rendered) <= MONITOR_POLL_JSON_BUDGET, len(rendered)


def write_promotion_readiness(runtime: Path) -> None:
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    json_path = runs_dir / "readiness.json"
    markdown_path = runs_dir / "readiness.md"
    record = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": "canary_promotion_readiness_smoke_group",
        "delivery_batch_scale": "multi_surface",
        "delivery_outcome": "primary_goal_outcome",
        "recommended_action": "fixture promotion readiness is fresh",
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }
    json_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text("# Canary promotion readiness\n", encoding="utf-8")
    (runs_dir / "index.jsonl").write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")


def write_fixture(
    root: Path,
    *,
    selected_target_key: str | None = TARGET_KEY,
    include_other_monitor: bool = False,
    include_blocked_advancement: bool = False,
    include_user_gate: bool = False,
    include_advancement: bool = False,
    primary_monitor_priority: str = "P0",
    primary_no_change_count: int = 1,
    other_no_change_count: int = 1,
    monitor_required_capabilities: tuple[str, ...] = (),
    monitor_task_repository: str | None = None,
) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    write_promotion_readiness(runtime)
    monitor_metadata = (
        f"target_key={selected_target_key} "
        if selected_target_key
        else ""
    )
    monitor_capability_metadata = (
        f"required_capabilities={','.join(monitor_required_capabilities)} "
        if monitor_required_capabilities
        else ""
    )
    monitor_repository_metadata = (
        f"task_repository={monitor_task_repository} "
        if monitor_task_repository
        else ""
    )
    other_monitor = (
        "- [ ] [P1] Poll another monitor target.\n"
        "  <!-- loopx:todo "
        "todo_id=todo_monitorpoll111 "
        "status=open "
        "task_class=continuous_monitor "
        "action_kind=poll "
        f"claimed_by={AGENT_ID} "
        f"target_key={OTHER_TARGET_KEY} "
        "cadence=15m "
        "next_due_at=2026-01-01T00:00:00+00:00 "
        "result_hash=old "
        f"consecutive_no_change={other_no_change_count} "
        "material_change=false "
        "-->\n"
        if include_other_monitor
        else ""
    )
    blocked_advancement = (
        "- [ ] [P1] Run the next official and countable matched benchmark pair.\n"
        "  <!-- loopx:todo "
        "todo_id=todo_monitorpollbenchmark "
        "status=blocked "
        "task_class=advancement_task "
        "action_kind=run_matched_benchmark "
        "required_capabilities=benchmark_runner "
        f"claimed_by={AGENT_ID} "
        "-->\n"
        if include_blocked_advancement
        else ""
    )
    gated_advancement = (
        "- [ ] [P1] Prepare the gated publication packet.\n"
        "  <!-- loopx:todo "
        "todo_id=todo_monitorpollblocked "
        "status=open "
        "task_class=advancement_task "
        f"claimed_by={AGENT_ID} "
        "-->\n"
        if include_user_gate
        else ""
    )
    advancement = (
        "- [ ] [P0] Advance the independent delivery task.\n"
        "  <!-- loopx:todo "
        "todo_id=todo_monitorpolladvance "
        "status=open "
        "task_class=advancement_task "
        "required_capabilities=network "
        f"claimed_by={AGENT_ID} "
        "-->\n"
        if include_advancement
        else ""
    )
    user_gate = (
        "\n## User Todo\n\n"
        "- [ ] [P0-user] Review the unrelated publication gate.\n"
        "  <!-- loopx:todo "
        "todo_id=todo_monitorpollgate "
        "status=open "
        "task_class=user_gate "
        "action_kind=review_publication_gate "
        f"blocks_agent={AGENT_ID} "
        "unblocks_todo_id=todo_monitorpollblocked "
        "-->\n"
        if include_user_gate
        else ""
    )
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Exercise due monitor poll writeback.\n\n"
        "## Agent Todo\n\n"
        f"- [ ] [{primary_monitor_priority}] Poll the update-note draft PR for material changes.\n"
        "  <!-- loopx:todo "
        f"todo_id={TODO_ID} "
        "status=open "
        "task_class=continuous_monitor "
        "action_kind=poll "
        f"{monitor_capability_metadata}"
        f"{monitor_repository_metadata}"
        f"claimed_by={AGENT_ID} "
        f"{monitor_metadata}"
        "cadence=15m "
        "next_due_at=2026-01-01T00:00:00+00:00 "
        "result_hash=old "
        f"consecutive_no_change={primary_no_change_count} "
        "material_change=false "
        "-->\n"
        f"{other_monitor}"
        f"{blocked_advancement}"
        f"{gated_advancement}"
        f"{advancement}"
        f"{user_gate}",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="monitor-poll-writeback",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[AGENT_ID],
        quota_allowed_slots=None,
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, state_file


def run_cli(registry_path: Path, *args: str) -> dict:
    return run_json_cli(
        *args,
        registry_path=registry_path,
        runtime_root=runtime_root_from_registry(registry_path),
        cwd=REPO_ROOT,
        include_returncode=False,
    )


def run_cli_expect_error(registry_path: Path, *args: str) -> dict:
    returncode, payload = run_json_cli_result(
        *args,
        registry_path=registry_path,
        runtime_root=runtime_root_from_registry(registry_path),
        cwd=REPO_ROOT,
    )
    assert returncode != 0, payload
    return payload


def run_cli_markdown_expect_error(registry_path: Path, *args: str) -> str:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root_from_registry(registry_path)),
        *args,
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, result.stdout
    assert result.stdout.strip(), result.stderr
    return result.stdout


def agent_todos(state_file: Path) -> list[dict]:
    fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return fields["agent_todos"]["items"]


def find_todo(state_file: Path, todo_id: str) -> dict:
    for item in agent_todos(state_file):
        if item.get("todo_id") == todo_id:
            return item
    raise AssertionError(f"missing todo {todo_id}")


def run_index_records(registry_path: Path) -> list[dict]:
    runtime = runtime_root_from_registry(registry_path)
    assert runtime is not None, registry_path
    return read_run_index(runtime, GOAL_ID)


def monitor_poll_records(registry_path: Path) -> list[dict]:
    return [
        record
        for record in run_index_records(registry_path)
        if record.get("classification") == "quota_monitor_poll"
    ]


def assert_due_monitor_selected(registry_path: Path, *, due_count: int = 1) -> None:
    quota = run_cli(
        registry_path,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert quota["should_run"] is True, quota
    contract = quota.get("work_lane_contract")
    assert isinstance(contract, dict), quota
    assert contract.get("obligation") == "attempt_due_monitor", quota
    assert contract.get("selected_todo_id") == TODO_ID, quota
    assert contract.get("monitor_due_count") == due_count, quota


def assert_unchanged_writeback() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-unchanged-") as tmp:
        registry_path, state_file = write_fixture(
            Path(tmp), primary_no_change_count=4
        )
        assert_due_monitor_selected(registry_path)

        payload = run_cli(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--result-hash",
            "old",
            "--execute",
        )
        assert payload["ok"] is True, payload
        assert payload["appended"] is True, payload
        assert "turn_instance_id" not in payload, payload
        assert "replayed" not in payload, payload
        writeback = payload["todo_writeback"]
        assert writeback["todo_id"] == TODO_ID, payload
        assert writeback["consecutive_no_change"] == 5, payload
        item = find_todo(state_file, TODO_ID)
        assert item["result_hash"] == "old", item
        assert item["consecutive_no_change"] == "5", item
        assert item["last_checked_at"], item
        assert item["next_due_at"] != "2026-01-01T00:00:00+00:00", item
        assert item["material_change"] == "false", item
        assert payload["classification"] == "quota_monitor_poll", payload
        assert payload["delivery_outcome"] == "surface_only", payload
        assert "no quota spend" in payload["health_check"], payload
        summary = payload["decision_summary"]
        assert summary["before"] == payload["monitor_event"]["before"], payload
        assert_compact_monitor_poll_decisions(payload)
        records = monitor_poll_records(registry_path)
        assert [record["classification"] for record in records] == ["quota_monitor_poll"], records

        followup = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--available-capability",
            "external_evidence_poll",
            "--codex-app",
        )
        assert followup["decision"] == "autonomous_replan_required", followup
        assert followup["effective_action"] == "autonomous_replan_required", followup
        assert followup["should_run"] is True, followup
        assert followup["heartbeat_recommendation"]["recommended_mode"] == (
            "autonomous_replan_required"
        ), followup
        assert followup["scheduler_hint"]["cadence_class"] == "active_work", followup
        lane = followup["work_lane_contract"]
        assert lane["lane"] == "continuous_monitor", lane
        assert lane["obligation"] == "quiet_until_material_monitor_transition", lane
        frontier = followup["goal_frontier_projection"]
        assert frontier["monitor_only_lanes"]["present"] is True, frontier
        assert frontier["monitor_only_lanes"]["quiet_until_material_transition"] is True, frontier
        assert frontier["replan_required"] is True, frontier
        assert followup["autonomous_replan_obligation"]["required"] is True, followup
        assert followup["interaction_contract"]["mode"] == "autonomous_replan", followup
        assert followup["interaction_contract"]["agent_channel"]["must_attempt"] is True, followup
        assert followup["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False, followup
        assert followup["execution_obligation"]["kind"] == "autonomous_replan_required", followup
        assert followup["execution_obligation"]["must_attempt_work"] is True, followup


def assert_interleaved_monitor_stalls_replan_blocked_benchmark() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-real-state-replay-") as tmp:
        registry_path, _ = write_fixture(
            Path(tmp),
            include_other_monitor=True,
            include_blocked_advancement=True,
            primary_no_change_count=28,
            other_no_change_count=28,
        )
        quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        )
        assert quota["decision"] == "autonomous_replan_required", quota
        assert quota["effective_action"] == "autonomous_replan_required", quota
        assert quota["goal_frontier_projection"]["replan_required"] is True, quota
        obligation = quota["autonomous_replan_obligation"]
        assert obligation["triggers"][0]["kind"] == "monitor_no_change_streak", obligation
        assert obligation["triggers"][0]["run_count"] == 28, obligation
        assert obligation["triggers"][0]["threshold"] == 5, obligation
        assert obligation["triggers"][0]["monitor_target_id"], obligation


def assert_stalled_monitor_does_not_preempt_runnable_advancement() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-runnable-advancement-") as tmp:
        registry_path, _ = write_fixture(
            Path(tmp),
            include_advancement=True,
            primary_no_change_count=28,
        )
        quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
        )
        assert quota["decision"] == "run", quota
        assert quota["effective_action"] == "normal_run", quota
        assert quota["work_lane_contract"]["lane"] == "advancement_task", quota
        assert quota.get("autonomous_replan_obligation") is None, quota


def assert_material_transition_followup() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-material-") as tmp:
        registry_path, state_file = write_fixture(
            Path(tmp),
            monitor_task_repository="git:github.com/huangruiteng/loopx",
        )
        assert_due_monitor_selected(registry_path)

        missing_route = run_cli_expect_error(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--available-capability",
            "external_evidence_poll",
            "--target-key",
            TARGET_KEY,
            "--result-hash",
            "new",
            "--material-change",
            "--next-agent-todo",
            "Review the material monitor transition and prepare a public-safe packet.",
        )
        assert "requires explicit successor action semantics" in missing_route[
            "reason"
        ], missing_route
        assert "--next-action-kind" in missing_route["reason"], missing_route

        missing_repository = run_cli_expect_error(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--available-capability",
            "external_evidence_poll",
            "--target-key",
            TARGET_KEY,
            "--result-hash",
            "new",
            "--material-change",
            "--next-agent-todo",
            "Review the material monitor transition and prepare a public-safe packet.",
            "--next-action-kind",
            "review_material_transition",
        )
        assert "repository-bound monitor successors" in missing_repository[
            "reason"
        ], missing_repository
        assert "--next-task-repository" in missing_repository[
            "reason"
        ], missing_repository
        unchanged_monitor = find_todo(state_file, TODO_ID)
        assert unchanged_monitor["result_hash"] == "old", unchanged_monitor
        assert unchanged_monitor["material_change"] == "false", unchanged_monitor

        payload = run_cli(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--available-capability",
            "external_evidence_poll",
            "--target-key",
            TARGET_KEY,
            "--result-hash",
            "new",
            "--material-change",
            "--next-agent-todo",
            "Review the material monitor transition and prepare a public-safe packet.",
            "--next-action-kind",
            "review_material_transition",
            "--next-task-repository",
            "git:github.com/huangruiteng/loopx",
            "--next-required-capability",
            "network",
            "--next-required-capability",
            "external_evidence_poll",
            "--execute",
        )
        assert payload["ok"] is True, payload
        assert payload["material_change"] is True, payload
        writeback = payload["todo_writeback"]
        assert writeback["consecutive_no_change"] == 0, payload
        assert writeback["next_todos"], payload
        monitor = find_todo(state_file, TODO_ID)
        assert monitor["result_hash"] == "new", monitor
        assert monitor["consecutive_no_change"] == "0", monitor
        assert monitor["material_change"] == "true", monitor
        successors = [
            item
            for item in agent_todos(state_file)
            if item.get("unblocks_todo_id") == TODO_ID
        ]
        assert successors, agent_todos(state_file)
        assert successors[0]["task_class"] == "advancement_task", successors[0]
        assert successors[0]["action_kind"] == "review_material_transition", successors[0]
        assert successors[0]["task_repository"] == "git:github.com/huangruiteng/loopx", successors[0]
        assert successors[0]["continuation_policy"] == "independent_handoff", successors[0]
        assert successors[0]["required_capabilities"] == [
            "network",
            "external_evidence_poll",
        ], successors[0]
        expected_target_key = (
            f"monitor-successor:{TODO_ID}:"
            f"{hashlib.sha256(b'new').hexdigest()[:16]}"
        )
        assert successors[0]["target_key"] == expected_target_key, successors[0]
        successor_id = successors[0]["todo_id"]
        assert payload["successor_todo_ids"] == [successor_id], payload
        receipt = payload["successor_receipts"][0]
        assert receipt["todo_id"] == successor_id, receipt
        assert receipt["task_repository"] == "git:github.com/huangruiteng/loopx", receipt
        assert receipt["target_key"] == expected_target_key, receipt
        assert writeback["successor_receipts"] == payload["successor_receipts"], writeback
        records = monitor_poll_records(registry_path)
        assert [record["classification"] for record in records] == ["quota_monitor_poll"], records
        assert records[0]["delivery_outcome"] == "outcome_progress", records[0]
        assert records[0]["successor_todo_ids"] == [successor_id], records[0]
        event = payload["monitor_event"]
        assert event["todo_writeback"]["successor_receipts"] == payload[
            "successor_receipts"
        ], event
        assert event["monitor_mode"] == "due_monitor_material_transition", event
        assert event["reason_summary"] == "due monitor observation produced a material transition", event
        assert "due monitor material transition observed" in payload["health_check"], payload
        assert "unchanged" not in payload["health_check"], payload
        compact_after = payload["after"]
        assert compact_after["selected_todo"]["todo_id"] == successor_id, compact_after
        assert compact_after["selected_todo"]["unblocks_todo_id"] == TODO_ID, compact_after
        compact_contract = compact_after["interaction_contract"]
        assert compact_contract["agent_channel"]["must_attempt"] is True, compact_contract
        assert compact_contract["agent_channel"]["primary_action"], compact_contract
        assert compact_contract["agent_channel"]["delivery_allowed"] is False, compact_contract
        assert compact_contract["cli_channel"]["spend_after_validation"] is False, compact_contract
        runtime_root = runtime_root_from_registry(registry_path)
        rollout_events = load_rollout_events(rollout_event_log_path(runtime_root, GOAL_ID))
        monitor_events = [
            item for item in rollout_events if item.get("event_kind") == "quota_monitor_poll"
        ]
        assert monitor_events[-1]["details"]["successor_todo_ids"] == successor_id, monitor_events[-1]

        handoff = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--available-capability",
            "external_evidence_poll",
            "--codex-app",
            "--turn-instance-id",
            "material-successor-selection",
        )
        assert handoff["ok"] is True, handoff
        assert handoff["decision"] == "run", handoff
        assert handoff["effective_action"] == "normal_run", handoff
        assert handoff["agent_lane_next_action"]["todo_id"] == successor_id, handoff
        lane = handoff["work_lane_contract"]
        assert lane["lane"] == "advancement_task", lane
        assert lane["obligation"] == "advance_one_bounded_segment", lane
        assert "due_monitor_context" not in lane.get("reason_codes", []), lane
        monitor_after = find_todo(state_file, TODO_ID)
        assert monitor_after["next_due_at"] != "2026-01-01T00:00:00+00:00", monitor_after
        scheduler = handoff["scheduler_hint"]
        assert scheduler["action"] == "run_now", scheduler
        assert scheduler["cadence_class"] == "active_work", scheduler
        assert scheduler["codex_app"]["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler
        contract = handoff["interaction_contract"]
        assert contract["agent_channel"]["must_attempt"] is True, contract
        assert contract["agent_channel"]["delivery_allowed"] is False, contract
        assert contract["cli_channel"]["spend_after_validation"] is False, contract
        assert contract["cli_channel"]["selection_required"] is True, contract
        assert "settlement_identity" not in handoff["heartbeat_receipt"], handoff
        selection = contract["cli_channel"]["selection_command"]
        selected = run_cli(registry_path, *shlex.split(
            selection["command_args_template"].replace("{todo_id}", successor_id)
        ))
        assert selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == successor_id, selected
        assert selected["interaction_contract"]["agent_channel"]["delivery_allowed"] is True, selected
        assert not find_todo(state_file, successor_id).get("claimed_by")  # Selection is not a claim.


def assert_external_material_transition_receipt_correlation() -> None:
    before = {
        "goal_id": GOAL_ID,
        "should_run": True,
        "requires_user_action": False,
        "effective_action": "external_evidence_observe",
        "recommended_action": "Observe the external result handle.",
        "external_evidence_observation": {
            "required": True,
            "must_attempt_observation": True,
            "delivery_allowed": False,
            "if_handle_live_and_unchanged": "quiet_noop_no_spend",
        },
        "work_lane_contract": {
            "lane": "continuous_monitor",
            "must_attempt_work": True,
            "monitor_policy": "read_only_observation_then_no_spend_if_unchanged",
            "reason_codes": ["external_monitor_context"],
        },
        "heartbeat_recommendation": {
            "recommended_mode": "external_evidence_observe_or_blocker",
            "reason": "Observe the external result handle before deciding whether to continue.",
        },
        "agent_identity": {"agent_id": AGENT_ID},
    }

    record = build_quota_monitor_poll_event(
        before,
        todo_id=TODO_ID,
        target_key=TARGET_KEY,
        result_hash="external-result-ready",
        material_change=True,
    )

    event = record["monitor_event"]
    assert event["material_change"] is True, record
    assert event["monitor_mode"] == "external_monitor_material_transition", record
    assert event["reason_summary"] == (
        "external monitor observation produced a material transition"
    ), record
    assert "external monitor material transition observed" in record["health_check"], record
    assert "unchanged" not in record["health_check"], record
    assert "no material transition" not in record["health_check"], record
    assert record["delivery_outcome"] == "outcome_progress", record


def assert_due_monitor_poll_allowed_with_open_user_gate() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-user-gate-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp), include_user_gate=True)
        quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        )
        assert quota["requires_user_action"] is True, quota
        contract = quota.get("work_lane_contract")
        assert isinstance(contract, dict), quota
        assert contract.get("obligation") == "attempt_due_monitor", quota

        payload = run_cli(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--target-key",
            TARGET_KEY,
            "--result-hash",
            "old",
            "--execute",
        )
        assert payload["ok"] is True, payload
        assert payload["agent_id"] == AGENT_ID, payload
        assert payload["todo_id"] == TODO_ID, payload
        assert payload["target_key"] == TARGET_KEY, payload
        user_channel = payload["after"]["interaction_contract"]["user_channel"]
        assert user_channel["action_required"] is True, user_channel
        assert user_channel["notify"] == "NOTIFY", user_channel
        assert user_channel["actions"], user_channel
        assert "publication gate" in user_channel["actions"][0], user_channel
        item = find_todo(state_file, TODO_ID)
        assert item["consecutive_no_change"] == "2", item
        assert item["next_due_at"] != "2026-01-01T00:00:00+00:00", item


def assert_target_key_cannot_hijack_selected_due_monitor() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-target-key-") as tmp:
        registry_path, state_file = write_fixture(
            Path(tmp),
            selected_target_key=None,
            include_other_monitor=True,
        )
        assert_due_monitor_selected(registry_path, due_count=2)

        payload = run_cli_expect_error(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--target-key",
            OTHER_TARGET_KEY,
            "--result-hash",
            "new",
            "--execute",
        )
        assert payload["ok"] is False, payload
        assert "monitor-poll requires" in payload["reason"], payload
        assert_compact_monitor_poll_decisions(payload)
        markdown = run_cli_markdown_expect_error(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--target-key",
            OTHER_TARGET_KEY,
            "--result-hash",
            "new",
            "--material-change",
            "--execute",
        )
        assert "- ok: `False`" in markdown, markdown
        assert "- mode: `monitor-poll`" in markdown, markdown
        assert "- todo_id: `todo_monitorpoll111`" in markdown, markdown
        assert f"- target_key: `{OTHER_TARGET_KEY}`" in markdown, markdown
        assert "- material_change: `True`" in markdown, markdown
        assert "- appended: `False`" in markdown, markdown
        assert "- registry_mutated: `False`" in markdown, markdown
        assert "- reason: monitor-poll requires" in markdown, markdown
        assert "`None`" not in markdown, markdown
        selected = find_todo(state_file, TODO_ID)
        other = find_todo(state_file, "todo_monitorpoll111")
        assert selected["result_hash"] == "old", selected
        assert other["result_hash"] == "old", other
        assert monitor_poll_records(registry_path) == [], run_index_records(registry_path)


def assert_compacted_auxiliary_due_monitor_can_write_back() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-compacted-due-") as tmp:
        registry_path, state_file = write_fixture(
            Path(tmp),
            include_other_monitor=True,
            include_advancement=True,
            primary_monitor_priority="P1",
        )
        quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
        )
        assert quota["work_lane_contract"]["obligation"] == "advance_one_bounded_segment", quota
        assert quota["agent_todo_summary"]["monitor_due_count"] == 2, quota
        assert len(quota["agent_todo_summary"]["monitor_due_items"]) == 1, quota
        projected_due_id = quota["agent_todo_summary"]["monitor_due_items"][0]["todo_id"]
        assert projected_due_id != "todo_monitorpoll111", quota

        payload = run_cli(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--todo-id",
            "todo_monitorpoll111",
            "--target-key",
            OTHER_TARGET_KEY,
            "--result-hash",
            "old",
            "--include-detail",
            "decisions",
            "--execute",
        )
        assert payload["ok"] is True, payload
        assert payload["todo_writeback"]["todo_id"] == "todo_monitorpoll111", payload
        assert "network" in payload["before"]["capability_gate"]["available"], payload
        assert "network" in payload["after"]["capability_gate"]["available"], payload
        other = find_todo(state_file, "todo_monitorpoll111")
        assert other["consecutive_no_change"] == "2", other
        assert other["next_due_at"] != "2026-01-01T00:00:00+00:00", other


def assert_capability_gated_monitor_poll_requires_declaration_parity() -> None:
    capabilities = ("network", "external_evidence_poll")
    capability_args = tuple(
        arg
        for capability in capabilities
        for arg in ("--available-capability", capability)
    )
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-capability-parity-") as tmp:
        registry_path, state_file = write_fixture(
            Path(tmp),
            monitor_required_capabilities=capabilities,
        )

        should_run = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        )
        assert should_run["effective_action"] == "capability_bridge_repair", should_run
        assert should_run["capability_gate"]["action"] == "repair_bridge", should_run
        assert should_run["capability_gate"]["missing"] == list(capabilities), should_run

        failure = run_cli_expect_error(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--target-key",
            TARGET_KEY,
            "--result-hash",
            "old",
            "--include-detail",
            "decisions",
            "--execute",
        )
        assert "monitor-poll recomputes should-run" in failure["reason"], failure
        retry = failure["capability_retry"]
        assert retry["missing"] == list(capabilities), retry
        assert retry["cli_args"] == list(capability_args), retry

        option_failure = run_cli_expect_error(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--material-change",
        )
        assert "capability_retry" not in option_failure, option_failure

        success = run_cli(
            registry_path,
            "quota",
            "monitor-poll",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            *capability_args,
            "--todo-id",
            TODO_ID,
            "--target-key",
            TARGET_KEY,
            "--result-hash",
            "old",
            "--include-detail",
            "decisions",
        )
        assert success["ok"] is True, success
        assert success["before"]["work_lane_contract"]["obligation"] == "attempt_due_monitor", success
        assert success["todo_writeback"]["todo_id"] == TODO_ID, success
        assert find_todo(state_file, TODO_ID)["consecutive_no_change"] == "1"


def assert_cli_help_names_capability_sensitive_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "quota", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    option_help = result.stdout[result.stdout.index("--available-capability") :]
    for command in (
        "quota should-run",
        "quota monitor-poll",
        "quota scheduler-ack",
        "quota scheduler-ack-current",
        "quota spend-slot",
    ):
        assert command in option_help, (command, option_help)
    assert "--turn-instance-id" in result.stdout, result.stdout


def assert_should_run_turn_receipt_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-heartbeat-receipt-") as tmp:
        registry_path, _ = write_fixture(Path(tmp), include_advancement=True)
        turn_id = "2026-07-23T02:30:00Z"
        ordinary = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
        )
        assert ordinary["decision"] == "run", ordinary
        assert "heartbeat_receipt" not in ordinary, ordinary
        assert "heartbeat_stall_writeback" not in ordinary, ordinary
        assert "appended" not in ordinary["rollout_event"], ordinary
        empty_turn = run_cli_expect_error(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--turn-instance-id",
            "",
        )
        assert empty_turn["status"] == "quota_validation_failed", empty_turn
        assert empty_turn["state"] == "blocked_validation", empty_turn
        assert empty_turn["error_code"] == "QUOTA_VALIDATION_FAILED", empty_turn
        assert "turn_instance_id must be 1-128" in empty_turn["reason"], empty_turn
        command = (
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "network",
            "--turn-instance-id",
            turn_id,
        )
        first = run_cli(registry_path, *command)
        replay = run_cli(registry_path, *command)
        assert first["decision"] == "run", first
        assert first["heartbeat_receipt"]["status"] == "committed", first
        assert first["heartbeat_receipt"]["stall_observation"] == "not_applicable", first
        assert replay["heartbeat_receipt"]["status"] == "replayed", replay
        assert replay["heartbeat_receipt"]["event_id"] == first["heartbeat_receipt"]["event_id"], (
            first,
            replay,
        )
        runtime_root = runtime_root_from_registry(registry_path)
        events = load_rollout_events(rollout_event_log_path(runtime_root, GOAL_ID))
        receipts = [
            event
            for event in events
            if event.get("event_kind") == "quota_should_run"
            and event.get("agent_id") == AGENT_ID
            and event.get("run_id") == turn_id
        ]
        assert len(receipts) == 1, receipts
        assert monitor_poll_records(registry_path) == [], monitor_poll_records(registry_path)


def assert_turn_scoped_monitor_poll_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-heartbeat-stall-receipt-") as tmp:
        registry_path, _ = write_fixture(Path(tmp))
        runtime_root = runtime_root_from_registry(registry_path)
        status_payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            limit=AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK,
        )
        turn_id = "2026-07-23T02:33:00Z"
        kwargs = {
            "goal_id": GOAL_ID,
            "registry_path": registry_path,
            "execute": True,
            "source": "heartbeat",
            "agent_id": AGENT_ID,
            "todo_id": TODO_ID,
            "result_hash": "old",
            "turn_instance_id": turn_id,
        }
        first = record_quota_monitor_poll(status_payload, **kwargs)
        replay = record_quota_monitor_poll(status_payload, **kwargs)
        assert first["appended"] is True and first["replayed"] is False, first
        assert replay["appended"] is False and replay["replayed"] is True, replay
        assert replay["turn_instance_id"] == turn_id, replay
        records = monitor_poll_records(registry_path)
        assert len(records) == 1, records
        assert records[0]["turn_instance_id"] == turn_id, records


def cli_monitor_poll_scheduler_hints(registry_path: Path, *scheduler_args: str) -> tuple[dict, dict]:
    payload = run_cli(
        registry_path,
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--include-detail",
        "decisions",
        *scheduler_args,
        "--todo-id",
        TODO_ID,
        "--result-hash",
        "old",
    )
    return payload["before"]["scheduler_hint"], payload["after"]["scheduler_hint"]


def assert_cli_monitor_poll_preserves_codex_app_scheduler_context() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-codex-app-") as tmp:
        hints = cli_monitor_poll_scheduler_hints(write_fixture(Path(tmp))[0], "--codex-app")
        for scheduler_hint in hints:
            assert scheduler_hint["codex_app"]["applicability"] == "applicable", scheduler_hint
            assert scheduler_hint["action"] != "repair_scheduler_execution_context", scheduler_hint


def assert_cli_monitor_poll_preserves_outer_controller_scheduler_context() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-outer-controller-") as tmp:
        hints = cli_monitor_poll_scheduler_hints(
            write_fixture(Path(tmp))[0], "--runtime-profile", "outer_controller"
        )
        for scheduler_hint in hints:
            execution_context = scheduler_hint["execution_context"]
            execution_phase = scheduler_hint["execution_phase"]
            assert scheduler_hint["action"] != "repair_scheduler_execution_context", scheduler_hint
            assert scheduler_hint["codex_app"]["applicability"] == "not_applicable", scheduler_hint
            assert "stateful_backoff" not in scheduler_hint["codex_app"], scheduler_hint
            assert (
                execution_context["host_surface"],
                execution_context["scheduler_owner"],
                execution_context["execution_mode"],
                execution_context["valid"],
            ) == ("generic_cli", "outer_controller", "isolated_headless", True), scheduler_hint
            assert execution_phase["disposition"] == "outer_controller_owned", scheduler_hint
            assert execution_phase["completed"] is True, scheduler_hint
            assert execution_phase["apply_needed"] is False, scheduler_hint
            assert execution_phase["ack_needed"] is False, scheduler_hint


def assert_cli_monitor_poll_invalid_scheduler_context_fails_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-invalid-context-") as tmp:
        hints = cli_monitor_poll_scheduler_hints(
            write_fixture(Path(tmp))[0],
            "--host-surface",
            "generic_cli",
            "--scheduler-owner",
            "outer_controller",
            "--execution-mode",
            "interactive",
        )
        for scheduler_hint in hints:
            execution_context = scheduler_hint["execution_context"]
            execution_phase = scheduler_hint["execution_phase"]
            assert scheduler_hint["action"] == "repair_scheduler_execution_context", scheduler_hint
            assert scheduler_hint["codex_app"]["applicability"] == "blocked_invalid_context", scheduler_hint
            assert "stateful_backoff" not in scheduler_hint["codex_app"], scheduler_hint
            assert execution_context["valid"] is False, scheduler_hint
            assert execution_context["errors"] == [
                "outer_controller requires execution_mode=isolated_headless"
            ], scheduler_hint
            assert execution_phase["disposition"] == "contract_error", scheduler_hint
            assert execution_phase["completed"] is False, scheduler_hint
            assert execution_phase["apply_needed"] is False, scheduler_hint
            assert execution_phase["ack_needed"] is False, scheduler_hint


def assert_writeback_helper_preview_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-poll-helper-parity-") as tmp:
        registry_path, _state_file = write_fixture(Path(tmp))
        resolved = resolve_monitor_todo_item(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
        )
        assert resolved["todo_id"] == TODO_ID, resolved
        assert resolved["target_key"] == TARGET_KEY, resolved
        generated_at = "2026-01-01T00:05:00+00:00"
        before = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
        source = _state_file.read_bytes()
        preview = write_monitor_poll_todo_state(
            registry_path=registry_path,
            runtime_root=runtime_root_from_registry(registry_path),
            goal_id=GOAL_ID,
            generated_at=generated_at,
            execute=False,
            todo_id=TODO_ID,
            result_hash="old",
            agent_id=AGENT_ID,
        )
        assert preview["schema_version"] == "monitor_poll_todo_writeback_v0", preview
        assert preview["dry_run"] is True, preview
        assert preview["goal_id"] == GOAL_ID, preview
        assert preview["todo_id"] == TODO_ID, preview
        assert preview["target_key"] == TARGET_KEY, preview
        assert preview["result_hash"] == "old", preview
        assert preview["material_change"] is False, preview
        assert preview["consecutive_no_change"] == 2, preview
        assert preview["last_checked_at"] == generated_at, preview
        assert preview["next_due_at"] != "2026-01-01T00:00:00+00:00", preview
        assert preview["cadence"] == "15m", preview
        for actor in (None, "codex-unregistered"):
            try:
                write_monitor_poll_todo_state(
                    registry_path=registry_path,
                    runtime_root=runtime_root_from_registry(registry_path),
                    goal_id=GOAL_ID, generated_at=generated_at,
                    execute=False, todo_id=TODO_ID, result_hash="old", agent_id=actor,
                )
            except ValueError as exc:
                assert "registered actor" in str(exc), str(exc)
            else:
                raise AssertionError("Monitor preview admitted an undeclared actor")
        after = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
        assert after["todo"] == before["todo"], after
        assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"], after
        assert _state_file.read_bytes() == source


def assert_cli_monitor_poll_uses_should_run_lookback() -> None:
    seen: dict[str, object] = {}

    def fake_collect_status(**kwargs: object) -> dict:
        seen["limit"] = kwargs.get("limit")
        return {"ok": True, "summary": {}}

    def fake_record_quota_monitor_poll(status_payload: dict, **kwargs: object) -> dict:
        seen["status_payload"] = status_payload
        seen["record_kwargs"] = kwargs
        return {"ok": True, "mode": "monitor-poll", "dry_run": True, "appended": False}

    def fake_print_payload(payload: dict, _fmt: str, _renderer: object) -> None:
        seen["payload"] = payload

    args = argparse.Namespace(
        quota_command="monitor-poll",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=["network"],
        include_scheduler_detail=False,
        slots=1,
        source="heartbeat",
        void_generated_at=None,
        reason_summary=None,
        todo_id=TODO_ID,
        target_key=None,
        result_hash="old",
        material_change=False,
        cadence=None,
        next_due_at=None,
        next_agent_todo=None,
        next_action_kind=None,
        next_task_repository=None,
        next_required_capabilities=None,
        next_continuation_policy=None,
        next_target_key=None,
        next_user_todo=None,
        next_user_task_class=None,
        next_claimed_by=None,
        codex_app=True,
        runtime_profile=None,
        host_surface=None,
        scheduler_owner=None,
        execution_mode=None,
        surface="codex_app",
        state_key="scheduler_hint.codex_app.stateful_backoff",
        applied_rrule=None,
        reset_token=None,
        identity_signature=None,
        dry_run=True,
        execute=False,
        limit=5,
        format="json",
    )
    original_collect_status = quota_command.collect_status
    original_record_monitor_poll = quota_command.record_quota_monitor_poll
    try:
        quota_command.collect_status = fake_collect_status
        quota_command.record_quota_monitor_poll = fake_record_quota_monitor_poll
        rc = quota_command.handle_quota_command(
            args,
            registry_path=REPO_ROOT / ".loopx" / "registry.json",
            runtime_root_arg=None,
            print_payload=fake_print_payload,
            append_cli_rollout_event=lambda **_: {},
        )
    finally:
        quota_command.collect_status = original_collect_status
        quota_command.record_quota_monitor_poll = original_record_monitor_poll

    assert rc == 0, rc
    assert seen["limit"] == AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK, seen
    assert seen["record_kwargs"]["available_capabilities"] == ["network"], seen
    scheduler_context = seen["record_kwargs"]["scheduler_execution_context"]
    assert scheduler_context.ok is True, scheduler_context
    assert scheduler_context.context.host_surface.value == "codex_app", scheduler_context
    assert scheduler_context.context.scheduler_owner.value == "host_automation", scheduler_context
    assert scheduler_context.context.execution_mode.value == "hosted_automation", scheduler_context
    assert seen["payload"] == {"ok": True, "mode": "monitor-poll", "dry_run": True, "appended": False}, seen


def main() -> int:
    assert_cli_help_names_capability_sensitive_commands()
    assert_should_run_turn_receipt_is_idempotent()
    assert_turn_scoped_monitor_poll_is_idempotent()
    assert_cli_monitor_poll_uses_should_run_lookback()
    assert_cli_monitor_poll_preserves_codex_app_scheduler_context()
    assert_cli_monitor_poll_preserves_outer_controller_scheduler_context()
    assert_cli_monitor_poll_invalid_scheduler_context_fails_closed()
    assert_writeback_helper_preview_contract()
    assert_unchanged_writeback()
    assert_interleaved_monitor_stalls_replan_blocked_benchmark()
    assert_stalled_monitor_does_not_preempt_runnable_advancement()
    assert_material_transition_followup()
    assert_external_material_transition_receipt_correlation()
    assert_due_monitor_poll_allowed_with_open_user_gate()
    assert_target_key_cannot_hijack_selected_due_monitor()
    assert_compacted_auxiliary_due_monitor_can_write_back()
    assert_capability_gated_monitor_poll_requires_declaration_parity()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
