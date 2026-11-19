from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_tool_behavior import (
    QuotaToolContext,
    QUOTA_FIRST_TOOL_INSTRUCTION,
    scripted_assistant_response as _stop_response,
    scripted_exec_tool_response as _tool_response,
)
from loopx.control_plane.testing.scoped_gate_successor_tool_behavior import (
    SCOPED_GATE_FINALIZATION_MESSAGE,
    SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_MAX_CALLS,
    SCOPED_GATE_SUCCESSOR_TODO_ID,
    DoubaoScopedGateSuccessorToolBehaviorActor,
    _build_scoped_gate_fixture,
)


@pytest.mark.parametrize("extra_reads", [0, SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_MAX_CALLS - 2])
def test_real_quota_notice_and_selected_successor_execute_in_one_tool_loop(
    tmp_path: Path, extra_reads: int,
) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    requests: list[dict[str, Any]] = []
    responses = [
        _tool_response(
            "call-quota",
            fixture.quota_guard_command.replace(
                '"${LOOPX_TURN:?}"',
                "turn-scoped-001",
            ),
        ),
        *(_tool_response(f"call-read-{index}", "pwd") for index in range(extra_reads)),
        _tool_response(
            "call-successor",
            "cat fixture/scoped-successor.json",
        ),
        _stop_response(
            content=(
                "Non-blocking notice: review the unrelated product first screen. "
                "Continuing with the selected successor now."
            ),
        ),
    ]

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        requests.append(json.loads(kwargs["body"]))
        return responses[len(requests) - 1]

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-real-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["failure_code"] is None
    assert receipt["decision"] == "execute"
    assert receipt["selected_todo_id"] == SCOPED_GATE_SUCCESSOR_TODO_ID
    assert receipt["user_action_required"] is True
    assert receipt["must_attempt_work"] is True
    assert receipt["delivery_allowed"] is True
    assert receipt["quiet_noop_allowed"] is False
    assert receipt["non_blocking_notice_surfaced"] is True
    assert receipt["selected_action_matched_todo"] is True
    assert receipt["tool_call_count"] == extra_reads + 2
    assert receipt["tool_call_limit"] == SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_MAX_CALLS
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        *(["workspace_read"] * extra_reads),
        "selected_action",
    ]
    assert receipt["boundary"] == {
        "raw_prompt_persisted": False,
        "raw_provider_response_persisted": False,
        "raw_assistant_content_persisted": False,
        "raw_command_persisted": False,
        "filesystem_writes_executed": True,
        "writes_limited_to_temporary_fixture": True,
        "external_writes_executed": False,
        "shell_commands_executed": False,
        "read_only_host_commands_executed": True,
    }
    assert "unrelated product first screen" not in json.dumps(
        receipt,
        sort_keys=True,
    )

    quota_result = QuotaToolContext.from_output(requests[1]["messages"][-1]["content"]).packet
    assert quota_result["selected_todo"]["todo_id"] == (
        SCOPED_GATE_SUCCESSOR_TODO_ID
    )
    user_channel = quota_result["interaction_contract"]["user_channel"]
    assert user_channel["action_required"] is True
    assert user_channel["notify"] == "NOTIFY"
    assert user_channel["actions"] == [
        "[P2] Review the unrelated product first screen."
    ]
    assert quota_result["interaction_contract"]["agent_channel"][
        "must_attempt"
    ] is True
    assert "response_plan" not in quota_result["interaction_contract"]
    system_prompt = requests[0]["messages"][0]["content"]
    assert QUOTA_FIRST_TOOL_INSTRUCTION in system_prompt
    assert "grants no authority to inspect or act on the user-gate target" in (
        system_prompt
    )
    assert "next tool call must be `cat <that exact path>`" in system_prompt
    assert "After the selected Todo tool succeeds, call no more tools" in (
        system_prompt
    )
    assert len(requests) == extra_reads + 3
    assert "tools" not in requests[-1]
    assert "tool_choice" not in requests[-1]
    assert requests[-1]["messages"][-2]["role"] == "tool"
    assert requests[-1]["messages"][-1] == {
        "role": "system",
        "content": SCOPED_GATE_FINALIZATION_MESSAGE,
    }


def test_gate_notice_plus_executed_successor_proves_non_blocking_semantics(
    tmp_path: Path,
) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response(
            "call-successor",
            "cat fixture/scoped-successor.json",
        ),
        _stop_response(
            ("Bounded qualification detail. " * 80)
            + "User gate: review the unrelated product first screen."
        ),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-scoped-fallback-copy",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["non_blocking_notice_surfaced"] is True


def test_model_can_recover_from_one_unexecuted_read_shape(
    tmp_path: Path,
) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response("call-rejected-read", "find . -type f | sort"),
        _tool_response(
            "call-successor",
            "cat fixture/scoped-successor.json",
        ),
        _stop_response("User gate: review the unrelated product first screen."),
    ]
    call_count = 0
    requests: list[dict[str, Any]] = []

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        nonlocal call_count
        requests.append(json.loads(kwargs["body"]))
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-read-recovery",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "workspace_read_rejected",
        "selected_action",
    ]
    feedback = requests[2]["messages"][-1]
    assert feedback["role"] == "tool"
    assert "without executing it" in feedback["content"]
    assert "`cat <that exact path>`" in feedback["content"]
    assert "do not rediscover the workspace or inspect state" in feedback["content"]
    assert "no additional read scope or tool budget" in feedback["content"]
    assert "using pwd, ls" not in feedback["content"]
    assert receipt["tool_call_limit"] == SCOPED_GATE_SUCCESSOR_TOOL_BEHAVIOR_MAX_CALLS


def test_mutating_command_is_not_retried_as_read_intent(tmp_path: Path) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response("call-mutation", "rm fixture/scoped-successor.json"),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-reject-mutation",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "unexpected_command"
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "unexpected_command",
    ]


def test_successor_action_without_visible_notice_fails(tmp_path: Path) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response(
            "call-successor",
            "cat fixture/scoped-successor.json",
        ),
        _stop_response("The selected successor read completed."),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-no-notice",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "non_blocking_notice_not_surfaced"
    assert receipt["non_blocking_notice_surfaced"] is False
    assert receipt["selected_action_matched_todo"] is True


def test_stopping_after_non_blocking_notice_fails(tmp_path: Path) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _stop_response(
            "Please review the first screen; I will wait before continuing."
        ),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-waited",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "waited_on_non_blocking_notice"
    assert receipt["observed_tool_sequence"] == ["quota_should_run"]


def test_notice_does_not_authorize_the_unrelated_decoy(tmp_path: Path) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response(
            "call-decoy",
            "cat fixture/blocked-first-screen.json",
            content=(
                "Non-blocking notice: review the unrelated product first screen. "
                "Continuing with the selected successor now."
            ),
        ),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-decoy",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "wrong_selected_todo_target"
    assert receipt["selected_action_matched_todo"] is False


def test_notice_before_quota_does_not_satisfy_post_quota_obligation(
    tmp_path: Path,
) -> None:
    fixture = _build_scoped_gate_fixture(tmp_path / "oracle")
    responses = [
        _tool_response(
            "call-quota",
            fixture.quota_guard_command,
            content=(
                "Non-blocking notice: review the unrelated product first screen. "
                "Continuing with the selected successor now."
            ),
        ),
        _tool_response(
            "call-successor",
            "cat fixture/scoped-successor.json",
        ),
        _stop_response("The selected successor read completed."),
    ]
    call_count = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal call_count
        response = responses[call_count]
        call_count += 1
        return response

    receipt = DoubaoScopedGateSuccessorToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="scoped-gate-successor-premature-notice",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "non_blocking_notice_not_surfaced"
