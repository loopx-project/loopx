"""Receipt-backed progress observation follows settlement, not Todo completion."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.goals.checkpoint_context_io import pending_first_delivery_progress
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _configure_completion_validation_todo,
    _initialize_fixture_authority, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("optional_patch", [False, True])
def test_open_progress_observation_agrees_with_original_settlement(
    provider, optional_patch, tmp_path, monkeypatch,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_completion_validation_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*args):
        rc, result = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, result
        return result

    def todo():
        return cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)["todo"]

    original = todo()
    assert original["completion_validation_required"] is True
    turn = "observed-open-progress"
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", turn)
    guard = cli("quota", "should-run", "--codex-app", *binding)
    assert guard["should_run"] is True
    options = ()
    if optional_patch:
        packet = project / "vision.json"
        packet.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
            "state": "vision_patch_proposed",
            "vision_patch": {"vision_summary": "Continue validating the scoped output.",
                             "acceptance_summary": "Intermediate evidence is valid; final validation remains open."},
            "path_delta": {"outcome": "continue", "prior_assumption": "The scoped route remains viable.",
                "observed_reality": "The intermediate output supports continuing that route.",
                "retained": ["The scoped route"], "evidence_refs": ["check:intermediate-output"]}}))
        options = ("--agent-vision-json", str(packet))
    refreshed = cli("refresh-state", *binding, "--classification", "validated_intermediate_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks", *options)
    assert refreshed["vision_checkpoint"]["required"] is False
    assert refreshed["vision_checkpoint"]["decision"] == ("patched" if optional_patch else "not_required")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    observed = refreshed["first_delivery_progress"]
    assert observed["stage"] == "settlement_pending", observed
    assert observed["direction_required"] is False and observed["direction_committed"] is False
    assert observed["outcome_retained"] is True and observed["result_committed"] is False
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before_observation = index.read_bytes()
    pending = pending_first_delivery_progress(runtime, GOAL_ID, AGENT_ID, registry_path=registry)
    assert pending["stage"] == "settlement_pending", pending
    assert index.read_bytes() == before_observation and _spend_run_count(runtime) == 0
    spend_args = ("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    paid = cli(*spend_args)
    assert paid["settlement_progress"]["state"] == "settled"
    assert paid["first_delivery_progress"]["stage"] == "settled", paid["first_delivery_progress"]
    assert paid["first_delivery_progress"]["direction_required"] is False
    assert _spend_run_count(runtime) == 1
    after_payment = index.read_bytes()
    assert pending_first_delivery_progress(runtime, GOAL_ID, AGENT_ID, registry_path=registry) is None
    cli("status", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--limit", "1")
    assert index.read_bytes() == after_payment
    assert cli(*spend_args)["appended"] is False
    assert index.read_bytes() == after_payment and _spend_run_count(runtime) == 1
    current = todo()
    assert current["status"] == "open"
    assert current["completion_validation_sha256"] == original["completion_validation_sha256"]
    assert current["completion_validation_required"] is True
    replay = cli("quota", "should-run", "--codex-app", *binding)
    assert replay["effective_action"] == "heartbeat_settled_skip", replay
    fresh_binding = (*binding[:-1], turn + "-next")
    fresh = cli("quota", "should-run", "--codex-app", *fresh_binding)
    assert fresh["should_run"] and fresh["selected_todo"]["todo_id"] == TODO_ID, fresh
    assert _spend_run_count(runtime) == 1
