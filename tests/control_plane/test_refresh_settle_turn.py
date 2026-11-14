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

# CLI effects belong to each fixture's producing project. The source checkout
# must not accidentally satisfy workspace verification for installed tests.


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_context_delivery_preserves_the_canonical_settlement_contract(tmp_path, monkeypatch, provider):
    """Presentation may change; the current Turn's executable owners must not."""
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    original_plan, captures = None, []
    for mode, alias in [("off", False), ("on", False), ("auto", False), ("off", True)]:
        capture = tmp_path / f"capture-{mode}-{alias}"
        rc, result = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
            "--context-delivery", mode, "--decision-output-dir", str(capture),
            *(["--turn-envelope"] if alias else []), cwd=project)
        assert rc == 0, result
        path = capture / "decision.json"
        source = json.loads(path.read_bytes())
        assert_complete_turn_decision(source)
        if mode == "off":
            if alias:
                assert result["schema_version"] == "loopx_turn_envelope_v0"
            else:
                assert result == source and "context_delivery" not in result
        plan = source["interaction_contract"]["cli_channel"]["settlement_plan"]
        assert plan["schema_version"] == "quota_settlement_plan_v1"
        assert plan["identity"] == source["heartbeat_receipt"]["settlement_identity"]
        spend, = (step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")
        assert spend["owner"] == "agent" and "command_template" in spend
        if original_plan is None:
            original_plan = plan
        assert plan == original_plan
        captures.append((path, path.read_bytes()))
        assert all(saved.read_bytes() == content for saved, content in captures)
        assert _spend_run_count(runtime) == 0
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("recorded_v2", [False, True], ids=["current", "recorded-v2"])
def test_explicit_combined_writeback_returns_the_original_turn_receipts(tmp_path, monkeypatch, provider, recorded_v2):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, original = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "off", cwd=project)
    assert rc == 0, original
    manual = original["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert manual["schema_version"] == "quota_settlement_plan_v1"
    assert manual["ordered_steps"][2]["owner"] == "agent"
    assert "command_template" in manual["ordered_steps"][2]
    capture = tmp_path / "capture"
    rc, view = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "on", "--turn-envelope", "--decision-output-dir", str(capture), cwd=project)
    assert rc == 0, view
    full = json.loads((capture / "decision.json").read_text())
    cli = full["interaction_contract"]["cli_channel"]
    plan = cli["settlement_plan"]
    assert plan == manual
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    spend = next(step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")
    assert spend["owner"] == "agent" and "command_template" in spend
    assert cli["next_cli_actions"] == [writeback["command_template"], spend["command_template"]]
    saved_captures = [(capture / "decision.json", (capture / "decision.json").read_bytes())]
    if recorded_v2:
        from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
        from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
        from tests.control_plane.test_complete_turn_decision import _resign_contract

        # Decoder fixture for the retired candidate's recorded wire shape.
        # It is not a second live producer and never overwrites the admission.
        plan["schema_version"] = "quota_settlement_plan_v2"
        for field in ("command_template", "semantic_closeout_command_template"):
            writeback[field] += " --settle-turn --settlement-receipt"
        spend.pop("command_template")
        spend["owner"] = "runtime"
        spend["precondition"] += (
            ". The writeback command runs this step and returns its exact receipt. "
            "After a partial failure follow only settlement_owed for this Turn. After settlement do not run another spend."
        )
        cli["next_cli_actions"] = [writeback["command_template"]]
        _resign_contract(full)
        assert_complete_turn_decision(full)
        historical = tmp_path / "recorded-v2.json"
        digest = write_decision_capture(historical, full)
        rc, page = _run_cli(registry, runtime, "quota", "context-read", "--capture-file", str(historical),
            "--capture-sha256", digest, "--select", "/interaction_contract/cli_channel/settlement_plan", cwd=project)
        assert rc == 0 and page["ok"] and page["eof"], page
        assert json.loads(page["content"])["selections"][0]["value"] == plan
        saved_captures.append((historical, historical.read_bytes()))
    command = writeback["command_template"]
    if not recorded_v2:
        command += " --settle-turn --settlement-receipt"
    for key, value in {"<validated_progress>": "verified_native_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation"}.items():
        command = command.replace(key, value)
    command += " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks"
    for replay in (False, True):
        rc, receipt = _run_generated_cli(command, registry_path=registry, cwd=project)
        assert rc == 0, receipt
        assert receipt["settlement_identity"] == plan["identity"]
        assert receipt["settlement_progress"]["state"] == "settled"
        assert receipt.get("idempotent_replay", False) is replay
        assert {step["step_kind"] for step in receipt["settlement_result"]["receipts"]} == {
            "validation", "durable_writeback", "quota_spend"}
        assert "settlement_owed" not in receipt
        assert _spend_run_count(runtime) == 1
        assert all(path.read_bytes() == body for path, body in saved_captures)
        from loopx.control_plane.quota.settlement import read_heartbeat_settlement

        scope = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
        complete = read_heartbeat_settlement(runtime, registry_path=registry, **scope)
        controls = read_heartbeat_settlement(runtime, registry_path=registry, include_record_bodies=False, **scope)
        assert complete.writeback_run and complete.spend_run and complete.heartbeat_receipt
        assert controls.writeback_run is None and controls.spend_run is None and controls.heartbeat_receipt is None
        assert controls.progress == complete.progress == receipt["settlement_progress"]
        for field in ("writeback", "spend", "delivery", "settlement"):
            original_result, projected_result = getattr(complete, field), getattr(controls, field)
            assert original_result.value and projected_result.value is None
            assert projected_result.receipts == original_result.receipts
            assert projected_result.failure == original_result.failure
        assert controls.identity == complete.identity
        details = receipt["result_details"]
        readback = subprocess.run(shlex.split(details["command"]), cwd=project,
            capture_output=True, text=True, check=False)
        page = json.loads(readback.stdout)
        assert readback.returncode == 0, page
        saved = json.loads(page["content"])
        assert saved["settlement_result"] == receipt["settlement_result"]
        assert saved["settlement_identity"] == receipt["settlement_identity"]
        assert "state" in saved and "state" not in receipt
        for field in ("projection_outbox", "runtime_projection_route", "shared_runtime_projection", "explore_graph_sync"):
            if field not in saved:
                continue
            assert field in details["deferred_fields"] and field not in receipt
            # A new nested obligation cannot silently become supporting IO just
            # because the known producer still says disabled/successful.
            future = copy.deepcopy(saved)
            future[field]["future_control"] = {"required_action": "inspect before continuing"}
            from loopx.cli_commands.refresh_settlement import present_settled_refresh
            projected = present_settled_refresh(future, runtime_root=runtime)
            assert projected[field] == future[field]
            unresolved = copy.deepcopy(saved)
            if field == "projection_outbox":
                unresolved[field]["next_action"] = "retry"
            elif field == "runtime_projection_route":
                unresolved[field]["conflict_count"] = 1
            elif field == "shared_runtime_projection":
                unresolved[field]["raw_artifacts_copied"] = True
            else:
                unresolved[field]["delivery_postcondition"]["blocks_delivery"] = True
            projected = present_settled_refresh(unresolved, runtime_root=runtime)
            assert projected[field] == unresolved[field]
        assert "vision_checkpoint" in receipt  # Settlement does not close acceptance.
        if replay:
            assert saved["projection_outbox"]["status"] == "current"
            assert "projection_outbox" in details["deferred_fields"]
            assert "projection_outbox" not in receipt
        assert len(json.dumps(receipt)) < len(json.dumps(saved))
    # Unknown future plans remain complete and unchanged, rather than acquiring
    # the current v2 execution ownership by a permissive fallback.
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    future = {key: copy.deepcopy(value) for key, value in original.items()
              if key not in {"schema_version", "contract_binding"}}
    future_plan = future["interaction_contract"]["cli_channel"]["settlement_plan"]
    future_plan["schema_version"] = "future_plan_for_readback"
    recomposed = compose_turn_decision(future)
    assert recomposed["interaction_contract"]["cli_channel"]["settlement_plan"] == future_plan
    rc, todo = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID, cwd=project)
    assert rc == 0 and todo["todo"]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_complete_source_budget_recovery_keeps_original_turn_and_single_debit(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    capture = tmp_path / "capture"
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding,
        "--context-delivery", "on", "--turn-envelope", "--decision-output-dir", str(capture), cwd=project)
    assert rc == 0, guard
    full = json.loads((capture / "decision.json").read_text())
    assert full["schema_version"] == "loopx_turn_envelope_v1"
    plan = full["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v1"
    command = next(step["command_template"] for step in plan["ordered_steps"]
                   if step["kind"] == "durable_writeback")
    for key, value in {"<validated_progress>": "verified_budget_recovery", "<scale>": "implementation",
                       "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation"}.items():
        command = command.replace(key, value)
    command += " --settle-turn --settlement-receipt"
    assert "--settle-turn" in shlex.split(command)
    assert "--settlement-receipt" in shlex.split(command)
    document = tmp_path / "vision.json"
    command += " " + shlex.join(["--agent-vision-json", str(document),
        "--no-global-sync", "--suppress-external-sinks"])
    packet = {"vision_patch": {"vision_summary": "Keep the original objective.", "last_patch_summary": "s" * 244},
              "path_delta": {"outcome": "continue", "prior_assumption": "Retain the route.",
                             "observed_reality": "o" * 354, "retained": ["a", "b", "c", "d"],
                             "changed": ["Measured change.", "c" * 178], "evidence_refs": ["a", "b", "c", "d", "e"]}}
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before_state = state.read_bytes()
    before_index = index.read_bytes() if index.exists() else None
    cases = [
        (packet, ("last_patch_summary uses 244", "path_delta.observed_reality uses 354",
                  "path_delta.retained has 4 items", "path_delta.changed[1] uses 178",
                  "path_delta.evidence_refs has 5 items")),
        ({"vision_patch": {"advancement_policy": "x" * 41}},
         ("advancement_policy uses 41 chars; limit is 32",)),
        ({"vision_patch": {"advancement_policy": "invalid"}}, ("advancement_policy",)),
    ]
    for invalid, diagnostics in cases:
        document.write_text(json.dumps(invalid))
        authored = document.read_bytes()
        rc, rejected = _run_generated_cli(command, registry_path=registry, cwd=project)
        assert rc == 1 and rejected["appended"] is False, rejected
        for diagnostic in diagnostics:
            assert diagnostic in rejected["error"], rejected
        assert document.read_bytes() == authored and state.read_bytes() == before_state
        assert (index.read_bytes() if index.exists() else None) == before_index
        assert _spend_run_count(runtime) == 0
    # The caller explicitly fixes the authored packet. The validator never
    # truncates it, changes the decision, re-admits the Turn or spends on error.
    packet["vision_patch"]["last_patch_summary"] = "Verified the bounded change."
    packet["path_delta"].update(observed_reality="Evidence supports continuing.", retained=["Keep acceptance."],
                               changed=["Measured change."], evidence_refs=["evidence:checked"])
    document.write_text(json.dumps(packet))
    for replay in (False, True):
        rc, accepted = _run_generated_cli(command, registry_path=registry, cwd=project)
        assert rc == 0, accepted
        assert accepted["settlement_identity"] == plan["identity"]
        assert accepted["settlement_progress"]["state"] == "settled"
        assert accepted.get("idempotent_replay", False) is replay
        assert {step["step_kind"] for step in accepted["settlement_result"]["receipts"]} == {
            "validation", "durable_writeback", "quota_spend"}
        assert _spend_run_count(runtime) == 1
    rc, history = _run_cli(registry, runtime, "history", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
    assert rc == 0, history
    goal = next(row for row in history["goals"] if row["id"] == GOAL_ID)
    saved = next(row["agent_vision"] for row in goal["latest_runs"] if row.get("agent_vision"))
    assert saved["vision_patch"] == packet["vision_patch"]
    assert saved["path_delta"] == {"schema_version": "goal_path_delta_v0", **packet["path_delta"]}
    from loopx.rollout_event_log import rollout_event_log_path
    events = [json.loads(line) for line in rollout_event_log_path(runtime, GOAL_ID).read_text().splitlines()]
    assert sum(row.get("event_kind") == "quota_should_run" for row in events) == 1


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
    from loopx.cli_commands import quota as quota_cli
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError

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
        # The CLI caches lazy exports after first use. Patch its actual call
        # boundary so this fault also fires after earlier in-process guards.
        fault.setattr(quota_cli, "compose_turn_decision", unavailable)
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
def test_explicit_complete_soft_claim_reuses_the_original_turn_receipt(tmp_path, provider):
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision

    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="soft_claim")
    command = ["quota", "should-run", "--runtime-profile", "generic_cli", "--context-delivery", "off",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, legacy = _run_cli(registry, runtime, *command, cwd=project)
    assert rc == 0, legacy
    assert "schema_version" not in legacy and "contract_binding" not in legacy
    original_cli = legacy["interaction_contract"]["cli_channel"]
    assert original_cli["settlement_plan"]["schema_version"] == "quota_settlement_plan_v1"
    rc, complete = _run_cli(registry, runtime, *command, "--complete-turn", cwd=project)
    assert rc == 0, complete
    assert_complete_turn_decision(complete)
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
            env={**os.environ, "LOOPX_CURRENT_RELEASE_FILE": str(pointer)},
            capture_output=True, encoding="utf-8", timeout=60)
        assert process.returncode == 0, (process.stdout, process.stderr)
        result = json.loads(process.stdout)
        assert result["ok"] and result["settlement_identity"] == identity
        return result

    if composed:
        action = [*prefix, *refresh, "--settle-turn", "--settlement-receipt"]
    else:
        owed = run([*prefix, *refresh])["settlement_owed"]
        assert _spend_run_count(runtime) == 0
        action = ["-Command", owed["command"]]
    first = run(action)
    assert first["settlement_progress"]["state"] == "settled"
    assert _spend_run_count(runtime) == 1
    if composed:
        from tests.control_plane.windows_private_acl import assert_private_acl

        details = first["result_details"]
        reference = Path(details["reference_file"])
        source = Path(json.loads(reference.read_text(encoding="utf-8"))["path"])
        assert_private_acl(reference)
        assert_private_acl(source)
        readback = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command",
            details["command"] + " --select /settlement_identity --select /settlement_result"],
            cwd=project, env={**os.environ, "LOOPX_CURRENT_RELEASE_FILE": str(pointer)},
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert readback.returncode == 0, (readback.stdout, readback.stderr)
        page = json.loads(readback.stdout)
        assert page["ok"] and page["eof"]
        assert {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]} == {
            "/settlement_identity": identity, "/settlement_result": first["settlement_result"],
        }
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
    assert "semantic_closeout" in writeback["precondition"]
    assert " --delivery-boundary semantic_closeout " in writeback["semantic_closeout_command_template"]
    if finish_todo:
        rc, complete = _run_generated_cli(validation["command_template"].replace(
            "<validated evidence>", "Independent validation of the original deliverable."), registry_path=registry, cwd=project)
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
        rc, result = _run_generated_cli(command, registry_path=registry, cwd=project)
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
    rc, ordinary = _run_generated_cli(shlex.join(ordinary_args), registry_path=registry, cwd=project)
    assert rc == 0 and ordinary["settlement_progress"]["state"] == "settled", ordinary
    assert [row["step_kind"] for row in ordinary["settlement_result"]["receipts"]] == [
        "validation", "durable_writeback"]
    assert _spend_run_count(runtime) == 1
    rc, todo = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID, cwd=project)
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
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    base = ["refresh-state", *binding, "--classification", "verified_slice",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks", "--settle-turn"]
    rc, rejected = _run_cli(registry, runtime, *base, "--delivery-outcome", "surface_only", cwd=project)
    assert rc != 0 and _spend_run_count(runtime) == 0, rejected
    rc, bad_path = _run_cli(registry, runtime, *base,
        "--delivery-workspace-path", "~loopx-missing-fixture-user", cwd=project)
    assert rc != 0 and bad_path["ok"] is False and _spend_run_count(runtime) == 0, bad_path
    rc, preview = _run_cli(registry, runtime, *base, "--delivery-boundary", "in_flight_continuation", "--dry-run", cwd=project)
    assert rc == 0 and preview["dry_run"] and _spend_run_count(runtime) == 0, preview
    # Invalid vision fails before writeback; no direct-debit bypass is allowed.
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "vision_patch": {"acceptance_summary": "x" * 421}}))
    rc, checkpoint = _run_cli(registry, runtime, *base, "--delivery-boundary", "semantic_closeout",
                              "--agent-vision-json", str(invalid), cwd=project)
    assert rc != 0 and _spend_run_count(runtime) == 0, checkpoint
    rc, missing = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID, "--settle-turn", cwd=project)
    assert rc != 0 and "exactly one" in missing["error"] and _spend_run_count(runtime) == 0
    rc, receipt_only = _run_cli(registry, runtime, "refresh-state", *binding, "--settlement-receipt", cwd=project)
    assert rc != 0 and "requires --settle-turn" in receipt_only["error"] and _spend_run_count(runtime) == 0


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
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
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
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks", "--settle-turn", cwd=project)
    assert rc == 0, result
    rc, next_turn = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "next-route", cwd=project)
    assert rc == 0, next_turn
    assert next_turn["selected_todo"]["todo_id"] == "todo_independent_gap"
    assert next_turn["selected_todo"].get("selected_by") != "in_flight_todo"
    rc, original = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID, cwd=project)
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
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    rc, original = _run_cli(registry, runtime, "refresh-state", *binding, "--classification", "verified",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks", cwd=project)
    assert rc == 0 and _spend_run_count(runtime) == 0, original
    def settle(payload):
        refresh_settlement.settle_refreshed_turn(payload, registry_path=registry,
            runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
            replan_obligation_id=None, turn_instance_id=TURN_ID, goal_ref=None,
            delivery_workspace_path=REPO_ROOT)
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
    from loopx.control_plane.quota.settlement import render_owed_host_command

    argv = owed.get("command_argv") or shlex.split(owed["command"])
    argv = ["foreign-loopx", *argv[1:]]
    if "command_argv" in owed:
        owed["command_argv"] = argv
    owed["command"] = render_owed_host_command(argv)
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
