from __future__ import annotations

import json
from pathlib import Path

from loopx.extensions.lark.goal_channel_blocked_notice import deliver_blocked_notices
from loopx.extensions.lark import goal_channel_lifecycle, goal_channel_notification
from loopx.extensions.lark.goal_channel_runtime import (
    configure_lark_goal_channel_automation,
)
from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    read_goal_channel_binding,
    write_goal_channel_binding,
)

GOAL_ID = "goal-blocked-public-fixture"
CHAT_ID = "oc_blocked_public_fixture"
MESSAGE_ID = "om_blocked_public_fixture"


def _binding(path: Path) -> None:
    write_goal_channel_binding(
        path,
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {
                GOAL_ID: {
                    "goal_id": GOAL_ID,
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": CHAT_ID},
                    "identity": {
                        "sender_identity": "bot",
                        "bot_app_id": "cli_public_fixture",
                        "cli_bin": "lark-cli",
                    },
                    "automation": {"blocked_notice_auto_notify_enabled": True},
                    "receipts": {},
                }
            },
        },
    )


def _result(data: object) -> dict[str, object]:
    return {
        "returncode": 0,
        "stdout": json.dumps(data),
        "stderr": "",
        "timed_out": False,
    }


def _runner(calls: list[list[str]], *, verify: bool = True, fail_send: bool = False):
    sent: dict[str, str] = {}

    def run(
        args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        calls.append(args)
        if args == ["lark-cli", "--version"]:
            return _result({"version": "1.0.56"})
        if "auth" in args and "status" in args:
            return _result(
                {
                    "ok": True,
                    "appId": "cli_public_fixture",
                    "identities": {
                        "bot": {
                            "available": True,
                            "verified": True,
                            "appName": "LoopX Bot",
                        }
                    },
                }
            )
        if args[-1:] == ["--help"]:
            return _result({"ok": True})
        if "chats" in args and "get" in args:
            return _result({"ok": True, "data": {"chat_id": CHAT_ID}})
        if "+chat-members-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "bots": [
                            {
                                "member_id": "ou_bot_fixture",
                                "app_id": "cli_public_fixture",
                            }
                        ]
                    },
                }
            )
        if "+messages-send" in args:
            if fail_send:
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "rejected",
                    "timed_out": False,
                }
            sent[MESSAGE_ID] = args[args.index("--text") + 1]
            return _result({"ok": True, "data": {"message_id": MESSAGE_ID}})
        if "+messages-mget" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "message_id": MESSAGE_ID,
                                "body": {
                                    "content": sent.get(MESSAGE_ID, "")
                                    if verify
                                    else "other"
                                },
                            }
                        ]
                    },
                }
            )
        return _result({"ok": True})

    return run


def _status(
    status: str = "blocked", *, reason: str = "Required input is missing"
) -> dict[str, object]:
    return {
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "agent_todos": {
                        "items": [
                            {
                                "todo_id": "todo_blocked_fixture",
                                "text": "Validate primary result",
                                "status": status,
                                "reason": reason,
                                "role": "agent",
                            }
                        ]
                    },
                }
            ]
        }
    }


def _send(
    path: Path,
    status: dict[str, object],
    calls: list[list[str]],
    *,
    verify: bool = True,
):
    return deliver_blocked_notices(
        goal_id=GOAL_ID,
        binding_path=path,
        status=status,
        quota_packet={},
        external_sink_delivery_authorized=True,
        runner=_runner(calls, verify=verify),
    )


def test_blocked_notice_send_readback_dedup_and_material_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    first = _send(path, _status(), calls)
    assert first["status"] == "sent_verified"
    assert first["delivered_count"] == 1
    assert len([x for x in calls if "+messages-send" in x]) == 1
    second = _send(path, _status(), calls)
    assert second["delivered_count"] == 1
    assert len([x for x in calls if "+messages-send" in x]) == 1
    changed = _send(path, _status(reason="Dependency changed"), calls)
    assert changed["delivered_count"] == 1
    assert len([x for x in calls if "+messages-send" in x]) == 2
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 2
    assert all(row["readback_verified"] is True for row in receipts.values())


def test_blocked_notice_unverified_is_not_claimed_as_delivered(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    result = _send(path, _status(), [], verify=False)
    assert result["status"] == "sent_unverified"
    assert result["pending_count"] == 1
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert next(iter(receipts.values()))["state"] == "sent_unverified"


def test_blocked_notice_disabled_keeps_human_gate_setting(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    registry = {"goals": [{"id": GOAL_ID}]}
    result = configure_lark_goal_channel_automation(
        registry=registry,
        goal_id=GOAL_ID,
        binding_path=path,
        blocked_notice_auto_notify=False,
        execute=True,
    )
    assert result["ok"] and result["readback_verified"]
    assert _send(path, _status(), [])["status"] == "disabled"
    stored = read_goal_channel_binding(path)["bindings"][GOAL_ID]["automation"]
    assert stored["blocked_notice_auto_notify_enabled"] is False
    assert "human_gate_auto_notify_enabled" not in stored


def test_blocked_notice_explicit_resolution_is_reconciled(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    _send(path, _status(), [])
    result = _send(path, _status(status="done"), [])
    assert result["status"] == "no_active_blocker"
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert next(iter(receipts.values()))["state"] == "resolved"


def test_reopened_same_blocker_delivers_new_transition(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    _send(path, _status(), calls)
    _send(path, _status(status="done"), calls)
    reopened = _send(path, _status(), calls)
    assert reopened["status"] == "sent_verified"
    assert len([args for args in calls if "+messages-send" in args]) == 2
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 2
    assert {receipt["state"] for receipt in receipts.values()} == {
        "resolved",
        "delivered",
    }


def test_owner_blocker_requests_action_and_agent_blocker_does_not(
    tmp_path: Path,
) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    status = {
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "user_todos": {
                        "items": [
                            {
                                "todo_id": "todo_owner_fixture",
                                "text": "Approve the change",
                                "status": "blocked",
                                "reason": "Owner decision is missing",
                                "role": "user",
                            }
                        ]
                    },
                }
            ]
        }
    }
    result = _send(path, status, calls)
    assert result["status"] == "sent_verified"
    messages = [
        args[args.index("--text") + 1] for args in calls if "+messages-send" in args
    ]
    assert len(messages) == 1 and "Owner action required." in messages[0]
    calls.clear()
    _send(path, _status(), calls)
    agent_message = next(
        args[args.index("--text") + 1] for args in calls if "+messages-send" in args
    )
    assert "No owner action required." in agent_message


def test_failed_provider_send_stays_pending_for_retry(tmp_path: Path) -> None:
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls: list[list[str]] = []
    result = deliver_blocked_notices(
        goal_id=GOAL_ID,
        binding_path=path,
        status=_status(),
        quota_packet={},
        external_sink_delivery_authorized=True,
        runner=_runner(calls, fail_send=True),
    )
    assert result["status"] == "provider_api_failed"
    assert result["pending_count"] == 1 and not result["readback_verified"]
    receipt = next(
        iter(read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"].values())
    )
    assert receipt["state"] == "pending"
    assert "message_id" not in receipt
    retried = _send(path, _status(), calls)
    assert retried["status"] == "sent_verified"
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 1


def test_material_refresh_uses_authorized_sink_and_preserves_suppression(
    tmp_path: Path, monkeypatch
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{}", encoding="utf-8")
    _binding(registry_path.with_name("goal-channel.json"))
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "load_registry",
        lambda path: {"goals": [{"id": GOAL_ID}]},
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_goal_source_runtime_route",
        lambda **kwargs: {
            "source_registry": str(registry_path),
            "source_runtime_root": str(tmp_path / "runtime"),
        },
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        goal_channel_lifecycle, "collect_status", lambda **kwargs: _status()
    )
    monkeypatch.setattr(
        goal_channel_lifecycle, "build_quota_should_run", lambda *args, **kwargs: {}
    )
    calls: list[list[str]] = []
    suppressed = goal_channel_lifecycle.sync_blocked_notice_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=False,
        runner=_runner(calls),
    )
    assert suppressed["status"] == "external_sink_suppressed"
    assert not any("+messages-send" in args for args in calls)
    delivered = goal_channel_lifecycle.sync_blocked_notice_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
        runner=_runner(calls),
    )
    assert delivered["status"] == "sent_verified"
    assert delivered["readback_verified"] is True


def test_public_status_projects_verified_count_without_private_provider_ids(tmp_path: Path, monkeypatch) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    path = registry_path.with_name("goal-channel.json")
    _binding(path)
    _send(path, _status(), [])
    monkeypatch.setattr(goal_channel_notification, "resolve_goal_source_runtime_route", lambda **kwargs: {
        "source_registry": str(registry_path),
    })
    projection = goal_channel_notification.build_goal_channel_notification_projection(
        registry_path=registry_path, registry={"goals": [{"id": GOAL_ID}]}, goal_id=GOAL_ID,
    )
    row = projection["goals"][0]
    assert row["blocked_notice_auto_notify_enabled"] is True
    assert row["blocked_notice_delivery"] == {
        "delivered_count": 1, "unverified_count": 0, "resolved_count": 0,
    }
    assert CHAT_ID not in json.dumps(projection)
    assert MESSAGE_ID not in json.dumps(projection)
