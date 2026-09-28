"""Settlement guidance renders the typed plan without changing its semantics."""
from __future__ import annotations

import pytest

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
