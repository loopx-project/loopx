"""A no-spend receipt must not erase evidence observed at Turn start."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from loopx.cli import build_parser
import loopx.cli_commands.quota as command
from loopx.cli_commands.quota_context import QuotaCommandContext
from loopx.control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    dispatch_turn_start_hooks,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)


@pytest.mark.parametrize("replayed", [False, True])
@pytest.mark.parametrize("observation", ["pending", "empty", "unavailable"])
def test_quiet_cli_retains_turn_start_evidence_after_stall_writeback(
    tmp_path, monkeypatch, replayed, observation,
):
    goal, turn = "fixture-goal", "fixture-turn"
    registry = tmp_path / "registry.json"
    registry.write_text("{}")
    read_command = f"loopx manager-inbox read --goal-id {goal}"

    def produce():
        if observation == "unavailable":
            raise OSError("synthetic inbox read outage")
        pending = observation == "pending"
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "manager.context_inbox",
            "capability_id": "manager-context",
            "phase": "turn_start",
            "status": "observed" if pending else "empty",
            "observation_count": int(pending),
            "agent_read_required": pending,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "local_private_state_mutated": False,
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": None,
        }

    dispatch = dispatch_turn_start_hooks([TurnStartHookRegistration(
        hook_id="manager.context_inbox", capability_id="manager-context",
        requested_read_scope=("owner_private_inbox",), requested_write_scope=(),
        producer=produce,
        required_read={"kind": "operator_inbox", "command": read_command,
                       "reason": "new owner request requires assessment",
                       "ordering": "before_work"},
    )])
    status = quota_status_payload(
        goal_id=goal, status="active", recommended_action="Observe when due.",
        agent_todo_items=[quota_todo_item(
            todo_id="todo_monitor", title="Observe when due.",
            task_class="continuous_monitor", cadence="12h",
            next_due_at="2099-01-01T00:00:00Z",
        )],
    )
    context = QuotaCommandContext(
        runtime_root=tmp_path, scan_roots=[], status_limit=1, status_goal_id=goal,
        status_payload=status, cache_metadata=None, scheduler_context=None,
        operator_inbox_urgency_projector=lambda **kwargs: {},
        detail_sections=frozenset(), heartbeat_turn_id=turn,
    )
    prepare = Mock(return_value=(None, dispatch, context))
    poll = Mock(return_value={"ok": True, "replayed": replayed})
    monkeypatch.setattr(command, "_prepare_quota_command_execution", prepare)
    monkeypatch.setattr(command, "find_quota_monitor_poll_turn",
                        lambda *a, **k: {} if replayed else None)
    monkeypatch.setattr(command, "record_quota_monitor_poll", poll)
    monkeypatch.setattr(command, "collect_status", lambda **k: status)
    # Storage/host telemetry are isolated; both live decisions, hook projection,
    # automatic stall reconstruction, capture and final CLI rendering are real.
    monkeypatch.setattr(command, "should_log_quota", lambda *a: False)
    monkeypatch.setattr(command, "observe_quota_result", lambda *a, **k: None)
    monkeypatch.setattr(command, "attach_host_poll_receipt", lambda *a, **k: None)
    spend = Mock(side_effect=AssertionError("an inbox observation grants no spend"))
    monkeypatch.setattr(command, "spend_quota_slot", spend)
    args = build_parser().parse_args([
        "--format", "json", "quota", "should-run", "--goal-id", goal,
        "--turn-instance-id", turn,
    ])
    capture = tmp_path / "capture"
    capture.mkdir()
    output = []
    assert command.handle_quota_command(
        args, registry_path=registry, runtime_root_arg=str(tmp_path),
        print_payload=lambda payload, *_: output.append(payload),
        append_cli_rollout_event=Mock(), capture_directory=capture,
    ) == 0
    full = json.loads((capture / "decision.json").read_text())
    compact = output[-1]
    for payload in (full, compact):
        assert payload["ok"] is True
        assert payload["effective_action"] == "monitor_quiet_skip"
        assert payload["should_run"] is False
        assert payload["heartbeat_stall_writeback"]["status"] == (
            "replayed" if replayed else "appended"
        )
        assert payload["turn_start_capability_hook_dispatch"]["results"] == dispatch["results"]
        assert "contexts" not in payload["turn_start_capability_hook_dispatch"]
        interaction = payload["interaction_contract"]
        reads = interaction["agent_channel"]["required_reads"]
        assert [r["command"] for r in reads] == (
            [read_command] if observation == "pending" else []
        )
        assert interaction["agent_channel"]["delivery_allowed"] is False
        assert interaction["cli_channel"]["spend_allowed_now"] is False
        assert interaction["user_channel"]["notify"] == (
            "NOTIFY" if observation == "pending" else "DONT_NOTIFY"
        )
    prepare.assert_called_once()
    poll.assert_called_once()
    assert poll.call_args.kwargs["turn_instance_id"] == turn
    spend.assert_not_called()
