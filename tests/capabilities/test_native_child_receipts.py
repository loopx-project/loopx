from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.multi_subagent.native_child_receipts import (
    latest_native_child_activity,
    load_native_child_activity,
    record_native_child,
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


def _admit(runtime_root: Path) -> None:
    append_rollout_event(
        rollout_event_log_path(runtime_root, GOAL),
        build_rollout_event(
            goal_id=GOAL, event_kind="quota_should_run", agent_id=AGENT,
            run_id=TURN, todo_id="todo-1", status="normal_run",
            details={"todo_id": "todo-1",
                     "settlement_effect_id": f"{GOAL}:{AGENT}:todo-1:{TURN}"},
        ),
    )


def _record(runtime_root: Path, operation_id: str, *, stage: str, outcome: str,
            **kwargs):
    return record_native_child(
        runtime_root=runtime_root, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, operation_id=operation_id, configured_limit=6,
        stage=stage, outcome=outcome, execute=True, **kwargs,
    )


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
    with pytest.raises(ValueError, match="completed"):
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

    queue["items"][0]["project_asset"] = {
        "orchestration": {**policy, "spawn_allowed": False},
    }
    disabled = status.build_attention_queue(
        contract={}, history={}, global_registry={}, runtime_root=tmp_path,
    )
    assert "native_child_activity" not in disabled["items"][0]["project_asset"]
