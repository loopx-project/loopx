"""The combined CLI retains exact-Turn authority, recovery and task freedom."""
from __future__ import annotations

import json
import copy
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _heartbeat_receipt_count, _initialize_fixture_authority,
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
        assert "delivery_result_context" not in before
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


def _semantic_result_args(template, material_fields):
    args = shlex.split(template)
    return [material_fields.get(word, word)
            for word in args[args.index("refresh-state"):args.index("--agent-vision-json")]]


def _direction_after_result(registry, runtime, project, guard, writeback_args, *, completed=False):
    basis = guard["delivery_result_context"]
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert basis["settlement_identity"] == identity
    if not completed:
        rc, retained = _run_cli(registry, runtime, *writeback_args,
            "--delivery-read-context", basis["read_context_id"], cwd=project)
        assert rc == 0 and retained["ok"] and retained["appended"], json.dumps(retained)
        assert retained["settlement_identity"] == identity
    assert _spend_run_count(runtime) == 0
    purpose = "first_delivery" if completed else "supplement_checkpoint"
    rc, direction = _run_cli(registry, runtime, "checkpoint-context", "--purpose", purpose,
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", TURN_ID, cwd=project)
    assert rc == 0 and direction["settlement_identity"] == identity, direction
    assert direction["basis"]["todo"]["status"] == ("done" if completed else "open")
    flags = ["--checkpoint-read-context", direction["read_context_id"]]
    return ["--first-delivery", *flags] if completed else flags


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

    material_fields = {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress"}
    template = writeback["semantic_closeout_command_template"]
    writeback_args = _semantic_result_args(template, material_fields)
    sinks = ["--no-global-sync", "--suppress-external-sinks"]
    direction_args = _direction_after_result(registry, runtime, project, original, [*writeback_args, *sinks])

    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Verified an intermediate result. The original work remains open."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The scoped result needed verification.",
                       "observed_reality": "The independently checked result advances the current route.",
                       "retained": ["Original requirements"], "evidence_refs": ["evidence:validation"]},
    }))
    command = template
    for key, value in {**material_fields, "<file>": shlex.quote(str(vision))}.items():
        command = command.replace(key, value)
    command += " " + shlex.join([*direction_args, *sinks])
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
def test_exact_spend_and_combined_replay_reject_disagreeing_billing_terms(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    original = guard["heartbeat_receipt"]["settlement_identity"]
    refresh = ["refresh-state", *binding, "--classification", "verified_billing_replay",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks"]
    combined = [*refresh, "--settle-turn", "--settlement-receipt"]
    spend = ["quota", "spend-slot", *binding, "--execute"]
    debit = [*spend, "--slots", "1", "--source", "heartbeat"]
    rc, delivery = _run_cli(registry, runtime, *refresh, cwd=project)
    assert rc == 0 and delivery["settlement_progress"]["state"] == "spend_required", delivery
    for slots, source in (("2", "heartbeat"), ("1", "visible-goal")):
        rc, wrong = _run_cli(registry, runtime, *spend, "--slots", slots, "--source", source, cwd=project)
        assert rc != 0 and not wrong["ok"] and not wrong["appended"], wrong
        assert _spend_run_count(runtime) == 0

    rc, settled = _run_cli(registry, runtime, *combined, cwd=project)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled
    assert settled["settlement_identity"]["effect_id"] == original["effect_id"]
    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    index = log.parent / "runs" / "index.jsonl"
    receipt_log = log.read_text(encoding="utf-8")
    initial_index = index.read_bytes()
    debit_record, = (record for line in initial_index.splitlines()
                     if (record := json.loads(line))["classification"] == "quota_slot_spent")
    artifact = Path(debit_record["json_path"])
    assert artifact.resolve().is_relative_to(runtime.resolve())
    for field, bad_value in ((None, None), ("slots", 2), ("source", "visible-goal"),
                              ("missing", None), ("legacy_empty", ""), ("legacy_empty", None),
                              ("missing_artifact", None), ("missing", None), ("legacy_empty", None),
                              ("slots", 2), ("source", "visible-goal"),
                              ("unknown_admission_source", None), ("unknown_legacy_terms", None)):
        rows = [json.loads(line) for line in receipt_log.splitlines()]
        receipt, = (row for row in rows if row["event_kind"] == "quota_spend" and row["agent_id"] == AGENT_ID)
        details = receipt["details"]
        assert details["settlement_effect_id"] == original["effect_id"]
        assert details["slots"] == 1 and details["source"] == "heartbeat"
        if field == "missing":
            details.pop("slots")
            details.pop("source")
        elif field == "legacy_empty":
            details.update(slots=bad_value, source=bad_value)
        elif field == "missing_artifact":
            artifact.rename(tmp_path / "legacy-unavailable-spend.json")
        elif field in ("unknown_admission_source", "unknown_legacy_terms"):
            admission, = (row for row in rows if row["event_kind"] == "quota_should_run")
            assert admission["details"].pop("quota_spend_source") == "heartbeat"
            if field == "unknown_legacy_terms":
                details.pop("slots")
                details.pop("source")
        elif field is not None:
            details[field] = bad_value
        log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        before = log.read_bytes()
        missing_artifact = not artifact.exists()
        # A caller cannot make a conflicting source valid by supplying that
        # same source on replay once only the index and receipt remain.
        commands = (combined, debit) if missing_artifact else (debit, combined)
        if field == "source" and missing_artifact:
            commands += ((*spend, "--slots", "1", "--source", bad_value),)
        for command in commands:
            rc, replay = _run_cli(registry, runtime, *command, cwd=project)
            if field in ("slots", "source", "unknown_admission_source"):
                assert rc != 0 and not replay["ok"], replay
                basis = "original Turn admission" if missing_artifact else "recorded debit"
                diagnostic = replay.get("reason") or replay.get("error") or ""
                expected = ("original Turn admission has no verifiable billing source"
                            if field == "unknown_admission_source"
                            else f"{basis} and quota spend receipt disagree on {field}")
                assert expected in diagnostic, replay
                assert replay.get("settlement_progress", {}).get("state") != "settled", replay
                assert not replay.get("settlement_owed"), replay
            else:
                assert rc == 0 and replay["ok"] and replay["idempotent_replay"], replay
                assert replay["settlement_identity"]["effect_id"] == original["effect_id"]
            assert not replay.get("appended"), replay
            assert log.read_bytes() == before and index.read_bytes() == initial_index
            assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("repair_entry", ["spend", "refresh"])
def test_missing_spend_receipt_preserves_the_original_debit(tmp_path, monkeypatch, provider, repair_entry):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
    assert rc == 0, guard
    original = guard["heartbeat_receipt"]["settlement_identity"]
    combined = ["refresh-state", *binding, "--classification", "verified_billing_repair",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
        "--settle-turn", "--settlement-receipt"]
    debit = ["quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute"]
    rc, settled = _run_cli(registry, runtime, *combined, cwd=project)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled

    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    index = log.parent / "runs" / "index.jsonl"
    original_index = index.read_bytes()
    debit_record, = (row for line in original_index.splitlines()
                     if (row := json.loads(line))["classification"] == "quota_slot_spent")
    artifact = Path(debit_record["json_path"])
    original_artifact = artifact.read_bytes()
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    receipt, = (row for row in records if row["event_kind"] == "quota_spend")
    retained = [row for row in records if row is not receipt]
    log.write_text("".join(json.dumps(row) + "\n" for row in retained), encoding="utf-8")

    rc, repaired = _run_cli(registry, runtime, *(debit if repair_entry == "spend" else combined), cwd=project)
    assert rc == 0 and repaired["ok"], repaired
    assert repaired["settlement_progress"]["state"] == "settled", repaired
    assert repaired["settlement_identity"]["effect_id"] == original["effect_id"]
    assert not repaired.get("settlement_owed"), repaired
    recovered_log = log.read_bytes()
    after = [json.loads(line) for line in recovered_log.splitlines()]
    assert [row for row in after if row["event_kind"] != "quota_spend"] == retained
    recovered, = (row for row in after if row["event_kind"] == "quota_spend")
    assert recovered["details"]["settlement_effect_id"] == original["effect_id"]
    assert recovered["details"]["slots"] == receipt["details"]["slots"] == 1
    assert recovered["details"]["source"] == receipt["details"]["source"] == "heartbeat"
    for command in (debit, combined):
        rc, replay = _run_cli(registry, runtime, *command, cwd=project)
        assert rc == 0 and replay["ok"] and replay["idempotent_replay"], replay
        assert replay["settlement_identity"]["effect_id"] == original["effect_id"]
        assert not replay.get("appended") and not replay.get("settlement_owed"), replay
    assert log.read_bytes() == recovered_log
    assert index.read_bytes() == original_index and artifact.read_bytes() == original_artifact
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("artifact_format", ["current", "legacy"])
def test_exact_spend_and_refresh_reject_another_turns_debit_file(
    tmp_path, monkeypatch, provider, artifact_format,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    commands, identities = {}, {}
    for turn in (TURN_ID, f"{TURN_ID}-second"):
        binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                   "--todo-id", TODO_ID, "--turn-instance-id", turn]
        rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding, cwd=project)
        assert rc == 0 and guard["should_run"], guard
        identities[turn] = guard["heartbeat_receipt"]["settlement_identity"]
        combined = ["refresh-state", *binding, "--classification", "verified_debit_identity",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
            "--settle-turn", "--settlement-receipt"]
        spend = ["quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute", "--verbose"]
        rc, settled = _run_cli(registry, runtime, *combined, cwd=project)
        assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled
        assert settled["settlement_identity"] == identities[turn]
        commands[turn] = (combined, spend)

    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    index = log.parent / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_bytes().splitlines()]
    target, sibling = (row for row in rows if row["classification"] == "quota_slot_spent")
    assert target["settlement_identity"] == identities[TURN_ID]
    assert sibling["settlement_identity"] == identities[f"{TURN_ID}-second"]
    artifacts = [Path(row["json_path"]) for row in (target, sibling)]
    if artifact_format == "legacy":
        # The historical Python writer retained Turn/effect bindings, but no
        # commit metadata. Equal timestamps must not authenticate another Turn.
        for row, path in zip((target, sibling), artifacts):
            record = json.loads(path.read_bytes())
            row.pop("quota_spend_commit")
            record.pop("quota_spend_commit")
            row["generated_at"] = record["generated_at"] = "2026-11-01T12:00:00Z"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        index.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    original_artifacts = [path.read_bytes() for path in artifacts]
    assert [(json.loads(data)["quota_event"]["slots"], json.loads(data)["quota_event"]["source"])
            for data in original_artifacts] == [(1, "heartbeat"), (1, "heartbeat")]
    original_index = index.read_bytes()
    original_log = log.read_bytes()
    target["json_path"] = sibling["json_path"]
    index.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    redirected_index = index.read_bytes()

    for receipt_kind in ("current", "legacy", "missing"):
        if receipt_kind != "current":
            retained = []
            for line in original_log.splitlines():
                row = json.loads(line)
                if row["event_kind"] == "quota_spend" and row["run_id"] == TURN_ID:
                    if receipt_kind == "missing":
                        continue
                    row["details"].pop("slots")
                    row["details"].pop("source")
                retained.append(row)
            log.write_text("".join(json.dumps(row) + "\n" for row in retained), encoding="utf-8")
        before = log.read_bytes()
        for command in commands[TURN_ID]:
            rc, rejected = _run_cli(registry, runtime, *command, cwd=project)
            assert rc != 0 and not rejected["ok"], rejected
            reason = (rejected.get("verbose_debug", {}).get("error")
                      or rejected.get("error") or rejected.get("reason") or "")
            assert "quota spend JSON artifact identity does not match its index row" in reason, rejected
            assert rejected.get("settlement_progress", {}).get("state") != "settled", rejected
            assert log.read_bytes() == before and index.read_bytes() == redirected_index
            assert _spend_run_count(runtime) == 2

    assert [path.read_bytes() for path in artifacts] == original_artifacts
    index.write_bytes(original_index)
    rc, recovered = _run_cli(registry, runtime, *commands[TURN_ID][0], cwd=project)
    assert rc == 0 and recovered["settlement_progress"]["state"] == "settled", recovered
    assert recovered["settlement_identity"] == identities[TURN_ID]
    assert _spend_run_count(runtime) == 2 and index.read_bytes() == original_index



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
    material_fields = {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress"}
    template = writeback["semantic_closeout_command_template"]
    writeback_args = _semantic_result_args(template, material_fields)
    direction_args = _direction_after_result(registry, runtime, project, guard,
        [*writeback_args, "--no-global-sync", "--suppress-external-sinks"], completed=finish_todo)
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
    command = template
    for key, value in {**material_fields, "<file>": shlex.quote(str(vision))}.items():
        command = command.replace(key, value)
    words = shlex.split(command)
    assert words[words.index("--delivery-boundary") + 1] == "semantic_closeout"
    command += " " + shlex.join([*direction_args, "--settle-turn", "--no-global-sync", "--suppress-external-sinks",
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
    rc, rejected = _run_cli(registry, runtime, *base, "--delivery-outcome", "surface_only", cwd=project)
    assert rc != 0 and _spend_run_count(runtime) == 0, rejected
    rc, bad_path = _run_cli(registry, runtime, *base,
        "--delivery-workspace-path", "~loopx-missing-fixture-user", cwd=project)
    assert rc != 0 and bad_path["ok"] is False and _spend_run_count(runtime) == 0, bad_path
    rc, preview = _run_cli(registry, runtime, *base, "--delivery-boundary", "in_flight_continuation",
                          "--dry-run", cwd=project)
    assert rc == 0 and preview["dry_run"] and _spend_run_count(runtime) == 0, preview
    # Invalid vision fails before writeback; no direct-debit bypass is allowed.
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "vision_patch": {"acceptance_summary": "x" * 421}}))
    rc, checkpoint = _run_cli(registry, runtime, *base, "--delivery-boundary", "semantic_closeout",
                              "--agent-vision-json", str(invalid), cwd=project)
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
    writeback = ["refresh-state", *binding, "--classification", "experiment_answered",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "semantic_closeout", "--no-global-sync", "--suppress-external-sinks"]
    direction_args = _direction_after_result(registry, runtime, project, guard, writeback)
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Scoped experiment is answered; independent acceptance remains."},
        "path_delta": {"outcome": "replan" if decision == "replan" else "continue",
            "prior_assumption": "The experiment could identify useful defects.",
            "observed_reality": "The scoped experiment is answered with retained evidence.",
            "retained": ["All original acceptance requirements"], "evidence_refs": ["evidence:scoped-result"]},
    }))
    rc, result = _run_cli(registry, runtime, *writeback, *direction_args,
        "--agent-vision-json", str(vision), "--settle-turn", cwd=project)
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
    original = json.loads((tmp_path / "capture" / "decision.json").read_bytes())
    material_fields = {"<validated_progress>": "verified_slice", "<scale>": "implementation",
                       "<outcome>": "outcome_progress"}
    template = writeback["semantic_closeout_command_template"]
    writeback_args = _semantic_result_args(template, material_fields)

    refs = ["result:probe", "source:" + "e" * 64]
    revision = "dataset:" + "d" * 64
    observation = {"schema_version": "explore_execution_observation_v0",
        "candidate_sha256": "c" * 64, "input_revision": revision,
        "planned_method_id": "initial", "executed_method_id": "revised",
        "change_reason": "A boundary check selected the revised method.",
        "elapsed_seconds": 0.125, "limit_seconds": 10, "search_iterations": 2,
        "stop_reason": "iteration_limit", "evidence_refs": sorted(refs),
        "result_ref": refs[0], "result_candidate_sha256": "c" * 64}
    attachment = {"schema_version": "explore_result_attachment_v0",
        "question": "Which method handles the boundary?", "applicability": "Synthetic boundary case only",
        "input_revision": revision, "status": "confirmed", "evidence_refs": refs,
        "observation": "The revised method handled the boundary case.",
        "interpretation": "Retain the observed method while validating the remaining cases.",
        "execution_observation": {**observation, "input_revision": "foreign-input"}}
    finding = tmp_path / "finding.json"
    finding.write_text(json.dumps(attachment))
    retained_args = [*writeback_args, "--explore-result-json", str(finding),
        "--no-global-sync", "--suppress-external-sinks"]
    rc, rejected = _run_cli(registry, runtime, *retained_args,
        "--delivery-read-context", original["delivery_result_context"]["read_context_id"], cwd=project)
    assert rc == 1 and rejected["appended"] is False, rejected
    assert "execution observation must retain this finding's input revision" in rejected["error"], rejected
    log = explore_result_log_path(runtime, GOAL_ID)
    assert _spend_run_count(runtime) == 0 and not log.exists()

    compact = {key: value for key, value in observation.items() if key != "input_revision"}
    compact.update(schema_version="explore_execution_from_finding_v0", evidence_refs="finding")
    attachment["execution_observation"] = compact
    finding.write_text(json.dumps(attachment))
    direction_args = _direction_after_result(registry, runtime, project, original, retained_args)

    vision = tmp_path / "vision.json"
    packet = {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The bounded execution has a reproducible result."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "The first method could cover the boundary.",
            "observed_reality": "The revised method handled the boundary case.",
            "retained": ["Validate the remaining cases."],
            "evidence_refs": [*refs, ".loopx/evidence/probe.json"]}}
    vision.write_text(json.dumps(packet))
    command = template
    for key, value in {**material_fields, "<file>": shlex.quote(str(vision))}.items():
        command = command.replace(key, value)
    if "--settle-turn" not in shlex.split(command):
        command += " --settle-turn --settlement-receipt"
    command += " " + shlex.join([*direction_args, "--no-global-sync", "--suppress-external-sinks"])
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
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
        "--delivery-workspace-path", str(project), cwd=project)
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
