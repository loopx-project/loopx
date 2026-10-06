from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo

GOAL_ID = "mcp-completion-validation"
AGENT = "claude"

_FAIL_COMMAND = f'{shlex.quote(sys.executable)} -c "raise SystemExit(1)"'


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
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
                "common_runtime_root": str(tmp_path / "runtime"),
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
    assert payload["validation"]["passed"] is False
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

def test_expected_lease_version_annotation_rejects_bool_at_the_boundary() -> None:
    """FastMCP validates tool arguments with pydantic, and lax pydantic
    coerces JSON ``true`` to ``1`` BEFORE any loopx code runs - by the time
    the lease CAS compares versions the bool is indistinguishable from a
    legitimate 1.  The boundary annotation is therefore the only place the
    rejection can happen, and it is single-sourced so the control-plane
    method and the FastMCP wrapper cannot drift."""

    import pydantic

    from loopx import goal_mode_mcp

    adapter = pydantic.TypeAdapter(goal_mode_mcp.ExpectedTaskLeaseVersion)
    for disguised in (True, False):
        try:
            adapter.validate_python(disguised)
        except pydantic.ValidationError:
            pass
        else:
            raise AssertionError(f"bool {disguised} was coerced to an int")
    assert adapter.validate_python(7) == 7
    assert adapter.validate_python(None) is None

    import typing

    hints = typing.get_type_hints(
        goal_mode_mcp.GoalModeMCPControlPlane.complete_task, include_extras=True
    )
    assert hints["task_lease_expected_version"] == goal_mode_mcp.ExpectedTaskLeaseVersion
