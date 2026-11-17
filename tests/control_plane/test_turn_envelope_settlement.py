"""Complete delivery preserves executable settlement; frozen v0 signatures stay verifiable."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import loopx
import pytest

from loopx.control_plane.quota.turn_envelope import (
    assert_complete_turn_decision,
    build_turn_envelope,
    compose_turn_decision,
    quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from tests.test_turn_envelope import _full_decision
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.cli_output_fixture import read_cli_context_for_oracle
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID,
    _configure_read_only_todo, _run_cli, _run_generated_cli,
    _spend_run_count, _write_fixture, _initialize_fixture_authority, _heartbeat_receipt_count,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("delivery", [None, "auto", "off"])
@pytest.mark.parametrize("envelope", [False, True])
def test_markdown_reads_same_complete_source_for_all_delivery_spellings(
    tmp_path, monkeypatch, record_property, provider, delivery, envelope,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    capture = tmp_path / "original-capture"
    root = Path(loopx.__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
         "--runtime-root", str(runtime), "--format", "markdown", "quota", "should-run",
         "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
         "--turn-instance-id", TURN_ID, "--decision-output-dir", str(capture),
         *(["--turn-envelope"] if envelope else []),
         *(["--context-delivery", delivery] if delivery else [])],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    record_property("markdown_chars", len(result.stdout))
    record_property("markdown_utf8_bytes", len(result.stdout.encode("utf-8")))
    original = (capture / "decision.json").read_bytes()
    source = json.loads(original)
    assert_complete_turn_decision(source)
    # Follow the same pinned recovery as JSON when delivery is paged. Off
    # carries the complete source inline, including the deprecated spelling.
    body, cost = read_cli_context_for_oracle(result.stdout, output_format="markdown")
    record_property("context_delivery", json.dumps(cost))
    shown = json.loads(body)
    assert shown["should_run"] is True
    assert shown["effective_action"] == "normal_run"
    assert json.dumps(shown, ensure_ascii=False) == json.dumps(source, ensure_ascii=False)
    if delivery == "off":
        assert not cost
    # Both readers recover the identical admitted source without a second guard.
    assert source["interaction_contract"]["agent_channel"]["work_context"]["complete"] is True
    assert source["heartbeat_receipt"]["settlement_identity"]["turn_instance_id"] == TURN_ID
    from loopx.control_plane.turn_driver.decision_capture import read_context_capture_file

    _, recovered, restored = read_context_capture_file(
        str(capture / "decision.json"), hashlib.sha256(original).hexdigest(), private=True,
    )
    assert recovered == original and restored == source
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("unsafe", ["symlink", "public"])
@pytest.mark.skipif(os.name != "posix", reason="Unsafe POSIX private transport; other hosts retain complete inline recovery")
def test_markdown_keeps_original_turn_when_optional_private_transport_is_unsafe(tmp_path, monkeypatch, provider, unsafe):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    transport = runtime / "context-delivery"
    target = tmp_path / "untrusted"
    target.mkdir(mode=0o755)
    if unsafe == "symlink":
        transport.symlink_to(target, target_is_directory=True)
    else:
        transport.mkdir(mode=0o755)
        transport.chmod(0o755)
    physical = target if unsafe == "symlink" else transport
    before_mode = physical.stat().st_mode
    root = Path(loopx.__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
         "--runtime-root", str(runtime), "--format", "markdown", "quota", "should-run",
         "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
         "--turn-instance-id", TURN_ID, "--runtime-profile", "generic_cli", "--turn-envelope"],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    body, cost = read_cli_context_for_oracle(result.stdout, output_format="markdown")
    complete = json.loads(body)
    assert complete["schema_version"] == "loopx_turn_envelope_v1" and not cost
    assert complete["interaction_contract"]["agent_channel"]["work_context"]["complete"] is True
    identity = complete["heartbeat_receipt"]["settlement_identity"]
    assert identity["turn_instance_id"] == TURN_ID
    assert identity == complete["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
    assert physical.stat().st_mode == before_mode and not list(physical.iterdir())
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("legacy_output", [False, True])
def test_complete_composition_rejects_retired_display_without_losing_source(legacy_output):
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected
    from loopx.control_plane.quota.turn_envelope import _turn_envelope_result

    payload = _full_decision()
    payload["future_requirement"] = {
        "raw": 'Preserve the final stop: 汉🙂"\\\n',
        "numbers": [1.0, -0.0, 2**60 + 1], "unknown": [None, False, {}, []],
    }
    before = deepcopy(payload)
    complete = compose_turn_decision(payload)
    assert json.dumps(complete["future_requirement"], ensure_ascii=False) == json.dumps(
        before["future_requirement"], ensure_ascii=False,
    )
    assert payload == before
    with pytest.raises(EffectRuntimeRejected, match="legacy quota display is retired"):
        _turn_envelope_result("compose_complete", payload=payload, source_edits=True,
            include_legacy_envelope=legacy_output)


def _plan():
    # Independent wire example, not generated by the projection under test.
    return {
        "schema_version": "quota_settlement_plan_v1",
        "identity": {
            "schema_version": "quota_settlement_identity_v0",
            "goal_id": "fixture-goal", "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001", "turn_instance_id": "turn-original",
            "effect_id": "fixture-goal:codex-fixture:todo_fixture0001:turn-original",
        },
        "ordered_steps": [
            {"kind": "validation", "owner": "agent", "command_condition": "todo_deliverable_complete"},
            {"kind": "durable_writeback", "owner": "agent", "precondition": "validation succeeded"},
            {"kind": "quota_spend", "owner": "agent", "expected_receipt": "quota_spend_receipt"},
            {"kind": "terminal_closeout", "owner": "agent", "conditional": True},
        ],
        "host_handoff": {"owner": "host", "kind": "scheduler_handoff", "inside_agent_settlement": False},
    }


def test_plan_and_long_commands_survive_without_mutating_cached_packet():
    source = _full_decision()
    cli = source["interaction_contract"]["cli_channel"]
    cli["settlement_plan"] = _plan()
    command = "loopx --runtime-root /" + "nested/" * 80 + " quota spend-slot --turn-instance-id turn-original --execute"
    cli["next_cli_actions"] = [command]
    cli["settlement_plan"]["ordered_steps"][2]["command_template"] = command
    before = deepcopy(source)
    envelope = build_turn_envelope(source)
    assert source == before
    assert envelope["writeback"]["settlement_plan"] == before["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert envelope["writeback"]["next_cli_actions"] == [command]
    assert envelope["action_signature"]["coverage"] == "turn_envelope_action_dimensions_v5"
    assert quota_action_signature_document(source) == turn_envelope_action_signature_document(envelope)


@pytest.mark.parametrize("mutation", ["identity", "order", "condition", "receipt", "handoff", "remove"])
def test_signature_rejects_settlement_semantic_loss(mutation):
    source = _full_decision()
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = _plan()
    envelope = build_turn_envelope(source)
    plan = envelope["writeback"]["settlement_plan"]
    if mutation == "identity":
        plan["identity"]["effect_id"] = "another-turn"
    elif mutation == "order":
        plan["ordered_steps"].reverse()
    elif mutation == "condition":
        plan["ordered_steps"][-1]["conditional"] = False
    elif mutation == "receipt":
        plan["ordered_steps"][2]["expected_receipt"] = "validation_receipt"
    elif mutation == "handoff":
        plan["host_handoff"]["inside_agent_settlement"] = True
    else:
        del envelope["writeback"]["settlement_plan"]
    assert quota_action_signature_document(source) != turn_envelope_action_signature_document(envelope)


def test_absent_plan_is_not_invented_for_historical_or_unbound_packets():
    source = _full_decision()
    before = build_turn_envelope(source)
    assert "settlement_plan" not in before["writeback"]
    assert before["action_signature"]["coverage"] == "turn_envelope_action_dimensions_v0"
    source["interaction_contract"]["cli_channel"].update(
        selection_required=True, next_cli_actions=[], spend_after_validation=False,
    )
    envelope = build_turn_envelope(source)
    assert "settlement_plan" not in envelope["writeback"]
    assert envelope["writeback"]["spend_after_validation"] is False


@pytest.mark.parametrize("profile_args, output_format, provider, ordinary_app", [
    (("--codex-app",), "json", "file", False),
    (("--runtime-profile", "generic_cli"), "json", "file", False),
    (("--runtime-profile", "codex_app_ssh_goal"), "json", "file", False),
    (("--runtime-profile", "generic_cli"), "markdown", "file", False),
    (("--runtime-profile", "generic_cli"), "markdown", "sqlite", False),
    (("--codex-app",), "json", "file", True),
    (("--runtime-profile", "codex_app_heartbeat"), "json", "sqlite", True),
    (("--codex-app",), "markdown", "sqlite", True),
])
def test_generated_commands_settle_original_turn_once(
    tmp_path, monkeypatch, profile_args, output_format, provider, ordinary_app,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    project, runtime, registry = _write_fixture(tmp_path / "quoted inputs")
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    args = ("quota", "should-run", *profile_args, "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
            "--turn-instance-id", TURN_ID, )
    presentation = ("--context-delivery", "off") if ordinary_app else ("--turn-envelope",)
    if output_format == "markdown":
        root = Path(loopx.__file__).resolve().parent.parent
        delivered = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "markdown", *args, *presentation],
            cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
            capture_output=True, text=True, check=False,
        )
        assert delivered.returncode == 0, delivered.stderr or delivered.stdout
        body, _ = read_cli_context_for_oracle(delivered.stdout, output_format="markdown")
        full = json.loads(body)
        plan = full["interaction_contract"]["cli_channel"]["settlement_plan"]
    elif ordinary_app:
        rc, full = _run_cli(registry, runtime, *args, *presentation, cwd=project)
        assert rc == 0, full
        plan = full["interaction_contract"]["cli_channel"]["settlement_plan"]
    else:
        rc, response = _run_cli(registry, runtime, *args, "--context-delivery", "auto", "--turn-envelope")
        assert rc == 0, response
        body, _ = read_cli_context_for_oracle(json.dumps(response), output_format="json")
        full = json.loads(body)
        rc, envelope = _run_cli(registry, runtime, *args, "--context-delivery", "off", "--turn-envelope")
        assert rc == 0, envelope
        assert_complete_turn_decision(envelope)
        plan = envelope["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert full["schema_version"] == "loopx_turn_envelope_v1"
    if ordinary_app:
        assert_complete_turn_decision(full)
        assert not {"context_delivery", "reference_file", "first_read_command"} & full.keys()
        assert plan["schema_version"] == "quota_settlement_plan_v1"
        assert plan["ordered_steps"][2]["owner"] == "agent"
    assert plan == full["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"] == full["heartbeat_receipt"]["settlement_identity"]
    assert [step["kind"] for step in plan["ordered_steps"]] == [
        "validation", "durable_writeback", "quota_spend", "terminal_closeout",
    ]
    assert plan["ordered_steps"][-1]["conditional"] is True
    assert plan["host_handoff"]["inside_agent_settlement"] is False
    spend_step = plan["ordered_steps"][2]
    runtime_spend = profile_args == ("--runtime-profile", "codex_app_ssh_goal")
    assert spend_step["owner"] == ("runtime" if runtime_spend else "agent")
    if runtime_spend:
        assert "command_template" not in spend_step
        assert plan["schema_version"] == "quota_settlement_plan_v2"
    else:
        spend_command = spend_step["command_template"]
        rc, premature = _run_generated_cli(spend_command, registry_path=registry)
        assert rc != 0, premature
    assert _spend_run_count(runtime) == 0
    writeback = plan["ordered_steps"][1]["command_template"]
    # A validated exclusion advances research without claiming the objective is met.
    # The same negative result is not a blocker merely because a candidate failed.
    guidance = plan["ordered_steps"][1]["precondition"]
    assert "Route elimination needs evidence" in guidance
    assert "failure alone is not progress" in guidance
    observation_only = writeback.replace("<validated_progress>", "validated_exclusion").replace(
        "<scale>", "single_surface").replace("<outcome>", "surface_only")
    rc, rejected_observation = _run_generated_cli(
        observation_only + " --progress-result-class unchanged"
        " --no-global-sync --suppress-external-sinks", registry_path=registry,
    )
    assert rc != 0, rejected_observation
    assert _spend_run_count(runtime) == 0
    invalid = writeback.replace("<validated_progress>", "validated_exclusion").replace(
        "<scale>", "single_surface").replace("<outcome>", "outcome_gap")
    rc, rejected = _run_generated_cli(
        invalid + " --progress-result-class advanced --progress-evidence-id exclusion-proof"
        " --no-global-sync --suppress-external-sinks", registry_path=registry,
    )
    assert rc != 0, rejected
    assert "requires --progress-result-class blocked" in rejected["error"]
    assert _spend_run_count(runtime) == 0
    writeback = writeback.replace("<validated_progress>", "validated_exclusion").replace(
        "<scale>", "single_surface").replace("<outcome>", "outcome_progress")
    writeback += " --progress-result-class advanced --progress-evidence-id exclusion-proof"
    rc, refreshed = _run_generated_cli(
        writeback + " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks",
        registry_path=registry,
    )
    assert rc == 0, refreshed
    # Recovery follows the actual owner; the retired spelling cannot force
    # a qualified native Host to expose the agent's manual spend command.
    if runtime_spend:
        assert refreshed["settlement_progress"]["state"] == "settled"
        assert refreshed["settlement_identity"] == plan["identity"]
        rc, replayed = _run_generated_cli(
            writeback + " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks",
            registry_path=registry,
        )
        assert rc == 0 and replayed["idempotent_replay"] is True, replayed
        assert replayed["settlement_progress"]["state"] == "settled"
    else:
        owed_command = refreshed["settlement_owed"]["command"]
        for replay, command in ((False, owed_command), (True, spend_command)):
            rc, spent = _run_generated_cli(command, registry_path=registry)
            assert rc == 0, spent
            assert spent["settlement_result"]["ok"] is True
            if replay:
                assert spent["idempotent_replay"] is True
    assert _spend_run_count(runtime) == 1
