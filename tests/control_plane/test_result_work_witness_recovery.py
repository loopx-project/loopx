"""A completed result cannot recover from missing or changed work evidence."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.todos import add_goal_todo, list_goal_todos
from tests.test_goal_mode_mcp_settlement import (
    AGENT_ID, GOAL_ID, _complete_default_task, _control, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("damage", ["missing", "identity", "read_id", "basis"])
def test_original_completion_requires_intact_selected_work_witness(provider, damage, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, _ = _write_fixture(tmp_path, provider=provider)
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["adapter"] = {"kind": "fixture_v0", "status": "connected-delivery"}
    registry.write_text(json.dumps(registration), encoding="utf-8")
    added = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Verify the retained candidate.", task_class="advancement_task",
        claimed_by=AGENT_ID, continuation_policy="same_agent_non_delivery")
    runtime = tmp_path / "runtime"
    control = _control(registry)
    admitted = json.loads(control.should_run())
    identity = admitted["delivery_result_context"]["settlement_identity"]
    run_cli = control.run_cli

    def lose_completion_response(args, **kwargs):
        output = run_cli(args, **kwargs)
        if args[:2] == ["todo", "complete"] and json.loads(output).get("ok"):
            return json.dumps({"ok": False, "error": "synthetic completion response lost"})
        return output

    control.run_cli = lose_completion_response
    result = json.loads(_complete_default_task(control, added["todo_id"], AGENT_ID,
        "Candidate verified.", next_agent_todo="Verify the remaining candidate."))
    assert result["ok"] is False
    before = list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
        todo_id=added["todo_id"], runtime_root_arg=str(runtime))["todo"]
    assert before["status"] == "done"
    contexts = runtime / "goals" / GOAL_ID / "checkpoint-contexts"
    witness = next((contexts / "work-witnesses").glob("*.json"))
    if damage == "missing":
        witness.unlink()
    else:
        payload = json.loads(witness.read_text(encoding="utf-8"))
        if damage == "identity":
            payload["identity"]["agent_id"] = "other-agent"
        elif damage == "read_id":
            payload["read_context_id"] = "other-read"
        else:
            payload["work_basis"].pop("goal")
        witness.write_text(json.dumps(payload), encoding="utf-8")
    next(contexts.glob("*.json")).unlink()
    control = _control(registry)
    pending = json.loads(control.should_run())
    assert pending["result_committed"] is True
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    rejected = json.loads(_complete_default_task(control, added["todo_id"], AGENT_ID,
        "Candidate verified.", next_agent_todo="Verify the remaining candidate."))
    assert rejected["ok"] is False, rejected
    after = list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
        todo_id=added["todo_id"], runtime_root_arg=str(runtime))["todo"]
    assert after == before
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()] if index.exists() else []
    assert not any(row.get("classification") == "quota_slot_spent" or row.get("result_work_context") for row in rows)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("damage", [None, "read_id", "content"])
def test_direct_writeback_checks_original_completion_commitment(provider, damage, tmp_path, monkeypatch):
    """A parseable primary cannot substitute different original work before R."""
    from loopx.control_plane.runtime.private_permissions import require_private_path

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, _ = _write_fixture(tmp_path, provider=provider)
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["adapter"] = {"kind": "fixture_v0", "status": "connected-delivery"}
    registry.write_text(json.dumps(registration), encoding="utf-8")
    added = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Verify the retained candidate.", task_class="advancement_task",
        claimed_by=AGENT_ID, continuation_policy="same_agent_non_delivery")
    runtime = tmp_path / "runtime"
    control = _control(registry)
    admitted = json.loads(control.should_run())
    identity = admitted["delivery_result_context"]["settlement_identity"]
    run_cli = control.run_cli

    def lose_completion_response(args, **kwargs):
        output = run_cli(args, **kwargs)
        if args[:2] == ["todo", "complete"] and json.loads(output).get("ok"):
            return json.dumps({"ok": False, "error": "synthetic completion response lost"})
        return output

    control.run_cli = lose_completion_response
    lost = json.loads(_complete_default_task(control, added["todo_id"], AGENT_ID,
        "Candidate verified.", next_agent_todo="Verify the remaining candidate."))
    assert lost["ok"] is False
    before = list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
        todo_id=added["todo_id"], runtime_root_arg=str(runtime))["todo"]
    assert before["status"] == "done"
    contexts = runtime / "goals" / GOAL_ID / "checkpoint-contexts"
    primary = next(contexts.glob("*.json"))
    if damage is not None:
        receipt = json.loads(primary.read_text(encoding="utf-8"))
        if damage == "read_id":
            receipt["read_context_id"] = "substituted-original-read"
        else:
            receipt["substituted_content"] = "Not part of the original completion."
        primary.write_text(json.dumps(receipt), encoding="utf-8")
    result = json.loads(run_cli(["refresh-state", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--classification", "mcp_completed_turn_writeback",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--todo-id", added["todo_id"], "--turn-instance-id", identity["turn_instance_id"],
        "--completion-todo-id", added["todo_id"], "--completion-turn-key", identity["effect_id"],
        "--no-global-sync", "--suppress-external-sinks"]))
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()] if index.exists() else []
    result_rows = [row for row in rows if row.get("result_work_context")]
    if damage is None:
        assert result["ok"] is True and result["appended"] is True, result
        assert len(result_rows) == 1
        witness = next((contexts / "work-witnesses").glob("*.json"))
        require_private_path(witness.parent, directory=True)
        require_private_path(witness)
        require_private_path(primary)
        assert json.loads(witness.read_text(encoding="utf-8"))["commit_attempt"]
    else:
        assert result["ok"] is False and result.get("appended") is not True, result
        assert not result_rows
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)
    assert list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
        todo_id=added["todo_id"], runtime_root_arg=str(runtime))["todo"] == before
