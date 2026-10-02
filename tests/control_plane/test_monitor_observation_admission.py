from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    DUE_MONITOR_TODO_ID,
    GOAL_ID,
    TODO_ID,
    _append_newly_due_monitor,
    _classification_count,
    _projected_cli_args,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)


def test_settled_advancement_hold_preserves_independent_monitor(
    tmp_path: Path,
) -> None:
    """A post-settlement hold does not strand a due independent observation."""
    status = "blocked"
    project, runtime, registry = _write_fixture(tmp_path)
    turn = "turn-settled-advancement-hold"
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--turn-instance-id", turn)
    capabilities = ("--available-capability", "network",
                    "--available-capability", "external_evidence_poll")
    guard = ("quota", "should-run", "--codex-app", *binding,
             *capabilities, "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *guard)
    assert rc == 0, first
    _append_newly_due_monitor(project, watch_only=True)
    rc, admitted = _run_cli(registry, runtime, *guard)
    assert rc == 0, admitted
    poll_command = admitted["interaction_contract"]["cli_channel"][
        "auxiliary_monitor_poll"]["command"]
    rc, refresh = _run_cli(
        registry, runtime, "refresh-state", *binding, "--todo-id", TODO_ID,
        "--classification", "fixture_inflight_validated",
        "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation",
        "--delivery-workspace-path", str(project),
        "--no-global-sync", "--suppress-external-sinks",
    )
    assert rc == 0, refresh
    rc, spent = _run_cli(registry, runtime, *_projected_cli_args(
        refresh["settlement_owed"]["command"], turn_instance_id=turn))
    assert rc == 0, spent
    rc, held = _run_cli(
        registry, runtime, "todo", "update", "--goal-id", GOAL_ID,
        "--todo-id", TODO_ID, "--agent-id", AGENT_ID, "--status", status,
        "--reason", "Await independent evidence after validated progress",
    )
    assert rc == 0, held
    rc, successor = _run_cli(
        registry, runtime, "todo", "add", "--goal-id", GOAL_ID,
        "--role", "agent", "--task-class", "advancement_task",
        "--claimed-by", AGENT_ID, "--text", "Validate an independent artifact",
    )
    assert rc == 0, successor
    poll_args = tuple("observed-after-hold" if token == "${LOOPX_MONITOR_RESULT_HASH:?}"
                      else token for token in _projected_cli_args(
                          poll_command, turn_instance_id=turn)) + ("--scan-path", str(project))
    for expected_replay in (False, True):
        rc, poll = _run_cli(registry, runtime, *poll_args)
        assert rc == 0, (poll.get("error_code"), poll.get("reason"), poll.get("error"))
        assert poll["replayed"] is expected_replay
        assert poll["settlement_todo_id"] == TODO_ID
        assert poll["turn_continuation"]["current_turn_settled"] is True
        assert poll["turn_continuation"]["same_turn_independent_settlement_allowed"] is False
        assert poll["settlement_resume"]["next_step"] is None
    rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    assert next(t for t in listed["todos"] if t["todo_id"] == TODO_ID)["status"] == status
    assert _classification_count(runtime, "quota_monitor_poll") == 1
    assert _spend_run_count(runtime) == 1


def test_receipt_bound_advancement_allows_one_auxiliary_due_monitor_receipt(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    turn_instance_id = "turn-advancement-with-auxiliary-monitor"
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--scan-path",
        str(project),
    )
    first_rc, first = _run_cli(registry_path, runtime, *guard_args)
    assert first_rc == 0, first
    assert first["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID

    _append_newly_due_monitor(project, watch_only=True)
    capability_args = (
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        *capability_args,
    )
    assert replay_rc == 0, replay
    assert replay["selected_todo"]["todo_id"] == TODO_ID
    assert "due_monitor_context" in replay["work_lane_contract"]["reason_codes"]

    auxiliary_cli = replay["interaction_contract"]["cli_channel"][
        "auxiliary_monitor_poll"
    ]
    assert auxiliary_cli["turn_instance_id"] == turn_instance_id
    poll_args = _projected_cli_args(
        auxiliary_cli["command"],
        turn_instance_id=turn_instance_id,
    )
    poll_args = tuple(
        "unchanged-auxiliary-monitor"
        if token == "${LOOPX_MONITOR_RESULT_HASH:?}"
        else token
        for token in poll_args
    ) + ("--scan-path", str(project))
    poll_rc, poll = _run_cli(registry_path, runtime, *poll_args)
    poll_replay_rc, poll_replay = _run_cli(
        registry_path,
        runtime,
        *poll_args,
    )

    assert poll_rc == 0, poll
    assert poll["settlement_todo_id"] == TODO_ID
    assert poll["todo_id"] == DUE_MONITOR_TODO_ID
    assert poll["before"]["selected_todo"]["todo_id"] == TODO_ID
    assert poll["after"]["selected_todo"]["todo_id"] == TODO_ID
    assert poll["material_change"] is False
    assert poll["turn_continuation"] == {
        "schema_version": "quota_turn_continuation_v0",
        "settlement_binding_matches_observation": False,
        "current_turn_settled": False,
        "same_turn_independent_settlement_allowed": False,
        "next_turn_required": False,
        "next_action": "continue the original advancement settlement in this Turn",
        "reason": (
            "the committed monitor-poll is an auxiliary observation and does not "
            "settle the advancement Turn"
        ),
    }
    assert (
        poll["after"]["interaction_contract"]["cli_channel"]["spend_after_validation"]
        is True
    )
    assert poll_replay_rc == 0, poll_replay
    assert poll_replay["replayed"] is True
    assert _classification_count(runtime, "quota_monitor_poll") == 1
    assert _spend_run_count(runtime) == 0

    final_guard_rc, final_guard = _run_cli(
        registry_path,
        runtime,
        *guard_args,
        *capability_args,
    )
    assert final_guard_rc == 0, final_guard
    assert final_guard["selected_todo"]["todo_id"] == TODO_ID
    assert final_guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == (
        TODO_ID
    )


@pytest.mark.parametrize(
    ("provider", "writeback", "spend_first"),
    [
        ("legacy", False, False),
        ("legacy", True, False),
        ("file", True, False),
        ("sqlite", True, False),
        ("legacy", True, True),
        ("file", True, True),
        ("sqlite", True, True),
    ],
)
def test_completed_advancement_retains_auxiliary_monitor_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    writeback: bool,
    spend_first: bool,
) -> None:
    from canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
    )
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from loopx.control_plane.coordination.local_authority import (
        read_canonical_todos_if_promoted,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry_path = _write_fixture(tmp_path)
    turn = "turn-completed-advancement-with-auxiliary-monitor"
    binding = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        turn,
    )
    capabilities = (
        "--available-capability",
        "network",
        "--available-capability",
        "external_evidence_poll",
    )
    guard = (
        "quota",
        "should-run",
        "--codex-app",
        *binding,
        *capabilities,
        "--scan-path",
        str(project),
    )
    rc, first = _run_cli(registry_path, runtime, *guard)
    assert rc == 0, first
    _append_newly_due_monitor(project, watch_only=True)
    rc, open_guard = _run_cli(registry_path, runtime, *guard)
    assert rc == 0, open_guard
    auxiliary = open_guard["interaction_contract"]["cli_channel"][
        "auxiliary_monitor_poll"
    ]
    poll_args = tuple(
        "completed-primary-observation"
        if token == "${LOOPX_MONITOR_RESULT_HASH:?}"
        else token
        for token in _projected_cli_args(auxiliary["command"], turn_instance_id=turn)
    ) + ("--scan-path", str(project))
    rc, complete = _run_cli(
        registry_path,
        runtime,
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
        "--evidence",
        "read-only fixture delivery validated",
        "--no-follow-up",
    )
    assert rc == 0, complete
    if provider != "legacy":
        rc, listed = _run_cli(
            registry_path, runtime, "todo", "list", "--goal-id", GOAL_ID
        )
        assert rc == 0, listed
        primary = next(todo for todo in listed["todos"] if todo["todo_id"] == TODO_ID)
        assert primary["status"] == "done"
        lease = {
            "schema_version": "task_lease_v0",
            "goal_id": GOAL_ID,
            "todo_id": DUE_MONITOR_TODO_ID,
            "owner": AGENT_ID,
            "write_scopes": [],
            "status": "active",
            "idempotency_key": "own-monitor-execution",
            "version": 3,
            "lease_epoch": 2,
            "acquired_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=listed["todos"],
            handoff_mode="hard_lease",
            leases=[lease],
        )
        initialize_canonical_authority(
            runtime,
            GOAL_ID,
            projection,
            state_path=project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
            provider=provider,
        )
    if writeback:
        rc, refresh = _run_cli(
            registry_path,
            runtime,
            "refresh-state",
            *binding,
            "--todo-id",
            TODO_ID,
            "--classification",
            "fixture_delivery_validated",
            "--delivery-batch-scale",
            "single_surface",
            "--delivery-outcome",
            "outcome_progress",
            "--delivery-workspace-path",
            str(project),
            "--no-global-sync",
            "--suppress-external-sinks",
        )
        assert rc == 0, refresh
    if spend_first:
        spend_command = refresh["settlement_owed"]["command"]
        rc, spent = _run_cli(
            registry_path,
            runtime,
            *_projected_cli_args(spend_command, turn_instance_id=turn),
        )
        assert rc == 0, spent
        assert _spend_run_count(runtime) == 1
    rc, poll = _run_cli(registry_path, runtime, *poll_args)
    assert rc == 0, (poll.get("error_code"), poll.get("reason"), poll.get("error"))
    assert poll["todo_id"] == DUE_MONITOR_TODO_ID
    assert poll["settlement_todo_id"] == TODO_ID
    assert poll["turn_continuation"]["current_turn_settled"] is spend_first
    assert poll["turn_continuation"]["next_turn_required"] is spend_first
    if provider != "legacy":
        canonical = read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id=GOAL_ID, include_leases=True
        )
        assert canonical["source_authority"] == f"{provider}_v0"
        observed = next(
            todo
            for todo in canonical["todos"]
            if todo["todo_id"] == DUE_MONITOR_TODO_ID
        )
        assert observed["result_hash"] == "completed-primary-observation"
        assert poll["todo_writeback"]["lease_proof"] == {
            "idempotency_key": "own-monitor-execution",
            "expected_version": 3,
        }
    resume = poll["settlement_resume"]
    assert resume["identity"]["todo_id"] == TODO_ID
    assert resume["identity"]["turn_instance_id"] == turn
    assert resume["grants_new_delivery"] is False
    assert resume["progress_ref"] == "$.settlement_progress"
    assert len(json.dumps(resume, ensure_ascii=False, indent=2)) < 2_000
    after_cli = poll["after"]["interaction_contract"]["cli_channel"]
    assert after_cli["settlement_resume_ref"] == "$.settlement_resume"
    assert after_cli["spend_after_validation"] is False
    assert poll["settlement_progress"]["state"] == (
        "settled"
        if spend_first
        else "spend_required"
        if writeback
        else "writeback_required"
    )
    rc, replay = _run_cli(registry_path, runtime, *poll_args)
    assert rc == 0, replay
    assert replay["replayed"] is True
    assert _classification_count(runtime, "quota_monitor_poll") == 1
    assert _spend_run_count(runtime) == int(spend_first)
    if provider != "legacy":
        rc, released = _run_cli(
            registry_path,
            runtime,
            "task-lease",
            "release",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            DUE_MONITOR_TODO_ID,
            "--owner",
            AGENT_ID,
            "--idempotency-key",
            "own-monitor-execution",
            "--expected-version",
            "3",
        )
        assert rc == 0, released
    if spend_first:
        assert resume["next_step"] is None
        assert "settlement_owed" not in poll
    elif not writeback:
        assert resume["next_step"]["kind"] == "durable_writeback"
        writeback_command = resume["next_step"]["command_template"]
        args = tuple(
            {
                "<validated_progress>": "fixture_delivery_validated",
                "<scale>": "single_surface",
                "<outcome>": "outcome_progress",
            }.get(token, token)
            for token in _projected_cli_args(writeback_command, turn_instance_id=turn)
        )
        rc, refresh = _run_cli(
            registry_path,
            runtime,
            *args,
            "--delivery-workspace-path",
            str(project),
            "--no-global-sync",
            "--suppress-external-sinks",
        )
        assert rc == 0, refresh
        spend_command = refresh["settlement_owed"]["command"]
    else:
        assert resume["next_step"]["kind"] == "quota_spend"
        spend_command = poll["settlement_owed"]["command"]
    spend_args = _projected_cli_args(spend_command, turn_instance_id=turn)
    for _ in range(2):
        rc, spend = _run_cli(registry_path, runtime, *spend_args)
        assert rc == 0, spend
        assert spend["settlement_identity"]["todo_id"] == TODO_ID
    assert _spend_run_count(runtime) == 1
    rc, replay = _run_cli(registry_path, runtime, *poll_args)
    assert rc == 0, replay
    assert replay["replayed"] is True
    assert replay["settlement_progress"]["state"] == "settled"
    assert replay["turn_continuation"]["current_turn_settled"] is True
    assert replay["settlement_resume"]["next_step"] is None
    assert _classification_count(runtime, "quota_monitor_poll") == 1
