"""Original-Turn retention and qualification under the enabled stride contract."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, REPO_ROOT, SELECTED_REPLAN_TODO_ID,
    _configure_completion_validation_todo, _configure_selected_todo_replan_fixture,
    _initialize_fixture_authority, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("program", ["candidate-cli", "refresh-state"])
def test_selected_replan_source_reaches_qualification_with_final_validator_pending(
    provider, program, tmp_path, monkeypatch,
):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _configure_completion_validation_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "source-qualification-original-turn"
    entries = project / "CLI entries with spaces"
    entries.mkdir()
    if sys.platform == "win32":
        launcher = entries / f"{program}.ps1"
        python = sys.executable.replace("'", "''")
        launcher.write_text(f"& '{python}' -m loopx.entrypoint @args\nexit $LASTEXITCODE\n", encoding="utf-8")
    else:
        launcher = entries / program
        launcher.write_text(f"#!{sys.executable}\nfrom loopx.entrypoint import main\nraise SystemExit(main())\n", encoding="utf-8")
        launcher.chmod(0o700)
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}

    def execute(command, **replacements):
        tokens = shlex.split(command) if isinstance(command, str) else command
        tokens = [str(replacements.get(token, token)) for token in tokens]
        if sys.platform == "win32":
            from loopx.command_invocation import command_argv
            tokens = command_argv(Path(tokens[0]), tokens[1:])
        result = subprocess.run(tokens, cwd=project, env=environment, capture_output=True, text=True)
        assert result.stdout.strip(), result.stderr
        return result.returncode, json.loads(result.stdout)

    prefix = [str(launcher), "--cli-bin", str(launcher), "--registry", str(registry),
              "--runtime-root", str(runtime), "--format", "json"]
    guard_args = [*prefix, "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
                  "--agent-id", AGENT_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
                  "--turn-instance-id", turn]
    rc, guard = execute(guard_args)
    if rc:
        assert guard["action_selection_qualification"]["state"] == "deferred", guard
        [selection] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        rc, guard = execute(selection)
    assert rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    control = guard["replan_action_packet"]["stride_control"]

    def readback():
        return read_heartbeat_settlement(runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
            todo_id=identity["todo_id"], turn_instance_id=turn, registry_path=registry)

    admitted = readback().heartbeat_receipt
    assert admitted["details"]["semantic_replan_obligation_id"] == control["reference_obligation_id"]
    assert admitted["details"]["semantic_replan_source_ref"]
    actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    rc, result = execute(actions[0])
    assert rc == 0, result
    retained = readback()
    assert retained.writeback_run["vision_checkpoint"]["satisfied"] is False
    assert not retained.writeback_run.get("autonomous_replan_ack", {}).get("recorded")
    assert retained.spend_run is None and _spend_run_count(runtime) == 0
    # A fresh process must recover the original retention, not a new admission.
    rc, cold = execute(guard_args)
    assert rc == 0 and cold["heartbeat_receipt"]["settlement_identity"] == identity, cold
    read, supplement, spend = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    rc, premature = execute(spend)
    assert rc == 1 and premature["appended"] is False, premature
    assert _spend_run_count(runtime) == 0
    rc, context = execute(read)
    assert rc == 0 and context["settlement_identity"] == identity, context
    todo = context["basis"]["todo"]
    assert todo["status"] == "open" and todo["completion_validation_required"] is True
    validation_digest = todo["completion_validation_sha256"]
    # The current Agent authors all three independent axes after the result read.
    decision = {
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Retention is verified; final task validation remains open."},
        "path_delta": {"outcome": "replan", "prior_assumption": "The open task could close directly.",
            "observed_reality": "Cold readback retains progress; final validation is still required.",
            "retained": ["Original result, task and final validation requirement."],
            "changed": ["Qualify progress before continuing final task validation."],
            "evidence_refs": [f"todo:{identity['todo_id']}"]},
        "stride_decision": {"schema_version": "stride_decision_v2", "strategy_version": "evidence_conditioned_v1",
            "reference_context_id": control["reference_context_id"],
            "reference_obligation_id": control["reference_obligation_id"],
            "basis": "uncertain_evidence", "action": "verify",
            "axes": {
                "work_slice": {"plan": "Qualify the original retained progress.", "reason": "Cold readback proves retention, not final completion."},
                "route_distance": {"plan": "Keep the original result and settlement owners.", "reason": "No evidence warrants replacing the retained candidate."},
                "validation_scope": {"plan": "Check refusal before direction and one debit after it.", "reason": "Progress qualification must not complete the final validator."}},
            "next_probe": {"scope": "One original-Turn qualification and replay.",
                "prediction": "Qualified progress settles while the task remains open.",
                "minimum_evidence": "Retained result, fresh direction and one replay-safe debit.",
                "adoption_criterion": "Original result and pending final validation are preserved.",
                "cost_bound": "One candidate and its cold recovery/replay checks."}},
    }
    decision_path = project / "fresh direction.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    rc, direction = execute(supplement, **{
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>": decision_path,
        "<read-context-id>": context["read_context_id"],
    })
    assert rc == 0 and direction["autonomous_replan_ack"]["recorded"] is True, direction
    assert direction["vision_checkpoint"]["satisfied"] is True
    rc, recovered = execute(guard_args)
    assert rc == 0, recovered
    [remaining] = recovered["interaction_contract"]["cli_channel"]["next_cli_actions"]
    rc, spent = execute(remaining)
    assert rc == 0 and spent["appended"] is True, spent
    assert execute(remaining)[1]["appended"] is False
    assert _spend_run_count(runtime) == 1
    final = readback()
    assert final.heartbeat_receipt == admitted
    assert final.writeback_event["event_id"] == retained.writeback_event["event_id"]
    rc, current = execute([*prefix, "todo", "list", "--goal-id", GOAL_ID,
        "--todo-id", identity["todo_id"], "--agent-id", AGENT_ID])
    assert rc == 0 and current["todo"]["status"] == "open", current
    assert current["todo"]["completion_validation_sha256"] == validation_digest
    assert not current["todo"].get("completion_receipt_id")
