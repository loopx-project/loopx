"""Real quota entrypoints preserve scheduler authority and read-only boundaries."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.canonical_snapshot import read_canonical_snapshot
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from loopx.control_plane.scheduler.state import APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY, load_scheduler_state
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

GOAL = "scheduler-arbitration-cli-fixture"
AGENT = "scheduler-arbitration-agent"
TODO = "todo_scheduler_work"
TURN = "turn-scheduler-arbitration"


def make_scheduler_fixture(root: Path, provider: str, scenario: str) -> dict:
    """Keep this synthetic source usable by source and installed-package replay."""
    project, runtime = root / "project", root / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    status = "active"
    row = (
        "- [ ] [P1] Validate the bounded source artifact.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=open claimed_by={AGENT} "
        "task_class=advancement_task action_kind=validate -->\n"
    )
    if scenario == "monitor":
        row = (
            "- [ ] [P2-monitor] Observe the bounded source after its deadline.\n"
            f"  <!-- loopx:todo todo_id={TODO} status=open claimed_by={AGENT} "
            "task_class=continuous_monitor action_kind=observe watch_only=true "
            "target_key=synthetic-source cadence=60m next_due_at=2099-01-01T00%3A00%3A00Z -->\n"
        )
    gate = (
        "## User Todo\n\n- [ ] [P0-user] Approve the bounded delivery.\n"
        "  <!-- loopx:todo todo_id=todo_scheduler_gate status=open task_class=user_gate "
        f"blocks_agent={AGENT} decision_scope=delivery:action:fixture priority=P0-USER -->\n\n"
        if scenario == "human_gate" else ""
    )
    state.write_text(f"---\nstatus: {status}\n---\n\n# Synthetic scheduler Goal\n\n" +
        gate + "## Agent Todo\n\n" + row)
    registry = root / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": status, "repo": str(project), "state_file": state.name,
        "activation_state": "stopped" if scenario == "stopped" else "active",
        "domain": "synthetic-scheduler",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 0.0 if scenario == "paused" else 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]},
    }]}))
    fields = parse_active_state_todos(state.read_text(), item_limit=None)
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=fields["agent_todos"]["items"] + fields.get("user_todos", {}).get("items", []),
        handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)
    context = ["--host-surface", "codex_app"] if scenario == "invalid_context" else ["--codex-app"]
    guard = ["quota", "should-run", *context, "--goal-id", GOAL, "--agent-id", AGENT,
             "--turn-instance-id", TURN, "--scan-path", str(project)]
    return {"project": project, "runtime": runtime, "registry": registry,
            "state": state, "guard": guard}


def call_scheduler_cli(fixture: dict, *args: str, expected_code: int = 0) -> dict:
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(fixture["registry"]),
        "--runtime-root", str(fixture["runtime"]), "--format", "json", *args,
    ], cwd=fixture["project"], env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == expected_code, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def scheduler_authority_snapshot(fixture: dict) -> dict:
    result = read_canonical_snapshot(rpc=effect_runtime_result, runtime_root=str(fixture["runtime"]),
        goal_id=GOAL, include_leases=True, projection_readback=None, timeout=20)
    assert result["status"] == "loaded", result
    return result


def _assert_no_private_arbitration(value: object) -> None:
    if isinstance(value, dict):
        assert not {"arbitration", "scheduler_arbitration", "_scheduler_arbitration"}.intersection(value)
        for item in value.values():
            _assert_no_private_arbitration(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_private_arbitration(item)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scenario,mode,action,cadence,reason", [
    ("active", "bounded_delivery", "run_now", "active_work", "interaction_agent_attempt_required"),
    ("human_gate", "user_gate", "backoff_waiting_for_user", "human_gate", "interaction_blocking_user_gate"),
    ("monitor", "monitor_quiet_skip", "backoff_until_material_transition", "monitor_wait", "interaction_monitor_quiet_wait"),
    ("paused", "skip", "stop_until_explicit_resume", "quota_paused", "quota_paused"),
    ("stopped", "skip", "stop_until_explicit_resume", "goal_stopped", "goal_stopped"),
    ("invalid_context", "bounded_delivery", "repair_scheduler_execution_context", "control_plane_repair", "invalid_scheduler_execution_context"),
])
def test_quota_cli_keeps_scheduler_authority_without_premature_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    scenario: str, mode: str, action: str, cadence: str, reason: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_scheduler_fixture(tmp_path, provider, scenario)
    try:
        before = {key: fixture[key].read_bytes() for key in ("registry", "state")}
        authority = scheduler_authority_snapshot(fixture)
        packet = call_scheduler_cli(fixture, *fixture["guard"])
        hint = packet["scheduler_hint"]
        assert packet["interaction_contract"]["mode"] == mode, packet
        assert (hint["schema_version"], hint["action"], hint["cadence_class"], hint["reason_code"]) == (
            "scheduler_hint_v0", action, cadence, reason,
        )
        _assert_no_private_arbitration(packet)
        assert {key: fixture[key].read_bytes() for key in before} == before
        assert scheduler_authority_snapshot(fixture) == authority
        assert load_scheduler_state(fixture["runtime"], goal_id=GOAL, agent_id=AGENT,
            state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY) is None
        events = load_rollout_events(rollout_event_log_path(fixture["runtime"], GOAL))
        assert not {"refresh_state", "quota_spend", "scheduler_ack"}.intersection(
            event["event_kind"] for event in events)
        index = fixture["runtime"] / "goals" / GOAL / "runs" / "index.jsonl"
        if scenario == "monitor":
            rows = [json.loads(line) for line in index.read_text().splitlines()]
            assert [(row["classification"], row["turn_instance_id"]) for row in rows] == [
                ("quota_monitor_poll", TURN)]
        else:
            assert not index.exists()
        if scenario == "invalid_context":
            assert hint["execution_phase"]["disposition"] == "contract_error"
            assert hint["codex_app"]["apply"] == "none"
            assert hint["consistency_error"]["source"] == "scheduler_execution_context"
        elif scenario in {"paused", "stopped"}:
            assert packet["should_run"] is False
            assert hint["codex_app"]["ack_required"] is False
            assert "recommended_rrule" not in hint["codex_app"]
        if scenario == "active":
            assert packet["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO
            refresh = call_scheduler_cli(fixture, "refresh-state", "--goal-id", GOAL,
                "--agent-id", AGENT, "--todo-id", TODO, "--turn-instance-id", TURN,
                "--classification", "validated_intermediate_progress", "--delivery-batch-scale", "implementation",
                "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
                "--delivery-workspace-path", str(fixture["project"]), "--no-global-sync", "--suppress-external-sinks")
            assert refresh["settlement_result"]["ok"] is True
            spent = call_scheduler_cli(fixture, "quota", "spend-slot", "--goal-id", GOAL,
                "--agent-id", AGENT, "--todo-id", TODO, "--turn-instance-id", TURN,
                "--slots", "1", "--source", "heartbeat", "--execute")
            assert spent["settlement_progress"]["state"] == "settled"
            restart_effect_runtime(wait_for_exit=True)
            replay = call_scheduler_cli(fixture, *fixture["guard"])
            assert replay["interaction_contract"]["mode"] == "heartbeat_settled_skip"
            assert replay["should_run"] is False
            assert replay["scheduler_hint"]["reason_code"] == "interaction_heartbeat_settled_replay"
            assert scheduler_authority_snapshot(fixture) == authority
            assert fixture["registry"].read_bytes() == before["registry"]
    finally:
        restart_effect_runtime(wait_for_exit=True)
