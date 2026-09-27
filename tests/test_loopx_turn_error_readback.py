"""Error readback preserves uncertainty without inventing execution facts."""
from __future__ import annotations

import pytest

from loopx.cli_commands.turn_decision import FreshTurnDecisionOwner, TurnTodoSelectionError
from loopx.cli_commands.turn_rendering import (
    build_turn_error_payload,
    render_loopx_turn_execution_markdown,
    render_loopx_turn_plan_markdown,
)


@pytest.mark.parametrize("command", ["plan", "run-once"])
def test_selection_refusal_keeps_quota_reason_in_json_and_markdown(command):
    calls = []

    def current_decision(**kwargs):
        calls.append(kwargs)
        return {"status_health_ok": False, "action_selection_qualification": {
            "requested_todo_id": "todo_fixture", "state": "deferred",
            "reason": "control_repair", "recovery_action": "reenter_guard_without_selection",
        }}

    owner = FreshTurnDecisionOwner(
        status_payload={"contract_summary": {"errors": 3}},
        scheduler_execution_context={}, operator_inbox_urgency_projector=lambda **_: {},
        build_turn_decision=current_decision, requested_todo_id="todo_fixture",
    )
    with pytest.raises(TurnTodoSelectionError) as caught:
        owner.resolve()
    assert calls == [{"requested_action_todo_id": "todo_fixture"}]
    result = build_turn_error_payload({}, caught.value, turn_command=command)
    assert result["error_code"] == "turn_todo_selection_deferred"
    assert result["selection_rejection"]["requested_todo_id"] == "todo_fixture"
    assert result["selection_rejection"]["reason_code"] == "control_repair"
    assert result["selection_rejection"]["contract_error_count"] == 3
    assert not any(result["effects"].values())
    assert "journal_observation" not in result
    renderer = render_loopx_turn_plan_markdown if command == "plan" else render_loopx_turn_execution_markdown
    assert "reason=control_repair" in renderer(result)


def test_pre_execution_error_preserves_hook_effects_without_claiming_a_host() -> None:
    result = build_turn_error_payload(
        {"effects": {"state_written": True}}, ValueError("invalid adapter"),
        turn_command="run-once",
    )
    assert result["effects"] == {
        "host_invoked": False, "state_written": True,
        "quota_spent": False, "scheduler_acknowledged": False,
    }
    assert "journal_observation" not in result


def test_interrupted_executor_retains_original_turn_observation_not_a_new_launch() -> None:
    inspection = {
        "journal_consistent": True, "journal_status": "in_progress",
        "completed_phases": ["host_execute", "typed_result", "validation"],
        "recorded_effects": {"host_invoked": True, "state_written": None,
                             "quota_spent": False, "scheduler_acknowledged": False},
        "recovery_decision": {"resume_from": "durable_writeback", "reinvoke_host": False},
        "private_body": "never expose this",
    }
    result = build_turn_error_payload(
        {"transaction": {"turn_key": "sha256:" + "a" * 64}},
        OSError("lost writeback reply"), turn_command="run-once",
        execution_started=True, journal_readback=inspection,
    )
    assert result["effects_scope"] == "current_invocation"
    assert all(value is None for value in result["effects"].values())
    assert result["journal_observation"]["recorded_effects"] == inspection["recorded_effects"]
    assert result["resume_turn_key"] == "sha256:" + "a" * 64
    assert "never expose" not in str(result)
    rendered = render_loopx_turn_execution_markdown(result)
    assert "lost writeback reply" in rendered
    assert "recovery_from: durable_writeback" in rendered
    assert "recovery_reinvoke_host: False" in rendered


def test_unreadable_journal_does_not_replace_original_error_with_false_effects() -> None:
    result = build_turn_error_payload(
        {}, ValueError("completion validation required"), turn_command="run-once",
        execution_started=True,
    )
    assert result["error"] == "completion validation required"
    assert result["effects"]["host_invoked"] is None
    assert result["effects"]["quota_spent"] is None
    assert result["journal_observation"] == {"scope": "original_turn", "status": "unavailable"}
