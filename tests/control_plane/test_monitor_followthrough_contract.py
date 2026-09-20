from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.scheduler.monitor_poll_writeback import (
    resolve_monitor_todo_item,
    write_monitor_poll_todo_state,
)
from loopx.control_plane.scheduler.monitor_todo import (
    monitor_todo_is_actionable_open,
)
from loopx.control_plane.testing.canary_harness import (
    run_json_cli,
    run_json_cli_result,
    write_fixture_registry,
)
from loopx.control_plane.todos.contract import resolve_next_user_task_class
from loopx.control_plane.work_items.work_lane import (
    preserve_heartbeat_receipt_bound_work_lane,
)
from loopx.quota import build_quota_should_run, record_quota_monitor_poll
from loopx.status import collect_status
from loopx.todos import add_goal_todo, list_goal_todos, update_goal_todo


GOAL_ID = "monitor-followthrough-fixture"
AGENT_ID = "codex-quality-qualification"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL_ID,
        domain="loopx-platform",
        adapter_kind="harness_self_improvement",
        registered_agents=[AGENT_ID, "codex-main-control"],
        quota_allowed_slots=None,
    )
    return registry, runtime, state


def _add_monitor(
    registry: Path,
    *,
    text: str,
    target_key: str,
    next_due_at: str = "2099-01-01T00:00:00+00:00",
) -> dict:
    return add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text=text,
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        monitor_metadata={
            "target_key": target_key,
            "cadence": "1h",
            "next_due_at": next_due_at,
            "watch_only": "true",
        },
    )


def test_exact_todo_id_precedes_ambiguous_target_key(tmp_path: Path) -> None:
    registry, _runtime, _state = _write_fixture(tmp_path)
    first = _add_monitor(registry, text="Poll the first target.", target_key="shared")
    second = _add_monitor(registry, text="Poll the second target.", target_key="shared")

    resolved = resolve_monitor_todo_item(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=second["todo_id"],
        target_key="shared",
    )

    assert resolved["todo_id"] == second["todo_id"]
    with pytest.raises(ValueError, match="matched multiple todos"):
        resolve_monitor_todo_item(
            registry_path=registry,
            goal_id=GOAL_ID,
            target_key="shared",
        )
    assert first["todo_id"] != second["todo_id"]


def test_todo_update_preserves_omitted_monitor_schedule_fields(tmp_path: Path) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
    )

    updated = run_json_cli(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        monitor["todo_id"],
        "--role",
        "agent",
        "--agent-id",
        AGENT_ID,
        "--note",
        "No material change.",
        "--watch-only",
        registry_path=registry,
        runtime_root=runtime,
    )

    assert updated["target_key"] == "public-release:42"
    assert updated["cadence"] == "1h"
    assert updated["next_due_at"] == "2099-01-01T00:00:00+00:00"
    assert updated["watch_only"] == "true"


def test_monitor_resume_when_is_a_supported_bounded_policy(
    tmp_path: Path,
) -> None:
    registry, _runtime, _state = _write_fixture(tmp_path)

    bounded = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Poll a merge target.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        resume_when="pr_merged:owner/repo#42",
        monitor_metadata={"target_key": "public-pr:42", "cadence": "1h"},
    )
    assert bounded["resume_when"] == "pr_merged:owner/repo#42"
    assert bounded["next_due_at"]
    persisted = resolve_monitor_todo_item(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=bounded["todo_id"],
    )
    assert persisted["next_due_at"] == bounded["next_due_at"]

    monitor = _add_monitor(
        registry,
        text="Poll a legacy merge target.",
        target_key="legacy-pr:42",
    )
    updated = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=monitor["todo_id"],
        agent_id=AGENT_ID,
        resume_when="pr_merged:owner/repo#42",
    )
    assert updated["resume_when"] == "pr_merged:owner/repo#42"

    assert monitor_todo_is_actionable_open(
        {
            "status": "open",
            "task_class": "continuous_monitor",
            "resume_when": "pr_merged:owner/repo#42",
            "resume_ready": False,
        }
    ) is False


def test_todo_add_cli_materializes_cadence_only_monitor_due_time(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)

    added = run_json_cli(
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--text",
        "Poll a public dependency on cadence.",
        "--task-class",
        "continuous_monitor",
        "--action-kind",
        "monitor",
        "--claimed-by",
        AGENT_ID,
        "--target-key",
        "public-dependency:cadence-only",
        "--cadence",
        "30m",
        "--watch-only",
        registry_path=registry,
        runtime_root=runtime,
    )

    assert added["cadence"] == "30m"
    assert added["next_due_at"]
    persisted = resolve_monitor_todo_item(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=added["todo_id"],
    )
    assert persisted["next_due_at"] == added["next_due_at"]
    assert persisted["target_key"] == "public-dependency:cadence-only"


def test_runtime_writeback_can_read_legacy_unbounded_monitor(tmp_path: Path) -> None:
    registry, _runtime, state = _write_fixture(tmp_path)
    state.write_text(
        state.read_text(encoding="utf-8")
        + "\n- [ ] [P2] Legacy public monitor.\n"
        + "  <!-- loopx:todo todo_id=todo_legacy_monitor status=open "
        + "task_class=continuous_monitor claimed_by=codex-quality-qualification "
        + "target_key=legacy-public cadence=1h -->\n",
        encoding="utf-8",
    )

    result = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        generated_at="2026-08-11T00:00:00Z",
        execute=False,
        todo_id="todo_legacy_monitor",
        result_hash="unchanged",
        agent_id=AGENT_ID,
    )

    assert result is not None
    assert result["todo_update"]["changed"] is True


def test_material_poll_reloads_status_and_projects_declared_successor(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )

    result = run_json_cli(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--available-capability",
        "network",
        "--todo-id",
        monitor["todo_id"],
        "--target-key",
        "public-release:42",
        "--result-hash",
        "merged-42",
        "--material-change",
        "--next-agent-todo",
        "Validate the exact merged release head.",
        "--next-action-kind",
        "validate_release_head",
        "--next-task-repository",
        "git:github.com/huangruiteng/loopx",
        "--next-required-capability",
        "network",
        "--next-continuation-policy",
        "same_agent_non_delivery",
        "--next-target-key",
        "release-head:huangruiteng/loopx#42@merged-42",
        "--next-claimed-by",
        AGENT_ID,
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )

    successor = result["todo_writeback"]["next_todos"][0]
    assert successor["status"] == "open"
    assert successor["action_kind"] == "validate_release_head"
    assert successor["task_repository"] == "git:github.com/huangruiteng/loopx"
    assert successor["continuation_policy"] == "same_agent_non_delivery"
    assert successor["required_capabilities"] == ["network"]
    assert successor["target_key"] == "release-head:huangruiteng/loopx#42@merged-42"
    assert result["successor_todo_ids"] == [successor["todo_id"]]
    assert result["after"]["selected_todo"]["todo_id"] == successor["todo_id"]
    authoring = result["authoring_contract"]
    assert authoring["schema_version"] == "monitor_advancement_authoring_v0"
    assert authoring["monitor"] == {
        "task_class": "continuous_monitor",
        "execution": "observe_only",
    }
    assert authoring["waiting_todo"]["successor_todo_ids"] == [
        successor["todo_id"]
    ]
    assert authoring["waiting_todo"]["status"] == "open"
    # This contract owns material writeback and successor selection. Whether
    # the current CLI turn can spend quota is evaluated by should-run tests.
    assert "Validate the exact merged release head." in state.read_text(encoding="utf-8")


def test_unchanged_due_poll_reloads_status_before_followthrough_decision(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    status_payload = collect_status(
        registry_path=registry,
        runtime_root_override=str(runtime),
        scan_roots=[Path(__file__).resolve()],
        limit=20,
    )
    reloaded_status: dict | None = None

    def reload_status() -> dict:
        nonlocal reloaded_status
        reloaded_status = collect_status(
            registry_path=registry,
            runtime_root_override=str(runtime),
            scan_roots=[Path(__file__).resolve()],
            limit=20,
        )
        return reloaded_status

    result = record_quota_monitor_poll(
        status_payload,
        goal_id=GOAL_ID,
        registry_path=registry,
        execute=True,
        source="heartbeat",
        agent_id=AGENT_ID,
        todo_id=monitor["todo_id"],
        target_key="public-release:42",
        result_hash="unchanged-42",
        status_reloader=reload_status,
    )

    assert result["todo_writeback"]["material_change"] is False
    assert reloaded_status is not None
    fresh = build_quota_should_run(
        reloaded_status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert result["after"]["should_run"] == fresh["should_run"]
    assert result["after"]["effective_action"] == fresh["effective_action"]
    assert result["after"].get("selected_todo") == fresh.get("selected_todo")
    assert result["decision_summary"]["after"]["should_run"] == fresh["should_run"]
    assert (
        result["decision_summary"]["after"]["effective_action"]
        == fresh["effective_action"]
    )


@pytest.mark.parametrize(
    ("material_change", "result_hash"),
    [(False, "unchanged-42"), (True, "material-42")],
    ids=["quiet", "material"],
)
def test_turn_scoped_monitor_poll_preserves_receipt_todo_after_capability_reentry(
    tmp_path: Path,
    material_change: bool,
    result_hash: str,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    admitted = _add_monitor(
        registry,
        text="[P1] Poll the admitted public issue target.",
        target_key="public-issue:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    newly_runnable = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="[P0] Poll the higher-priority public review queue.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        required_capabilities=["network", "external_evidence_poll"],
        monitor_metadata={
            "target_key": "public-review-queue",
            "cadence": "1h",
            "next_due_at": "2000-01-01T00:00:00+00:00",
            "watch_only": "true",
        },
    )
    turn_id = "2026-08-18T20:33:30.889Z"

    guard = run_json_cli(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert guard["selected_todo"]["todo_id"] == admitted["todo_id"]
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == admitted[
        "todo_id"
    ]
    guard_replay = run_json_cli(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert guard_replay["agent_lane_next_action"][
        "receipt_bound_monitor_phase"
    ] == "poll_due"

    command = (
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
        "--todo-id",
        admitted["todo_id"],
        "--target-key",
        "public-issue:42",
        "--result-hash",
        result_hash,
        *(("--material-change",) if material_change else ()),
        "--execute",
    )
    result = run_json_cli(
        *command,
        registry_path=registry,
        runtime_root=runtime,
    )

    assert result["before"]["selected_todo"]["todo_id"] == admitted["todo_id"]
    assert result["before"]["selected_todo"]["selection_binding"] == (
        "heartbeat_receipt"
    )
    assert result["todo_writeback"]["todo_id"] == admitted["todo_id"]
    assert result["material_change"] is material_change
    assert result["turn_instance_id"] == turn_id
    assert result["replayed"] is False

    replay = run_json_cli(
        *command,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert replay["replayed"] is True
    assert replay["appended"] is False

    different_replay_command = list(command)
    different_replay_command[different_replay_command.index(result_hash)] = (
        "different-observation"
    )
    returncode, replay_conflict = run_json_cli_result(
        *different_replay_command,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert returncode == 1
    assert replay_conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    assert replay_conflict["conflict_fields"] == ["result_hash"]

    wrong_todo_command = list(command)
    wrong_todo_command[wrong_todo_command.index(admitted["todo_id"])] = (
        newly_runnable["todo_id"]
    )
    returncode, conflict = run_json_cli_result(
        *wrong_todo_command,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert returncode == 1
    assert conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    assert admitted["todo_id"] in conflict["reason"]


def test_same_turn_material_monitor_poll_is_no_spend_closeout_before_successor(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    admitted = _add_monitor(
        registry,
        text="[P1] Poll the admitted public release target.",
        target_key="public-release:receipt-settlement",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    turn_id = "2026-08-21T09:48:02.405Z"
    guard_args = (
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )

    guard = run_json_cli(
        *guard_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert guard["selected_todo"]["todo_id"] == admitted["todo_id"]

    poll = run_json_cli(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
        "--todo-id",
        admitted["todo_id"],
        "--target-key",
        "public-release:receipt-settlement",
        "--result-hash",
        "merged-receipt-settlement",
        "--material-change",
        "--next-agent-todo",
        "Validate the exact merged release head.",
        "--next-action-kind",
        "validate_release_head",
        "--next-task-repository",
        "git:github.com/huangruiteng/loopx",
        "--next-required-capability",
        "network",
        "--next-continuation-policy",
        "same_agent_non_delivery",
        "--next-target-key",
        "release-head:receipt-settlement",
        "--next-claimed-by",
        AGENT_ID,
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )
    successor_id = poll["successor_todo_ids"][0]
    assert poll["after"]["selected_todo"]["todo_id"] == admitted["todo_id"]

    # The production CLI must not confuse an observation row with a committed
    # closeout. Keep the exact guard and Todo fixed while corrupting only the
    # commit evidence in this disposable runtime.
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    committed_index = index.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in committed_index.splitlines()]
    for metadata in (None, {}, {"effect_id": "quota-monitor-poll:wrong-turn"}):
        mutated = [
            {**row, "quota_monitor_poll_commit": metadata}
            if row["classification"] == "quota_monitor_poll" else row
            for row in rows
        ]
        index.write_text(
            "".join(json.dumps(row) + "\n" for row in mutated), encoding="utf-8"
        )
        incomplete = run_json_cli(*guard_args, registry_path=registry, runtime_root=runtime)
        assert incomplete["agent_lane_next_action"]["receipt_bound_monitor_phase"] == "poll_due"
        assert incomplete["execution_obligation"]["must_attempt_work"] is True
    index.write_text(committed_index, encoding="utf-8")

    replay = run_json_cli(
        *guard_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert replay["selected_todo"]["todo_id"] == admitted["todo_id"]
    assert replay["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert replay["agent_lane_next_action"]["receipt_bound_monitor_phase"] == (
        "settled"
    )
    assert replay["work_lane_contract"]["obligation"] == (
        "finish_settled_receipt_bound_monitor_turn"
    )
    assert replay["work_lane_contract"]["selected_todo_id"] == admitted["todo_id"]
    assert replay["work_lane_contract"]["deferred_work_lane"]["lane"] == (
        "advancement_task"
    )
    assert "without another" in replay["work_lane_contract"]["action"]
    assert replay["should_run"] is False
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["execution_obligation"]["must_attempt_work"] is False
    assert replay["heartbeat_recommendation"]["agent_must_attempt"] is False
    assert replay["interaction_contract"]["mode"] == "heartbeat_settled_skip"
    assert replay["interaction_contract"]["user_channel"]["notify"] == "DONT_NOTIFY"
    assert replay["automation_liveness"]["automation_action"] == "keep_active_quiet"
    assert replay["heartbeat_receipt"]["status"] == "replayed"

    classifications = [
        row["classification"]
        for row in map(json.loads, index.read_text(encoding="utf-8").splitlines())
    ]
    assert classifications.count("quota_monitor_poll") == 1
    assert "state_refreshed" not in classifications
    assert "quota_slot_spent" not in classifications

    next_turn_args = list(guard_args)
    next_turn_args[next_turn_args.index(turn_id)] = "2026-08-21T09:51:02.405Z"
    next_turn = run_json_cli(
        *next_turn_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert next_turn["selected_todo"]["todo_id"] == successor_id


def test_receipt_bound_monitor_without_poll_stays_poll_due_after_reschedule(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="[P1] Poll a public release target before settlement.",
        target_key="public-release:missing-poll",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    turn_id = "2026-08-21T10:47:02.405Z"
    guard_args = (
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    guard = run_json_cli(
        *guard_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]

    update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=monitor["todo_id"],
        agent_id=AGENT_ID,
        monitor_metadata={"next_due_at": "2099-01-01T00:00:00+00:00"},
    )

    replay = run_json_cli(
        *guard_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert replay["selected_todo"]["todo_id"] == monitor["todo_id"]
    assert replay["agent_lane_next_action"]["receipt_bound_monitor_phase"] == (
        "poll_due"
    )
    assert replay["work_lane_contract"]["obligation"] == "attempt_due_monitor"
    assert replay["should_run"] is True


def test_receipt_bound_monitor_replay_requires_an_explicit_phase() -> None:
    with pytest.raises(
        ValueError,
        match="receipt-bound monitor selection requires an explicit monitor phase",
    ):
        preserve_heartbeat_receipt_bound_work_lane(
            {
                "schema_version": "work_lane_contract_v1",
                "lane": "continuous_monitor",
                "must_attempt_work": True,
            },
            selected_todo={
                "todo_id": "todo_monitor_without_phase",
                "task_class": "continuous_monitor",
                "selection_binding": "heartbeat_receipt",
            },
        )


def test_same_turn_unchanged_monitor_poll_is_already_settled(tmp_path: Path) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="[P1] Poll an unchanged public release target.",
        target_key="public-release:unchanged-settlement",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    turn_id = "2026-08-21T10:48:02.405Z"
    guard_args = (
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    guard = run_json_cli(
        *guard_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    poll = run_json_cli(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn_id,
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
        "--todo-id",
        monitor["todo_id"],
        "--target-key",
        "public-release:unchanged-settlement",
        "--result-hash",
        "unchanged-settlement",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )
    assert poll["material_change"] is False

    replay = run_json_cli(
        *guard_args,
        registry_path=registry,
        runtime_root=runtime,
    )
    assert replay["selected_todo"]["todo_id"] == monitor["todo_id"]
    assert replay["agent_lane_next_action"]["receipt_bound_monitor_phase"] == (
        "settled"
    )
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["execution_obligation"]["must_attempt_work"] is False
    assert replay["heartbeat_recommendation"]["agent_must_attempt"] is False


def test_turn_scoped_monitor_poll_requires_committed_receipt(tmp_path: Path) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )

    returncode, result = run_json_cli_result(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        "2026-08-18T20:33:31.000Z",
        "--todo-id",
        monitor["todo_id"],
        "--result-hash",
        "unchanged-42",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )

    assert returncode == 1
    assert result["error_code"] == "heartbeat_receipt_identity_conflict"
    assert "requires a committed heartbeat receipt" in result["reason"]


def test_material_poll_user_action_does_not_block_existing_advancement(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    advancement = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Continue the independent quality task.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )

    result = run_json_cli(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--todo-id",
        monitor["todo_id"],
        "--result-hash",
        "review-ready-42",
        "--material-change",
        "--next-user-todo",
        "Review the validated release when convenient.",
        "--next-user-task-class",
        "user_action",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )

    reminder = result["todo_writeback"]["next_todos"][0]
    assert reminder["task_class"] == "user_action"
    assert reminder["bound_agent"] == AGENT_ID
    assert reminder["blocks_agent"] is None
    assert reminder["unblocks_todo_id"] is None
    assert result["after"]["selected_todo"]["todo_id"] == advancement["todo_id"]


def test_material_poll_user_followup_requires_explicit_class_without_writeback(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a protected release target.",
        target_key="protected-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    before_state = state.read_text(encoding="utf-8")
    before_todos = list_goal_todos(
        registry_path=registry,
        goal_id=GOAL_ID,
    )

    returncode, result = run_json_cli_result(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--todo-id",
        monitor["todo_id"],
        "--result-hash",
        "approval-required-42",
        "--material-change",
        "--next-user-todo",
        "Approve the protected release.",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )

    assert returncode == 1
    assert result["ok"] is False
    assert result["reason"] == (
        "--next-user-todo requires explicit --next-user-task-class "
        "user_action|user_gate"
    )
    assert "todo_writeback" not in result
    assert state.read_text(encoding="utf-8") == before_state
    assert (
        list_goal_todos(
            registry_path=registry,
            goal_id=GOAL_ID,
        )
        == before_todos
    )


def test_material_poll_explicit_user_gate_blocks_bound_agent(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a protected release target.",
        target_key="protected-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )

    result = run_json_cli(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--todo-id",
        monitor["todo_id"],
        "--result-hash",
        "approval-required-42",
        "--material-change",
        "--next-user-todo",
        "Approve the protected release.",
        "--next-user-task-class",
        "user_gate",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )

    gate = result["todo_writeback"]["next_todos"][0]
    assert gate["task_class"] == "user_gate"
    assert gate["bound_agent"] == AGENT_ID
    assert gate["blocks_agent"] == AGENT_ID
    assert gate["unblocks_todo_id"] == monitor["todo_id"]
    assert result["after"]["effective_action"] == "operator_gate_notify"


def test_next_user_task_class_contract_requires_explicit_semantics() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "--next-user-todo requires explicit --next-user-task-class "
            r"user_action\|user_gate"
        ),
    ):
        resolve_next_user_task_class("Review the release.", None)

    assert (
        resolve_next_user_task_class("Review the release.", "user_action")
        == "user_action"
    )
    assert (
        resolve_next_user_task_class("Approve the release.", "user_gate")
        == "user_gate"
    )


def test_monitor_poll_user_task_class_requires_user_followup(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )

    returncode, result = run_json_cli_result(
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "generic_cli",
        "--todo-id",
        monitor["todo_id"],
        "--result-hash",
        "review-ready-42",
        "--material-change",
        "--next-user-task-class",
        "user_action",
        "--execute",
        registry_path=registry,
        runtime_root=runtime,
    )

    assert returncode == 1
    assert result["ok"] is False
    assert result["reason"] == "--next-user-task-class requires --next-user-todo"


def test_material_poll_reload_failure_reports_persisted_writeback(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Poll a public release target.",
        target_key="public-release:42",
        next_due_at="2000-01-01T00:00:00+00:00",
    )
    status_payload = collect_status(
        registry_path=registry,
        runtime_root_override=str(runtime),
        scan_roots=[Path(__file__).resolve()],
        limit=20,
    )

    def fail_status_reload() -> dict:
        raise RuntimeError("synthetic status reload failure")

    result = record_quota_monitor_poll(
        status_payload,
        goal_id=GOAL_ID,
        registry_path=registry,
        execute=True,
        source="heartbeat",
        agent_id=AGENT_ID,
        todo_id=monitor["todo_id"],
        result_hash="merged-42",
        material_change=True,
        next_agent_todo="Validate the persisted material transition successor.",
        next_action_kind="validate_material_transition",
        status_reloader=fail_status_reload,
    )

    assert result["ok"] is True
    warning = result["status_reload_warning"]
    assert warning["schema_version"] == "monitor_poll_status_reload_warning_v0"
    assert warning["error_type"] == "RuntimeError"
    assert warning["persisted_writeback"] is True
    assert warning["after_projection_fresh"] is False
    assert "rerun quota should-run" in warning["recommended_action"]
    assert result["monitor_event"]["status_reload_warning"] == warning
    successor = result["todo_writeback"]["next_todos"][0]
    assert successor["continuation_policy"] == "independent_handoff"
    assert successor["target_key"].startswith(
        f"monitor-successor:{monitor['todo_id']}:"
    )
    assert "Validate the persisted material transition successor." in state.read_text(
        encoding="utf-8"
    )


def test_todo_help_distinguishes_assignment_from_lifecycle_actor() -> None:
    from loopx.cli import build_parser

    parser = build_parser()
    todo_action = next(
        action
        for action in parser._actions
        if getattr(action, "dest", None) == "command"
    )
    todo_parser = todo_action.choices["todo"]
    claimed_by = next(
        action for action in todo_parser._actions if action.dest == "claimed_by"
    )
    agent_id = next(
        action for action in todo_parser._actions if action.dest == "agent_id"
    )

    assert "assignment target" in claimed_by.help
    assert "not the lifecycle actor" in claimed_by.help
    assert "attribute the lifecycle actor" in agent_id.help
