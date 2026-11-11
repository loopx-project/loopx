"""A file gate created before its dependent is repairable through the public CLI."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result


@pytest.fixture
def tmp_path(tmp_path_factory):
    # The hashed SQLite filename must fit under the native Windows path limit.
    return tmp_path_factory.mktemp("gate")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_gate_first_link_consumes_scope_and_resumes_dependent(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(
        tmp_path, provider=provider, handoff_mode="hard_lease", registered_agents=["agent-a", "agent-b"])

    def cli(*args):
        return run_json_cli(*args, "--goal-id", "goal-a", registry_path=registry, runtime_root=runtime)

    def snapshot():
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a", include_leases=True)

    gate = cli("todo", "add", "--role", "user", "--task-class", "user_gate", "--agent-id", "agent-a",
        "--decision-scope", "resource:action:prices_file", "--text", "Provide inputs/prices.json")
    target = cli("todo", "add", "--role", "agent", "--task-class", "advancement_task", "--claimed-by", "agent-a",
        "--status", "blocked", "--required-decision-scope", "resource:action:prices_file",
        "--resume-when", f"todo_done:{gate['todo_id']}", "--text", "Calculate inventory value with supplied prices")
    before = snapshot()
    link_args = ("todo", "update", "--role", "user", "--todo-id", gate["todo_id"], "--agent-id", "agent-a",
        "--unblocks-todo-id", target["todo_id"], "--update-operation-id", "link-file-gate",
        "--update-expected-provider-revision", before["provider_revision"])
    assert cli(*link_args, "--dry-run")["status"] == "planned"
    assert snapshot() == before
    assert cli(*link_args)["status"] == "applied"
    assert cli(*link_args)["status"] == "replayed"
    linked = snapshot()
    assert linked["leases"] == before["leases"]
    assert cli("todo", "list", "--todo-id", target["todo_id"])["todo"]["required_decision_scopes"]

    prices = state.parents[3] / "inputs" / "prices.json"
    prices.parent.mkdir()
    prices.write_text('{"item": 125}\n', encoding="utf-8")
    assert json.loads(prices.read_text(encoding="utf-8")) == {"item": 125}
    completed = cli("todo", "complete", "--role", "user", "--todo-id", gate["todo_id"], "--agent-id", "agent-a",
        "--decision-outcome", "approve", "--evidence", "Requested prices file is present with integer prices")
    assert completed["decision_scope_resolution"]["remaining_required_decision_scopes"] == []
    assert completed["unblock_resume"]["state"] == "resumed"
    resumed = cli("todo", "list", "--todo-id", target["todo_id"])["todo"]
    assert resumed["status"] == "open"
    assert resumed["claimed_by"] == "agent-a"
    assert resumed["required_decision_scopes"] == []
    assert resumed["resume_condition"]["satisfied"] is True
    claim = cli("todo", "claim", "--todo-id", target["todo_id"], "--agent-id", "agent-a", "--claimed-by", "agent-a",
        "--claim-operation-id", "resume-value-work")
    assert claim["execution_managed"] is True

    denied, _ = run_json_cli_result("todo", "update", "--role", "user", "--goal-id", "goal-a",
        "--todo-id", gate["todo_id"], "--agent-id", "agent-a", "--unblocks-todo-id", "todo_other",
        registry_path=registry, runtime_root=runtime)
    assert denied != 0
