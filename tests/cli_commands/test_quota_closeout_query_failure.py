"""The public quota response distinguishes a lost read from a lost write."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from loopx.cli import build_parser
import loopx.cli_commands.quota as command
from loopx.cli_commands.quota_context import QuotaCommandContext
from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous
import loopx.control_plane.quota.unsettled_host_turn as closeout
from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("turn_envelope", [False, True])
@pytest.mark.parametrize("failure", ["query", "write"])
def test_cli_preserves_query_failure_and_existing_receipt(
    tmp_path, monkeypatch, existing, turn_envelope, failure,
):
    goal, agent, turn = "fixture-goal", "fixture-agent", "fixture-turn"
    registry = tmp_path / "registry.json"
    registry.write_text('{}')
    if existing:
        event = build_rollout_event(
            event_kind="quota_should_run", goal_id=goal, agent_id=agent,
            run_id=turn, status="run", summary="synthetic prior admission",
            details={"turn_instance_id": turn, "stall_observation": "not_applicable"},
        )
        append_rollout_event(rollout_event_log_path(tmp_path, goal), event)
    args = build_parser().parse_args([
        "--format", "json", "quota", "should-run", "--goal-id", goal,
        "--agent-id", agent, "--turn-instance-id", turn,
    ] + (["--turn-envelope"] if turn_envelope else []))
    context = QuotaCommandContext(
        runtime_root=tmp_path, status_limit=1, status_goal_id=goal,
        status_payload={}, cache_metadata=None, scheduler_context=None,
        operator_inbox_urgency_projector=lambda **kwargs: {},
        detail_sections=frozenset(), heartbeat_turn_id=turn,
    )
    # Only isolate unrelated collection/hooks and inject the transport loss.
    # Receipt lookup, exception translation, CLI failure projection, and final
    # compact/envelope rendering run through their production implementations.
    monkeypatch.setattr(command, "_dispatch_quota_turn_start_hooks", lambda *a, **k: (None, False))
    monkeypatch.setattr(command, "prepare_quota_command_context", lambda *a, **k: context)
    transport = Mock(side_effect=EffectRuntimeResponseAmbiguous(
        closeout.PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_METHOD if failure == "query"
        else "quota.heartbeat.commit", timeout=5,
    ))
    monkeypatch.setattr(closeout, "effect_runtime_result", transport)

    def decision(*a, **k):
        if failure == "write":
            return transport()
        return closeout._prior_closeout_preflight(
            runtime_root=tmp_path, goal_id=goal, agent_id=agent,
            current_turn_instance_id=turn,
        )

    monkeypatch.setattr(command, "build_live_quota_should_run_decision", decision)
    output = []
    append = Mock(side_effect=AssertionError("failed preflight cannot admit a Turn"))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = command.handle_quota_command(
        args, registry_path=registry, runtime_root_arg=str(tmp_path),
        print_payload=lambda payload, *_: output.append(json.loads(json.dumps(payload))),
        append_cli_rollout_event=append,
    )
    assert result == 1
    payload = output[-1]
    assert payload["ok"] is False
    assert payload["should_run"] is False
    receipt = payload["heartbeat_receipt"]
    assert transport.call_count == 1
    append.assert_not_called()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    if failure == "write":
        assert payload["effective_action"] == "heartbeat_receipt_write_failed"
        assert receipt["status"] == "write_failed"
        return
    assert payload["error_code"] == "quota_closeout_query_unavailable"
    assert payload["effective_action"] == "control_plane_health_repair"
    assert "closeout state is unknown" in payload["reason"]
    assert "same Turn identity" in payload["recommended_action"]
    assert "repairing heartbeat receipt" not in payload["recommended_action"]
    assert receipt["status"] == ("replayed" if existing else "not_committed")
    if existing:
        assert receipt["event_id"] == event["event_id"]
    else:
        assert receipt["reason_code"] == payload["error_code"]
