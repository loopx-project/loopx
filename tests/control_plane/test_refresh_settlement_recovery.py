"""Maintenance and rejected writes cannot conceal an admitted Turn's debt."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from contextlib import chdir, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, ALTERNATIVE_TODO_ID,
    _heartbeat_receipt_count, _initialize_fixture_authority, _run_cli,
    _run_generated_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("profile", ["codex_app_heartbeat", "codex_app_ssh_goal"])
def test_unadmitted_explicit_turn_does_not_report_settlement_debt(tmp_path, monkeypatch, provider, profile):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease", leases=[])
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--runtime-profile", profile,
        *binding, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 0 and guard["execution_preparation"]["state"] == "claim_required", guard
    assert not guard["normal_delivery_allowed"], guard
    receipts = _heartbeat_receipt_count(runtime, TURN_ID)
    refresh = ["refresh-state", *binding, "--classification", "local_upgrade_status_updated",
        "--delivery-outcome", "surface_only", "--no-global-sync", "--suppress-external-sinks"]
    # A bound candidate is not admitted work, even when the caller names it.
    rc, refused = _run_cli(registry, runtime, *refresh, "--todo-id", TODO_ID,
        "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 1 and not refused["appended"], refused
    assert "settlement_recovery" not in refused, refused
    assert "settlement_recovery_error" not in refused, refused
    for turn_args, expected_rc in [([], 0), (["--turn-instance-id", TURN_ID], 1)]:
        rc, maintained = _run_cli(registry, runtime, *refresh, *turn_args, cwd=project)
        # A Turn without its work binding remains a refused refresh.
        assert rc == expected_rc and maintained["appended"] == (expected_rc == 0), maintained
        assert "settlement_recovery" not in maintained, maintained
    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
    assert _spend_run_count(runtime) == 0
    # Claiming and rechecking the same Turn can positively admit its work.
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    rc, claimed = _run_cli(registry, runtime, "todo", "claim", *binding,
        "--claimed-by", AGENT_ID, "--todo-id", TODO_ID, cwd=project)
    assert rc == 0 and claimed["execution_managed"], claimed
    rc, ready = _run_cli(registry, runtime, "quota", "should-run", "--runtime-profile", profile,
        *binding, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 0 and ready["normal_delivery_allowed"], ready
    assert ready["heartbeat_receipt"]["settlement_identity"] == identity
    rc, maintained = _run_cli(registry, runtime, *refresh, cwd=project)
    assert rc == 0 and maintained["settlement_recovery"]["settlement_identity"] == identity, maintained
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("profile,spend_source", [("codex_app_heartbeat", "heartbeat"), ("codex_app_ssh_goal", "visible-goal")])
def test_rejected_write_and_upgrade_state_recover_original_turn_once(tmp_path, monkeypatch, provider, profile, spend_source):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID]
    refresh = ["refresh-state", *binding, "--no-global-sync", "--suppress-external-sinks"]
    maintenance = [*refresh, "--classification", "local_upgrade_status_updated", "--delivery-outcome", "surface_only"]
    rc, setup = _run_cli(registry, runtime, *maintenance, cwd=project)
    assert rc == 0 and setup["ok"] and "settlement_recovery" not in setup, setup
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--runtime-profile", profile, *binding,
        "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 0 and guard["normal_delivery_allowed"], guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    receipts = _heartbeat_receipt_count(runtime, TURN_ID)

    # The original incident omitted --turn-instance-id, then wrote surface_only.
    rc, refused = _run_cli(registry, runtime, *refresh, "--todo-id", TODO_ID,
        "--classification", "verified_delivery", "--delivery-outcome", "outcome_progress", cwd=project)
    assert rc == 1 and not refused["appended"], refused
    assert "turn_instance_id" in refused["error"]
    recovery = refused["settlement_recovery"]
    assert recovery["required"] and recovery["settlement_identity"] == identity
    assert recovery["settlement_progress"]["next_step"] == "durable_writeback"
    assert "settlement_owed" not in recovery
    rc, surface = _run_cli(registry, runtime, *maintenance, cwd=project)
    assert rc == 0 and surface["appended"], surface
    assert surface["settlement_recovery"] == recovery
    assert "settlement_identity" not in surface  # This update is not a Turn writeback.
    assert _spend_run_count(runtime) == 0 and _heartbeat_receipt_count(runtime, TURN_ID) == receipts
    from loopx.state_refresh import render_state_refresh_markdown
    assert TURN_ID in render_state_refresh_markdown(surface)
    assert recovery["next_cli_actions"][0] in render_state_refresh_markdown(surface)

    # A conflicting selection is never silently repaired to a different work item.
    rc, foreign = _run_cli(registry, runtime, *refresh, "--todo-id", ALTERNATIVE_TODO_ID,
        "--turn-instance-id", TURN_ID, "--delivery-outcome", "outcome_progress", cwd=project)
    assert rc == 1 and "settlement_recovery" not in foreign, foreign
    assert "binding" in foreign["settlement_recovery_error"]
    rc, other_agent = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID,
        "--agent-id", "another-agent", "--classification", "other_lane_status", "--delivery-outcome", "surface_only",
        "--no-global-sync", "--suppress-external-sinks", cwd=project)
    assert "settlement_recovery" not in other_agent

    # Execute the emitted recovery, not a hand-constructed replacement identity.
    rc, recovered = _run_generated_cli(recovery["next_cli_actions"][0], registry_path=registry, cwd=project)
    assert rc == 0 and recovered["normal_delivery_allowed"], recovered
    assert recovered["heartbeat_receipt"]["settlement_identity"] == identity
    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
    command = next(action for action in recovered["interaction_contract"]["cli_channel"]["next_cli_actions"]
        if "refresh-state" in shlex.split(action))
    command = command.replace("<validated_progress>", "verified_delivery").replace("<scale>", "implementation").replace("<outcome>", "outcome_progress")
    command += " --format json --no-global-sync --suppress-external-sinks"
    assert "--turn-instance-id" in shlex.split(command), command
    assert "<" not in shlex.join(shlex.split(command)), command
    # Interrupt the generated combined operation between writeback and debit.
    # Maintenance must recover this partial commit without rewriting progress.
    from loopx.cli import main
    from loopx.cli_commands import refresh_settlement
    def unavailable_child(argv, **kwargs):
        assert "spend-slot" in argv and TURN_ID in argv
        raise OSError("fixture interrupt before quota debit")
    stdout = StringIO()
    with monkeypatch.context() as fault, chdir(project), redirect_stdout(stdout):
        fault.setattr(refresh_settlement, "subprocess", SimpleNamespace(
            run=unavailable_child, TimeoutExpired=subprocess.TimeoutExpired))
        rc = main(shlex.split(command)[1:])
    written = json.loads(stdout.getvalue())
    assert rc == 1 and written["settlement_identity"] == identity, written
    assert written["settlement_progress"]["next_step"] == "quota_spend"

    # Maintenance after a committed write offers only the owed debit, no replan.
    rc, after_write = _run_cli(registry, runtime, *maintenance, cwd=project)
    assert rc == 0, after_write
    owed = after_write["settlement_recovery"]["settlement_owed"]
    assert owed["effect_id"] == identity["effect_id"]
    assert after_write["settlement_recovery"]["settlement_progress"]["quota_spend_source"] == spend_source
    spend = after_write["settlement_recovery"]["next_cli_actions"][0]
    assert spend == owed["command"] and "spend-slot" in spend
    for _ in range(2):
        rc, settled = _run_generated_cli(spend, registry_path=registry, cwd=project)
        assert rc == 0 and settled["settlement_result"]["ok"], settled
        assert {receipt["step_kind"] for receipt in settled["settlement_result"]["receipts"]} >= {"durable_writeback", "quota_spend"}
        assert _spend_run_count(runtime) == 1
    rc, after_settlement = _run_cli(registry, runtime, *maintenance, cwd=project)
    assert rc == 0 and "settlement_recovery" not in after_settlement, after_settlement


def test_unavailable_recovery_read_preserves_committed_state_result(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime)
    from loopx.cli import main
    from loopx.cli_commands import refresh_recovery
    def unavailable_read(*args, **kwargs):
        raise RuntimeError("fixture closeout reader unavailable")
    monkeypatch.setattr(refresh_recovery, "read_heartbeat_settlement", unavailable_read)
    stdout = StringIO()
    with chdir(project), redirect_stdout(stdout):
        rc = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--classification", "local_upgrade_status_updated", "--delivery-outcome", "surface_only",
            "--no-global-sync", "--suppress-external-sinks"])
    result = json.loads(stdout.getvalue())
    assert rc == 0 and result["appended"], result
    assert result["settlement_recovery_error"] == "fixture closeout reader unavailable"
    assert "settlement_recovery" not in result and _spend_run_count(runtime) == 0
    assert json.loads(Path(result["json_path"]).read_text())["classification"] == "local_upgrade_status_updated"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_returned_recovery_command_runs_verbatim_in_powershell(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path / "owner's workspace")
    _initialize_fixture_authority(project, runtime, provider=provider)
    checkout = Path(__file__).resolve().parents[2]
    launcher = tmp_path / "pinned release" / "loopx.ps1"
    launcher.parent.mkdir()
    shutil.copyfile(checkout / "scripts/loopx.ps1", launcher)
    launcher.with_name("loopx-current-release.json").write_text(json.dumps({
        "release_root": str(checkout), "python": sys.executable,
    }), encoding="utf-8")
    environment = {**os.environ, "LOOPX_USAGE_PING": "0"}
    environment.pop("LOOPX_CURRENT_RELEASE_FILE", None)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID]
    rc, admitted = _run_cli(registry, runtime, "quota", "should-run", "--runtime-profile",
        "codex_app_heartbeat", *binding, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
        cwd=project)
    assert rc == 0 and admitted["normal_delivery_allowed"], admitted
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    guard_count = _heartbeat_receipt_count(runtime, TURN_ID)
    refresh = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--cli-bin", str(launcher),
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", "refresh-state",
        *binding, "--classification", "local_upgrade_status_updated", "--delivery-outcome", "surface_only",
        "--no-global-sync", "--suppress-external-sinks"], cwd=project, env=environment, text=True,
        capture_output=True, timeout=60)
    assert refresh.returncode == 0, refresh.stderr
    recovery = json.loads(refresh.stdout)["settlement_recovery"]
    assert recovery["settlement_identity"] == identity
    command, = recovery["next_cli_actions"]
    def run_returned(command):
        answer = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive",
            "-Command", command], cwd=project, env=environment, text=True,
            capture_output=True, timeout=60)
        assert answer.returncode == 0, answer.stderr
        return json.loads(answer.stdout)

    recovered = run_returned(command)
    if recovered.get("schema_version") == "context_capture_navigation_v1":
        page = run_returned(recovered["recovery"]["command"])
        digest = page["sha256"]
        raw = bytearray()
        while True:
            assert page["ok"] and page["sha256"] == digest and page["offset"] == len(raw)
            raw.extend(page["content"].encode())
            if page["eof"]:
                assert len(raw) == page["total_bytes"]
                break
            page = run_returned(page["next_command"])
        recovered = json.loads(raw)
    assert recovered["heartbeat_receipt"]["settlement_identity"] == identity
    assert _heartbeat_receipt_count(runtime, TURN_ID) == guard_count
    assert _spend_run_count(runtime) == 0
