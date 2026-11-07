from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
)

GOAL_ID = "mcp-completion-validation"
AGENT = "claude"

_FAIL_COMMAND = f'{shlex.quote(sys.executable)} -c "raise SystemExit(1)"'


def _write_fixture(tmp_path: Path, *, provider="file", mode="soft_claim") -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-08-19T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=[],
            handoff_mode=mode,
        ),
        state_path=state, provider=provider,
    )
    return registry, state


def _add_todo(registry: Path, *, validation_command: str | None = None) -> str:
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one bounded change.",
        task_class="advancement_task",
        claimed_by=AGENT,
        validation_command=validation_command,
    )
    return str(todo["todo_id"])


def _control(registry: Path) -> GoalModeMCPControlPlane:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": GOAL_ID,
            "registry": str(registry),
            "agent_id": AGENT,
        },
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    return control


def _agent_todo_status(state: Path, todo_id: str) -> str:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    item = next(
        item
        for item in todos["agent_todos"]["items"]
        if item["todo_id"] == todo_id
    )
    return str(item["status"])


def _first_json_blob(output: str) -> dict:
    return json.loads(output.split("\n--- ", 1)[0])


def test_mcp_complete_task_fails_closed_on_failing_declared_validation(
    tmp_path: Path,
) -> None:
    """The MCP route inherits the declared-command gate: a failing command
    blocks the completion with a typed receipt and skips settlement."""
    registry, state = _write_fixture(tmp_path)
    todo_id = _add_todo(registry, validation_command=_FAIL_COMMAND)

    output = _control(registry).complete_task(todo_id, AGENT, "claimed done")

    payload = _first_json_blob(output)
    assert payload["ok"] is False
    assert payload["completed"] is False
    assert payload["validation_blocked_completion"] is True
    assert (
        payload["validation_failure"]["validation_receipt"]["passed"]
        is False
    )
    assert "spend-slot" not in output
    assert "refresh-state" not in output
    assert _agent_todo_status(state, todo_id) != "done"


@pytest.mark.parametrize("no_follow_up", [False, True])
def test_mcp_advancement_validation_precedes_controller_owned_settlement(
    tmp_path: Path, no_follow_up: bool,
) -> None:
    registry, state = _write_fixture(tmp_path)
    validation = state.parent / "check.py"
    validation.write_text("from pathlib import Path\nPath('validation-ran').write_text('passed')\n")
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one repository advancement.",
        task_class="advancement_task",
        claimed_by=AGENT,
        validation_command=shlex.join([sys.executable, str(validation)]),
    )
    todo_id = str(todo["todo_id"])

    payload = _first_json_blob(
        _control(registry).complete_task(todo_id, AGENT, "validated delivery", no_follow_up=no_follow_up)
    )

    assert payload["ok"] is True, payload
    assert payload["completed"] is True
    assert payload["settlement_identity"]["todo_id"] == todo_id
    assert (state.parent / "validation-ran").read_text() == "passed"
    assert payload["settlement"]["durable_writeback"]["ok"] is True
    assert payload["settlement"]["quota_spend"]["appended"] is True
    assert ("terminal_closeout" in payload["settlement"]) is no_follow_up
    assert _agent_todo_status(state, todo_id) == "done"

@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_mcp_claim_reference_completes_once_with_managed_execution(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = _write_fixture(tmp_path, provider=provider, mode="hard_lease")
    todo_id = _add_todo(registry)
    control = _control(registry)
    claimed = json.loads(control.claim_task(todo_id, AGENT))
    assert claimed["ok"] and claimed["execution_managed"], claimed
    assert "lease" not in claimed
    reference = claimed["claim_operation_id"]
    other = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="A different bounded change.", task_class="advancement_task", claimed_by=AGENT)
    foreign_reference = json.loads(control.claim_task(other["todo_id"], AGENT))["claim_operation_id"]
    for invalid in ("", foreign_reference):
        denied = _first_json_blob(control.complete_task(todo_id, AGENT, "evidence:validated", claim_operation_id=invalid))
        assert not denied["ok"], denied
        assert _agent_todo_status(state, todo_id) == "open"
    args = dict(claim_operation_id=reference)
    completed = _first_json_blob(control.complete_task(todo_id, AGENT, "evidence:validated", **args))
    assert completed["ok"] and completed["completed"], completed
    assert completed["settlement"]["quota_spend"]["appended"]
    def snapshot():
        return read_canonical_todos_if_promoted(runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, include_leases=True)
    before = snapshot()
    assert next(lease for lease in before["leases"] if lease["todo_id"] == todo_id)["status"] == "released"
    replay = _first_json_blob(control.complete_task(todo_id, AGENT, "evidence:validated", **args))
    assert replay["ok"], replay
    assert snapshot() == before
    assert _agent_todo_status(state, todo_id) == "done"


def test_mcp_completion_schema_exposes_task_reference_not_lease_protocol(monkeypatch):
    import asyncio
    from mcp.server.fastmcp.exceptions import ToolError
    from loopx.goal_mode_mcp import create_fastmcp_server

    server, control = create_fastmcp_server(GoalModeMCPConfig(
        server_name="test", runtime_profile="claude_code", legacy_host_surface="claude_code"), lambda: {})
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "complete_task")
    properties = tool.inputSchema["properties"]
    assert "claim_operation_id" in properties
    assert not any("lease" in name for name in properties)
    with pytest.raises(TypeError, match="unexpected keyword"):
        control.complete_task("todo", AGENT, "evidence", task_lease_expected_version=1)
    writes = []
    monkeypatch.setattr(control, "complete_task", lambda *args, **kwargs: writes.append(kwargs) or "completed")
    for retired in ({"task_lease_expected_version": 1}, {"task_lease_idempotency_key": "old"}):
        with pytest.raises(ToolError, match="Unsupported complete_task"):
            asyncio.run(server.call_tool("complete_task", {
                "todo_id": "todo", "agent_id": AGENT, "evidence": "evidence", **retired}))
    assert writes == []
    asyncio.run(server.call_tool("complete_task", {
        "todo_id": "todo", "agent_id": AGENT, "evidence": "evidence", "claim_operation_id": "accepted-claim"}))
    assert len(writes) == 1 and writes[0]["claim_operation_id"] == "accepted-claim"
