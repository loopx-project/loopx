"""A receipt-admitted planning review does not execute its selected Todo."""
from __future__ import annotations

import json
import hashlib

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.goals.activation import build_goal_activation
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID,
    _bind_selected_replan_guard, _configure_selected_todo_replan_fixture,
    _initialize_fixture_authority, _initialize_git_checkout, _run_cli,
    _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("case", [
    "planning", "foreign_turn", "explicit_execution", "prepared_execution", "stopped",
])
def test_planning_closeout_keeps_business_execution_authority(tmp_path, monkeypatch, provider, case):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_git_checkout(project)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    turn = "lease-scoped-planning"
    guard = _bind_selected_replan_guard(registry, runtime, project, turn)
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert guard["decision"] == "autonomous_replan_required"
    assert guard["normal_delivery_allowed"] is False
    assert "execution_preparation" not in guard
    rc, before = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID)
    assert rc == 0
    todo = before["todo"]
    rc, lease_before = _run_cli(registry, runtime, "task-lease", "inspect", "--goal-id", GOAL_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID)
    assert rc == 0 and lease_before["active"] is False
    args = ["refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--turn-instance-id", "foreign-planning" if case == "foreign_turn" else turn,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--classification", "verified_planning_scope",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "semantic_closeout", "--delivery-workspace-path", str(project),
        "--autonomous-replan-recorded", "--repair-delta-kind", "successor_or_supersede",
        "--progress-result-class", "advanced", "--progress-surface-id", "surface:planning-scope",
        "--progress-hypothesis-id", "hypothesis:planning-scope", "--progress-probe-kind", "probe:planning-scope",
        "--progress-evidence-id", "evidence:planning-scope", "--no-global-sync", "--suppress-external-sinks",
        "--settle-turn", "--settlement-receipt"]
    if case == "explicit_execution":
        args += ["--task-lease-idempotency-key", "unadmitted-execution", "--task-lease-expected-version", "1"]
    if case == "stopped":
        data = json.loads(registry.read_text())
        data["goals"][0]["activation"] = build_goal_activation(
            state="stopped", updated_at="2026-01-01T00:00:00Z", reason="fixture Stop")
        registry.write_text(json.dumps(data))
    if case == "prepared_execution":
        # This isolated receipt declares business execution, even though it
        # also carries planning context. Its original proof must still hold.
        log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        admitted = next(row for row in reversed(rows)
            if row["event_kind"] == "quota_should_run" and row.get("run_id") == turn)
        admitted["details"].update(prepared_lease_required=True,
            prepared_lease_key_sha256=hashlib.sha256(b"unadmitted-execution").hexdigest(),
            prepared_lease_version=1, prepared_lease_epoch=1)
        log.write_text("".join(json.dumps(row) + "\n" for row in rows))
    vision = {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Retain the original task and verify the complete planning scope."},
        "path_delta": {"outcome": "replan", "prior_assumption": "The next scope needed review.",
            "observed_reality": "A bounded comparison identifies the next complete check.",
            "retained": ["Original task and acceptance"], "evidence_refs": ["evidence:planning-scope"]},
        "stride_decision": guard["replan_action_packet"]["stride_control"]["minimal_example"]}
    vision_path = tmp_path / "vision.json"
    vision_path.write_text(json.dumps(vision))
    args += ["--agent-vision-json", str(vision_path)]
    rc, result = _run_cli(registry, runtime, *args, cwd=project)
    if case == "planning":
        assert rc == 0 and result["ok"], result.get("error") or result
        assert result["settlement_identity"] == identity
        assert result["settlement_progress"]["state"] == "settled"
        assert _spend_run_count(runtime) == 1
        rc, replay = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0 and replay["idempotent_replay"], replay
        assert _spend_run_count(runtime) == 1
    else:
        assert rc != 0 and not result["ok"] and not result.get("appended"), result
        assert _spend_run_count(runtime) == 0
    rc, after = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID)
    assert rc == 0 and after["todo"] == todo
    rc, lease_after = _run_cli(registry, runtime, "task-lease", "inspect", "--goal-id", GOAL_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID)
    assert rc == 0 and lease_after == lease_before
