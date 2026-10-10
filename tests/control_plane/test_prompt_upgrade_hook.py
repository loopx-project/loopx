"""Deferred upgrade -> conditional read -> quiet, using isolated real host stores."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import shlex
import sqlite3
import subprocess
import sys

import pytest

from loopx.control_plane.heartbeat import automation_upgrade as upgrade
from loopx.control_plane.heartbeat import installed_prompt_update as lifecycle
from loopx.control_plane.heartbeat import prompt_upgrade_hook as hooks
from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from test_automation_prompt_upgrade import fixture, _set_fixture_prompt


def _deferred(tmp_path, monkeypatch, *, cli_bin="loopx", explicit_root=True):
    home, path, database, registry, _ = fixture(tmp_path)
    registration = json.loads(registry.read_text())
    registration["goals"][0]["coordination"] = {"registered_agents": ["agent-a"]}
    registry.write_text(json.dumps(registration))
    root = tmp_path / "runtime"
    root_arg = str(root) if explicit_root else None
    desired = upgrade.bootstrap_prompt(registry=registry, goal_id="fixture-goal",
        agent_id="agent-a", runtime_root=root_arg, cli_bin=cli_bin)
    legacy = desired.replace(upgrade.BOOTSTRAP, upgrade._LEGACY_BOOTSTRAP, 1).removesuffix(
        upgrade.BOOTSTRAP_INSTRUCTION) + upgrade._LEGACY_INSTRUCTION
    _set_fixture_prompt(path, database, legacy)
    before = lifecycle.snapshot(registry=registry, home=home, runtime_root=root_arg, cli_bin=cli_bin)
    def unavailable():
        raise ValueError("host is running")
    monkeypatch.setattr(lifecycle, "require_closed_app", unavailable)
    monkeypatch.setenv("CODEX_HOME", str(home))
    result = lifecycle.reconcile(before=before, registry=registry, home=home,
        runtime_root=root_arg, cli_bin=cli_bin)
    assert result["results"] == [{"automation_id": "watch", "status": "deferred", "reason": "host is running"}]
    return home, path, database, registry, root, legacy, desired, before


def _reads(registry, root, *, agent_id="agent-a", dispatch=None):
    return hooks.extend_prompt_upgrade_reads(dispatch, registry=registry, runtime_root=root,
        goal_id="fixture-goal", agent_id=agent_id)


@pytest.mark.parametrize("explicit_root", [False, True])
def test_pending_read_uses_fresh_real_cli_plan_and_stops_after_adoption(tmp_path, monkeypatch, explicit_root):
    home, path, database, registry, root, legacy, desired, before = _deferred(
        tmp_path, monkeypatch, explicit_root=explicit_root)
    receipt = hooks.receipt_path(root, registry, home)
    contents = receipt.read_bytes()
    assert legacy not in contents.decode() and desired not in contents.decode()
    assert "api_update_request" not in contents.decode()
    assert receipt.stat().st_mode & 0o077 == 0
    host_before = path.read_bytes(), database.read_bytes()
    hint = _reads(registry, root)["required_reads"][0]
    assert hint["source"] == "turn_start_capability_hook"
    command = shlex.split(hint["command"])
    assert command[command.index("--automation-id") + 1] == "watch"
    result = subprocess.run([sys.executable, "-m", "loopx.cli", *command[1:]],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["entries"][0]["desired_prompt"] == desired
    assert (path.read_bytes(), database.read_bytes()) == host_before
    assert receipt.read_bytes() == contents
    # Both host mirrors acknowledge adoption; no receipt mutation/ACK is needed.
    _set_fixture_prompt(path, database, desired)
    existing = {"required_reads": [{"kind": "existing"}]}
    assert _reads(registry, root, dispatch=existing) is existing
    assert receipt.read_bytes() == contents
    lifecycle.reconcile(before=before, registry=registry, home=home,
        runtime_root=str(root) if explicit_root else None)
    assert not receipt.exists()


@pytest.mark.parametrize("change", ["prompt", "thread", "divergent", "deleted", "missing", "ambiguous", "schema", "invalid_id"])
def test_stale_ambiguous_or_unreadable_receipts_cannot_inject_a_repair(tmp_path, monkeypatch, change):
    home, path, database, registry, root, legacy, _, _ = _deferred(tmp_path, monkeypatch)
    if change == "prompt":
        _set_fixture_prompt(path, database, legacy + "\nOwner customization")
    elif change in {"thread", "deleted"}:
        field, value = ("target_thread_id", "thread-b") if change == "thread" else ("status", "DELETED")
        old = "thread-a" if change == "thread" else "PAUSED"
        path.write_text(path.read_text().replace(f'{field} = "{old}"', f'{field} = "{value}"'))
        with sqlite3.connect(database) as connection:
            connection.execute(f"UPDATE automations SET {field}=?", (value,))
    elif change == "divergent":
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE automations SET prompt='different'")
    elif change == "missing":
        path.unlink()
    else:
        receipt = hooks.receipt_path(root, registry, home)
        payload = json.loads(receipt.read_text())
        if change == "schema":
            payload["schema_version"] = "unknown"
        else:
            payload["entries"]["other" if change == "ambiguous" else "../outside"] = payload["entries"]["watch"]
            if change == "invalid_id":
                del payload["entries"]["watch"]
        receipt.write_text(json.dumps(payload))
    existing = {"required_reads": [{"kind": "existing"}]}
    assert _reads(registry, root, dispatch=existing) is existing


def test_scope_is_exact_and_schedule_changes_do_not_hide_pending_prompt(tmp_path, monkeypatch):
    home, path, database, registry, root, _, _, _ = _deferred(tmp_path, monkeypatch, cli_bin="loopx-canary")
    assert _reads(registry, root, agent_id="other") is None
    assert _reads(registry.with_name("other.json"), root) is None
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other-host"))
    assert _reads(registry, root) is None
    monkeypatch.setenv("CODEX_HOME", str(home))
    path.write_text(path.read_text().replace("FREQ=HOURLY", "FREQ=DAILY"))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE automations SET rrule='FREQ=DAILY'")
    hint = _reads(registry, root)["required_reads"][0]
    command = shlex.split(hint["command"])
    assert command[0] == "loopx-canary"
    assert command[command.index("--cli-bin") + 1] == "loopx-canary"
    assert "FREQ=" not in hint["command"]


def test_partial_reconciliation_preserves_other_pending_entries(tmp_path, monkeypatch):
    home, _, _, registry, root, _, _, before = _deferred(tmp_path, monkeypatch)
    entries = {"other": {**before["entries"][0], "agent_id": "agent-b"}}
    hooks.record_deferred_upgrades(registry=registry, home=home, runtime_root=str(root),
        cli_bin="loopx", entries=entries, results=[{"automation_id": "other", "status": "deferred"}])
    hooks.record_deferred_upgrades(registry=registry, home=home, runtime_root=str(root),
        cli_bin="loopx", entries={}, results=[{"automation_id": "watch", "status": "current"}])
    assert set(json.loads(hooks.receipt_path(root, registry, home).read_text())["entries"]) == {"other"}


@pytest.mark.parametrize("route_source", ["quota_cli_invocation", "loopx_turn_run_once"])
def test_upgrade_read_projection_preserves_work_authority(tmp_path, monkeypatch, route_source):
    home, path, database, registry, root, _, desired, _ = _deferred(tmp_path, monkeypatch)
    monkeypatch.setattr("loopx.control_plane.scheduler.scheduler_hint.now_utc",
        lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    status = quota_status_payload(goal_id="fixture-goal", status="active", recommended_action="Advance the selected work",
        coordination={"registered_agents": ["agent-a"]},
        agent_todo_items=[{"todo_id": "todo_ordinary_work", "index": 1,
            "text": "[P1] Advance the selected work", "role": "agent", "status": "open",
            "priority": "P1", "task_class": "advancement_task", "claimed_by": "agent-a"}])
    kwargs = dict(goal_id="fixture-goal", agent_id="agent-a", available_capabilities=["shell"],
        include_scheduler_detail=False, codex_app_current_rrule="FREQ=HOURLY",
        registry_path=registry, runtime_root=root, route_source=route_source,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile("codex_app_heartbeat"))
    receipt = hooks.receipt_path(root, registry, home)
    contents = receipt.read_bytes()
    receipt.unlink()
    baseline = build_live_quota_should_run_decision(status, **kwargs)
    receipt.write_bytes(contents)
    pending = build_live_quota_should_run_decision(status, **kwargs)
    assert "turn_start_capability_hook_dispatch" not in baseline
    assert "required_reads" not in pending
    reads = pending["interaction_contract"]["agent_channel"]["required_reads"]
    hint = next(x for x in reads if x.get("kind") == "automation_prompt_upgrade")
    dispatch = pending["turn_start_capability_hook_dispatch"]
    assert set(dispatch) == {"required_reads"}
    assert len(dispatch["required_reads"]) == 1
    assert dispatch["required_reads"][0]["command"] == hint["command"]
    assert hint["source"] == "turn_start_capability_hook"
    assert len(hint["command"]) > 360
    assert hint in compact_quota_should_run_cli_payload(pending)["interaction_contract"]["agent_channel"]["required_reads"]
    envelope = build_turn_envelope(pending)
    assert hint in envelope["required_reads"]
    assert envelope["compaction"]["budget_bytes"] == 8192 + 1536
    assert envelope["compaction"]["hook_prompt_budget_bytes"] == 1536
    assert build_turn_envelope(baseline)["compaction"]["budget_bytes"] == 8192
    assert baseline.get("turn_start_capability_hook_dispatch") is None
    dispatch = pending["turn_start_capability_hook_dispatch"]
    assert set(dispatch) == {"required_reads"}
    assert len(dispatch["required_reads"]) == 1
    read = dispatch["required_reads"][0]
    assert read["kind"] == hint["kind"] == "automation_prompt_upgrade"
    assert read["command"] == hint["command"]
    assert read["ordering"] == "before_work"
    assert read["prompt_budget_bytes"] == 1536
    # Rebuilding for a required read must not lose the selected authority.
    for command in pending["interaction_contract"]["cli_channel"]["next_cli_actions"]:
        if command.startswith("loopx "):
            argv = shlex.split(command)
            assert argv[argv.index("--registry") + 1] == str(registry)
            assert argv[argv.index("--runtime-root") + 1] == str(root)
    # The dispatch names the hook that produced the read, and the projected hint
    # must still carry every field of that read unchanged.
    assert read["hook_id"] == "heartbeat.prompt_upgrade"
    assert read["capability_id"] == "automation-prompt-upgrade"
    assert {key: read[key] for key in hint} == hint
    for key in baseline.keys() | pending.keys():
        if key not in {
            "required_reads",
            "interaction_contract",
            "protocol_action_packet",
            "turn_start_capability_hook_dispatch",
        }:
            assert pending.get(key) == baseline.get(key), key
    _set_fixture_prompt(path, database, desired)
    assert build_live_quota_should_run_decision(status, **kwargs) == baseline
    # Other hosts never load the Codex receipt hook at all.
    monkeypatch.setattr(hooks, "extend_prompt_upgrade_reads", lambda *a, **k: pytest.fail("unexpected Codex hook"))
    for profile in ("generic_cli", "codex_cli", "trae_app"):
        kwargs["scheduler_execution_context"] = scheduler_execution_context_for_runtime_profile(profile)
        build_live_quota_should_run_decision(status, **kwargs)


def test_healthy_lane_does_not_dispatch_or_read_host_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))
    monkeypatch.setattr(hooks, "dispatch_turn_start_hooks", lambda *a: pytest.fail("healthy lane dispatched"))
    assert _reads(tmp_path / "registry.json", tmp_path / "runtime") is None
    assert not list(tmp_path.iterdir())


def test_real_quota_cli_exposes_hint_without_bypassing_health_gate(tmp_path, monkeypatch):
    _, path, database, registry, root, _, desired, _ = _deferred(tmp_path, monkeypatch)
    # This minimal lifecycle fixture deliberately lacks a healthy active state.
    command = [sys.executable, "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry), "--runtime-root", str(root),
        "quota", "should-run", "--goal-id", "fixture-goal", "--agent-id", "agent-a",
        "--codex-app", "--scan-path", str(tmp_path / "STATE.md")]
    pending_result = subprocess.run(command, capture_output=True, text=True)
    pending = json.loads(pending_result.stdout)
    assert any(x.get("kind") == "automation_prompt_upgrade"
        for x in pending["interaction_contract"]["agent_channel"]["required_reads"])
    assert pending["should_run"] is False
    _set_fixture_prompt(path, database, desired)
    current_result = subprocess.run(command, capture_output=True, text=True)
    current = json.loads(current_result.stdout)
    assert not current.get("required_reads")
    assert current_result.returncode == pending_result.returncode == 1
    for key in ("should_run", "decision", "reason", "state"):
        assert current[key] == pending[key]
