from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_tool_behavior import (
    QUOTA_FIRST_TOOL_INSTRUCTION,
    ScriptedAssistantAction,
    ScriptedDoubaoExecTransport,
    ScriptedExecToolAction,
)
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (
    TERMINAL_SETTLEMENT_FIXTURE_TODO_ID,
    DoubaoTerminalSettlementToolBehaviorActor,
    _build_fixture,
)


def _latest_quota_packet(request: Mapping[str, Any]) -> dict[str, Any]:
    for message in reversed(request["messages"]):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and "interaction_contract" in payload:
            return payload
    raise AssertionError("scripted model did not receive the real quota packet")


def _writeback_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    command = packet["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
    command = (
        command.replace("<validated_progress>", "terminal_settlement_validated")
        .replace("<scale>", "single_surface")
        .replace("<outcome>", "outcome_progress")
        .replace('"${LOOPX_TURN:?}"', "turn-test-001")
    )
    return ScriptedExecToolAction(command=command)


def _spend_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    command = packet["interaction_contract"]["cli_channel"]["next_cli_actions"][1]
    return ScriptedExecToolAction(
        command=command.replace('"${LOOPX_TURN:?}"', "turn-test-001")
    )


def _terminal_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    packet = _latest_quota_packet(request)
    steps = packet["interaction_contract"]["cli_channel"]["settlement_plan"][
        "ordered_steps"
    ]
    command = next(
        item["command_template"]
        for item in steps
        if item["kind"] == "terminal_closeout"
    )
    return ScriptedExecToolAction(
        command=command.replace('"${LOOPX_TURN:?}"', "turn-test-001").replace(
            "'<validated evidence>'",
            "'fixture-settlement-proof'",
        )
    )


def _latest_reentry_transition(request: Mapping[str, Any]) -> dict[str, Any]:
    for message in request["messages"]:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        transition = (
            payload.get("replan_transition") if isinstance(payload, dict) else None
        )
        if isinstance(transition, dict):
            return transition
    raise AssertionError("scripted model did not receive the real rejection packet")


def _reentry_todo_action(index: int):
    def action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        transition = _latest_reentry_transition(request)
        commands = [
            item
            for item in transition["next_cli_actions"]
            if "loopx todo complete" in item
        ]
        command = commands[index]
        command = command[command.index("loopx ") :].replace(
            "<public-safe no-follow-up rationale>",
            f"validated terminal slice {index + 1}; no successor remains",
        )
        return ScriptedExecToolAction(command=command)

    return action


def _reentry_quota_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
    transition = _latest_reentry_transition(request)
    command = next(
        item for item in transition["next_cli_actions"] if "quota should-run" in item
    )
    return ScriptedExecToolAction(command=command)


@pytest.mark.parametrize("cat_options", ["", "-- "])
@pytest.mark.parametrize("batch_read", [False, True])
def test_real_tool_loop_settles_final_todo_after_spend(
    tmp_path: Path, cat_options: str, batch_read: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"',
                    "turn-test-001",
                )
            ),
            ScriptedExecToolAction(command=(
                f"cat {cat_options}.codex/goals/terminal-settlement-fixture/ACTIVE_GOAL_STATE.md "
                "fixture/settlement-proof.json"
                if batch_read else f"cat {cat_options}fixture/settlement-proof.json"
            )),
            _writeback_action,
            _spend_action,
            _terminal_action,
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="terminal-settlement-tool-loop-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["failure_code"] is None, str(receipt["failure_code"])
    assert receipt["qualification_passed"] is True, json.dumps(receipt, sort_keys=True)
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "delivery_validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    assert receipt["settlement_receipt_steps"] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    assert receipt["terminal_order_observed"] is True
    assert receipt["selected_todo_id"] == TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
    assert receipt["decision"] == "execute"
    assert receipt["boundary"] == {
        "raw_prompt_persisted": False,
        "raw_provider_response_persisted": False,
        "raw_command_persisted": False,
        "filesystem_writes_executed": True,
        "writes_limited_to_temporary_fixture": True,
        "external_writes_executed": False,
        "shell_commands_executed": False,
        "read_only_host_commands_executed": True,
    }
    assert "settlement-proof.json" not in json.dumps(receipt, sort_keys=True)

    first = transport.requests[0]
    system_prompt = first["messages"][0]["content"]
    assert QUOTA_FIRST_TOOL_INSTRUCTION in system_prompt
    assert "the host validates that tool result" in system_prompt
    assert "immediately after the proof read" in system_prompt
    assert first["messages"][1] == {"role": "user", "content": fixture.task_body}
    assert TERMINAL_SETTLEMENT_FIXTURE_TODO_ID not in fixture.task_body
    quota_result = _latest_quota_packet(transport.requests[2])
    assert quota_result["selected_todo"]["todo_id"] == (
        TERMINAL_SETTLEMENT_FIXTURE_TODO_ID
    )


@pytest.mark.parametrize("batch_read", [False, True])
def test_tool_loop_rejects_terminal_closeout_before_spend(
    tmp_path: Path, batch_read: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"',
                    "turn-test-001",
                )
            ),
            ScriptedExecToolAction(command=(
                "cat -- .codex/goals/terminal-settlement-fixture/ACTIVE_GOAL_STATE.md "
                "fixture/settlement-proof.json"
                if batch_read else "cat fixture/settlement-proof.json"
            )),
            _terminal_action,
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="terminal-closeout-before-spend",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "terminal_closeout_before_spend"
    assert receipt["settlement_receipt_steps"] == []
    assert receipt["tool_call_receipts"][-1]["redacted_command_shape"] == {
        "parseable": True,
        "token_count_bucket": 12,
        "executable_family": "control_plane",
        "workspace_operation": "other",
        "git_operation": None,
        "python_module": None,
        "contains_loopx": True,
        "loopx_command_path": "todo",
        "loopx_command_action": "complete",
        "operator_before_loopx": False,
        "operator_after_loopx": False,
        "has_environment_assignment": False,
        "mentions_proof_target": False,
        "multiline": False,
    }


@pytest.mark.parametrize("batch_read", [False, True])
def test_tool_loop_rejects_spend_before_writeback(
    tmp_path: Path, batch_read: bool,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=fixture.quota_guard_command.replace(
                    '"${LOOPX_TURN:?}"',
                    "turn-test-001",
                )
            ),
            ScriptedExecToolAction(command=(
                "cat -- .codex/goals/terminal-settlement-fixture/ACTIVE_GOAL_STATE.md "
                "fixture/settlement-proof.json"
                if batch_read else "cat fixture/settlement-proof.json"
            )),
            _spend_action,
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="terminal-spend-before-writeback",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "quota_spend_before_writeback"
    assert receipt["settlement_receipt_steps"] == []


@pytest.mark.parametrize("read_command", [
    "cat -- fixture/missing.json fixture/settlement-proof.json",
    "cat -- fixture/settlement-proof.json fixture/missing.json",
])
def test_batch_read_failure_cannot_admit_terminal_writeback(
    tmp_path: Path, read_command: str,
) -> None:
    fixture = _build_fixture(tmp_path / "oracle")
    transport = ScriptedDoubaoExecTransport([
        ScriptedExecToolAction(command=fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"', "turn-test-001",
        )),
        ScriptedExecToolAction(command=read_command),
        _writeback_action,
    ])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder", transport=transport,
    ).qualify(
        qualification_id="terminal-batch-read-failure", fixture_root=tmp_path / "actor",
    )
    assert receipt["qualification_passed"] is False
    assert receipt["settlement_receipt_steps"] == []
    assert len(transport.requests) == 2


def test_real_rejection_loop_settles_projected_todos_then_stops(
    tmp_path: Path,
) -> None:
    transport = ScriptedDoubaoExecTransport(
        [
            _reentry_todo_action(0),
            _reentry_todo_action(1),
            _reentry_quota_action,
            ScriptedAssistantAction(content="Terminal lifecycle is settled."),
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify_reentry(
        qualification_id="terminal-reentry-tool-loop-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True, json.dumps(receipt, sort_keys=True)
    assert receipt["failure_code"] is None
    assert receipt["provider_call_count"] == 4
    assert receipt["tool_call_count"] == 3
    assert receipt["projected_todo_count"] == 2
    assert receipt["settled_todo_count"] == 2
    assert receipt["completion_identity_preserved"] is True
    assert receipt["terminal_quota_observed"] is True
    assert receipt["model_stopped_after_terminal_quota"] is True
    assert receipt["observed_tool_sequence"] == [
        "todo_lifecycle_settlement",
        "todo_lifecycle_settlement",
        "terminal_quota_reentry",
    ]
    transition = _latest_reentry_transition(transport.requests[0])
    assert len(transition["triggers"]) == 2
    assert all(
        trigger["completion_identity_source"] == "unscoped_completion"
        for trigger in transition["triggers"]
    )
    assert all(
        str(trigger["completion_turn_key"]).startswith("local_completion_")
        for trigger in transition["triggers"]
    )
    receipt_text = json.dumps(receipt, sort_keys=True)
    assert all(
        trigger["todo_id"] not in receipt_text for trigger in transition["triggers"]
    )


def test_rejection_loop_rejects_repeated_refresh(tmp_path: Path) -> None:
    transport = ScriptedDoubaoExecTransport(
        [
            ScriptedExecToolAction(
                command=(
                    "loopx refresh-state --goal-id terminal-reentry-fixture "
                    "--agent-id codex-terminal-reentry"
                )
            )
        ]
    )

    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify_reentry(
        qualification_id="terminal-reentry-repeat-refresh",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "repeated_refresh_state"
    assert receipt["settled_todo_count"] == 0


def test_rejection_loop_rejects_completion_identity_drift(tmp_path: Path) -> None:
    def drifted_action(request: Mapping[str, Any]) -> ScriptedExecToolAction:
        action = _reentry_todo_action(0)(request)
        command = action.command.replace(
            "local_completion_",
            "local_completion_drift_",
            1,
        )
        return ScriptedExecToolAction(command=command)

    transport = ScriptedDoubaoExecTransport([drifted_action])
    receipt = DoubaoTerminalSettlementToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify_reentry(
        qualification_id="terminal-reentry-identity-drift",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "lifecycle_settlement_identity_mismatch"
    assert receipt["settled_todo_count"] == 0
