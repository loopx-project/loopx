from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
)
from loopx.cli_commands import quota_scheduler_followup
from loopx.control_plane.quota.scheduler_ack import _run_native_followup
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.rollout_event_log import build_rollout_event, rollout_event_log_path


GOAL_ID = "receipt-fence"
AGENT_ID = "agent-a"


def _append(runtime_root: Path, **fields: object) -> None:
    path = rollout_event_log_path(runtime_root, GOAL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = build_rollout_event(goal_id=GOAL_ID, agent_id=AGENT_ID, **fields)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


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
    goal_ref = {
        "goal_id": "receipt-fence",
        "goal_instance_id": "ginst_" + "a" * 32,
    }
    _run_native_followup(
        runtime_root=tmp_path,
        before={},
        host_facts={},
        use_current_hint=False,
        reason_summary=None,
        turn_instance_id="turn-1",
        goal_ref=goal_ref,
    )
    request = captured["request"]
    assert request["turn_instance_id"] == "turn-1"
    assert request["goal_ref"] == goal_ref
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
    assert request["goal_ref"] is None
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
    goal_ref = {
        "goal_id": "goals",
        "goal_instance_id": "ginst_" + "b" * 32,
    }
    quota.record_quota_scheduler_ack(
        {"runtime_root": str(tmp_path)},
        goal_id="goals",
        agent_id="agent-a",
        before_decision={"decision": {}},
        turn_instance_id="turn-1",
        goal_ref=goal_ref,
    )
    assert captured["turn_instance_id"] == "turn-1"
    assert captured["goal_ref"] == goal_ref


def _followup_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "quota_command": "scheduler-ack-current",
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "codex_app_current_rrule": None,
        "applied_rrule": "FREQ=MINUTELY;INTERVAL=3",
        "host_match_observed": False,
        "execute": True,
        "surface": "trae_app",
        "state_key": None,
        "available_capabilities": [],
        "reset_token": None,
        "identity_signature": None,
        "reason_summary": None,
        "use_current_hint": False,
    }
    values.update(overrides)
    return Namespace(**values)


def _capture_ack(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
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
    return calls


def test_superseded_receipt_is_decided_by_the_native_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-2")
    calls = _capture_ack(monkeypatch)

    payload = quota_scheduler_followup.build_scheduler_followup_payload(
        {},
        _followup_args(),
        registry_path=tmp_path / "registry.json",
        runtime_root=runtime_root,
        turn_instance_id="turn-1",
        scheduler_context=None,
        operator_inbox_urgency_projector=lambda **_kwargs: {},
    )

    # The Python route keeps no second freshness opinion: it binds the
    # originating Turn and lets the native fence answer stale/missing.
    assert payload == {"ok": True}
    assert calls and calls[0]["turn_instance_id"] == "turn-1"


def test_scheduler_followup_builds_from_the_tolerant_settlement_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _decision(*_args, **kwargs):
        captured.update(kwargs)
        return {"effective_action": "execute_todo"}

    monkeypatch.setattr(
        quota_scheduler_followup,
        "build_live_quota_should_run_decision",
        _decision,
    )
    quota_scheduler_followup._build_scheduler_followup_decision(
        {},
        _followup_args(),
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        heartbeat_receipt=None,
        turn_instance_id=None,
        codex_app_current_rrule=None,
        scheduler_context=None,
        operator_inbox_urgency_projector=lambda **_kwargs: {},
    )
    assert captured["tolerate_malformed_settlement_history"] is True


def test_malformed_log_line_does_not_block_the_ack_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    _append(runtime_root, event_kind="quota_should_run", run_id="turn-1")
    path = rollout_event_log_path(runtime_root, GOAL_ID)
    with path.open("ab") as handle:
        handle.write(
            b'{"schema_version":"loopx_rollout_event_v0",'
            b'"event_kind":"quota_should_run","goal_id":"receipt-fence",'
            b'"agent_id":"agent-a","run_id":"turn-bad","summary":"'
            b"\xff"
            b'"}\n'
        )
    calls = _capture_ack(monkeypatch)

    payload = quota_scheduler_followup.build_scheduler_followup_payload(
        {},
        _followup_args(),
        registry_path=tmp_path / "registry.json",
        runtime_root=runtime_root,
        turn_instance_id="turn-1",
        scheduler_context=None,
        operator_inbox_urgency_projector=lambda **_kwargs: {},
    )

    assert payload == {"ok": True}
    assert calls and calls[0]["turn_instance_id"] == "turn-1"


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

@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    ("command", "include_turn"),
    [
        ("scheduler-ack-current", True),
        ("scheduler-fail-current", True),
        ("scheduler-ack", True),
        ("scheduler-ack", False),
    ],
)
def test_real_compatibility_cli_survives_a_damaged_audit_row(
    tmp_path: Path, provider: str, command: str, include_turn: bool
) -> None:
    registry, runtime_root, project = write_cli_fixture(
        tmp_path / provider, scoped_agents=True, provider=provider,
    )
    goal_id = "needs-operator"
    turn_id = "turn-damaged-history"
    code, guard = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--trae_app",
        "--app-automation-current-rrule",
        "FREQ=MINUTELY;INTERVAL=3",
        "--turn-instance-id",
        turn_id,
        registry_path=registry,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert code == 0, guard

    log_path = rollout_event_log_path(runtime_root, goal_id)
    with log_path.open("ab") as handle:
        handle.write(
            b'{"schema_version":"loopx_rollout_event_v0",'
            b'"event_kind":"quota_should_run","goal_id":"needs-operator",'
            b'"agent_id":"' + SCOPED_AGENT_ID.encode() + b'",'
            b'"run_id":"later-turn","summary":"'
            b"\xff"
            b'"}\n'
        )

    # The ACK derives its applied RRULE from the current hint; the failure
    # return records the hint target. Both are ordinary scheduler writebacks.
    operation_args = ("--use-current-hint",) if command == "scheduler-ack" else ()
    turn_args = ("--turn-instance-id", turn_id) if include_turn else ()
    code, result = run_json_cli_result(
        "quota",
        command,
        "--goal-id",
        goal_id,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--trae_app",
        *turn_args,
        "--execute",
        *operation_args,
        registry_path=registry,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert code == 0, result
    assert result["ok"] is True, result
    assert result["scheduler_state_mutated"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scheduler_followup_cache_preview_cannot_poison_strict_status(
    tmp_path: Path, provider: str
) -> None:
    registry, runtime_root, project = write_cli_fixture(
        tmp_path / provider, scoped_agents=True, provider=provider,
    )
    goal_id = "needs-operator"
    turn_id = "turn-cache-preview"
    code, guard = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--trae_app",
        "--app-automation-current-rrule",
        "FREQ=MINUTELY;INTERVAL=3",
        "--turn-instance-id",
        turn_id,
        registry_path=registry,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert code == 0, guard

    log_path = rollout_event_log_path(runtime_root, goal_id)
    with log_path.open("ab") as handle:
        handle.write(
            b'{"schema_version":"loopx_rollout_event_v0",'
            b'"event_kind":"quota_should_run","goal_id":"needs-operator",'
            b'"agent_id":"' + SCOPED_AGENT_ID.encode() + b'",'
            b'"run_id":"later-turn","summary":"'
            b"\xff"
            b'"}\n'
        )

    before_code, before_status = run_json_cli_result(
        "quota",
        "status",
        "--goal-id",
        goal_id,
        registry_path=registry,
        runtime_root=runtime_root,
        cwd=project,
    )
    code, preview = run_json_cli_result(
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        goal_id,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--trae_app",
        "--turn-instance-id",
        turn_id,
        "--execute",
        "--write-projection-cache",
        registry_path=registry,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert code == 0, preview
    cache = preview["status_projection_cache"]
    assert cache["written"] is False
    assert cache["skipped_reason"] == "tolerant_scheduler_followup_view"
    assert not Path(cache["path"]).exists()

    after_code, after_status = run_json_cli_result(
        "quota",
        "status",
        "--goal-id",
        goal_id,
        "--use-projection-cache",
        registry_path=registry,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert after_code == before_code
    assert after_status.get("status_projection_cache", {}).get("hit") is not True
