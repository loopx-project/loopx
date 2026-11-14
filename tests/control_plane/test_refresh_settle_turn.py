"""The combined CLI retains exact-Turn authority, recovery and task freedom."""
from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, REPO_ROOT, TODO_ID, TURN_ID, _heartbeat_receipt_count, _initialize_fixture_authority,
    _run_cli, _run_generated_cli, _spend_run_count, _write_fixture,
)


def _ready_settlement_guard(tmp_path, monkeypatch, project, runtime, registry, progressive, *, claim_after_guard,
                            complete=False):
    from test_codex_context_lifecycle import setup

    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    setup(journal, monkeypatch)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID]
    host = ["--codex-app"]
    if complete:
        host = ["--runtime-profile", "generic_cli", "--complete-turn"]
        monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
        monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
        monkeypatch.setenv("LOOPX_PHASE_DEADLINE_EPOCH", "9876543210.125")

    def guard(directory):
        rc, nav = _run_cli(registry, runtime, "quota", "should-run", *host, *binding,
            "--turn-instance-id", TURN_ID, "--context-delivery", progressive, "--decision-output-dir", str(directory),
            "--codex-context-dir", str(journal), cwd=project)
        assert rc == 0 and nav["ok"], nav
        saved = json.loads((directory / "decision.json").read_bytes())
        if complete:
            from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision

            assert saved["schema_version"] == "loopx_turn_envelope_v1"
            assert_complete_turn_decision(saved)
            assert "9876543210.125" not in json.dumps(saved)
            assert saved["goal_boundary"]["stop_condition"]
        return nav, saved

    if claim_after_guard:
        _, before = guard(tmp_path / "before-claim")
        cli = before["interaction_contract"]["cli_channel"]
        assert cli["execution_preparation"]["state"] == "claim_required"
        assert not before["normal_delivery_allowed"]
        assert (cli.get("settlement_plan") or {}).get("schema_version") != "quota_settlement_plan_v2"
        assert before["heartbeat_receipt"]["turn_instance_id"] == TURN_ID

    rc, claimed = _run_cli(registry, runtime, "todo", "claim", *binding, "--claimed-by", AGENT_ID, cwd=project)
    assert rc == 0, claimed
    nav, original = guard(tmp_path / "capture")
    if claim_after_guard:
        receipt = original["heartbeat_receipt"]
        assert receipt["status"] == "upgraded" and receipt["closeout_required"]
        assert receipt["turn_instance_id"] == TURN_ID and receipt.get("settlement_binding_owed") is not True
    return nav, original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("case", ["combined", "after_claim", "after_claim_off", "interrupted"])
@pytest.mark.parametrize("complete", [False, True])
def test_ready_native_guard_publishes_a_runnable_settlement_plan(tmp_path, monkeypatch, provider, case, complete):
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    off = case == "after_claim_off"
    guard, original = _ready_settlement_guard(tmp_path, monkeypatch, project, runtime, registry,
        "off" if off else "on", claim_after_guard=case.startswith("after_claim"), complete=complete)
    cli = original["interaction_contract"]["cli_channel"]
    plan = cli["settlement_plan"]
    identity = original["heartbeat_receipt"]["settlement_identity"]
    assert plan["identity"] == identity
    assert cli["execution_preparation"]["state"] == "ready"
    validate, writeback, spend, terminal = plan["ordered_steps"]
    assert validate["command_condition"] == "todo_deliverable_complete"
    assert terminal["conditional"] is True and terminal["owner"] == "agent"
    if not off or complete:
        assert plan["schema_version"] == "quota_settlement_plan_v2"
        assert "--settle-turn --settlement-receipt" in writeback["semantic_closeout_command_template"]
        assert spend["owner"] == "runtime" and "command_template" not in spend
        assert cli["next_cli_actions"] == [writeback["command_template"]]
        if not off:
            _, recovery = read_context_read_reference(guard["recovery"]["reference_file"])
            projected = json.loads(Path(recovery["path"]).read_bytes())
            assert projected["interaction_contract"]["cli_channel"]["settlement_plan"] == plan
        if case in {"combined", "after_claim"}:
            from tests.control_plane.codex_checkpoint_fixture import selected_context_values

            command, pages = guard["first_read_command"], []
            while command:
                words = shlex.split(command)
                rc, page = _run_cli(registry, runtime, *words[words.index("quota"):], cwd=project)
                assert rc == 0 and page["ok"] and page["offset"] == sum(len(body.encode()) for body in pages)
                pages.append(page["content"])
                command = page.get("next_command")
            values = selected_context_values("".join(pages))
            cli_path = "/interaction_contract/cli_channel"
            assert cli_path + "/next_cli_actions" not in values
            assert values[cli_path + "/settlement_plan/ordered_steps/1/command_template"] == writeback["command_template"]
    else:
        assert plan["schema_version"] == "quota_settlement_plan_v1"
        assert "--settle-turn" not in writeback["semantic_closeout_command_template"]
        assert cli["next_cli_actions"] == [writeback["command_template"], spend["command_template"]]
        assert spend["owner"] == "agent"

    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Verified an intermediate result. The original work remains open."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The scoped result needed verification.",
                       "observed_reality": "The independently checked result advances the current route.",
                       "retained": ["Original requirements"], "evidence_refs": ["evidence:validation"]},
    }))
    command = writeback["semantic_closeout_command_template"]
    for key, value in {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<file>": shlex.quote(str(vision))}.items():
        command = command.replace(key, value)
    command += " --no-global-sync --suppress-external-sinks"
    if case == "interrupted":
        from contextlib import chdir, redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        from loopx.cli import main
        from loopx.cli_commands import refresh_settlement

        def fail_child(argv, **kwargs):
            assert "spend-slot" in argv and identity["turn_instance_id"] in argv
            raise OSError("fixture child unavailable before debit")

        stdout = StringIO()
        with monkeypatch.context() as fault, chdir(project), redirect_stdout(stdout):
            fault.setattr(refresh_settlement, "subprocess", SimpleNamespace(
                run=fail_child, TimeoutExpired=subprocess.TimeoutExpired))
            assert main(shlex.split(command)[1:]) != 0
        failed = json.loads(stdout.getvalue())
        assert failed["appended"] and failed["settlement_identity"] == identity, failed
        assert failed["settlement_progress"]["state"] == "spend_required" and _spend_run_count(runtime) == 0
        owed = failed["settlement_owed"]
        assert owed["effect_id"] == identity["effect_id"] and owed["kind"] == "quota_spend"
        rc, result = _run_generated_cli(owed["command"], registry_path=registry, cwd=project)
    else:
        rc, result = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and result["ok"] and result["settlement_identity"] == identity, result
    if off and not complete:
        assert result["settlement_owed"]["kind"] == "quota_spend" and _spend_run_count(runtime) == 0
        rc, result = _run_generated_cli(spend["command_template"], registry_path=registry, cwd=project)
        assert rc == 0 and result["ok"], result
    assert result["settlement_progress"]["state"] == "settled"
    assert result["settlement_identity"] == identity
    # Claim re-entry appends the readiness upgrade under the original Turn.
    expected_admission_events = 2 if case.startswith("after_claim") else 1
    assert _heartbeat_receipt_count(runtime, TURN_ID) == expected_admission_events
    assert _spend_run_count(runtime) == 1
    rc, replay = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and replay["settlement_progress"]["state"] == "settled", replay
    assert replay["idempotent_replay"] and _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_receipt_capture_failure_keeps_committed_receipts_and_exact_retry(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    private = runtime / "context-delivery"
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o755)
    command = ["refresh-state", *binding, "--classification", "verified_receipt_recovery",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
        "--settle-turn", "--settlement-receipt"]
    rc, failed = _run_cli(registry, runtime, *command, cwd=project)
    assert rc != 0 and failed["ok"] is False
    assert failed["settlement_progress"]["state"] == "settled"
    assert len(failed["settlement_result"]["receipts"]) == 3
    assert _spend_run_count(runtime) == 1
    private.chmod(0o700)
    rc, replay = _run_cli(registry, runtime, *command, cwd=project)
    assert rc == 0, replay
    assert replay["settlement_identity"] == failed["settlement_identity"]
    assert len(replay["settlement_result"]["receipts"]) == 3
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_complete_turn_composition_failure_preserves_original_guard_and_recovers(tmp_path, monkeypatch, capsys, provider):
    import hashlib
    from loopx.cli import main
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
    from loopx.control_plane.quota import turn_envelope

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID]
    assert _run_cli(registry, runtime, "todo", "claim", *binding, "--claimed-by", AGENT_ID, cwd=project)[0] == 0
    command = ["quota", "should-run", *binding, "--turn-instance-id", TURN_ID,
        "--runtime-profile", "generic_cli", "--complete-turn", "--context-delivery", "off"]
    directory = tmp_path / "failed-capture"
    def unavailable(*args, **kwargs):
        raise EffectRuntimeStartupError("synthetic complete-context transport unavailable", diagnostic_code="fixture_unavailable")
    with monkeypatch.context() as fault:
        fault.chdir(project)
        fault.setattr(turn_envelope, "compose_turn_decision", unavailable)
        assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            *command, "--decision-output-dir", str(directory)]) == 1
        failed = json.loads(capsys.readouterr().out)
    saved = directory / "decision.json"
    original = saved.read_bytes()
    original_packet = json.loads(original)
    assert original_packet.get("schema_version") is None
    assert failed["heartbeat_receipt"]["settlement_identity"] == original_packet["heartbeat_receipt"]["settlement_identity"]
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0
    recovered_bytes, page_count = b"", 0
    while True:
        rc, page = _run_cli(registry, runtime, "quota", "context-read", "--capture-file", str(saved),
            "--capture-sha256", hashlib.sha256(original).hexdigest(), "--page-bytes", "8000",
            "--max-page-output-bytes", "12000", "--offset", str(len(recovered_bytes)), cwd=project)
        assert rc == 0 and page["ok"], page
        assert page["offset"] == len(recovered_bytes)
        recovered_bytes += page["content"].encode()
        page_count += 1
        if page["eof"]:
            break
    assert page_count > 1 and recovered_bytes == original
    rc, recovered = _run_cli(registry, runtime, *command, "--decision-output-dir", str(tmp_path / "recovered"), cwd=project)
    assert rc == 0 and recovered["schema_version"] == "loopx_turn_envelope_v1", recovered
    assert recovered["heartbeat_receipt"]["settlement_identity"] == failed["heartbeat_receipt"]["settlement_identity"]
    assert saved.read_bytes() == original
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("command", ["status", "context-read", "spend-slot"])
def test_complete_turn_rejects_other_routes_before_authority_io(tmp_path, command):
    registry, runtime = tmp_path / "absent.json", tmp_path / "absent-runtime"
    rc, failed = _run_cli(registry, runtime, "quota", command, "--complete-turn")
    assert rc == 1 and failed["reason"] == "--complete-turn requires JSON quota should-run"
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_explicit_complete_soft_claim_reuses_the_original_legacy_receipt(tmp_path, provider):
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="soft_claim")
    command = ["quota", "should-run", "--runtime-profile", "generic_cli", "--context-delivery", "off",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, legacy = _run_cli(registry, runtime, *command, cwd=project)
    assert rc == 0 and legacy.get("schema_version") is None
    original_cli = legacy["interaction_contract"]["cli_channel"]
    assert original_cli["settlement_plan"]["schema_version"] == "quota_settlement_plan_v1"
    rc, complete = _run_cli(registry, runtime, *command, "--complete-turn", cwd=project)
    assert rc == 0 and complete["schema_version"] == "loopx_turn_envelope_v1", complete
    cli = complete["interaction_contract"]["cli_channel"]
    assert "execution_preparation" not in cli  # A soft claim never becomes a fabricated ready lease.
    assert cli["settlement_plan"]["schema_version"] == "quota_settlement_plan_v2"
    assert cli["settlement_plan"]["identity"] == legacy["heartbeat_receipt"]["settlement_identity"]
    assert complete["goal_boundary"] == legacy["goal_boundary"]
    assert complete["normal_delivery_allowed"] == legacy["normal_delivery_allowed"]
    assert len(cli["next_cli_actions"]) == 1
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0
    assert original_cli["settlement_plan"]["schema_version"] == "quota_settlement_plan_v1"


def _read_selected_result(command):
    bodies = []
    while command:
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=False)
        page = json.loads(result.stdout)
        assert result.returncode == 0 and len(result.stdout.encode()) <= 64000, page
        bodies.append(page["content"])
        command = page.get("next_command")
    return {row["pointer"]: row["value"] for row in json.loads("".join(bodies))["selections"]}


def test_receipt_keeps_unknown_controls_and_never_hides_partial_failure(tmp_path):
    from loopx.cli_commands.refresh_settlement import present_settled_refresh

    payload = {"ok": True, "settlement_progress": {"state": "settled"},
        "state": {"large_supporting_snapshot": "x" * 3000},
        "future_control": {"required_resolution": "preserve exactly"},
        "projection_outbox": {"status": "pending", "next_action": "retry"}}
    receipt = present_settled_refresh(payload, runtime_root=tmp_path)
    assert receipt["future_control"] == payload["future_control"]
    assert receipt["projection_outbox"] == payload["projection_outbox"]
    for update in ({"ok": False}, {"dry_run": True}, {"settlement_owed": {"command": "original"}},
                   {"settlement_progress": {"state": "spend_required"}}):
        original = {**payload, **update}
        assert present_settled_refresh(original, runtime_root=tmp_path) is original
    large = {**payload, "future_control": {"required_resolution": "x" * 100000}}
    bounded = present_settled_refresh(large, runtime_root=tmp_path)
    assert "Read the complete selected result controls before acting" in bounded["instruction"]
    assert "Supporting snapshots" in bounded["instruction"]
    selected = _read_selected_result(bounded["command"])
    assert selected["/future_control"] == large["future_control"]


def test_wide_failed_spend_keeps_nested_reader_and_parent_controls(tmp_path):
    from loopx.cli_commands.quota_capture import budgeted_settlement_result
    from loopx.cli_commands.refresh_settlement import present_settled_refresh

    child = {"ok": False, "appended": False, "future_recovery_control": {"detail": "step " * 15000}}
    child_view = budgeted_settlement_result(child, runtime_root=tmp_path, output_bytes=64000,
        reader_argv=[sys.executable, "-m", "loopx.entrypoint"])
    parent = {"ok": False, "error": "Original refusal", "quota_spend": child_view,
        "settlement_identity": {"effect_id": "original-effect"},
        "settlement_owed": {"command": "original owed command"},
        "another_control": "detail " * 10000}
    bounded = present_settled_refresh(parent, runtime_root=tmp_path)
    assert bounded["ok"] is False and "Read the complete selected result controls" in bounded["instruction"]
    selected = _read_selected_result(bounded["command"])
    assert selected["/settlement_identity"] == parent["settlement_identity"]
    assert selected["/settlement_owed"] == parent["settlement_owed"]
    assert selected["/quota_spend"] == child_view
    child_controls = _read_selected_result(selected["/quota_spend"]["command"])
    assert child_controls["/future_recovery_control"] == child["future_recovery_control"]

    (tmp_path / "context-delivery").chmod(0o755)
    fallback = present_settled_refresh(parent, runtime_root=tmp_path)
    assert fallback.pop("result_delivery_error")["error_code"] == "quota_decision_transport_failed"
    assert fallback == parent


def _repository_delivery(tmp_path, provider):
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "action_kind=validate", "action_kind=validate task_repository=git:github.com/example/delivery"))
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "remote", "add", "origin",
                    "https://github.com/example/delivery.git"], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
    delivery = tmp_path / "producing workspace"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "--detach", str(delivery)],
                   check=True, capture_output=True)
    # The delivered project may itself contain a different LoopX implementation.
    # Changing the accounting cwd must not execute that implementation.
    trap = delivery / "loopx"
    trap.mkdir()
    (trap / "__init__.py").write_text("raise RuntimeError('wrong implementation selected')\n")
    _initialize_fixture_authority(project, runtime, provider=provider)
    return project, runtime, registry, delivery


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_combined_settlement_uses_producing_worktree_and_originating_cli(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _delivery = _repository_delivery(tmp_path, provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    identity = guard["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
    refresh = ["refresh-state", *binding, "--classification", "verified_worktree",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
               "--delivery-workspace-path", "../producing workspace", "--settle-turn"]
    for replay in (False, True):
        rc, result = _run_cli(registry, runtime, *refresh, cwd=project)
        assert rc == 0, result
        assert result["settlement_progress"]["state"] == "settled"
        assert result["settlement_identity"] == identity
        assert result["delivery_workspace"]["workspace_kind"] == "independent_git_worktree"
        assert _spend_run_count(runtime) == 1
        assert result.get("idempotent_replay", False) is replay


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_wrong_or_missing_child_workspace_retains_owed_command_for_exact_recovery(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, delivery = _repository_delivery(tmp_path, provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    refresh = ["refresh-state", *binding, "--classification", "verified_worktree",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks"]
    producing = [*refresh, "--delivery-workspace-path", str(delivery)]
    rc, original = _run_cli(registry, runtime, *producing, cwd=project)
    assert rc == 0 and _spend_run_count(runtime) == 0, original
    for workspace in (project, tmp_path / "missing"):
        rc, failed = _run_cli(registry, runtime, *refresh, "--delivery-workspace-path", str(workspace),
            "--settle-turn", "--settlement-receipt", cwd=project)
        assert rc != 0 and failed["ok"] is False, failed
        assert failed["settlement_progress"]["state"] == "spend_required"
        assert failed["settlement_identity"] == original["settlement_identity"]
        assert failed["settlement_owed"]["command"] == original["settlement_owed"]["command"]
        assert _spend_run_count(runtime) == 0
        if workspace == project:
            child = failed["quota_spend"]
            assert child["ok"] is False and child["appended"] is False
            workspace_guard = child["workspace_guard"]
            assert workspace_guard["action"] == "return_to_delivery_worktree"
            assert workspace_guard["current_workspace"] == "canonical_checkout"
            assert workspace_guard["blocks_delivery"] is True
            assert "workspace that produced" in workspace_guard["required_action"]
            assert child["before"]["selected_todo"]["todo_id"] == TODO_ID and child["after"] is None
    rc, recovered = _run_cli(registry, runtime, *producing, "--settle-turn", cwd=project)
    assert rc == 0, recovered
    assert recovered["settlement_progress"]["state"] == "settled"
    assert recovered["settlement_identity"] == original["settlement_identity"]
    assert _spend_run_count(runtime) == 1


@pytest.mark.skipif(os.name != "nt", reason="Exercises the native Windows PowerShell launcher")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_legacy_posix_quoted_powershell_launcher_fails_before_settlement(
    tmp_path, monkeypatch, provider,
):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    marker = tmp_path / "launcher-started.txt"
    launcher = tmp_path / "candidate's launcher" / "loopx.ps1"
    launcher.parent.mkdir()
    python = sys.executable.replace("'", "''")
    marker_arg = str(marker).replace("'", "''")
    launcher.write_text(
        f"Set-Content -LiteralPath '{marker_arg}' -Value started\n"
        f"& '{python}' -m loopx.entrypoint @args\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    prefix = ["-File", str(launcher), "--cli-bin", str(launcher), "--format", "json",
              "--registry", str(registry), "--runtime-root", str(runtime)]
    refresh = ["refresh-state", *binding, "--classification", "verified",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--delivery-boundary", "in_flight_continuation", "--no-global-sync",
               "--suppress-external-sinks"]
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    prepared = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", *prefix, *refresh], cwd=project,
        env=environment, capture_output=True, timeout=60,
    )
    assert prepared.returncode == 0, (prepared.stdout, prepared.stderr)
    owed = json.loads(prepared.stdout)["settlement_owed"]
    assert marker.exists() and _spend_run_count(runtime) == 0
    marker.unlink()

    # Immutable baseline oracle: this is the exact POSIX rendering used by the
    # parent commit before host-aware PowerShell rendering was introduced.
    legacy_command = shlex.join(owed["command_argv"])
    legacy = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", legacy_command],
        cwd=project, env=environment, capture_output=True, timeout=60,
    )
    assert legacy.returncode != 0, (legacy.stdout, legacy.stderr)
    assert not marker.exists() and _spend_run_count(runtime) == 0

    repaired = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", owed["command"]],
        cwd=project, env=environment, capture_output=True, timeout=60,
    )
    assert repaired.returncode == 0, (repaired.stdout, repaired.stderr)
    result = json.loads(repaired.stdout)
    assert marker.exists()
    assert result["settlement_progress"]["state"] == "settled"
    assert result["settlement_identity"] == identity
    assert _spend_run_count(runtime) == 1


@pytest.mark.skipif(os.name != "nt", reason="Exercises the native Windows PowerShell launcher")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("relative_launcher", [False, True], ids=["quoted-launcher", "relative-launcher"])
def test_pinned_powershell_launcher_settles_original_turn_once(
    tmp_path, monkeypatch, provider, composed, relative_launcher,
):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    if relative_launcher:
        # A later argument needs quoting even though the executable does not.
        registry = registry.rename(registry.with_name("operator's O\u2019Neil registry.json"))
        launcher_arg = "./bin/loopx.ps1"
        launcher = project / launcher_arg
    else:
        launcher = tmp_path / "candidate's O\u2019Neil launcher" / "loopx.ps1"
        launcher_arg = str(launcher)
    _initialize_fixture_authority(project, runtime, provider=provider)
    launcher.parent.mkdir()
    shutil.copyfile(REPO_ROOT / "scripts/loopx.ps1", launcher)
    pointer = tmp_path / "current-release.json"
    pointer.write_text(
        json.dumps({"release_root": str(REPO_ROOT), "python": sys.executable}), encoding="utf-8",
    )
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    prefix = ["-File", launcher_arg, "--cli-bin", launcher_arg, "--format", "json",
              "--registry", str(registry), "--runtime-root", str(runtime)]
    refresh = ["refresh-state", *binding, "--classification", "verified", "--delivery-batch-scale", "implementation",
               "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
               "--no-global-sync", "--suppress-external-sinks"]

    def run(args):
        process = subprocess.run([powershell, "-NoProfile", "-NonInteractive", *args], cwd=project,
            env={**os.environ, "LOOPX_CURRENT_RELEASE_FILE": str(pointer)}, capture_output=True, timeout=60)
        assert process.returncode == 0, (process.stdout, process.stderr)
        result = json.loads(process.stdout)
        assert result["ok"] and result["settlement_identity"] == identity
        return result

    if composed:
        action = [*prefix, *refresh, "--settle-turn"]
    else:
        owed = run([*prefix, *refresh])["settlement_owed"]
        assert _spend_run_count(runtime) == 0
        action = ["-Command", owed["command"]]
    first = run(action)
    assert first["settlement_progress"]["state"] == "settled"
    assert _spend_run_count(runtime) == 1
    if not composed:
        argv = owed["command_argv"]
        assert argv[:3] == [launcher_arg, "--cli-bin", launcher_arg]
        assert argv[argv.index("--registry") + 1] == str(registry)
    replay = run(action)
    assert replay["idempotent_replay"]
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("finish_todo", [False, True])
def test_same_turn_progress_or_completion_and_replay_without_extra_guard(
    tmp_path, monkeypatch, provider, finish_todo,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 0, guard
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    validation, writeback = plan["ordered_steps"][:2]
    assert validation["command_condition"] == "todo_deliverable_complete"
    assert " --delivery-boundary semantic_closeout " in writeback["semantic_closeout_command_template"]
    if finish_todo:
        rc, complete = _run_generated_cli(validation["command_template"].replace(
            "<validated evidence>", "Independent validation of the original deliverable."), registry_path=registry)
        assert rc == 0, complete
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0",
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": (
            "Original scoped validation completed; wider Goal remains open." if finish_todo else
            "Verified progress advances the original task; its remaining acceptance stays open.")},
        "path_delta": {"outcome": "continue", "prior_assumption": "The scoped deliverable needed validation.",
                       "observed_reality": (
                           "The original deliverable passed independent validation." if finish_todo else
                           "A bounded verified outcome advances work without completing the deliverable."),
                       "retained": ["Original requirements"], "evidence_refs": ["evidence:validation"]},
    }))
    command = writeback["semantic_closeout_command_template"]
    for key, value in {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<file>": shlex.quote(str(vision))}.items():
        command = command.replace(key, value)
    words = shlex.split(command)
    assert words[words.index("--delivery-boundary") + 1] == "semantic_closeout"
    command += " " + shlex.join(["--settle-turn", "--no-global-sync", "--suppress-external-sinks",
        "--delivery-workspace-path", str(project)])
    # A hostile ambient executable must not select another checkout/accounting owner.
    trap = tmp_path / "bin"
    trap.mkdir()
    (trap / "loopx").write_text("#!/bin/sh\nexit 73\n")
    (trap / "loopx").chmod(0o700)
    monkeypatch.setenv("PATH", str(trap) + ":" + __import__("os").environ["PATH"])
    for replay in (False, True):
        rc, result = _run_generated_cli(command, registry_path=registry)
        assert rc == 0, json.dumps(result, ensure_ascii=False)
        assert result["settlement_progress"]["state"] == "settled"
        assert result["settlement_identity"] == plan["identity"]
        receipts = result["settlement_result"]["receipts"]
        assert [row["step_kind"] for row in receipts] == ["validation", "durable_writeback", "quota_spend"], replay
        assert all(row["status"] == "committed" and row["effect_id"] == plan["identity"]["effect_id"]
                   for row in receipts)
        assert _spend_run_count(runtime) == 1
        assert result.get("idempotent_replay", False) is replay
    ordinary_args = shlex.split(command)
    ordinary_args.remove("--settle-turn")
    rc, ordinary = _run_generated_cli(shlex.join(ordinary_args), registry_path=registry)
    assert rc == 0 and ordinary["settlement_progress"]["state"] == "settled", ordinary
    assert [row["step_kind"] for row in ordinary["settlement_result"]["receipts"]] == [
        "validation", "durable_writeback"]
    assert _spend_run_count(runtime) == 1
    rc, todo = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0 and todo["todo"]["status"] == ("done" if finish_todo else "open")
    events = runtime / "goals" / GOAL_ID / "rollout-events.jsonl"
    if not events.exists():
        from loopx.rollout_event_log import rollout_event_log_path
        events = rollout_event_log_path(runtime, GOAL_ID)
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert sum(row.get("event_kind") == "quota_should_run" for row in rows) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_failed_writeback_dry_run_and_missing_binding_cannot_spend(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    base = ["refresh-state", *binding, "--classification", "verified_slice",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks", "--settle-turn"]
    rc, rejected = _run_cli(registry, runtime, *base, "--delivery-outcome", "surface_only")
    assert rc != 0 and _spend_run_count(runtime) == 0, rejected
    rc, bad_path = _run_cli(registry, runtime, *base,
        "--delivery-workspace-path", "~loopx-missing-fixture-user")
    assert rc != 0 and bad_path["ok"] is False and _spend_run_count(runtime) == 0, bad_path
    rc, preview = _run_cli(registry, runtime, *base, "--delivery-boundary", "in_flight_continuation", "--dry-run")
    assert rc == 0 and preview["dry_run"] and _spend_run_count(runtime) == 0, preview
    # Invalid vision fails before writeback; no direct-debit bypass is allowed.
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "vision_patch": {"acceptance_summary": "x" * 421}}))
    rc, checkpoint = _run_cli(registry, runtime, *base, "--delivery-boundary", "semantic_closeout",
                              "--agent-vision-json", str(invalid))
    assert rc != 0 and _spend_run_count(runtime) == 0, checkpoint
    rc, missing = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID, "--settle-turn")
    assert rc != 0 and "exactly one" in missing["error"] and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("decision", ["replan", "semantic_closeout"])
def test_fresh_turn_honors_the_written_route_and_keeps_unfinished_requirements(
    tmp_path, monkeypatch, provider, decision,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text() + f"\n- [ ] [P0] Verify the independent remaining acceptance gap.\n"
        f"  <!-- loopx:todo todo_id=todo_independent_gap status=open claimed_by={AGENT_ID} task_class=advancement_task action_kind=validate -->\n")
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Scoped experiment is answered; independent acceptance remains."},
        "path_delta": {"outcome": "replan" if decision == "replan" else "continue",
            "prior_assumption": "The experiment could identify useful defects.",
            "observed_reality": "The scoped experiment is answered with retained evidence.",
            "retained": ["All original acceptance requirements"], "evidence_refs": ["evidence:scoped-result"]},
    }))
    rc, result = _run_cli(registry, runtime, "refresh-state", *binding,
        "--classification", "experiment_answered", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary",
        "semantic_closeout",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks", "--settle-turn")
    assert rc == 0, result
    rc, next_turn = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "next-route")
    assert rc == 0, next_turn
    assert next_turn["selected_todo"]["todo_id"] == "todo_independent_gap"
    assert next_turn["selected_todo"].get("selected_by") != "in_flight_todo"
    rc, original = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0 and original["todo"]["status"] == "open"
    assert "Validate and settle the selected delivery" in original["todo"]["text"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_execution_evidence_uses_explicit_finding_binding_in_settlement(tmp_path, monkeypatch, provider):
    from loopx.capabilities.explore.result_log import explore_result_log_path
    from loopx.configure_goal import configure_goal

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    configure_goal(registry_path=registry, goal_id=GOAL_ID, execute=True, explore_mode="evidence")
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, entry = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "on", "--decision-output-dir", str(tmp_path / "capture"), cwd=project)
    assert rc == 0 and entry["schema_version"] == "context_capture_navigation_v1", entry
    rc, detail = _run_cli(registry, runtime, "quota", "context-read", "--reference-file",
        entry["recovery"]["reference_file"], "--select", "/interaction_contract/cli_channel/settlement_plan",
        "--page-bytes", "24000", "--max-page-output-bytes", "64000", cwd=project)
    assert rc == 0 and detail["eof"], detail
    plan = json.loads(detail["content"])["selections"][0]["value"]
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")

    refs = ["result:probe", "source:" + "e" * 64]
    revision = "dataset:" + "d" * 64
    observation = {"schema_version": "explore_execution_observation_v0",
        "candidate_sha256": "c" * 64, "input_revision": revision,
        "planned_method_id": "initial", "executed_method_id": "revised",
        "change_reason": "A boundary check selected the revised method.",
        "elapsed_seconds": 0.125, "limit_seconds": 10, "search_iterations": 2,
        "stop_reason": "iteration_limit", "evidence_refs": sorted(refs),
        "result_ref": refs[0], "result_candidate_sha256": "c" * 64}
    vision = tmp_path / "vision.json"
    packet = {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The bounded execution has a reproducible result."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "The first method could cover the boundary.",
            "observed_reality": "The revised method handled the boundary case.",
            "retained": ["Validate the remaining cases."],
            "evidence_refs": [*refs, ".loopx/evidence/probe.json"]},
        "explore_result": {"schema_version": "explore_result_from_path_delta_v0",
            "question": "Which method handles the boundary?", "applicability": "Synthetic boundary case only",
            "input_revision": revision, "status": "confirmed", "evidence_refs": refs}}
    command = writeback["semantic_closeout_command_template"]
    for key, value in {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<file>": shlex.quote(str(vision))}.items():
        command = command.replace(key, value)
    if "--settle-turn" not in shlex.split(command):
        command += " --settle-turn --settlement-receipt"
    command += " --no-global-sync --suppress-external-sinks"

    packet["explore_result"]["execution_observation"] = {**observation, "input_revision": "foreign-input"}
    vision.write_text(json.dumps(packet))
    rc, rejected = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 1 and rejected["appended"] is False, rejected
    assert "execution observation must retain this finding's input revision" in rejected["error"], rejected
    log = explore_result_log_path(runtime, GOAL_ID)
    assert _spend_run_count(runtime) == 0 and not log.exists()

    compact = {key: value for key, value in observation.items() if key != "input_revision"}
    compact.update(schema_version="explore_execution_from_finding_v0", evidence_refs="finding")
    packet["explore_result"]["execution_observation"] = compact
    vision.write_text(json.dumps(packet))
    rc, settled = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled.get("error", settled)
    assert settled["settlement_identity"] == plan["identity"]
    assert settled["quota_spend"]["ok"] and "settlement_owed" not in settled

    example = writeback["optional_attachments"][0]["optional_execution_observation_example"]
    assert example["execution_observation"]["schema_version"] == compact["schema_version"]
    assert compact["schema_version"] not in json.dumps(entry)
    assert settled["explore_result"]["execution_observation"] == observation
    rc, context = _run_cli(registry, runtime, "explore", "turn-context", "--goal-id", GOAL_ID,
                          "--agent-id", AGENT_ID, cwd=project)
    assert rc == 0 and context["graph"]["writeback_results"][0]["execution_observation"] == observation
    before = log.read_bytes()
    rc, replay = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc == 0 and replay["idempotent_replay"] and replay["settlement_identity"] == plan["identity"], replay
    assert _spend_run_count(runtime) == 1 and log.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("response", ["complete", "lost", "missing_receipt", "wrong_turn", "wrong_writeback", "wrong_event"])
def test_owed_command_checks_child_receipts_or_recovers_without_another_debit(tmp_path, monkeypatch, provider, response):
    from loopx.cli_commands import refresh_settlement

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    rc, original = _run_cli(registry, runtime, "refresh-state", *binding, "--classification", "verified",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks")
    assert rc == 0 and _spend_run_count(runtime) == 0, original
    def settle(payload):
        refresh_settlement.settle_refreshed_turn(payload, registry_path=registry,
            runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            replan_obligation_id=None, turn_instance_id=TURN_ID, goal_ref=None)
    real_run = subprocess.run
    real_read = refresh_settlement.read_heartbeat_settlement
    calls = []
    readbacks = []
    def observe_readback(*args, **kwargs):
        readbacks.append(kwargs["turn_instance_id"])
        return real_read(*args, **kwargs)
    def observe_child(command, **kwargs):
        calls.append(command)
        result = real_run(command, **kwargs)
        assert result.returncode == 0
        if response == "lost":
            raise OSError("Synthetic response loss after the real commitment")
        child = json.loads(result.stdout)
        receipts = child["settlement_result"]["receipts"]
        if response == "missing_receipt":
            receipts.pop()
        elif response == "wrong_turn":
            child["settlement_identity"]["turn_instance_id"] = "different-turn"
        elif response == "wrong_writeback":
            receipts[1]["source_ref"] = "rollout_event:foreign-writeback"
        elif response == "wrong_event":
            child["rollout_event"]["event_id"] = "different-event"
        return subprocess.CompletedProcess(command, result.returncode, json.dumps(child), result.stderr)
    monkeypatch.setattr(refresh_settlement, "read_heartbeat_settlement", observe_readback)
    monkeypatch.setattr(refresh_settlement.subprocess, "run", observe_child)
    wrong = copy.deepcopy(original)
    wrong["settlement_owed"]["command"] = wrong["settlement_owed"]["command"].replace(TURN_ID, "different-turn")
    settle(wrong)
    assert not wrong["ok"] and not calls and readbacks == [TURN_ID] and _spend_run_count(runtime) == 0
    readbacks.clear()
    foreign = copy.deepcopy(original)
    owed = foreign["settlement_owed"]
    argv = owed.get("command_argv") or shlex.split(owed["command"])
    argv = ["foreign-loopx", *argv[1:]]
    if "command_argv" in owed:
        owed["command_argv"] = argv
    owed["command"] = refresh_settlement.render_owed_host_command(argv)
    settle(foreign)
    assert not foreign["ok"] and "current CLI route" in foreign["error"]
    assert not calls and readbacks == [TURN_ID] and _spend_run_count(runtime) == 0
    readbacks.clear()
    result = copy.deepcopy(original)
    settle(result)
    assert result["ok"] and result["settlement_progress"]["state"] == "settled", result
    assert readbacks == ([] if response == "complete" else [TURN_ID])
    if response == "lost":
        assert "Synthetic response loss" in result["quota_spend"]["transport_warning"]
    assert len(calls) == 1 and _spend_run_count(runtime) == 1
    monkeypatch.setattr(refresh_settlement.subprocess, "run", real_run)
    replay = copy.deepcopy(original)
    settle(replay)
    assert replay["ok"] and replay["quota_spend"]["idempotent_replay"] and _spend_run_count(runtime) == 1
    assert readbacks == ([TURN_ID] if response == "complete" else [TURN_ID, TURN_ID])
    assert result["settlement_result"] == replay["settlement_result"]
