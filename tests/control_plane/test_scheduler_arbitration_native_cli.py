"""Real quota entrypoints preserve scheduler authority and read-only boundaries."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


def make_scheduler_fixture(
    root: Path, provider: str, scenario: str, *, monitor_clock: datetime | None = None,
) -> dict:
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
    if scenario == "monitor" or monitor_clock is not None:
        row = (
            "- [ ] [P2-monitor] Observe the bounded source after its deadline.\n"
            f"  <!-- loopx:todo todo_id={TODO} status=open claimed_by={AGENT} "
            "task_class=continuous_monitor action_kind=observe watch_only=true "
            "target_key=synthetic-source cadence=60m next_due_at=2099-01-01T00%3A00%3A00Z -->\n"
        )
    if monitor_clock is not None:
        from urllib.parse import quote

        due = quote((monitor_clock + timedelta(minutes=25)).isoformat(), safe="")
        checked = quote((monitor_clock - timedelta(minutes=30)).isoformat(), safe="")
        expires = quote((monitor_clock + timedelta(minutes=85)).isoformat(), safe="")
        row = row.replace("2099-01-01T00%3A00%3A00Z", due).replace(
            " -->", f" last_checked_at={checked} expires_at={expires} -->",
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
             "--turn-instance-id", TURN, ]
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


def _write_host_rrule(automation: Path, minutes: int) -> None:
    automation.parent.mkdir(parents=True, exist_ok=True)
    automation.write_text(
        'version = 1\nid = "fixture"\nkind = "heartbeat"\nstatus = "ACTIVE"\n'
        f'name = "Synthetic scheduler"\nprompt = "Advance `{GOAL}` from active state. Agent: `{AGENT}`."\n'
        f'rrule = "FREQ=MINUTELY;INTERVAL={minutes}"\n'
        'target_thread_id = "synthetic-thread"\n',
    )


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


@pytest.mark.parametrize("human_gate", [False, True])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_deadline_clamp_generated_ack_commits_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    human_gate: bool,
) -> None:
    from loopx.control_plane.quota.scheduler_ack import (
        record_quota_scheduler_ack_for_decision,
    )
    from loopx.control_plane.scheduler import scheduler_hint as hint_module
    from loopx.control_plane.scheduler.execution_context import (
        scheduler_execution_context_for_runtime_profile,
    )
    from tests.control_plane.scheduler_arbitration_fixture import (
        build_native_scheduler_hint,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fixture = make_scheduler_fixture(
        tmp_path,
        provider,
        "human_gate" if human_gate else "monitor",
        monitor_clock=now,
    )
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic-thread")
    automation = codex_home / "automations" / "fixture" / "automation.toml"

    def host_rrule(minutes: int) -> None:
        _write_host_rrule(automation, minutes)

    try:
        host_rrule(30)
        packet = call_scheduler_cli(fixture, *fixture["guard"])
        authority = scheduler_authority_snapshot(fixture)
        # Establish valid earlier ACKs through the native commit owner. Only
        # the synthetic projection clock moves; the monitor facts stay fixed.
        state = None
        with monkeypatch.context() as clock:
            for offset, minutes in [(-30, 15), (-15, 30)]:
                at = now + timedelta(minutes=offset)
                clock.setattr(hint_module, "now_utc", lambda: at)
                hint = build_native_scheduler_hint(
                    packet,
                    codex_app_scheduler_state=state,
                    codex_app_current_rrule=f"FREQ=MINUTELY;INTERVAL={minutes}",
                    scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
                        "codex_app_heartbeat"
                    ),
                )
                committed = record_quota_scheduler_ack_for_decision(
                    {"goal_id": GOAL, "scheduler_hint": hint},
                    runtime_root=fixture["runtime"],
                    goal_id=GOAL,
                    agent_id=AGENT,
                    execute=True,
                    applied_rrule=f"FREQ=MINUTELY;INTERVAL={minutes}",
                    generated_at=at.isoformat(),
                )
                assert committed["ok"] is True, committed
                state = committed["scheduler_commit"]["state"]
        assert state["progression_index"] == 1
        assert state["progression_minutes"] == [15, 30]

        guard = [*fixture["guard"], "--turn-instance-id", TURN + "-clamp"]
        clamped = call_scheduler_cli(fixture, *guard)
        app = clamped["scheduler_hint"]["app_automation"]
        assert app["example_progression_minutes"] == [15]
        assert app["stateful_backoff"]["reset_token"] != state["reset_token"]
        assert app["stateful_backoff"]["progression_index"] == 0
        host_rrule(15)
        args = app["ack_hint"]["cli_args"]
        ack = call_scheduler_cli(fixture, *args)
        assert ack["ok"] is True, ack
        assert ack["scheduler_commit"]["written"] is True
        persisted = load_scheduler_state(
            fixture["runtime"],
            goal_id=GOAL,
            agent_id=AGENT,
            state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
        )
        assert persisted["progression_index"] == 0
        assert persisted["progression_minutes"] == [15]
        assert persisted["last_applied_rrule"] == "FREQ=MINUTELY;INTERVAL=15"
        replay = call_scheduler_cli(fixture, *args)
        assert replay["ok"] is True, replay
        assert replay["scheduler_commit"]["replayed"] is True
        assert replay["scheduler_commit"]["written"] is False
        assert (
            load_scheduler_state(
                fixture["runtime"],
                goal_id=GOAL,
                agent_id=AGENT,
                state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
            )
            == persisted
        )
        assert scheduler_authority_snapshot(fixture) == authority
        events = load_rollout_events(rollout_event_log_path(fixture["runtime"], GOAL))
        assert not {"refresh_state", "quota_spend"}.intersection(
            event["event_kind"] for event in events
        )
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_elapsed_quiet_backoff_reads_host_and_resets_for_native_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from loopx.control_plane.quota.scheduler_ack import record_quota_scheduler_ack_for_decision
    from loopx.control_plane.scheduler import scheduler_hint as hint_module
    from loopx.control_plane.scheduler.execution_context import (
        scheduler_execution_context_for_runtime_profile,
    )
    from tests.control_plane.scheduler_arbitration_fixture import build_native_scheduler_hint

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_scheduler_fixture(tmp_path, provider, "monitor")
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic-thread")
    automation = codex_home / "automations" / "fixture" / "automation.toml"
    _write_host_rrule(automation, 30)
    try:
        # The reset profile includes full monitor facts; a compact display is
        # deliberately insufficient to seed the same historical decision.
        packet = call_scheduler_cli(fixture, *fixture["guard"], "--include-detail", "agent-todos")
        authority = scheduler_authority_snapshot(fixture)
        # Seed only this disposable history through the real commit owner.
        # All subsequent CLI processes use their real clock and host reader.
        now = datetime.now(timezone.utc).replace(microsecond=0)
        state = None
        with monkeypatch.context() as clock:
            for offset, minutes in [(-90, 15), (-60, 30)]:
                at = now + timedelta(minutes=offset)
                clock.setattr(hint_module, "now_utc", lambda: at)
                hint = build_native_scheduler_hint(
                    packet, codex_app_scheduler_state=state,
                    codex_app_current_rrule=f"FREQ=MINUTELY;INTERVAL={minutes}",
                    scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
                        "codex_app_heartbeat"
                    ),
                )
                assert hint["reset_policy"]["reset_token"] == (
                    packet["scheduler_hint"]["reset_policy"]["reset_token"]
                )
                seeded = record_quota_scheduler_ack_for_decision(
                    {"goal_id": GOAL, "scheduler_hint": hint},
                    runtime_root=fixture["runtime"], goal_id=GOAL, agent_id=AGENT,
                    execute=True, applied_rrule=f"FREQ=MINUTELY;INTERVAL={minutes}",
                    generated_at=at.isoformat(),
                )
                assert seeded["ok"] is True, seeded
                state = seeded["scheduler_commit"]["state"]
        assert state["progression_index"] == 1

        guard = [*fixture["guard"], "--turn-instance-id", TURN + "-elapsed"]
        elapsed = call_scheduler_cli(fixture, *guard)
        assert elapsed["interaction_contract"]["user_channel"]["notify"] == "DONT_NOTIFY"
        assert elapsed["interaction_contract"]["agent_channel"]["must_attempt"] is False
        app = elapsed["scheduler_hint"]["app_automation"]
        assert app["recommended_interval_minutes"] == 60
        assert app["stateful_backoff"]["host_observation"]["current_rrule"] == (
            "FREQ=MINUTELY;INTERVAL=30"
        )
        _write_host_rrule(automation, 60)
        ack_args = app["ack_hint"]["cli_args"]
        ack = call_scheduler_cli(fixture, *ack_args)
        assert ack["ok"] is True, ack
        assert ack["scheduler_commit"]["state"]["progression_index"] == 2
        restart_effect_runtime(wait_for_exit=True)
        replay = call_scheduler_cli(fixture, *ack_args)
        assert replay["scheduler_commit"]["replayed"] is True
        assert replay["scheduler_commit"]["written"] is False
        held = call_scheduler_cli(
            fixture, *fixture["guard"], "--turn-instance-id", TURN + "-early",
        )
        assert held["scheduler_hint"]["app_automation"]["apply"] == "none_already_applied"
        assert scheduler_authority_snapshot(fixture) == authority
        events = load_rollout_events(rollout_event_log_path(fixture["runtime"], GOAL))
        assert not {"refresh_state", "quota_spend"}.intersection(
            event["event_kind"] for event in events
        )

        added = call_scheduler_cli(
            fixture, "todo", "add", "--goal-id", GOAL, "--role", "agent",
            "--claimed-by", AGENT, "--text", "Validate the newly available source.",
            "--task-class", "advancement_task", "--action-kind", "validate",
            "--operation-id", "source-became-actionable",
        )
        fresh = call_scheduler_cli(
            fixture, *fixture["guard"], "--turn-instance-id", TURN + "-work",
        )
        assert fresh["selected_todo"]["todo_id"] == added["todo_id"]
        assert fresh["scheduler_hint"]["action"] == "run_now"
        assert fresh["scheduler_hint"]["app_automation"]["recommended_interval_minutes"] == 3
    finally:
        restart_effect_runtime(wait_for_exit=True)
