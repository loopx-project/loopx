"""The ordinary legacy writer must qualify its own terminal reentry source."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from loopx.cli import main
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from tests.control_plane.test_todo_mutation_authority import (
    AUTHOR_AGENT, GOAL_ID, REVIEW_AGENT, _write_fixture,
)


@pytest.mark.parametrize("edit,expected_actor", [
    (["--evidence", "evidence:planning-only"], AUTHOR_AGENT),
    (["--priority", "P2"], AUTHOR_AGENT),
    (["--clear-priority"], AUTHOR_AGENT),
    (["--note", "Peer context.", "--evidence", "evidence:mixed"], REVIEW_AGENT),
    (["--text", "Clarify source.", "--evidence", "evidence:mixed"], REVIEW_AGENT),
    (["--claimed-by", REVIEW_AGENT], REVIEW_AGENT),
])
def test_legacy_cli_update_preserves_planning_actor(tmp_path, edit, expected_actor):
    registry, state = _write_fixture(tmp_path, lifecycle_authority=[{
        "agent_id": REVIEW_AGENT, "actions": ["update", "reassign"], "requires_reason": True,
    }])
    prefix = ["--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"),
              "--format", "json"]

    def call(*args, code=0):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            actual = main([*prefix, *args])
        payload = json.loads(output.getvalue())
        assert actual == code, {key: payload.get(key) for key in ("error", "error_code")}
        return payload

    try:
        todo = call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
                    "--text", "Verify source.", "--priority", "P1",
                    "--claimed-by", AUTHOR_AGENT, "--task-class", "advancement_task")["todo_id"]
        common = ["todo", "update", "--goal-id", GOAL_ID, "--todo-id", todo]
        call(*common, "--agent-id", AUTHOR_AGENT, "--note", "Owner context.",
             "--evidence", "evidence:original")
        detail = ["todo", "list", "--goal-id", GOAL_ID, "--todo-id", todo]
        assert call(*detail)["todo"]["last_actor_agent_id"] == AUTHOR_AGENT
        delegated = [*common, "--agent-id", REVIEW_AGENT, *edit]
        before = state.read_bytes()
        call(*delegated, code=1)  # A grant still requires its actual reason.
        assert state.read_bytes() == before
        delegated += ["--authority-reason", "Maintain the source."]
        call(*delegated, "--dry-run")
        assert state.read_bytes() == before
        changed = call(*delegated)
        assert changed["changed"]
        row = call(*detail)["todo"]
        assert row["last_actor_agent_id"] == expected_actor
        if "--claimed-by" not in edit:
            assert row["claimed_by"] == AUTHOR_AGENT
        after = state.read_bytes()
        assert not call(*delegated)["changed"]
        assert state.read_bytes() == after
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import restart_effect_runtime; restart_effect_runtime()"],
                       env=dict(os.environ), check=True, capture_output=True, timeout=30)


def test_legacy_cli_actor_survives_planning_clear_and_terminal_replay(tmp_path, monkeypatch):
    from tests.control_plane.test_todo_mutation_authority import _add_agent_todo

    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    registry, state = _write_fixture(tmp_path, lifecycle_authority=[{
        "agent_id": REVIEW_AGENT, "actions": ["update"], "requires_reason": True,
    }])
    todo = _add_agent_todo(registry, claimed_by=None)["todo_id"]
    prefix = ["--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"), "--format", "json"]

    def call(*args, code=0):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main([*prefix, *args])
        payload = json.loads(output.getvalue())
        assert rc == code, {key: payload.get(key) for key in ("error", "error_code", "reason", "mutation_authority")}
        return payload

    def detail():
        return call("todo", "list", "--goal-id", GOAL_ID, "--todo-id", todo)["todo"]

    update = ["todo", "update", "--goal-id", GOAL_ID, "--todo-id", todo]
    call("todo", "claim", "--goal-id", GOAL_ID, "--todo-id", todo,
         "--agent-id", AUTHOR_AGENT, "--claimed-by", AUTHOR_AGENT)
    assert detail()["last_actor_agent_id"] == AUTHOR_AGENT
    call(*update, "--agent-id", AUTHOR_AGENT, "--note", "Owner context", "--evidence", "evidence:owner-validation", "--priority", "P2")
    assert detail()["last_actor_agent_id"] == AUTHOR_AGENT
    peer = [*update, "--agent-id", REVIEW_AGENT, "--authority-reason", "Maintain validated planning evidence"]
    planning_edit = ["--evidence", "evidence:peer-validation"]
    before = state.read_bytes()
    call(*update, "--agent-id", REVIEW_AGENT, "--evidence", "evidence:unadmitted", code=1)
    assert state.read_bytes() == before
    call(*peer, *planning_edit, "--dry-run")
    assert state.read_bytes() == before
    call(*peer, *planning_edit)
    assert detail()["last_actor_agent_id"] == AUTHOR_AGENT
    assert detail()["claimed_by"] == AUTHOR_AGENT
    before = state.read_bytes()
    call(*peer, *planning_edit)
    assert state.read_bytes() == before

    call(*peer, "--note", "Peer copy correction", "--evidence", "evidence:mixed-validation")
    assert detail()["last_actor_agent_id"] == REVIEW_AGENT
    assert detail()["claimed_by"] == AUTHOR_AGENT
    # A supplied but unchanged copy field must not turn planning into a new actor.
    call(*update, "--agent-id", AUTHOR_AGENT, "--note", "Peer copy correction", "--clear-priority")
    assert detail()["last_actor_agent_id"] == REVIEW_AGENT
    before = state.read_bytes()
    call(*peer, "--note", "Peer copy correction", "--clear-priority", "--dry-run")
    assert state.read_bytes() == before

    retire = ["todo", "supersede", "--goal-id", GOAL_ID, "--todo-id", todo,
              "--agent-id", AUTHOR_AGENT, "--reason", "Retire the validated approach."]
    call(*retire, "--dry-run")
    assert state.read_bytes() == before
    call(*retire)
    assert detail()["last_actor_agent_id"] == AUTHOR_AGENT
    before = state.read_bytes()
    call(*retire)
    assert state.read_bytes() == before


@pytest.mark.parametrize("historical_source", [False, True])
def test_legacy_completion_actor_recovers_and_settles_exact_turn(tmp_path, monkeypatch, historical_source):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\nobjective: Validate the source.\n---\n\n"
                     "# Active Goal State\n\n## Objective\n\nValidate the source.\n\n"
                     "## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n\n")
    goal, agent = "legacy-source", "source-owner"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "domain": "synthetic-local-test", "status": "active", "repo": str(project),
        "state_file": state.name, "quota": {"compute": 1},
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [agent, "other-owner"]},
    }]}))
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def call(*args, code=0):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main([*prefix, *args])
        payload = json.loads(output.getvalue())
        assert rc == code, {key: payload.get(key) for key in ("error", "error_code", "reason", "settlement_progress")}
        return payload

    try:
        assert call("check", "--scan-root", str(project))["ok"]
        added = call("todo", "add", "--goal-id", goal, "--role", "agent", "--claimed-by", agent,
                     "--text", "Validate the source.", "--task-class", "advancement_task",
                     "--action-kind", "validate", "--target-key", "source")
        todo = added["todo_id"]
        completion = ["todo", "complete", "--goal-id", goal, "--todo-id", todo, "--agent-id", agent,
                      "--evidence", "evidence:source-validated"]
        call(*completion)
        detail = ["todo", "list", "--goal-id", goal, "--todo-id", todo]
        original = call(*detail)["todo"]
        assert original["last_actor_agent_id"] == agent
        original_bytes = state.read_bytes()
        call(*completion)
        assert state.read_bytes() == original_bytes  # Exact replay does not stamp provenance.
        peer_completion = list(completion)
        peer_completion[peer_completion.index("--agent-id") + 1] = "other-owner"
        call(*peer_completion, code=1)
        assert state.read_bytes() == original_bytes
        if historical_source:
            # A persisted completion from before actor transport was supported.
            state.write_text(state.read_text().replace(f" last_actor_agent_id={agent}", ""))
            assert call(*detail)["todo"].get("last_actor_agent_id") is None
            original_bytes = state.read_bytes()

        turn = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        quota = ["quota", "should-run", "--goal-id", goal, "--agent-id", agent,
                 "--available-capability", "shell_command", "--codex-app", "--turn-instance-id", turn]
        guard = call(*quota)
        obligation = guard["autonomous_replan_obligation"]
        assert obligation["resolution_mode"] == "todo_lifecycle_settlement"
        receipt = guard["heartbeat_receipt"]
        closeout = ["todo", "complete", "--goal-id", goal, "--todo-id", todo, "--agent-id", agent,
                    "--completion-identity-key", original["completion_turn_key"], "--no-follow-up",
                    "--note", "The source is validated."]
        call(*closeout, "--dry-run")
        assert state.read_bytes() == original_bytes
        call(*closeout)
        row = call(*detail)["todo"]
        assert row["last_actor_agent_id"] == agent
        assert row["completion_turn_key"] == original["completion_turn_key"]
        assert row["completed_at"] == original["completed_at"]
        recovered = call(*quota)
        assert recovered["heartbeat_receipt"]["settlement_identity"] == receipt["settlement_identity"]
        assert recovered["autonomous_replan_obligation"]["resolution_mode"] == "receipt_bound_replan_settlement"
        assert recovered["autonomous_replan_obligation"]["obligation_id"] == obligation["obligation_id"]

        admitted = receipt["recorded_at"]
        old_updated = (datetime.fromisoformat(admitted.replace("Z", "+00:00")) - timedelta(seconds=5)).isoformat()
        for change in [{"last_actor_agent_id": None}, {"last_actor_agent_id": "other-owner"},
                       {"completion_turn_key": "different-completion"}, {"updated_at": old_updated},
                       {"no_followup": False}]:
            candidate = {"source_obligation": obligation, "obligation": obligation, "lifecycle_settled": True,
                         "guard_recorded_at": admitted, "items": [{**row, **change}]}
            with pytest.raises(EffectRuntimeRejected) as rejected:
                effect_runtime_result("work_item.replan_semantics.project", {
                    "operation": "receipt_bound_obligation", "guard_scoped": True, "replay_phase": "open",
                    "selected_obligation_id": obligation["obligation_id"], "agent_id": agent,
                    "current_obligation": None, "transition_candidates": [candidate]})
            assert rejected.value.diagnostic_code == "heartbeat_receipt_identity_conflict"

        vision = tmp_path / "vision.json"
        vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_closed",
            "vision_patch": {"acceptance_summary": "The accepted source actor and identity fences are verified."},
            "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
                "prior_assumption": "The source was unverified.", "observed_reality": "The source and contrary cases passed.",
                "retained": ["Accepted actor provenance and exact completion identity"],
                "stopped": ["Repeat validation of the completed fixture"], "evidence_refs": ["evidence:source-validated"]}}))
        call("refresh-state", "--goal-id", goal, "--agent-id", agent,
             "--turn-instance-id", turn, "--replan-obligation-id", obligation["obligation_id"],
             "--autonomous-replan-recorded", "--repair-delta-kind", "goal_vision_patch",
             "--classification", "source_verified", "--delivery-batch-scale", "multi_surface",
             "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
             "--progress-result-class", "advanced", "--progress-evidence-id", "evidence:source-validated",
             "--no-global-sync", "--suppress-external-sinks")
        spend = ["quota", "spend-slot", "--goal-id", goal, "--agent-id", agent, "--turn-instance-id", turn,
                 "--replan-obligation-id", obligation["obligation_id"], "--slots", "1", "--source", "heartbeat", "--execute"]
        assert call(*spend)["ok"]
        assert call(*spend)["ok"]
        index = next(runtime.glob("goals/*/runs/index.jsonl"))
        runs = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
        assert sum(run.get("classification") == "quota_slot_spent" for run in runs) == 1
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import restart_effect_runtime; restart_effect_runtime()"],
                       env=dict(os.environ), check=True, capture_output=True, timeout=30)
