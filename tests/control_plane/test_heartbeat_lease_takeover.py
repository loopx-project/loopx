"""Current ownership may resume an unfinished Turn, without accepting stale work."""
from __future__ import annotations

import json
import shlex
import sys

import pytest

from tests.control_plane.test_execution_loss_closeout_cli import (
    OWNER, TURN, _canonical, _fixture, _guard_args, _readback, _receipt_lines, _spend_run_count,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("complete", [False, True], ids=["progress", "completion"])
def test_current_holder_resumes_original_turn_and_settles_once(tmp_path, monkeypatch, capsys, provider, complete):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    cli = context["cli"]
    original_lines = _receipt_lines(context)
    code, decision = cli(*_guard_args(context))
    assert code == 0 and decision["normal_delivery_allowed"] is True, decision
    readback = _readback(context)
    assert readback.heartbeat_receipt["details"]["settlement_receipt_revision"] == "lease_takeover"
    assert readback.prepared_lease["prepared_lease_epoch"] == context["current_lease"]["lease_epoch"]
    assert _receipt_lines(context)[:len(original_lines)] == original_lines
    assert _spend_run_count(context["runtime"]) == 0

    if complete:
        code, completed = cli("todo", "complete", "--goal-id", context["goal_id"],
            "--agent-id", OWNER, "--todo-id", context["todo"], "--turn-instance-id", TURN,
            "--claim-operation-id", context["current_claim"]["claim_operation_id"],
            "--evidence", "evidence:current-validation")
        assert code == 0 and completed["ok"], completed
        assert (context["project"] / "completion-validator-ran").read_text() == "ran"

    plan = decision["interaction_contract"]["cli_channel"]["settlement_plan"]
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Revalidate saved work under current ownership."},
        "path_delta": {"outcome": "continue", "prior_assumption": "Prior work needs current verification.",
                       "observed_reality": "The current holder verified the saved result.",
                       "retained": ["Original task"], "evidence_refs": ["evidence:revalidated-result"]},
    }))
    tokens = shlex.split(writeback["semantic_closeout_command_template"])
    replacements = {"<validated_progress>": "revalidated_result", "<scale>": "implementation",
                    "<outcome>": "outcome_progress", "<file>": str(vision)}
    args = [replacements.get(token, token) for token in tokens[tokens.index("refresh-state"):]]
    args += ["--no-global-sync", "--suppress-external-sinks"]
    stale = args.copy()
    key_index = next(i for i, value in enumerate(stale) if value.startswith("--task-lease-idempotency-key="))
    stale[key_index] = f"--task-lease-idempotency-key={context['original_lease']['idempotency_key']}"
    stale[stale.index("--task-lease-expected-version") + 1] = str(context["original_lease"]["version"])
    code, rejected = cli(*stale)
    assert code == 1 and rejected["appended"] is False, rejected
    assert _spend_run_count(context["runtime"]) == 0
    code, result = cli(*args)
    assert code == 0 and result["settlement_progress"]["state"] == "settled", result
    assert result["settlement_identity"] == plan["identity"]
    assert result["settlement_identity"]["turn_instance_id"] == TURN
    assert _spend_run_count(context["runtime"]) == 1
    code, replay = cli(*args)
    assert code == 0 and replay["idempotent_replay"], replay
    assert _spend_run_count(context["runtime"]) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_takeover_uses_the_current_validator_and_does_not_inherit_old_acceptance(tmp_path, monkeypatch, capsys, provider):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    cli = context["cli"]
    claim = context["current_claim"]["claim_operation_id"]
    validation = [sys.executable, "-c", "from pathlib import Path; assert Path('current-artifact').read_text() == 'valid'"]
    code, updated = cli("todo", "update", "--goal-id", context["goal_id"], "--todo-id", context["todo"],
        "--agent-id", OWNER, "--claim-operation-id", claim, "--update-operation-id", "current-validator",
        "--update-expected-provider-revision", _canonical(context)["provider_revision"],
        "--validation-command-json", json.dumps(validation))
    assert code == 0 and updated["ok"], json.dumps(updated)
    code, resumed = cli(*_guard_args(context))
    assert code == 0 and resumed["normal_delivery_allowed"], resumed
    completion = ["todo", "complete", "--goal-id", context["goal_id"], "--agent-id", OWNER,
        "--todo-id", context["todo"], "--turn-instance-id", TURN, "--claim-operation-id", claim,
        "--evidence", "evidence:current-validator"]
    code, rejected = cli(*completion)
    assert code != 0 and rejected["ok"] is False, rejected
    assert _spend_run_count(context["runtime"]) == 0
    (context["project"] / "current-artifact").write_text("valid")
    code, completed = cli(*completion)
    assert code == 0 and completed["ok"], completed
    assert not (context["project"] / "completion-validator-ran").exists()
