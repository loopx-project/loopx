"""A configured Goal command cannot override the admitted work or settlement."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
import test_quota_settlement_cli as cli

from loopx.control_plane.quota.turn_envelope import build_turn_envelope
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
    receipt_counts = []
    for name, view in (("compact", ()), ("detail", ("--include-detail", "all")),
                       ("envelope", ("--turn-envelope",))):
        capture = tmp_path / (name + "-capture")
        code, current = cli._run_cli(registry, runtime, *args, *binding, *view,
                                     "--decision-output-dir", str(capture), cwd=project)
        assert code == 0, current
        saved = capture / "decision.json"
        original = saved.read_bytes()
        full = json.loads(original)
        interaction = full["interaction_contract"]
        recommendation = full["heartbeat_recommendation"]
        assert interaction["agent_channel"]["delivery_allowed"] is True
        assert interaction["user_channel"]["action_required"] is False
        assert "command" not in recommendation
        assert recommendation["recommended_mode"] == interaction["mode"]
        assert recommendation["reason"] == interaction["agent_channel"]["primary_action"]
        assert recommendation["spend_policy"] == interaction["cli_channel"]["spend_policy"]
        assert full["selected_todo"]["todo_id"] == cli.TODO_ID
        envelope = current if name == "envelope" else build_turn_envelope(current)
        assert envelope["action_signature"]["matches"] is True
        assert extract_turn_authority({"turn_envelope": envelope})["primary_action"] == interaction["agent_channel"]["primary_action"]
        assert full["agent_command"] == COMMAND  # Configuration remains source evidence.
        if name != "envelope":
            assert current["heartbeat_recommendation"] == recommendation
        captures.append((saved, original))
        receipt_counts.append(cli._heartbeat_receipt_count(runtime, cli.TURN_ID))

    identity = ("--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
                "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID)
    code, refresh = cli._run_cli(
        registry, runtime, "refresh-state", *identity,
        "--classification", "validated_intermediate_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation",
        "--no-global-sync", "--suppress-external-sinks", cwd=project,
    )
    assert code == 0, refresh
    spend_args = ("quota", "spend-slot", *identity, "--slots", "1",
                  "--source", "heartbeat", "--execute")
    code, spent = cli._run_cli(registry, runtime, *spend_args, cwd=project)
    assert code == 0 and spent["appended"] is True, spent
    code, retry = cli._run_cli(registry, runtime, *spend_args, cwd=project)
    assert code == 0 and retry["appended"] is False, retry
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
                              "--turn-instance-id", "read-only-command-plan", cwd=project)
    assert code == 0, plan
    assert cli.TODO_ID in extract_turn_authority(plan)["primary_action"]
    assert COMMAND not in json.dumps(plan["turn_envelope"]["contract_capsule"])


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
