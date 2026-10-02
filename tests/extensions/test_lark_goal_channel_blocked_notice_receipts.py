from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.extensions.lark import goal_channel_lifecycle, goal_channel_notification
from loopx.extensions.lark.goal_channel_blocked_notice import deliver_blocked_notices
from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding, write_goal_channel_binding
from tests.extensions.test_lark_goal_channel_blocked_notice import (
    CHAT_ID, GOAL_ID, _binding, _runner, _send, _status,
)


@pytest.mark.parametrize("recovery_status,receipt_state", [("open", "resumed"), ("done", "resolved")])
def test_repeated_recovery_reopens_delivery_without_clock_or_dict_order_dependency(
    tmp_path: Path, monkeypatch, recovery_status: str, receipt_state: str,
) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    # Separate transitions can be observed within the same timestamp precision.
    monkeypatch.setattr("loopx.extensions.lark.goal_channel_blocked_notice.now_iso", lambda: "2026-01-01T00:00:00Z")
    calls: list[list[str]] = []
    for _ in range(3):
        _send(path, _status(), calls)
        _send(path, _status(recovery_status, reason=""), calls)
        payload = read_goal_channel_binding(path)
        binding = payload["bindings"][GOAL_ID]
        binding["receipts"] = dict(reversed(list(binding["receipts"].items())))
        write_goal_channel_binding(path, payload)
    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 3
    assert len({args[args.index("--idempotency-key") + 1] for args in sends}) == 3
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 3
    assert {row["state"] for row in receipts.values()} == {receipt_state}


@pytest.mark.parametrize("observation", ["missing", "unknown", "waiting"])
def test_incomplete_or_still_blocked_observation_does_not_retire_receipt(
    tmp_path: Path, observation: str,
) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    _send(path, _status(), [])
    status = _status()
    group = status["attention_queue"]["items"][0]["agent_todos"]
    if observation == "missing":
        group["items"] = []
        group["truncated"] = True
    elif observation == "unknown":
        group["items"][0].pop("status")
    else:
        group["items"][0].update(status="open", resume_when="todo_done:todo_dependency", resume_ready=False)
    _send(path, status, [])
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert all(row["state"] not in {"resolved", "resumed"} for row in receipts.values())


def test_delivery_budget_applies_to_pending_effects_across_refreshes(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    status = _status()
    group = status["attention_queue"]["items"][0]["agent_todos"]
    template = group["items"][0]
    group["items"] = [{**template, "todo_id": f"todo_blocker_{index}"} for index in range(17)]
    calls: list[list[str]] = []
    for expected_delivered in (8, 16, 17, 17):
        result = _send(path, status, calls)
        assert result["notice_count"] == 17
        assert result["delivered_count"] == expected_delivered
        assert result["pending_count"] == 17 - expected_delivered
        assert result["readback_verified"] is (expected_delivered == 17)
        assert result["status"] == ("sent_verified" if expected_delivered == 17 else "pending")
        assert len([args for args in calls if "+messages-send" in args]) == expected_delivered
        receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
        assert sum(row["state"] == "pending" for row in receipts.values()) == 17 - expected_delivered
        assert result["deferred_count"] == 17 - expected_delivered
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 17
    assert all(row["state"] == "delivered" for row in receipts.values())


@pytest.mark.parametrize("named_target", [False, True])
def test_switching_channel_preserves_history_and_requires_current_destination_readback(
    tmp_path: Path, named_target: bool,
) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    for chat_id in (CHAT_ID, "oc_second_public_fixture", "oc_second_public_fixture"):
        payload = read_goal_channel_binding(path)
        binding = payload["bindings"][GOAL_ID]
        target = None
        if named_target:
            binding["target_ref"] = "notice-target"
            target = {"name": "notice-target", "provider": "lark",
                      "channel": {"chat_id": chat_id}, "identity": binding["identity"]}
        else:
            binding["channel"]["chat_id"] = chat_id
        write_goal_channel_binding(path, payload)
        result = deliver_blocked_notices(
            goal_id=GOAL_ID, binding_path=path, status=_status(), quota_packet={},
            provider_target=target, external_sink_delivery_authorized=True, runner=_runner(calls),
        )
        assert result["readback_verified"] is True
    sends = [args for args in calls if "+messages-send" in args]
    assert [args[args.index("--chat-id") + 1] for args in sends] == [CHAT_ID, "oc_second_public_fixture"]
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert {row["chat_id"] for row in receipts.values()} == {CHAT_ID, "oc_second_public_fixture"}


def test_persistent_failures_do_not_starve_unattempted_effects(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    status = _status()
    group = status["attention_queue"]["items"][0]["agent_todos"]
    template = group["items"][0]
    group["items"] = [{**template, "todo_id": f"todo_retry_{i}", "text": f"Blocked task {i}"} for i in range(9)]
    calls: list[list[str]] = []
    for _ in range(2):
        result = deliver_blocked_notices(
            goal_id=GOAL_ID, binding_path=path, status=status, quota_packet={},
            external_sink_delivery_authorized=True, runner=_runner(calls, fail_send=True),
        )
        assert result["pending_count"] == 9 and result["deferred_count"] == 1
    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 16  # Eight attempts per refresh, even when transport fails.
    assert "Blocked task 8" in sends[8][sends[8].index("--text") + 1]


def test_superseded_blocker_retires_once_without_resending(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    _send(path, _status(), calls)
    status = _status()
    status["attention_queue"]["items"][0]["agent_todos"]["items"][0]["superseded_by"] = "todo_successor"
    for _ in range(2):
        assert _send(path, status, calls)["status"] == "no_active_blocker"
    assert len([args for args in calls if "+messages-send" in args]) == 1
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 1 and next(iter(receipts.values()))["state"] == "superseded"


def test_return_to_earlier_cause_is_a_new_material_transition(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    for reason in ("Input missing", "Dependency missing", "Input missing"):
        _send(path, _status(reason=reason), calls)
    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 3
    assert len({args[args.index("--idempotency-key") + 1] for args in sends}) == 3


@pytest.mark.parametrize("enabled,authorized", [(False, True), (True, False)])
def test_disabled_and_suppressed_delivery_leave_binding_and_transport_untouched(
    tmp_path: Path, enabled: bool, authorized: bool,
) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    payload = read_goal_channel_binding(path)
    payload["bindings"][GOAL_ID]["automation"]["blocked_notice_auto_notify_enabled"] = enabled
    write_goal_channel_binding(path, payload)
    before = path.read_bytes()
    calls: list[list[str]] = []
    result = deliver_blocked_notices(
        goal_id=GOAL_ID, binding_path=path, status=_status(), quota_packet={},
        external_sink_delivery_authorized=authorized, runner=_runner(calls),
    )
    assert result["status"] == ("external_sink_suppressed" if enabled else "disabled")
    assert not calls and path.read_bytes() == before


def test_default_off_refresh_does_not_enter_provider_or_notice_projection(tmp_path: Path, monkeypatch) -> None:
    registry_path = tmp_path / ".loopx/registry.json"
    registry_path.parent.mkdir()
    path = registry_path.with_name("goal-channel.json")
    _binding(path)
    payload = read_goal_channel_binding(path)
    payload["bindings"][GOAL_ID]["automation"].pop("blocked_notice_auto_notify_enabled")
    write_goal_channel_binding(path, payload)
    before = path.read_bytes()
    monkeypatch.setattr(goal_channel_lifecycle, "load_registry", lambda path: {})
    monkeypatch.setattr(goal_channel_lifecycle, "resolve_goal_source_runtime_route", lambda **kwargs: {
        "source_registry": str(registry_path), "source_runtime_root": str(tmp_path / "runtime"),
    })
    def unexpected(*args, **kwargs):
        pytest.fail("Default-off refresh entered blocked notice activation or projection")
    for name in ("resolve_extension_activation", "collect_status", "build_quota_should_run", "deliver_blocked_notices"):
        monkeypatch.setattr(goal_channel_lifecycle, name, unexpected)
    result = goal_channel_lifecycle.sync_blocked_notice_after_refresh(
        registry_path=registry_path, runtime_root_override=None, goal_id=GOAL_ID,
        agent_id=None, external_sink_delivery_authorized=True,
    )
    assert result["status"] == "disabled" and path.read_bytes() == before


@pytest.mark.parametrize("named_target", [False, True])
def test_public_readback_follows_current_target_and_recovery(tmp_path: Path, monkeypatch, named_target: bool) -> None:
    from loopx.extensions.lark.goal_channel_targets import GOAL_CHANNEL_TARGETS_SCHEMA_VERSION
    registry_path = tmp_path / ".loopx/registry.json"
    registry_path.parent.mkdir()
    path = registry_path.with_name("goal-channel.json")
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    _binding(path)
    _send(path, _status(), [])
    payload = read_goal_channel_binding(path)
    binding = payload["bindings"][GOAL_ID]
    target = None
    if named_target:
        binding["target_ref"] = "notice-target"
        target = {"name": "notice-target", "provider": "lark",
                  "channel": {"chat_id": "oc_second_public_fixture"}, "identity": binding["identity"]}
        (runtime_root / "goal-channel-targets.json").write_text(json.dumps({
            "schema_version": GOAL_CHANNEL_TARGETS_SCHEMA_VERSION, "targets": {"notice-target": target},
        }), encoding="utf-8")
    else:
        binding["channel"]["chat_id"] = "oc_second_public_fixture"
    write_goal_channel_binding(path, payload)
    monkeypatch.setattr(goal_channel_notification, "resolve_goal_source_runtime_route", lambda **kwargs: {
        "source_registry": str(registry_path), "source_runtime_root": str(runtime_root),
    })
    def counts():
        projection = goal_channel_notification.build_goal_channel_notification_projection(
            registry_path=registry_path, registry={"goals": [{"id": GOAL_ID}]}, goal_id=GOAL_ID,
        )
        assert CHAT_ID not in json.dumps(projection)
        assert "oc_second_public_fixture" not in json.dumps(projection)
        return projection["goals"][0]["blocked_notice_delivery"]
    assert counts() == {"delivered_count": 0, "unverified_count": 0, "resolved_count": 0}
    for status, expected in [(_status(), (1, 0)), (_status("open", reason=""), (0, 1))]:
        deliver_blocked_notices(
            goal_id=GOAL_ID, binding_path=path, status=status, quota_packet={},
            provider_target=target, external_sink_delivery_authorized=True, runner=_runner([]),
        )
        assert counts() == {"delivered_count": expected[0], "unverified_count": 0, "resolved_count": expected[1]}
