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


@pytest.mark.parametrize("command", ["plan", "run-once"])
def test_runtime_conflict_preserves_machine_diagnostic_without_parsing_message(command):
    from loopx.control_plane.effect_runtime import EffectRuntimeConflict

    result = build_turn_error_payload(
        {"transaction": {"turn_key": "sha256:" + "b" * 64}},
        EffectRuntimeConflict("The operation was refused", diagnostic_code="goal_stopped"),
        turn_command=command, execution_started=True,
    )
    assert result["error_code"] == "goal_stopped"
    assert result["error"] == "The operation was refused"
    assert all(value is None for value in result["effects"].values())
    if command == "run-once":
        assert result["resume_turn_key"] == "sha256:" + "b" * 64


def test_generic_error_does_not_invent_a_machine_diagnostic():
    result = build_turn_error_payload({}, ValueError("goal_stopped"), turn_command="run-once")
    assert "error_code" not in result


def test_ordinary_turn_cli_keeps_runtime_conflict_after_host_result(tmp_path, monkeypatch, capsys):
    import json
    import sys

    from loopx.cli import main
    from loopx.control_plane.effect_runtime import EffectRuntimeConflict
    from loopx.control_plane.turn_driver import executor
    from tests.test_loopx_turn_codex_cli import _fake_codex
    from tests.test_loopx_turn_driver import _write_live_fixture

    project, runtime, registry = _write_live_fixture(tmp_path)
    executable, log = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    original_write = executor._write_journal
    rejected = []

    def reject_result(path, journal, **kwargs):
        if "host_result" in journal:
            rejected.append(journal["turn_key"])
            raise EffectRuntimeConflict("Result no longer accepted", diagnostic_code="goal_stopped")
        return original_write(path, journal, **kwargs)

    monkeypatch.setattr(executor, "_write_journal", reject_result)
    code = main([
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host", "codex-cli", "--project", str(project), "--codex-bin", str(executable),
        "--codex-sandbox", "read-only", "--no-global-sync", "--iteration-context", "fresh",
        "--turn-instance-id", "runtime-conflict-after-host-result",
        "--validation-command-json", json.dumps([sys.executable, "-c", "pass"]), "--execute",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and rejected and log.exists(), payload
    assert payload["error_code"] == "goal_stopped"
    assert payload["resume_turn_key"] == rejected[0]
    assert payload["effects"]["quota_spent"] is None
    observation = payload["journal_observation"]
    assert observation["status"] == "observed"
    assert observation["completed_phases"] == []
    assert observation["recorded_effects"]["quota_spent"] is False


def test_existing_exception_code_and_payload_keep_precedence():
    from loopx.control_plane.effect_runtime import EffectRuntimeConflict

    class CodedConflict(EffectRuntimeConflict):
        code = "existing_boundary_code"
        payload = {"reason": "existing_reason"}

    result = build_turn_error_payload(
        {}, CodedConflict("refused", diagnostic_code="goal_stopped"), turn_command="run-once",
    )
    assert result["error_code"] == "existing_boundary_code"
    assert result["reason"] == "existing_reason"
