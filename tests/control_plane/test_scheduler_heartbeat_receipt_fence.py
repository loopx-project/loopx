from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from loopx.cli_commands import quota_scheduler_followup
from loopx.control_plane.quota import heartbeat_receipt
from loopx.control_plane.quota.scheduler_ack import _run_native_followup
from loopx.rollout_event_log import build_rollout_event, rollout_event_log_path


GOAL_ID = "receipt-fence"
AGENT_ID = "agent-a"


def _append(runtime_root: Path, **fields: object) -> None:
    path = rollout_event_log_path(runtime_root, GOAL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = build_rollout_event(goal_id=GOAL_ID, agent_id=AGENT_ID, **fields)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _status(runtime_root: Path, turn: str) -> str:
    return heartbeat_receipt.heartbeat_receipt_status(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=turn,
    )


def test_receipt_status_distinguishes_missing_fresh_and_superseded(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    assert _status(runtime_root, "turn-1") == "missing"

    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    assert _status(runtime_root, "turn-1") == "fresh"
    assert _status(runtime_root, "turn-2") == "missing"

    _append(runtime_root, event_kind="quota_should_run", run_id="turn-2")
    assert _status(runtime_root, "turn-1") == "stale"
    assert _status(runtime_root, "turn-2") == "fresh"


def test_later_normal_run_audit_supersedes_the_receipt(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    _append(
        runtime_root,
        event_kind="quota_should_run",
        status="normal_run",
        details={"ok": True, "should_run": True},
    )
    assert _status(runtime_root, "turn-1") == "stale"


def test_unrelated_normal_run_audit_does_not_supersede(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    _append(
        runtime_root,
        event_kind="quota_should_run",
        status="normal_run",
        details={"ok": True, "should_run": False},
    )
    assert _status(runtime_root, "turn-1") == "fresh"


def test_native_followup_request_carries_the_turn_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    class _Probe:
        returncode = 0
        stdout = "v24.19.0"
        stderr = ""

    def _run(command, **kwargs):
        if "input" not in kwargs:
            return _Probe()
        captured["request"] = json.loads(kwargs["input"])
        return _Completed()

    monkeypatch.setattr(
        "loopx.control_plane.quota.scheduler_ack.subprocess.run", _run
    )
    _run_native_followup(
        runtime_root=tmp_path,
        before={},
        host_facts={},
        use_current_hint=False,
        reason_summary=None,
        turn_instance_id="turn-1",
    )
    request = captured["request"]
    assert request["turn_instance_id"] == "turn-1"
    assert request["require_heartbeat_receipt"] is True

    _run_native_followup(
        runtime_root=tmp_path,
        before={},
        host_facts={},
        use_current_hint=False,
        reason_summary=None,
    )
    request = captured["request"]
    assert request["turn_instance_id"] is None
    assert request["require_heartbeat_receipt"] is False


def test_ack_route_forwards_the_turn_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx import quota

    captured: dict[str, object] = {}

    def _capture(before, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        quota, "record_quota_scheduler_ack_for_decision", _capture
    )
    quota.record_quota_scheduler_ack(
        {"runtime_root": str(tmp_path)},
        goal_id="goals",
        agent_id="agent-a",
        before_decision={"decision": {}},
        turn_instance_id="turn-1",
    )
    assert captured["turn_instance_id"] == "turn-1"


def test_superseded_receipt_blocks_before_any_scheduler_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-2")
    calls: list[dict[str, object]] = []

    def _ack(_status, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        quota_scheduler_followup, "record_quota_scheduler_ack", _ack
    )
    args = Namespace(
        quota_command="scheduler-ack-current",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    payload = quota_scheduler_followup.build_scheduler_followup_payload(
        {},
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root=runtime_root,
        turn_instance_id="turn-1",
        scheduler_context=None,
        operator_inbox_urgency_projector=lambda **_kwargs: {},
    )

    assert payload["ok"] is False
    assert payload["status"] == "heartbeat_receipt_stale"
    assert payload["error_code"] == "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE"
    assert payload["scheduler_state_mutated"] is False
    assert calls == []


def test_fresh_receipt_still_reaches_the_ack_with_its_turn_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    calls: list[dict[str, object]] = []

    def _ack(_status, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        quota_scheduler_followup, "record_quota_scheduler_ack", _ack
    )
    monkeypatch.setattr(
        quota_scheduler_followup,
        "_build_scheduler_followup_decision",
        lambda *_args, **_kwargs: {"effective_action": "execute_todo"},
    )
    args = Namespace(
        quota_command="scheduler-ack-current",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        codex_app_current_rrule=None,
        applied_rrule="FREQ=MINUTELY;INTERVAL=3",
        host_match_observed=False,
        execute=True,
        surface="trae_app",
        state_key=None,
        available_capabilities=[],
        reset_token=None,
        identity_signature=None,
        reason_summary=None,
        use_current_hint=False,
    )
    payload = quota_scheduler_followup.build_scheduler_followup_payload(
        {},
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root=runtime_root,
        turn_instance_id="turn-1",
        scheduler_context=None,
        operator_inbox_urgency_projector=lambda **_kwargs: {},
    )

    assert payload == {"ok": True}
    assert calls and calls[0]["turn_instance_id"] == "turn-1"
