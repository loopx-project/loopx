"""Settlement guidance renders the typed plan without changing its semantics."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from loopx.control_plane.todos.markdown import render_todo_markdown


def test_settlement_recovery_preserves_order_conditions_and_commands() -> None:
    payload = {
        "error": "same-Turn settlement is incomplete",
        "settlement_plan": {
            "schema_version": "quota_settlement_plan_v1",
            "ordered_steps": [
                None,
                {
                    "kind": "validation",
                    "precondition": "validate the original deliverable",
                    "command_condition": "todo_deliverable_complete",
                    "command_template": "loopx todo complete --todo-id todo_fixture",
                },
                {
                    "kind": "durable_writeback",
                    "precondition": "write back the validated result",
                    "conditional": True,
                    "command_template": "loopx refresh-state --todo-id todo_fixture",
                    "semantic_closeout_command_template": "loopx refresh-state --todo-id todo_fixture --delivery-boundary semantic_closeout",
                },
                {"kind": "quota_spend", "precondition": "writeback receipt exists"},
                {
                    "kind": "terminal_closeout",
                    "precondition": "all settlement receipts exist",
                    "command_template": "loopx todo complete --no-follow-up",
                },
            ],
        },
    }
    rendered = render_todo_markdown(payload)
    assert rendered.split("## Same-Turn settlement plan", 1)[1] == (
        "\n\n"
        "- validation: validate the original deliverable\n"
        "  - command (todo_deliverable_complete): `loopx todo complete --todo-id todo_fixture`\n"
        "- durable_writeback: write back the validated result\n"
        "  - command (conditional): `loopx refresh-state --todo-id todo_fixture`\n"
        "  - semantic closeout: `loopx refresh-state --todo-id todo_fixture --delivery-boundary semantic_closeout`\n"
        "- quota_spend: writeback receipt exists\n"
        "- terminal_closeout: all settlement receipts exist\n"
        "  - command: `loopx todo complete --no-follow-up`"
    )


@pytest.mark.parametrize("plan", [None, [], {}, {"schema_version": "unsupported"}])
def test_untyped_settlement_plan_does_not_render(plan: object) -> None:
    assert "Same-Turn settlement plan" not in render_todo_markdown(
        {"error": "incomplete", "settlement_plan": plan}
    )


def test_success_does_not_render_a_recovery_plan() -> None:
    assert "Same-Turn settlement plan" not in render_todo_markdown(
        {"ok": True, "settlement_plan": {"schema_version": "quota_settlement_plan_v1"}}
    )


def test_empty_typed_settlement_plan_keeps_its_heading() -> None:
    assert render_todo_markdown(
        {"error": "incomplete", "settlement_plan": {"schema_version": "quota_settlement_plan_v1"}}
    ).endswith("\n\n## Same-Turn settlement plan\n")


@pytest.mark.parametrize("handoff_mode", ["soft_claim", "hard_lease"])
def test_real_cli_recovery_markdown_exposes_the_same_semantic_command(tmp_path, monkeypatch, handoff_mode):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, runtime, provider="sqlite", handoff_mode=handoff_mode)
    binding = ["--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
               "--todo-id", fixture.TODO_ID, "--turn-instance-id", "markdown-feedback"]
    if handoff_mode == "hard_lease":
        code, claimed = fixture._run_cli(registry, runtime, "todo", "claim", "--goal-id", fixture.GOAL_ID,
            "--todo-id", fixture.TODO_ID, "--agent-id", fixture.AGENT_ID, "--claimed-by", fixture.AGENT_ID,
            "--claim-operation-id", "markdown-execution", cwd=project)
        assert code == 0 and claimed["execution_managed"], claimed
    code, guard = fixture._run_cli(registry, runtime, "quota", "should-run", *binding,
        "--runtime-profile", "generic_cli", cwd=project)
    assert code == 0 and guard["should_run"], guard
    complete = ["todo", "complete", *binding, "--no-follow-up", "--evidence", "evidence:route-feedback"]
    if handoff_mode == "hard_lease":
        complete += ["--claim-operation-id", "markdown-execution"]
    code, recovery = fixture._run_cli(registry, runtime, *complete, cwd=project)
    assert code == 1, recovery
    expected = recovery["settlement_plan"]["ordered_steps"][1]["semantic_closeout_command_template"]
    markdown = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "markdown", *complete], cwd=project,
        env={**os.environ, "PYTHONPATH": str(fixture.REPO_ROOT)}, capture_output=True, text=True)
    assert markdown.returncode == 1, markdown.stdout
    assert "semantic closeout: `" + expected + "`" in markdown.stdout
    spend_precondition = recovery["settlement_plan"]["ordered_steps"][2]["precondition"]
    assert "fresh open-Todo semantic checkpoint" in spend_precondition
    assert "- quota_spend: " + spend_precondition in markdown.stdout
