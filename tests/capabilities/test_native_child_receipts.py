from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.multi_subagent.native_child_receipts import (
    latest_native_child_activity,
    load_native_child_activity,
    native_child_activity,
    record_native_child,
)
from loopx.control_plane.projects.registry_codec import (
    source_session_registry_transaction,
)
from loopx.rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
)


GOAL = "native-child-fixture"
AGENT = "generic-coordinator"
TURN = "turn-native-1"
INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _admit(
    runtime_root: Path,
    *,
    goal_ref: dict[str, str] | None = None,
) -> None:
    append_rollout_event(
        rollout_event_log_path(runtime_root, GOAL),
        build_rollout_event(
            goal_id=GOAL, event_kind="quota_should_run", agent_id=AGENT,
            run_id=TURN, todo_id="todo_native_1", status="normal_run",
            details={"todo_id": "todo_native_1",
                     "settlement_effect_id": f"{GOAL}:{AGENT}:todo_native_1:{TURN}"},
            goal_ref=goal_ref,
        ),
    )


def _record(runtime_root: Path, operation_id: str, *, stage: str, outcome: str,
            **kwargs):
    return record_native_child(
        runtime_root=runtime_root, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, operation_id=operation_id, configured_limit=6,
        stage=stage, outcome=outcome, execute=True, **kwargs,
    )


def _write_source_registry(
    registry_path: Path,
    runtime_root: Path,
    instance_id: str,
) -> None:
    payload = {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "common_runtime_root": str(runtime_root),
        "projects": [],
        "goals": [
            {
                "id": GOAL,
                "goal_instance_id": instance_id,
                "status": "active",
                "execution_authority": False,
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
        "retired_goal_instances": [],
    }
    with source_session_registry_transaction(
        registry_path,
        operation="native_child_goal_instance_test",
        create=lambda: payload,
    ) as transaction:
        transaction.commit(payload)


def test_native_child_receipts_are_partitioned_by_exact_goal_owner(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    registry = tmp_path / "project" / ".loopx" / "registry.json"
    goal_ref_a = {"goal_id": GOAL, "goal_instance_id": INSTANCE_A}
    goal_ref_b = {"goal_id": GOAL, "goal_instance_id": INSTANCE_B}
    _admit(runtime, goal_ref=goal_ref_a)
    _admit(runtime, goal_ref=goal_ref_b)
    _write_source_registry(registry, runtime, INSTANCE_B)

    result = record_native_child(
        runtime_root=runtime,
        registry_path=registry,
        goal_ref=goal_ref_b,
        goal_id=GOAL,
        agent_id=AGENT,
        turn_instance_id=TURN,
        operation_id="op-b",
        configured_limit=6,
        stage="decision",
        operation="spawn",
        outcome="started",
        entrypoint_id="generic_host",
        execute=True,
    )
    assert result["native_child_activity"]["operation_count"] == 1
    events = load_rollout_events(rollout_event_log_path(runtime, GOAL))
    assert events[-1]["goal_ref"] == goal_ref_b
    assert native_child_activity(
        events,
        goal_id=GOAL,
        agent_id=AGENT,
        turn_instance_id=TURN,
        configured_limit=6,
        goal_ref=goal_ref_a,
    )["operation_count"] == 0
    assert native_child_activity(
        events,
        goal_id=GOAL,
        agent_id=AGENT,
        turn_instance_id=TURN,
        configured_limit=6,
    )["operation_count"] == 0


def test_generic_report_adoption_is_idempotent_and_survives_restart(tmp_path: Path):
    _admit(tmp_path)
    unknown = load_native_child_activity(
        tmp_path, goal_id=GOAL, agent_id=AGENT, turn_instance_id=TURN,
        configured_limit=6,
    )
    assert unknown["observation"] == "unknown"
    assert unknown["configured_limit"] == 6
    assert unknown["launched_count"] == 0
    assert unknown["host_attested"] is False

    first = _record(
        tmp_path, "op-1", stage="decision", operation="spawn",
        outcome="started", entrypoint_id="generic_host",
    )
    replay = _record(
        tmp_path, "op-1", stage="decision", operation="spawn",
        outcome="started", entrypoint_id="generic_host",
    )
    assert first["appended"] is True
    assert replay["appended"] is False
    assert first["receipt"]["event_id"] == replay["receipt"]["event_id"]
    assert first["native_child_activity"]["parent_accepted_count"] == 0

    _record(tmp_path, "op-1", stage="result", outcome="completed")
    reviewed = _record(
        tmp_path, "op-1", stage="review", outcome="accepted",
        evidence_ref="evidence-1", validation_ref="validation-1",
    )
    activity = load_native_child_activity(
        tmp_path, goal_id=GOAL, agent_id=AGENT, turn_instance_id=TURN,
        configured_limit=6,
    )
    assert activity == reviewed["native_child_activity"]
    assert activity["observation"] == "coordinator_reported"
    assert activity["launched_count"] == 1
    assert activity["parent_accepted_count"] == 1
    assert activity["operations"][0]["entrypoint_id"] == "generic_host"
    assert latest_native_child_activity(
        load_rollout_events(rollout_event_log_path(tmp_path, GOAL)),
        goal_id=GOAL, configured_limit=6,
    ) == activity
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL))) == 4


def test_skip_capacity_rejection_and_no_same_turn_retry(tmp_path: Path):
    _admit(tmp_path)
    skipped = _record(
        tmp_path, "skip-1", stage="decision", operation="skip",
        outcome="skipped", entrypoint_id="another_host",
        reason_code="no_independent_work",
    )["native_child_activity"]
    assert skipped["skipped_count"] == 1
    assert skipped["observed_capacity"] == "not_observed"
    rejected = _record(
        tmp_path, "op-1", stage="decision", operation="spawn",
        outcome="capacity_rejected", entrypoint_id="another_host",
        reason_code="host_capacity_exhausted",
    )["native_child_activity"]
    assert rejected["capacity_rejected_count"] == 1
    assert rejected["retry_same_turn"] is False
    with pytest.raises(ValueError, match="same-Turn"):
        _record(
            tmp_path, "op-2", stage="decision", operation="followup",
            outcome="started", entrypoint_id="another_host",
        )
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL))) == 3


def test_typed_host_failure_stops_same_turn_retry_but_keeps_parent_reporting(
    tmp_path: Path,
):
    _admit(tmp_path)
    failed = _record(
        tmp_path, "op-1", stage="decision", operation="spawn",
        outcome="host_failed", entrypoint_id="generic_host",
        reason_code="host_unavailable",
    )["native_child_activity"]
    assert failed["host_failed_count"] == 1
    assert failed["retry_same_turn"] is False
    with pytest.raises(ValueError, match="same-Turn"):
        _record(
            tmp_path, "op-2", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="generic_host",
        )
    skipped = _record(
        tmp_path, "skip-1", stage="decision", operation="skip",
        outcome="skipped", entrypoint_id="generic_host",
        reason_code="parent_work_priority",
    )["native_child_activity"]
    assert skipped["skipped_count"] == 1


def test_no_unadmitted_or_conflicting_receipts(tmp_path: Path):
    with pytest.raises(ValueError, match="admitted"):
        _record(
            tmp_path, "op-1", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="generic_host",
        )
    _admit(tmp_path)
    with pytest.raises(ValueError, match="started"):
        _record(tmp_path, "op-1", stage="review", outcome="accepted",
                evidence_ref="evidence-1", validation_ref="validation-1")
    _record(
        tmp_path, "op-1", stage="decision", operation="spawn",
        outcome="started", entrypoint_id="generic_host",
    )
    with pytest.raises(ValueError, match="conflicting"):
        _record(
            tmp_path, "op-1", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="other_host",
        )
    with pytest.raises(ValueError, match="terminal"):
        _record(tmp_path, "op-1", stage="review", outcome="accepted",
                evidence_ref="evidence-1", validation_ref="validation-1")
    with pytest.raises(ValueError, match="compact opaque id"):
        _record(
            tmp_path, "op-2", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="/private/source",
        )


def test_cli_readback_and_disabled_policy_do_not_mutate(tmp_path: Path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": [{
        "id": GOAL, "repo": str(tmp_path), "status": "active",
        "registered_agents": [AGENT],
        "spawn_policy": {"mode": "multi_subagent", "allowed": True, "max_children": 6},
    }]}))
    runtime_root = tmp_path / "runtime"
    _admit(runtime_root)
    base = [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime_root), "--format", "json",
            "native-child", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", TURN]
    command = [*base, "record", "--operation-id", "op-1", "--stage", "decision",
               "--operation", "spawn", "--outcome", "started",
               "--entrypoint-id", "generic_host"]
    preview = subprocess.run(command, text=True, capture_output=True)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["dry_run"] is True
    assert len(load_rollout_events(rollout_event_log_path(runtime_root, GOAL))) == 1
    recorded = subprocess.run([*command, "--execute"], text=True, capture_output=True)
    assert recorded.returncode == 0, recorded.stderr
    read = subprocess.run([*base, "read"], text=True, capture_output=True)
    assert read.returncode == 0, read.stderr
    assert json.loads(read.stdout)["native_child_activity"]["launched_count"] == 1
    context = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--runtime-root", str(runtime_root), "agent-context", "--format", "json",
         "--goal-id", GOAL, "--agent-id", AGENT, "--phase", "after_delegate_result",
         "--turn-instance-id", TURN],
        text=True, capture_output=True,
    )
    assert context.returncode == 0, context.stderr
    context_payload = json.loads(context.stdout)
    assert context_payload["host_receipts_observed"] is False
    assert context_payload["native_child_activity"]["launched_count"] == 1
    [contribution] = context_payload["agent_context"]["contributions"]
    assert contribution["facts"]["native_child_activity"]["launched_count"] == 1

    disabled = json.loads(registry.read_text())
    disabled["goals"][0]["spawn_policy"]["allowed"] = False
    registry.write_text(json.dumps(disabled))
    rejected = subprocess.run([*base, "read"], text=True, capture_output=True)
    assert rejected.returncode == 1
    assert "enabled multi_subagent" in json.loads(rejected.stdout)["error"]


def test_goal_status_only_attaches_reported_activity_to_enabled_goal(
    tmp_path: Path, monkeypatch,
):
    import loopx.status as status

    _admit(tmp_path)
    _record(
        tmp_path, "op-1", stage="decision", operation="skip",
        outcome="skipped", entrypoint_id="generic_host",
        reason_code="no_independent_work",
    )
    policy = {"mode": "multi_subagent", "spawn_allowed": True, "max_children": 6}
    queue = {"items": [{"goal_id": GOAL, "project_asset": {"orchestration": policy}}]}
    monkeypatch.setattr(status, "_build_attention_queue_read_model", lambda **_kwargs: queue)
    projected = status.build_attention_queue(
        contract={}, history={}, global_registry={}, runtime_root=tmp_path,
    )
    activity = projected["items"][0]["project_asset"]["native_child_activity"]
    assert activity["skipped_count"] == 1
    assert activity["observation"] == "coordinator_reported"

    # A bounded status snapshot may retain a later result but not its decision.
    # The status projection must recover the complete Turn from the event log.
    _record(tmp_path, "op-2", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="generic_host")
    _record(tmp_path, "op-2", stage="result", outcome="completed")
    projected = status.build_attention_queue(
        contract={}, history={}, global_registry={}, runtime_root=tmp_path,
        events_for_goal=lambda _goal_id, *, limit: load_rollout_events(
            rollout_event_log_path(tmp_path, GOAL), limit=limit,
        )[-1:],
    )
    activity = projected["items"][0]["project_asset"]["native_child_activity"]
    assert activity["launched_count"] == 1
    assert activity["skipped_count"] == 1

    queue["items"][0]["project_asset"] = {
        "orchestration": {**policy, "spawn_allowed": False},
    }
    disabled = status.build_attention_queue(
        contract={}, history={}, global_registry={}, runtime_root=tmp_path,
    )
    assert "native_child_activity" not in disabled["items"][0]["project_asset"]


def test_report_phase_is_checked_once_under_the_append_lock(tmp_path: Path, monkeypatch):
    import loopx.capabilities.multi_subagent.native_child_receipts as native

    _admit(tmp_path)
    readback, append_once = native.read_heartbeat_settlement, native.append_rollout_event_once
    calls = []

    def read_once(*args, **kwargs):
        calls.append(kwargs["turn_instance_id"])
        return readback(*args, **kwargs)

    monkeypatch.setattr(native, "read_heartbeat_settlement", read_once)
    first = _record(tmp_path, "op-1", stage="decision", operation="spawn",
                    outcome="started", entrypoint_id="generic_host")
    assert calls == [TURN]
    replay = _record(tmp_path, "op-1", stage="decision", operation="spawn",
                     outcome="started", entrypoint_id="generic_host")
    assert replay["receipt"]["event_id"] == first["receipt"]["event_id"]
    assert calls == [TURN, TURN]

    def close_before_append(*args, **kwargs):
        append_rollout_event(rollout_event_log_path(tmp_path, GOAL), build_rollout_event(
            goal_id=GOAL, event_kind="todo_complete", agent_id=AGENT, run_id=TURN,
            todo_id="todo_native_1", status="done",
            details={"settlement_effect_id": f"{GOAL}:{AGENT}:todo_native_1:{TURN}"},
        ))
        return append_once(*args, **kwargs)

    monkeypatch.setattr(native, "append_rollout_event_once", close_before_append)
    with pytest.raises(ValueError, match="open, work-admitted"):
        _record(tmp_path, "op-new", stage="decision", operation="spawn",
                outcome="started", entrypoint_id="generic_host")
    assert calls == [TURN, TURN, TURN]
    assert load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=6)["operation_count"] == 1


def test_negative_guard_facts_and_binding_conflicts_do_not_reauthorize_reports(tmp_path: Path):
    _admit(tmp_path)
    _record(tmp_path, "op-1", stage="decision", operation="spawn",
            outcome="started", entrypoint_id="generic_host")
    log = rollout_event_log_path(tmp_path, GOAL)
    append_rollout_event(log, build_rollout_event(
        goal_id=GOAL, event_kind="quota_should_run", agent_id=AGENT, run_id=TURN,
        status="normal_run", details={"todo_id": "todo_native_1",
            "settlement_effect_id": f"{GOAL}:{AGENT}:todo_native_1:{TURN}",
            "must_attempt_work": True, "delivery_allowed": False},
    ))
    with pytest.raises(ValueError, match="open, work-admitted"):
        _record(tmp_path, "op-new", stage="decision", operation="spawn",
                outcome="started", entrypoint_id="generic_host")
    # A duplicate is a fact read, not permission to start the operation again.
    assert _record(tmp_path, "op-1", stage="decision", operation="spawn",
        outcome="started", entrypoint_id="generic_host")["appended"] is False
    append_rollout_event(log, build_rollout_event(
        goal_id=GOAL, event_kind="quota_should_run", agent_id=AGENT, run_id=TURN,
        status="normal_run", details={"todo_id": "todo_other_binding",
            "settlement_effect_id": f"{GOAL}:{AGENT}:todo_other_binding:{TURN}"},
    ))
    with pytest.raises(ValueError, match="settlement-bound"):
        _record(tmp_path, "op-1", stage="decision", operation="spawn",
                outcome="started", entrypoint_id="generic_host")
    assert sum(row["event_kind"] == "native_child_decision" for row in load_rollout_events(log)) == 1


def test_foreign_goal_facts_cannot_supply_a_native_stage_prerequisite(tmp_path: Path):
    _admit(tmp_path)
    foreign = build_rollout_event(goal_id="other-goal", event_kind="native_child_decision",
        agent_id=AGENT, run_id=TURN, case_id="foreign-op", status="started",
        details={"operation": "spawn", "outcome": "started", "entrypoint_id": "generic_host"})
    append_rollout_event(rollout_event_log_path(tmp_path, GOAL), foreign)
    with pytest.raises(ValueError, match="started"):
        _record(tmp_path, "foreign-op", stage="result", outcome="completed")
    assert native_child_activity([foreign], goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=6)["operation_count"] == 0


def test_cli_runtime_failure_is_visible_and_never_writes_a_report(tmp_path: Path, monkeypatch, capsys):
    import loopx.capabilities.multi_subagent.native_child_receipts as native
    from loopx.cli import main

    runtime = tmp_path / "runtime"
    _admit(runtime)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": [{
        "id": GOAL, "repo": str(tmp_path), "status": "active", "registered_agents": [AGENT],
        "spawn_policy": {"mode": "multi_subagent", "allowed": True, "max_children": 6},
    }]}))

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("TypeScript runtime unavailable: synthetic diagnostic")

    monkeypatch.setattr(native, "read_heartbeat_settlement", unavailable)
    code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "native-child", "record", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN,
        "--operation-id", "op-1", "--stage", "decision", "--operation", "spawn",
        "--outcome", "started", "--entrypoint-id", "generic_host", "--execute"])
    assert code == 1
    assert "TypeScript runtime unavailable" in json.loads(capsys.readouterr().out)["error"]
    assert len(load_rollout_events(rollout_event_log_path(runtime, GOAL))) == 1
