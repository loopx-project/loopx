"""Conversation evidence reads use real canonical providers without granting work."""

import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.capabilities.manager_context.inspection import (
    CONTEXT_TOOL_NAME, TOOL_NAME, ManagerInspection,
)
from loopx.todos import add_goal_todo, list_goal_todos


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_common_context_recovers_constraints_from_canonical_authority(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    note = "Public research context. " * 30 + "Keep this a draft; do not publish."
    scope = {"schema_version": "decision_scope_v0", "kind": "write_scope",
             "granularity": "action", "scope_key": "public_report"}
    add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                  text="Collect public evidence", claimed_by="agent-a")
    before = list_goal_todos(registry_path=registry, goal_id="goal-a")
    dependency = before["todos"][0]["todo_id"]
    add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                  text="Prepare the research report", claimed_by="agent-a", note=note,
                  status="deferred", resume_when="todo_done:" + dependency,
                  required_decision_scopes=[scope])
    canonical = list_goal_todos(registry_path=registry, goal_id="goal-a")
    target = next(row for row in canonical["todos"] if row.get("note") == note)
    state.unlink()  # The compatibility display is not the source of these reads.
    allowed, observed = [True], []
    tool = ManagerInspection(
        context={"goals": [{"goal_id": "goal-a"}]}, registry_path=registry,
        runtime_root=runtime, owner_scope=True, scope_valid=lambda: allowed[0],
        record=observed.append,
    )
    for tool_name in (TOOL_NAME, CONTEXT_TOOL_NAME):
        overview = tool.read(tool_name, {"view": "todos", "goal_id": "goal-a"})
        row = next(row for row in overview["rows"] if row["todo_id"] == target["todo_id"])
        assert row["content_truncated"] is True
        assert row["resume_when"] == "todo_done:" + dependency
        assert row["resume_ready"] is False
        assert row["required_decision_scopes"] == [scope]
        exact = tool.read(tool_name, {"view": "todos", "goal_id": "goal-a",
                                      "todo_id": target["todo_id"]})
        assert exact["rows"][0]["continuation"] == note
        assert exact["rows"][0]["content_truncated"] is False
        assert exact["source"]["authority_revision"] == canonical["authority_read"]["provider_revision"]
        assert not tool.read(tool_name, {"view": "todos", "goal_id": "outside",
                                         "todo_id": target["todo_id"]})["ok"]
    allowed[0] = False
    assert not tool.read(CONTEXT_TOOL_NAME, {"view": "todos", "goal_id": "goal-a"})["ok"]
    assert len(observed) == 4
    assert list_goal_todos(registry_path=registry, goal_id="goal-a") == canonical
    assert not state.exists()

    # The shipped CLI uses the same exact selector but an external audience.
    cli = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
         "goal-portfolio", "--manager-view", "todos", "--goal-id", "goal-a",
         "--todo-id", target["todo_id"]], capture_output=True, text=True, timeout=45,
    )
    assert cli.returncode == 0, cli.stderr
    exported = json.loads(cli.stdout)
    assert [row["todo_id"] for row in exported["rows"]] == [target["todo_id"]]
    assert exported["rows"][0]["required_decision_scopes"] == [scope]
    assert "continuation" not in exported["rows"][0]
    assert note not in cli.stdout
