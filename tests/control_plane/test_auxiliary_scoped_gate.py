"""Auxiliary observation must use its own scoped gate and fixed primary Turn."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, DUE_MONITOR_TODO_ID, GOAL_ID, TODO_ID,
    _append_newly_due_monitor, _run_cli, _spend_run_count, _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos, update_goal_todo


def _scoped_fixture(tmp_path, monkeypatch, provider, route="source", *, with_gate=True):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, source = _write_fixture(tmp_path)
    update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=TODO_ID,
        claimed_by=AGENT_ID, agent_id=AGENT_ID, priority="P0")
    gated = add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="agent",
        text="Work awaiting owner input", status="blocked", task_class="advancement_task", claimed_by=AGENT_ID)
    gate = (add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="user",
        text="Decide the gated work", task_class="user_gate", bound_agent=AGENT_ID,
        blocks_agent=AGENT_ID, unblocks_todo_id=gated["todo_id"], agent_id=AGENT_ID) if with_gate else None)
    _append_newly_due_monitor(project, priority="P1", watch_only=True)
    if provider != "legacy":
        todos = list_goal_todos(registry_path=source, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL_ID, projection,
            state_path=project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md", provider=provider)
    registry = source
    if route == "global":
        registry = runtime / "registry.global.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["registry_role"] = "global-local"
        payload["goals"][0]["source_registry"] = str(source)
        registry.write_text(json.dumps(payload), encoding="utf-8")
    return project, runtime, source, registry, gate


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("route", ["source", "global"])
@pytest.mark.parametrize("gate_change", ["none", "targets_monitor", "unknown_scope", "global_gate", "primary_blocked"])
def test_emitted_auxiliary_command_honors_an_unrelated_gate(tmp_path, monkeypatch, provider, route, gate_change):
    project, runtime, source, registry, gate = _scoped_fixture(tmp_path, monkeypatch, provider, route)
    turn = "auxiliary-scoped-gate-turn"
    guard_args = ["quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        "--scan-path", str(project)]
    rc, guard = _run_cli(registry, runtime, *guard_args)
    assert rc == 0, json.dumps(guard, ensure_ascii=False)
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
    assert guard["requires_user_action"] is True
    projection = guard["interaction_contract"]["cli_channel"]["auxiliary_monitor_poll"]
    assert projection["availability"] == "ready"
    args = shlex.split(projection["command"])[1:]
    assert args[args.index("--registry") + 1] == str(registry)
    assert args[args.index("--target-key") + 1] == "due-monitor-fixture"
    args = tuple("observed-target" if x == "${LOOPX_MONITOR_RESULT_HASH:?}" else x for x in args)
    def emitted():
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *args, "--scan-path", str(project)],
            cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True)
        return result.returncode, json.loads(result.stdout)
    if gate_change != "none":
        if gate_change == "targets_monitor":
            update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
                agent_id=AGENT_ID, unblocks_todo_id=DUE_MONITOR_TODO_ID)
        elif gate_change == "global_gate":
            update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
                agent_id=AGENT_ID, global_gate=True, clear_blocks_agent=True)
        elif gate_change == "primary_blocked":
            update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=TODO_ID,
                agent_id=AGENT_ID, status="blocked")
        else:
            add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="user", task_class="user_gate",
                text="Decide an unspecified dependency", bound_agent=AGENT_ID,
                blocks_agent=AGENT_ID, agent_id=AGENT_ID)
        rc, current = _run_cli(registry, runtime, *guard_args)
        if gate_change == "primary_blocked":
            assert rc == 1 and current["error_code"] == "heartbeat_receipt_identity_conflict", current
        else:
            assert rc == 0, current
            offered = current["interaction_contract"]["cli_channel"].get("auxiliary_monitor_poll", {})
            assert offered.get("availability") != "ready"
            assert "command" not in offered
        rc, denied = emitted()
        expected = ("heartbeat_receipt_identity_conflict" if gate_change == "primary_blocked"
            else "monitor_poll_admission_rejected")
        assert rc == 1 and denied["error_code"] == expected, denied
        assert _spend_run_count(runtime) == 0
        rows = list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=DUE_MONITOR_TODO_ID)["todos"]
        assert rows[0].get("result_hash") is None
        return
    rc, observed = emitted()
    assert rc == 0, observed
    assert observed["settlement_todo_id"] == TODO_ID
    assert observed["todo_id"] == DUE_MONITOR_TODO_ID
    assert observed["turn_continuation"]["current_turn_settled"] is False
    assert observed["turn_continuation"]["next_turn_required"] is False
    rc, replay = emitted()
    assert rc == 0 and replay["replayed"] is True, replay
    assert _spend_run_count(runtime) == 0
    rc, after = _run_cli(registry, runtime, *guard_args)
    assert rc == 0 and after["selected_todo"]["todo_id"] == TODO_ID, after


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_gate_changed_after_preflight_cannot_commit_observation(tmp_path, monkeypatch, provider):
    from loopx.quota import record_quota_monitor_poll
    from loopx.status import collect_status
    from loopx.control_plane.quota import monitor_poll
    project, runtime, source, registry, gate = _scoped_fixture(tmp_path, monkeypatch, provider)
    turn = "auxiliary-commit-gate-turn"
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        "--scan-path", str(project))
    assert rc == 0 and guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID, guard
    status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
        scan_roots=[project], limit=20, goal_id=GOAL_ID, include_public_boundary_scan=False)
    original = monitor_poll._provider_writeback
    def interleaved(plan, **kwargs):
        update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
            agent_id=AGENT_ID, unblocks_todo_id=DUE_MONITOR_TODO_ID)
        return original(plan, **kwargs)
    monkeypatch.setattr(monitor_poll, "_provider_writeback", interleaved)
    def invoke():
        return record_quota_monitor_poll(status, goal_id=GOAL_ID, registry_path=registry,
            execute=True, agent_id=AGENT_ID, todo_id=DUE_MONITOR_TODO_ID,
            target_key="due-monitor-fixture", result_hash="commit-observation", cadence="30m",
            turn_instance_id=turn, receipt_bound_todo_id=TODO_ID,
            available_capabilities=["network", "external_evidence_poll"])
    if provider == "legacy":
        result = invoke()
        assert result["ok"] is False and "current User gate dependencies" in result["reason"], result
    else:
        from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
        with pytest.raises(LocalCoordinationAuthorityUnavailable, match="current User gate dependencies") as rejected:
            invoke()
        assert rejected.value.payload["no_effect"]["operation_id"]

    rows = list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=DUE_MONITOR_TODO_ID)["todos"]
    assert rows[0].get("result_hash") is None
    assert _spend_run_count(runtime) == 0

    # A rejected effect can retry after the scoped gate is restored. After that
    # historical commit, gate revocation must not strand exact receipt replay.
    monkeypatch.setattr(monitor_poll, "_provider_writeback", original)
    update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
        agent_id=AGENT_ID, unblocks_todo_id=gate["unblocks_todo_id"])
    observed = invoke()
    assert observed["ok"] is True, observed
    update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
        agent_id=AGENT_ID, unblocks_todo_id=DUE_MONITOR_TODO_ID)
    replay = invoke()
    assert replay["ok"] is True and replay["replayed"] is True, replay
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("business_committed", [False, True])
def test_old_pending_plan_preserves_identity_but_not_new_write_authority(tmp_path, monkeypatch, provider, business_committed):
    from loopx.quota import record_quota_monitor_poll
    from loopx.status import collect_status
    from loopx.control_plane.quota import monitor_poll
    from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
    project, runtime, source, registry, gate = _scoped_fixture(tmp_path, monkeypatch, provider)
    turn = "historical-auxiliary-plan-turn"
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn, "--available-capability", "network",
        "--available-capability", "external_evidence_poll", "--scan-path", str(project))
    assert rc == 0 and guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID, guard
    status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
        scan_roots=[project], limit=20, goal_id=GOAL_ID, include_public_boundary_scan=False)
    original = monitor_poll._provider_writeback
    def interrupted(plan, **kwargs):
        if business_committed:
            original(plan, **kwargs)
        pending = list((runtime / "goals" / GOAL_ID / "runs/.transactions/quota-monitor-poll").glob("*.json"))
        assert len(pending) == 1
        # Synthetic compatibility fixture: pre-guard v1 WAL has no policy bit.
        receipt = json.loads(pending[0].read_text())
        receipt["provider_plan"].pop("gate_scope_guard")
        pending[0].write_text(json.dumps(receipt))
        raise ValueError("synthetic response interruption")
    monkeypatch.setattr(monitor_poll, "_provider_writeback", interrupted)
    def invoke():
        return record_quota_monitor_poll(status, goal_id=GOAL_ID, registry_path=registry,
            execute=True, agent_id=AGENT_ID, todo_id=DUE_MONITOR_TODO_ID,
            target_key="due-monitor-fixture", result_hash="historical-observation", cadence="30m",
            turn_instance_id=turn, receipt_bound_todo_id=TODO_ID,
            available_capabilities=["network", "external_evidence_poll"])
    assert invoke()["ok"] is False
    monkeypatch.setattr(monitor_poll, "_provider_writeback", original)
    update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
        agent_id=AGENT_ID, unblocks_todo_id=DUE_MONITOR_TODO_ID)
    if business_committed:
        recovered = invoke()
        assert recovered["ok"] is True, recovered
        assert invoke()["replayed"] is True
    else:
        if provider == "legacy":
            assert invoke()["ok"] is False
        else:
            with pytest.raises(LocalCoordinationAuthorityUnavailable, match="current User gate dependencies"):
                invoke()
        rows = list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=DUE_MONITOR_TODO_ID)["todos"]
        assert rows[0].get("result_hash") is None
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_no_gate_auxiliary_command_retains_commit_and_replay_parity(tmp_path, monkeypatch, provider):
    project, runtime, _source, registry, _gate = _scoped_fixture(tmp_path, monkeypatch, provider, with_gate=False)
    turn = "auxiliary-no-gate-parity"
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn, "--available-capability", "network",
        "--available-capability", "external_evidence_poll", "--scan-path", str(project))
    assert rc == 0 and guard["requires_user_action"] is False, guard
    projection = guard["interaction_contract"]["cli_channel"]["auxiliary_monitor_poll"]
    assert projection["availability"] == "ready"
    args = shlex.split(projection["command"])[1:]
    args = ["no-gate-observation" if part == "${LOOPX_MONITOR_RESULT_HASH:?}" else part for part in args]
    for expected_replay in [False, True]:
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *args,
            "--scan-path", str(project)], text=True, capture_output=True)
        payload = json.loads(result.stdout)
        assert result.returncode == 0 and payload["ok"] is True, payload
        assert payload.get("replayed", False) is expected_replay
        assert payload["settlement_todo_id"] == TODO_ID
    assert _spend_run_count(runtime) == 0
