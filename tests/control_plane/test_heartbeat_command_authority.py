"""A configured Goal command cannot override the admitted work or settlement."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
import test_quota_settlement_cli as cli

from loopx.control_plane.quota.turn_envelope import turn_plan_host_source
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.work_items.interaction_contract import build_interaction_contract
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority


COMMAND = "loopx synthetic-command"


def _command_fixture(tmp_path, provider):
    project, runtime, registry = cli._write_fixture(tmp_path)
    cli._configure_selectable_alternative(project)
    cli._initialize_fixture_authority(project, runtime, provider=provider)
    code, approval = cli._run_cli(
        registry, runtime, "operator-gate", "--goal-id", cli.GOAL_ID,
        "--decision", "approve", "--reason-summary", "Synthetic read-only command approval",
        "--agent-command", COMMAND, "--no-global-sync", cwd=project,
    )
    assert code == 0, approval
    return project, runtime, registry


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unbound_command_cannot_replace_current_selection(tmp_path, provider):
    project, runtime, registry = _command_fixture(tmp_path, provider)
    args = ("quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
            "--agent-id", cli.AGENT_ID, "--turn-instance-id", cli.TURN_ID)

    code, unbound = cli._run_cli(registry, runtime, *args, cwd=project)
    assert code == 0, unbound
    assert unbound["interaction_contract"]["cli_channel"]["selection_required"] is True
    assert "command" not in unbound["heartbeat_recommendation"]
    assert unbound["interaction_contract"]["user_channel"]["action_required"] is False
    assert unbound["heartbeat_recommendation"]["agent_must_attempt"] == unbound["interaction_contract"]["agent_channel"]["must_attempt"]



@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_selection_and_settlement_override_goal_command(tmp_path, provider):
    project, runtime, registry = _command_fixture(tmp_path, provider)
    args = ("quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
            "--agent-id", cli.AGENT_ID, "--turn-instance-id", cli.TURN_ID)
    binding = ("--todo-id", cli.TODO_ID)
    captures = []
    settlement_identities = []
    receipt_counts = []
    for name, view in (("default", ()), ("detail", ("--include-detail", "all"))):
        capture = tmp_path / (name + "-capture")
        code, current = cli._run_cli(registry, runtime, *args, *binding, *view,
                                     "--decision-output-dir", str(capture), cwd=project)
        assert code == 0, current
        saved = capture / "decision.json"
        original = saved.read_bytes()
        full = json.loads(original)
        assert current == full and full["schema_version"] == "loopx_turn_envelope_v1"
        interaction = full["interaction_contract"]
        recommendation = full["heartbeat_recommendation"]
        assert interaction["agent_channel"]["delivery_allowed"] is True
        assert interaction["user_channel"]["action_required"] is False
        assert "command" not in recommendation
        assert recommendation["recommended_mode"] == interaction["mode"]
        assert recommendation["reason"] == interaction["agent_channel"]["primary_action"]
        assert recommendation["spend_policy"] == interaction["cli_channel"]["spend_policy"]
        assert full["selected_todo"]["todo_id"] == cli.TODO_ID
        plan = build_loopx_turn_plan(full, host="generic-cli", execution_mode="isolated-headless",
                                    turn_instance_id=cli.TURN_ID)
        authority = extract_turn_authority(turn_plan_host_source(plan))
        assert authority["primary_action"] == interaction["agent_channel"]["primary_action"]
        settlement_identity = interaction["cli_channel"]["settlement_plan"]["identity"]
        assert settlement_identity == full["heartbeat_receipt"]["settlement_identity"]
        assert settlement_identity == plan["transaction"]["settlement_plan"]["identity"]
        assert full["agent_command"] == COMMAND  # Configuration remains source evidence.
        captures.append((saved, original))
        settlement_identities.append(settlement_identity)
        receipt_counts.append(cli._heartbeat_receipt_count(runtime, cli.TURN_ID))

    identity = ("--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
                "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID)
    refresh_args = (
        "refresh-state", *identity,
        "--classification", "validated_intermediate_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation",
        "--no-global-sync", "--suppress-external-sinks", "--settle-turn", "--settlement-receipt",
    )
    code, refresh = cli._run_cli(registry, runtime, *refresh_args, cwd=project)
    assert code == 0 and refresh["quota_spend"]["appended"], refresh
    assert all(identity == refresh["settlement_identity"] for identity in settlement_identities)
    assert refresh["settlement_progress"]["state"] == "settled"
    code, retry = cli._run_cli(registry, runtime, *refresh_args, cwd=project)
    assert code == 0 and retry["idempotent_replay"] and not retry["appended"], retry
    assert retry["settlement_result"]["receipts"] == refresh["settlement_result"]["receipts"]
    code, settled = cli._run_cli(registry, runtime, *args, *binding, cwd=project)
    assert code == 0, settled
    assert settled["interaction_contract"]["mode"] == "heartbeat_settled_skip"
    assert "command" not in settled["heartbeat_recommendation"]
    assert cli._spend_run_count(runtime) == 1
    assert len(set(receipt_counts)) == 1
    assert cli._heartbeat_receipt_count(runtime, cli.TURN_ID) == receipt_counts[0]
    assert all(saved.read_bytes() == original for saved, original in captures)
    code, plan = cli._run_cli(registry, runtime, "turn", "plan", "--goal-id", cli.GOAL_ID,
                              "--agent-id", cli.AGENT_ID, *binding,
                              "--turn-instance-id", "read-only-command-plan", "--include-transaction-detail", cwd=project)
    assert code == 0, plan
    assert cli.TODO_ID in extract_turn_authority(turn_plan_host_source(plan))["primary_action"]
    assert COMMAND not in extract_turn_authority(turn_plan_host_source(plan))["primary_action"]


@pytest.mark.parametrize("mode", ["monitor_quiet_skip", "autonomous_replan", "agent_workspace_repair"])
def test_overridden_command_guidance_uses_current_contract(mode):
    payload = {
        "goal_id": "command-authority-fixture", "should_run": True,
        "effective_action": mode, "normal_delivery_allowed": False,
        "agent_identity": {"agent_id": "synthetic-agent"},
        "execution_obligation": {"kind": mode, "must_attempt_work": mode != "monitor_quiet_skip",
                                 "delivery_allowed": False},
        # Refinement may already have changed the mode while retaining command.
        "heartbeat_recommendation": {"recommended_mode": mode, "command": COMMAND,
                                     "spend_policy": "spend after the old command", "notify": "DONT_NOTIFY"},
    }
    contract = build_interaction_contract(payload)
    recommendation = payload["heartbeat_recommendation"]
    assert "command" not in recommendation
    assert recommendation["recommended_mode"] == contract["mode"]
    assert recommendation["agent_must_attempt"] == contract["agent_channel"]["must_attempt"]
    assert recommendation["spend_policy"] == contract["cli_channel"]["spend_policy"]


def test_unscoped_command_and_command_free_guidance_keep_compatibility():
    payload = {"goal_id": "command-authority-fixture", "should_run": True,
               "normal_delivery_allowed": True,
               "agent_identity": {"agent_id": "synthetic-agent"},
               "execution_obligation": {"must_attempt_work": True, "delivery_allowed": True},
               "heartbeat_recommendation": {"recommended_mode": "run_agent_command", "command": COMMAND,
                                            "notify": "DONT_NOTIFY", "spend_policy": "spend after validation"}}
    original = deepcopy(payload["heartbeat_recommendation"])
    contract = build_interaction_contract(payload)
    assert contract["mode"] == "bounded_delivery"
    assert payload["heartbeat_recommendation"] == original
    payload["heartbeat_recommendation"].pop("command")
    payload["heartbeat_recommendation"]["recommended_mode"] = "bounded_progress_segment"
    payload["selected_todo"] = {"todo_id": "todo_current"}
    original = deepcopy(payload["heartbeat_recommendation"])
    build_interaction_contract(payload)
    assert payload["heartbeat_recommendation"] == original


def test_governed_intent_retains_its_exact_command_without_the_goal_command():
    from loopx.control_plane.quota.live_decision import _apply_pending_capability_intent_precedence

    payload = {"goal_id": "command-authority-fixture", "should_run": True,
               "agent_identity": {"agent_id": "synthetic-agent"},
               "agent_command": COMMAND,
               "heartbeat_recommendation": {"command": COMMAND, "notify": "DONT_NOTIFY"}}
    intent_command = "loopx synthetic-intent --execute"
    assert _apply_pending_capability_intent_precedence(payload, {
        "state": "pending", "action_summary": "Consume the authorized synthetic intent",
        "command": intent_command,
    })
    assert payload["pending_capability_intent"]["command"] == intent_command
    assert payload["interaction_contract"]["mode"] == "governed_capability_intent"
    assert intent_command in json.dumps(payload["interaction_contract"]["cli_channel"])
    assert "command" not in payload["heartbeat_recommendation"]
