from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from control_plane.quota_context_fixture import context_reading_transport, latest_quota_payload
from loopx.control_plane.todos.todo_semantics import todo_summary_monitor_schedule_gap_items
from loopx.control_plane.testing.capability_monitor_repair_tool_behavior import (
    CAPABILITY_REPAIR_BLOCKED_TODO_ID,
    CAPABILITY_REPAIR_MONITOR_TODO_ID,
    CAPABILITY_REPAIR_TARGET_CAPABILITY,
    DoubaoCapabilityMonitorRepairToolBehaviorActor,
    _build_capability_repair_fixture,
)
from loopx.control_plane.testing.model_tool_behavior import (
    QuotaToolContext,
    ScriptedExecToolAction,
    scripted_assistant_response as _scripted_assistant_response,
    scripted_exec_tool_response as _tool_response,
)


def _stop_response(content: str = "Waiting.") -> dict[str, Any]:
    return _scripted_assistant_response(content)


def _callsite_command(*, target: str = "fixture/private-source.json") -> str:
    return f"cat {target}"


def _projected_reentry_command(request: Mapping[str, Any]) -> str:
    for message in reversed(request.get("messages") or []):
        if not isinstance(message, Mapping) or message.get("role") != "tool":
            continue
        try:
            packet = QuotaToolContext.from_output(str(message.get("content") or "")).packet
        except json.JSONDecodeError:
            continue
        reentry = (packet.get("interaction_contract") or {}).get("cli_channel", {}).get("runtime_capability_reentry")
        if isinstance(reentry, Mapping):
            return str(reentry["candidates"][0]["command"])
    raise AssertionError("quota result did not project runtime capability re-entry")


def test_real_quota_capability_repair_executes_before_monitor_fallback(
    tmp_path: Path,
) -> None:
    fixture = _build_capability_repair_fixture(tmp_path / "oracle")
    assert "--available-capability shell" in fixture.quota_guard_command
    assert "--available-capability filesystem_read" in fixture.quota_guard_command
    assert "--available-capability filesystem_write" in fixture.quota_guard_command
    assert "--available-capability network" in fixture.quota_guard_command
    assert "--available-capability private_read" not in fixture.quota_guard_command
    assert fixture.quota_guard_command in fixture.task_body

    def repair_action(request):
        channel = latest_quota_payload(request)["interaction_contract"]["cli_channel"]
        return ScriptedExecToolAction(channel["runtime_capability_reentry"]["candidates"][0]["command"])

    transport = context_reading_transport([
        ScriptedExecToolAction(fixture.quota_guard_command),
        ScriptedExecToolAction(_callsite_command()),
        repair_action,
    ])
    requests = transport.requests

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-monitor-repair-real-001",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is True
    assert receipt["failure_code"] is None
    assert receipt["decision"] == "execute"
    assert receipt["selected_todo_id"] is None
    assert receipt["user_action_required"] is False
    assert receipt["must_attempt_work"] is True
    assert receipt["delivery_allowed"] is False
    assert receipt["quiet_noop_allowed"] is False
    assert receipt["repair_missing"] == [CAPABILITY_REPAIR_TARGET_CAPABILITY]
    assert receipt["capability_callsite_verified"] is True
    assert receipt["same_turn_quota_reentry_completed"] is True
    assert receipt["blocked_todo_reentered"] is True
    assert receipt["same_turn_delivery_allowed"] is True
    assert receipt["monitor_fallback_avoided"] is True
    sequence = receipt["observed_tool_sequence"]
    assert sequence[0] == "quota_should_run"
    assert sequence[1:-2] and set(sequence[1:-2]) == {"context_read"}
    assert sequence[-2:] == ["capability_callsite", "quota_reentry"]
    assert receipt["boundary"] == {
        "raw_prompt_persisted": False,
        "raw_provider_response_persisted": False,
        "raw_command_persisted": False,
        "writes_limited_to_temporary_fixture": True,
        "external_writes_executed": False,
        "shell_commands_executed": False,
        "task_facing_reads_executed": True,
        "control_plane_writes_executed": False,
        "advancement_checkpoints_written": False,
    }
    assert CAPABILITY_REPAIR_BLOCKED_TODO_ID not in json.dumps(
        receipt,
        sort_keys=True,
    )

    trigger_message = requests[0]["messages"][1]["content"]
    assert "<heartbeat>" in trigger_message
    assert (
        "<current_time_iso>2026-08-12T00:00:00+08:00</current_time_iso>"
        in trigger_message
    )
    assert fixture.task_body in trigger_message

    quota_result = latest_quota_payload(requests[-1])
    # Native output is navigation, not an ordering of duplicated inline keys.
    # Preserve receipt metadata and validate the canonical repair owner below;
    # capability repair does not itself establish admission or settlement.
    assert isinstance(quota_result["heartbeat_receipt"], Mapping)
    assert quota_result["interaction_contract"]["cli_channel"][
        "runtime_capability_reentry"]["candidates"]
    assert quota_result.get("selected_todo") is None
    assert quota_result["interaction_contract"]["mode"] == (
        "capability_bridge_repair"
    )
    assert quota_result["capability_gate"]["action"] == "repair_bridge"
    assert CAPABILITY_REPAIR_TARGET_CAPABILITY in quota_result[
        "capability_gate"
    ]["repair_missing"]
    monitors = todo_summary_monitor_schedule_gap_items(quota_result["agent_todo_summary"])
    assert [row["todo_id"] for row in monitors] == [CAPABILITY_REPAIR_MONITOR_TODO_ID]


def test_actor_rejects_capability_repair_before_quota(tmp_path: Path) -> None:
    def transport(**_: Any) -> Mapping[str, Any]:
        return _tool_response("call-1", _callsite_command())

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-repair-before-quota",
        fixture_root=tmp_path,
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "capability_callsite_before_quota"


def test_actor_rejects_bounded_json_registry_preview_after_quota(
    tmp_path: Path,
) -> None:
    fixture = _build_capability_repair_fixture(tmp_path / "oracle")
    commands = [
        fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"',
            "turn-capability-preview-001",
        ),
        (
            "cat $HOME/.codex/loopx/registry.global.json | "
            "python3 -m json.tool 2>/dev/null | head -200"
        ),
    ]
    calls = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal calls
        command = commands[calls]
        calls += 1
        return _tool_response(f"call-{calls}", command)

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-registry-preview",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "post_quota_backtracking"
    assert receipt["observed_tool_sequence"] == [
        "quota_should_run",
        "post_quota_backtracking",
    ]


def test_actor_allows_bounded_fixture_layout_preview_before_repair(
    tmp_path: Path, monkeypatch,
) -> None:
    fixture_root = tmp_path / "actor"
    fixture = _build_capability_repair_fixture(fixture_root)
    # Construct canonical authority once; the actor reuses this prepared fixture.
    monkeypatch.setattr(
        "loopx.control_plane.testing.capability_monitor_repair_tool_behavior._build_capability_repair_fixture",
        lambda root: fixture,
    )
    state_path = (
        fixture.project_root
        / ".codex/goals/portfolio-goal/ACTIVE_GOAL_STATE.md"
    )
    commands = [
        (
            f"cd {fixture.project_root} && "
            "export LOOPX_TURN=${LOOPX_TURN:-2026-08-11T16:00:00Z} && "
            "ls -la 2>/dev/null && "
            "echo ---STATE--- && "
            f"cat {state_path.relative_to(fixture.project_root)} 2>/dev/null"
        ),
        fixture.quota_guard_command.replace(
            '"${LOOPX_TURN:?}"',
            "turn-capability-layout-001",
        ),
        _callsite_command(),
        None,
    ]
    calls = 0

    def transport(**kwargs: Any) -> Mapping[str, Any]:
        nonlocal calls
        command = commands[calls]
        if command is None:
            request = json.loads(kwargs["body"])
            command = _projected_reentry_command(request)
        calls += 1
        return _tool_response(f"call-{calls}", command)

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-layout-preview",
        fixture_root=fixture_root,
    )

    assert receipt["qualification_passed"] is True
    assert receipt["observed_tool_sequence"] == [
        "workspace_read",
        "quota_should_run",
        "capability_callsite",
        "quota_reentry",
    ]


def test_actor_rejects_monitor_fallback_after_quota(tmp_path: Path) -> None:
    fixture = _build_capability_repair_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response(
            "call-monitor",
            "loopx quota monitor-poll --goal-id portfolio-goal "
            f"--todo-id {CAPABILITY_REPAIR_MONITOR_TODO_ID}",
        ),
    ]
    calls = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-monitor-fallback",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "monitor_fallback"
    assert receipt["observed_tool_sequence"][-1] == "monitor_fallback"


def test_actor_rejects_quiet_wait_after_quota(tmp_path: Path) -> None:
    fixture = _build_capability_repair_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _stop_response(),
    ]
    calls = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-quiet-wait",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "waited_on_capability_reentry"


def test_actor_rejects_wrong_capability_callsite(tmp_path: Path) -> None:
    fixture = _build_capability_repair_fixture(tmp_path / "oracle")
    responses = [
        _tool_response("call-quota", fixture.quota_guard_command),
        _tool_response("call-wrong", _callsite_command(target="fixture/other.json")),
    ]
    calls = 0

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    receipt = DoubaoCapabilityMonitorRepairToolBehaviorActor(
        api_key="test-only-placeholder",
        transport=transport,
    ).qualify(
        qualification_id="capability-wrong-target",
        fixture_root=tmp_path / "actor",
    )

    assert receipt["qualification_passed"] is False
    assert receipt["failure_code"] == "unexpected_command"
