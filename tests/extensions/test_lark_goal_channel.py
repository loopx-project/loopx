from __future__ import annotations

import json
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from loopx.cli_commands import goal_channel as goal_channel_cli
from loopx.cli_commands import goal_channel_operation as goal_channel_operation_cli
from loopx.extensions.lark import goal_channel_contracts
from loopx.extensions.lark import goal_channel_lifecycle
from loopx.extensions.lark import goal_channel_runtime
from loopx.extensions.lark.goal_channel import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    configure_lark_goal_channel_automation,
    doctor_lark_goal_channel,
    notify_lark_goal_channel_gate,
    read_goal_channel_binding,
    setup_lark_goal_channel,
    sync_lark_goal_channel,
)
from loopx.extensions.lark.goal_channel_message_delivery import (
    GoalChannelDeliveryStageError,
)
from loopx.extensions.lark.goal_channel_runtime import (
    auto_notify_lark_goal_channel_gate,
)
from loopx.extensions.lark.goal_channel_contracts import (
    goal_channel_connection_id,
    write_goal_channel_binding,
)
from loopx.extensions.lark.goal_topic_connections import disconnect_lark_goal_topic
from loopx.file_lock import exclusive_file_lock, LockAcquireTimeoutError
from loopx.extensions.lark.presentation.kanban import (
    lark_kanban_schema_payload,
    save_lark_kanban_board_config,
)


GOAL_ID = "goal-public-fixture"
CHAT_ID = "oc_public_fixture"
CONTROL_MESSAGE_ID = "om_control_public_fixture"
GATE_MESSAGE_ID = "om_gate_public_fixture"


def _registry(tmp_path: Path) -> dict[str, Any]:
    return {
        "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [
            {
                "id": GOAL_ID,
                "objective": "Deliver a public-safe collaboration channel.",
                "repo": str(tmp_path),
                "state_file": str(tmp_path / "ACTIVE_GOAL_STATE.md"),
                "adapter": {"kind": "read_only_project_map_v0"},
            }
        ],
    }


def _result(payload: object) -> dict[str, object]:
    return {
        "returncode": 0,
        "stdout": json.dumps(payload),
        "stderr": "",
        "timed_out": False,
    }


def _fake_runner(
    calls: list[list[str]],
):
    sent_texts: dict[str, str] = {}

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
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
                        "user": {"available": True, "verified": True},
                        "bot": {
                            "available": True,
                            "verified": True,
                            "appName": "LoopX Bot",
                        },
                    },
                }
            )
        if args[-1:] == ["--help"]:
            return _result({"ok": True})
        if "+chat-create" in args:
            return _result({"ok": True, "data": {"chat_id": CHAT_ID}})
        if "chats" in args and "get" in args:
            return _result({"ok": True, "data": {"chat_id": CHAT_ID}})
        if "+chat-members-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "users": [{"member_id": "ou_public_fixture"}],
                        "bots": [
                            {
                                "member_id": "ou_bot_public_fixture",
                                "app_id": "cli_public_fixture",
                            }
                        ],
                    },
                }
            )
        if "chat.members" in args and "create" in args:
            return _result({"ok": True, "data": {"invalid_id_list": []}})
        if "+base-create" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "base_token": "base_public_fixture",
                        "table_id": "tbl_public_fixture",
                    },
                }
            )
        if "+base-get" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "base": {
                            "url": "https://example.invalid/base/public-fixture",
                        }
                    },
                }
            )
        if "+view-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"name": "Worker Queue", "view_id": "vew_worker"},
                            {"name": "Kanban", "view_id": "vew_kanban"},
                            {"name": "User Gates", "view_id": "vew_gates"},
                        ]
                    },
                }
            )
        if "+field-list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {"fields": lark_kanban_schema_payload()["fields"]},
                }
            )
        if "+messages-send" in args:
            text = args[args.index("--text") + 1]
            message_id = (
                GATE_MESSAGE_ID
                if text.startswith("LoopX · Action required")
                else CONTROL_MESSAGE_ID
            )
            sent_texts[message_id] = text
            return _result({"ok": True, "data": {"message_id": message_id}})
        if "+messages-mget" in args:
            message_id = args[args.index("--message-ids") + 1]
            return _result(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "message_id": message_id,
                                "body": {"content": sent_texts.get(message_id, "")},
                            }
                        ]
                    },
                }
            )
        if "pins" in args and "create" in args:
            data = json.loads(args[args.index("--data") + 1])
            return _result({"ok": True, "data": {"pin": data}})
        if "pins" in args and "list" in args:
            return _result(
                {
                    "ok": True,
                    "data": {"items": [{"message_id": CONTROL_MESSAGE_ID}]},
                }
            )
        return _result({"ok": True})

    return runner


def _write_binding(binding_path: Path, kanban_path: Path) -> None:
    binding_path.write_text(
        json.dumps(
            {
                "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
                "bindings": {
                    GOAL_ID: {
                        "goal_id": GOAL_ID,
                        "provider": "lark",
                        "enabled": True,
                        "channel": {
                            "chat_id": CHAT_ID,
                            "chat_name": f"LoopX - {GOAL_ID}",
                            "pinned_message_id": CONTROL_MESSAGE_ID,
                        },
                        "kanban": {
                            "config_path": str(kanban_path),
                            "base_token": "base_public_fixture",
                            "table_id": "tbl_public_fixture",
                            "base_url": "https://example.invalid/base/public-fixture",
                        },
                        "identity": {
                            "mode": "local_user",
                            "sender_profile": "",
                            "sender_identity": "bot",
                            "bot_app_id": "cli_public_fixture",
                            "bot_display_name": "",
                            "cli_bin": "lark-cli",
                        },
                        "receipts": {},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _gate_test_binding(tmp_path: Path) -> Path:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    _write_binding(binding_path, tmp_path / ".loopx" / "lark-kanban.json")
    return binding_path


def _notify_test_gate(
    *,
    tmp_path: Path,
    binding_path: Path,
    quota_packet: dict[str, Any],
    runner: Any,
) -> dict[str, Any]:
    return notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        execute=True,
        runner=runner,
    )


def _provider_target() -> dict[str, Any]:
    return {
        "name": "loopx-dev",
        "provider": "lark",
        "enabled": True,
        "channel": {
            "chat_id": CHAT_ID,
            "chat_name": "LoopX Development",
        },
        "identity": {
            "mode": "local_user",
            "sender_profile": "",
            "sender_identity": "bot",
            "bot_app_id": "cli_public_fixture",
            "bot_display_name": "",
            "cli_bin": "lark-cli",
        },
    }


def _assert_public_packet(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "oc_" not in serialized
    assert "om_" not in serialized
    assert "base_public_fixture" not in serialized
    assert "tbl_public_fixture" not in serialized
    assert str(Path.home()) not in serialized
    assert "sender_profile" not in serialized
    assert "config_path" not in serialized


def test_target_setup_keeps_shared_provider_values_out_of_goal_binding(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    calls: list[list[str]] = []
    runner = _fake_runner(calls)

    result = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        target_name="loopx-dev",
        provider_target=_provider_target(),
        execute=True,
        runner=runner,
    )

    assert result["ok"] is True
    raw = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert raw["target_ref"] == "loopx-dev"
    assert raw["channel"] == {"pinned_message_id": CONTROL_MESSAGE_ID}
    assert raw["identity"] == {}
    assert "goal-channels" in raw["kanban"]["config_path"]
    assert CHAT_ID not in json.dumps(raw)
    assert "cli_public_fixture" not in json.dumps(raw)

    doctor = doctor_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        provider_target=_provider_target(),
        runner=runner,
    )
    assert doctor["ok"] is True

    notified = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        provider_target=_provider_target(),
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
        execute=True,
        runner=_fake_runner([]),
    )
    assert notified["ok"] is True
    after_notify = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert len(after_notify["receipts"]) == 2
    assert CHAT_ID not in json.dumps(after_notify)
    assert "cli_public_fixture" not in json.dumps(after_notify)


def test_two_goals_share_target_but_keep_kanban_and_receipts_separate(
    tmp_path: Path,
) -> None:
    second_goal_id = "goal-second-public-fixture"
    registry = _registry(tmp_path)
    registry["goals"].append(
        {
            **registry["goals"][0],
            "id": second_goal_id,
            "objective": "Deliver a second isolated Goal Channel projection.",
        }
    )
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    runner = _fake_runner([])

    for goal_id in (GOAL_ID, second_goal_id):
        result = setup_lark_goal_channel(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            binding_path=binding_path,
            target_name="loopx-dev",
            provider_target=_provider_target(),
            execute=True,
            runner=runner,
        )
        assert result["ok"] is True

    bindings = read_goal_channel_binding(binding_path)["bindings"]
    first = bindings[GOAL_ID]
    second = bindings[second_goal_id]
    assert first["target_ref"] == second["target_ref"] == "loopx-dev"
    assert first["kanban"]["config_path"] != second["kanban"]["config_path"]
    assert set(first["receipts"]).isdisjoint(second["receipts"])
    assert CHAT_ID not in json.dumps(bindings)


def test_setup_is_preview_only_and_does_not_write_binding(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    calls: list[list[str]] = []

    payload = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        execute=False,
        runner=_fake_runner(calls),
    )

    assert payload["ok"] is True
    assert payload["status"] == "preview_ready"
    assert payload["external_write_performed"] is False
    assert binding_path.exists() is False
    assert not any("+chat-create" in args for args in calls)
    assert not any("+messages-send" in args for args in calls)
    _assert_public_packet(payload)


def test_private_binding_atomic_failure_preserves_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    original = {
        "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
        "bindings": {"existing": {"enabled": True}},
    }
    write_goal_channel_binding(binding_path, original)
    original_bytes = binding_path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        assert Path(str(source)).stat().st_mode & 0o777 == 0o600
        assert Path(str(destination)) == binding_path
        raise OSError("fixture replace failure")

    monkeypatch.setattr(
        "loopx.extensions.lark.private_json.os.replace",
        fail_replace,
    )

    with pytest.raises(OSError, match="fixture replace failure"):
        write_goal_channel_binding(
            binding_path,
            {
                "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
                "bindings": {"replacement": {"enabled": True}},
            },
        )

    assert binding_path.read_bytes() == original_bytes
    assert binding_path.stat().st_mode & 0o777 == 0o600
    assert list(binding_path.parent.glob(f".{binding_path.name}.*.tmp")) == []


def test_setup_and_disconnect_share_a_complete_file_transaction(tmp_path: Path) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    entered, release, disconnect_started = Event(), Event(), Event()
    calls: list[list[str]] = []
    runner = _fake_runner(calls)

    def delayed_runner(args, cwd, timeout):
        if "+chat-create" in args:
            entered.set()
            assert release.wait(3)
        return runner(args, cwd, timeout)

    def disconnect():
        disconnect_started.set()
        return disconnect_lark_goal_topic(
            binding_path=binding_path,
            goal_id=GOAL_ID,
            connection_id=goal_channel_connection_id(GOAL_ID, None),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        setup = pool.submit(
            setup_lark_goal_channel,
            registry=_registry(tmp_path),
            registry_path=tmp_path / ".loopx" / "registry.json",
            goal_id=GOAL_ID,
            binding_path=binding_path,
            kanban_config_path=tmp_path / ".loopx" / "lark-kanban.json",
            bot_app_id="cli_public_fixture",
            execute=True,
            runner=delayed_runner,
        )
        try:
            assert entered.wait(3)
            # Real kernel lock must cover provider effects, not only final save.
            with pytest.raises(LockAcquireTimeoutError):
                with exclusive_file_lock(binding_path, timeout_seconds=0):
                    pass
            removed = pool.submit(disconnect)
            assert disconnect_started.wait(3)
        finally:
            release.set()
        assert setup.result()["ok"]
        assert removed.result()["status"] == "disconnected"
    assert GOAL_ID not in read_goal_channel_binding(binding_path)["bindings"]


def test_default_setup_preview_does_not_create_a_lock(tmp_path: Path) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    result = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=tmp_path / ".loopx" / "registry.json",
        goal_id=GOAL_ID,
        binding_path=binding_path,
        runner=_fake_runner([]),
    )
    assert result["status"] == "preview_ready"
    assert not binding_path.exists()
    assert not binding_path.with_name(binding_path.name + ".lock").exists()


def test_setup_execute_persists_private_binding_after_verified_pin(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    calls: list[list[str]] = []

    payload = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_public_fixture",
        execute=True,
        runner=_fake_runner(calls),
    )

    assert payload["ok"] is True
    assert payload["status"] == "configured"
    assert payload["readback_verified"] is True
    assert payload["details"]["control_message_pinned"] is True
    persisted = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert persisted["channel"]["chat_id"] == CHAT_ID
    assert persisted["channel"]["pinned_message_id"] == CONTROL_MESSAGE_ID
    assert persisted["kanban"]["base_token"] == "base_public_fixture"
    assert persisted["kanban"]["base_url"] == (
        "https://example.invalid/base/public-fixture"
    )
    assert persisted["identity"]["bot_app_id"] == "cli_public_fixture"
    assert binding_path.stat().st_mode & 0o777 == 0o600
    assert kanban_path.stat().st_mode & 0o777 == 0o600
    assert any(
        "+chat-create" in args
        and args[args.index("--as") + 1] == "user"
        and args[args.index("--bots") + 1] == "cli_public_fixture"
        for args in calls
    )
    assert any("+messages-send" in args for args in calls)
    assert any(
        "+messages-send" in args and args[args.index("--as") + 1] == "bot"
        for args in calls
    )
    assert any("pins" in args and "create" in args for args in calls)
    assert any("pins" in args and "list" in args for args in calls)
    control_send = next(
        args
        for args in calls
        if "+messages-send" in args
        and not args[args.index("--text") + 1].startswith("LoopX human gate")
    )
    assert (
        "Kanban: https://example.invalid/base/public-fixture"
        in control_send[control_send.index("--text") + 1]
    )
    _assert_public_packet(payload)


def test_setup_execute_defaults_human_gate_auto_notify_on(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    calls: list[list[str]] = []

    payload = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_public_fixture",
        execute=True,
        runner=_fake_runner(calls),
    )
    assert payload["ok"] is True
    assert payload["status"] == "configured"
    persisted = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert persisted["automation"]["human_gate_auto_notify_enabled"] is True
    marker_path = goal_channel_contracts.human_gate_auto_notify_marker_path(
        binding_path, GOAL_ID
    )
    assert goal_channel_contracts.human_gate_auto_notify_marker_enabled(marker_path)


def test_setup_execute_preserves_explicit_auto_notify_opt_out(tmp_path: Path) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    payload = read_goal_channel_binding(binding_path)
    payload["bindings"][GOAL_ID]["automation"] = {
        "human_gate_auto_notify_enabled": False
    }
    write_goal_channel_binding(binding_path, payload)
    calls: list[list[str]] = []

    result = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=tmp_path / ".loopx" / "registry.json",
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_public_fixture",
        execute=True,
        runner=_fake_runner(calls),
    )
    assert result["ok"] is True
    assert result["status"] == "configured"
    persisted = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert persisted["automation"]["human_gate_auto_notify_enabled"] is False
    marker_path = goal_channel_contracts.human_gate_auto_notify_marker_path(
        binding_path, GOAL_ID
    )
    assert not goal_channel_contracts.human_gate_auto_notify_marker_enabled(marker_path)


def test_configure_auto_notify_is_preview_first_and_persists_private_opt_in(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    original_bytes = binding_path.read_bytes()

    preview = configure_lark_goal_channel_automation(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        human_gate_auto_notify=True,
        execute=False,
    )

    assert preview["ok"] is True
    assert preview["status"] == "preview_ready"
    assert preview["details"]["human_gate_auto_notify_enabled"] is True
    assert binding_path.read_bytes() == original_bytes

    applied = configure_lark_goal_channel_automation(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        human_gate_auto_notify=True,
        execute=True,
    )

    assert applied["ok"] is True
    assert applied["status"] == "configured"
    assert applied["readback_verified"] is True
    binding = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert binding["automation"]["human_gate_auto_notify_enabled"] is True
    marker_path = goal_channel_contracts.human_gate_auto_notify_marker_path(
        binding_path,
        GOAL_ID,
    )
    assert goal_channel_contracts.human_gate_auto_notify_marker_enabled(marker_path)
    assert binding["channel"]["chat_id"] == CHAT_ID
    assert binding_path.stat().st_mode & 0o777 == 0o600
    _assert_public_packet(preview)
    _assert_public_packet(applied)


def test_configure_can_disable_auto_notify_for_incomplete_binding(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    goal_binding = binding["bindings"][GOAL_ID]
    goal_binding["enabled"] = False
    goal_binding["automation"] = {"human_gate_auto_notify_enabled": True}
    write_goal_channel_binding(binding_path, binding)

    payload = configure_lark_goal_channel_automation(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        human_gate_auto_notify=False,
        execute=True,
    )

    assert payload["ok"] is True
    assert payload["readback_verified"] is True
    persisted = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert persisted["enabled"] is False
    assert persisted["automation"]["human_gate_auto_notify_enabled"] is False
    marker_path = goal_channel_contracts.human_gate_auto_notify_marker_path(
        binding_path,
        GOAL_ID,
    )
    assert marker_path.exists() is False
    _assert_public_packet(payload)


def test_auto_notify_gate_is_disabled_when_unconfigured_and_suppressible(
    tmp_path: Path,
) -> None:
    # A raw binding with no automation setting is unconfigured (disabled); the
    # setup path now defaults human-gate auto-notify to on (see
    # test_setup_execute_defaults_human_gate_auto_notify_on).
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "Approve the bounded external write.",
    }
    calls: list[list[str]] = []

    disabled = auto_notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        external_sink_delivery_authorized=True,
        runner=_fake_runner(calls),
    )
    assert disabled["ok"] is True
    assert disabled["enabled"] is False
    assert disabled["status"] == "disabled"
    assert calls == []

    configure_lark_goal_channel_automation(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        human_gate_auto_notify=True,
        execute=True,
    )
    suppressed = auto_notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        external_sink_delivery_authorized=False,
        runner=_fake_runner(calls),
    )
    assert suppressed["ok"] is True
    assert suppressed["enabled"] is True
    assert suppressed["status"] == "external_sink_suppressed"
    assert calls == []


def test_auto_notify_gate_sends_only_for_quota_selected_gate(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        base_url="https://example.invalid/base/public-fixture",
    )
    _write_binding(binding_path, kanban_path)
    configure_lark_goal_channel_automation(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        human_gate_auto_notify=True,
        execute=True,
    )
    calls: list[list[str]] = []
    runner = _fake_runner(calls)

    no_gate = auto_notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet={"state": "eligible"},
        external_sink_delivery_authorized=True,
        runner=runner,
    )
    assert no_gate["ok"] is True
    assert no_gate["status"] == "not_selected"
    assert not any("+messages-send" in args for args in calls)

    gate = auto_notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
        external_sink_delivery_authorized=True,
        runner=runner,
    )
    assert gate["ok"] is True
    assert gate["status"] == "sent_verified"
    assert gate["external_write_performed"] is True
    assert gate["readback_verified"] is True
    assert sum("+messages-send" in args for args in calls) == 1


def _refresh_gate_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binding_path = _gate_test_binding(tmp_path)
    registry_path = binding_path.parent / "registry.json"
    registry_path.write_text(json.dumps(_registry(tmp_path)), encoding="utf-8")
    configure_lark_goal_channel_automation(
        registry=_registry(tmp_path), goal_id=GOAL_ID, binding_path=binding_path,
        human_gate_auto_notify=True, execute=True,
    )
    monkeypatch.setattr(goal_channel_lifecycle, "resolve_extension_activation",
                        lambda *args, **kwargs: {"status": "active"})
    monkeypatch.setattr(goal_channel_lifecycle, "collect_status", lambda **kwargs: {})
    monkeypatch.setattr(goal_channel_lifecycle, "build_quota_should_run",
                        lambda *args, **kwargs: {
                            "state": "operator_gate", "notify_user_on_gate": True,
                            "gate_prompt": "Approve the bounded external write.",
                        })

    def run(runner, *, authorized=True):
        return goal_channel_lifecycle.sync_human_gate_after_refresh(
            registry_path=registry_path, runtime_root_override=None,
            goal_id=GOAL_ID, agent_id="agent-public-fixture",
            external_sink_delivery_authorized=authorized, runner=runner,
        )

    return binding_path, run


@pytest.mark.parametrize(
    ("command", "failure", "stage", "reason", "write_status"),
    [
        ("+messages-send", "timeout", "provider_send", "timeout", "unknown"),
        ("+messages-send", "spawn", "provider_send", "runtime_unavailable", "not_performed"),
        ("+messages-send", "reject", "provider_send", "provider_send_rejected", "not_performed"),
        ("+messages-send", "empty", "provider_send", "delivery_outcome_unknown", "unknown"),
        ("+messages-send", "unexpected", "provider_send", "unexpected_failure", "unknown"),
        ("+messages-mget", "timeout", "provider_readback", "timeout", "performed"),
        ("+messages-mget", "spawn", "provider_readback", "runtime_unavailable", "performed"),
        ("+messages-mget", "reject", "provider_readback", "provider_api_failed", "performed"),
        ("+messages-mget", "empty", "provider_readback", "readback_mismatch", "performed"),
    ],
)
def test_refresh_gate_preserves_safe_transport_failure_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    command: str, failure: str, stage: str, reason: str, write_status: str,
) -> None:
    _, run = _refresh_gate_fixture(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    base = _fake_runner(calls)

    def runner(args, cwd, timeout):
        if command not in args:
            return base(args, cwd, timeout)
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, timeout, output="private provider details")
        if failure == "spawn":
            raise FileNotFoundError("private CLI path")
        if failure == "unexpected":
            raise RuntimeError("private channel and provider details")
        return {
            "returncode": 1 if failure == "reject" else 0,
            "stdout": "private provider details" if failure == "reject" else "",
            "stderr": "", "timed_out": False,
        }

    result = run(runner)
    assert result["ok"] is False
    assert result["delivery_postcondition"]["blocks_delivery"] is True
    assert result["failure"] == {
        "schema_version": "loopx_goal_channel_gate_failure_v0",
        "stage": stage, "reason_code": reason,
        "external_write_status": write_status,
    }
    assert result["external_write_performed"] is (write_status == "performed")
    assert sum("+messages-send" in args for args in calls) == 1
    for private_detail in (
        "private provider details", "private CLI path", "private channel and provider details",
    ):
        assert private_detail not in json.dumps(result)
    _assert_public_packet(result)


def test_refresh_gate_receipt_failure_keeps_send_identity_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding_path, run = _refresh_gate_fixture(tmp_path, monkeypatch)
    before = binding_path.read_bytes()
    calls: list[list[str]] = []
    runner = _fake_runner(calls)
    save = goal_channel_runtime.save_goal_binding

    writes = 0
    def fail(**kwargs):
        nonlocal writes
        writes += 1
        if writes > 1:
            raise PermissionError("private receipt path")
        save(**kwargs)

    monkeypatch.setattr(goal_channel_runtime, "save_goal_binding", fail)
    result = run(runner)
    assert result["failure"]["stage"] == "receipt_write"
    assert result["failure"]["reason_code"] == "permission_denied"
    assert result["failure"]["external_write_status"] == "performed"
    assert result["external_write_performed"] is True
    assert binding_path.read_bytes() != before  # prepared body survives final receipt failure
    monkeypatch.setattr(goal_channel_runtime, "save_goal_binding", save)
    assert run(runner)["status"] == "sent_verified"
    sends = [args for args in calls if "+messages-send" in args]
    keys = [args[args.index("--idempotency-key") + 1] for args in sends]
    assert len(keys) == 2 and keys[0] == keys[1]
    assert run(runner)["status"] == "already_sent"
    assert sum("+messages-send" in args for args in calls) == 2
    _assert_public_packet(result)


def test_refresh_gate_selection_failure_is_local_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, run = _refresh_gate_fixture(tmp_path, monkeypatch)

    def fail(**kwargs):
        raise TimeoutError("private coordination address")

    monkeypatch.setattr(goal_channel_lifecycle, "collect_status", fail)
    calls: list[list[str]] = []
    result = run(_fake_runner(calls))
    assert result["failure"]["stage"] == "gate_selection"
    assert result["failure"]["reason_code"] == "timeout"
    assert result["failure"]["external_write_status"] == "not_attempted"
    assert calls == []
    _assert_public_packet(result)


@pytest.mark.parametrize("stage", ["extension_activation", "binding_resolution"])
def test_refresh_gate_local_configuration_failure_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    _, run = _refresh_gate_fixture(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        raise ValueError("private configuration details")

    symbol = "resolve_extension_activation" if stage == "extension_activation" else "read_goal_channel_binding"
    monkeypatch.setattr(goal_channel_lifecycle, symbol, fail)
    calls: list[list[str]] = []
    result = run(_fake_runner(calls))
    assert result["failure"]["stage"] == stage
    assert result["failure"]["reason_code"] == "invalid_input_or_config"
    assert calls == []
    _assert_public_packet(result)


def test_refresh_gate_observer_uses_command_position_and_preserves_runner_inputs() -> None:
    calls = []

    def runner(args, cwd, timeout):
        calls.append((args, cwd, timeout))
        return _result({"message_id": GATE_MESSAGE_ID})

    observation = goal_channel_lifecycle._DeliveryObservation(runner)
    argv = ["custom-lark-cli", "auth", "status", "--text", "im", "+messages-send"]
    observation(argv, Path("fixture"), 12)
    assert observation.stage == "provider_preflight"
    assert observation.write_status == "not_attempted"
    profiled = ["custom-lark-cli", "--profile", "fixture", "im", "+messages-send"]
    observation(profiled, None, 7)
    assert observation.stage == "provider_send" and observation.write_status == "performed"
    assert calls == [(argv, Path("fixture"), 12), (profiled, None, 7)]


@pytest.mark.parametrize("mode", ["disabled", "suppressed", "not_selected", "cooldown"])
def test_refresh_gate_noop_has_no_extra_provider_calls_or_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    binding_path, run = _refresh_gate_fixture(tmp_path, monkeypatch)
    if mode == "disabled":
        configure_lark_goal_channel_automation(
            registry=_registry(tmp_path), goal_id=GOAL_ID, binding_path=binding_path,
            human_gate_auto_notify=False, execute=True,
        )
    elif mode in {"not_selected", "cooldown"}:
        packet = {"state": "eligible"} if mode == "not_selected" else {
            "state": "operator_gate", "notify_user_on_gate": True,
            "user_gate_notification_cooldown": {"notification_suppressed": True},
        }
        monkeypatch.setattr(goal_channel_lifecycle, "build_quota_should_run",
                            lambda *args, **kwargs: packet)
    def stale_extension(*args, **kwargs):
        raise AssertionError("a safe no-op must not require extension admission")

    monkeypatch.setattr(goal_channel_lifecycle, "resolve_extension_activation", stale_extension)
    calls: list[list[str]] = []
    result = run(_fake_runner(calls), authorized=mode != "suppressed")
    assert result["ok"] is True
    assert "failure" not in result and "failure_summary" not in result
    assert result["status"] == ("external_sink_suppressed" if mode == "suppressed" else mode)
    assert calls == []


def test_refresh_gate_already_sent_does_not_require_extension_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, run = _refresh_gate_fixture(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    runner = _fake_runner(calls)
    assert run(runner)["status"] == "sent_verified"
    calls.clear()

    def stale_extension(*args, **kwargs):
        raise AssertionError("verified delivery replay must not re-admit transport")

    monkeypatch.setattr(goal_channel_lifecycle, "resolve_extension_activation", stale_extension)
    result = run(runner)
    assert result["status"] == "already_sent"
    assert result["delivery_postcondition"]["satisfied"] is True
    assert "extension_activation" not in result
    assert calls == []


def test_setup_inherits_existing_bot_identity_when_flags_are_omitted(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        base_url="https://example.invalid/base/public-fixture",
    )
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["identity"] = {
        "mode": "project_bot",
        "sender_profile": "loopx-project-bot",
        "sender_identity": "bot",
        "bot_app_id": "cli_public_fixture",
        "bot_display_name": "LoopX Bot",
        "cli_bin": "custom-lark-cli",
    }
    binding_path.write_text(json.dumps(binding) + "\n", encoding="utf-8")
    calls: list[list[str]] = []

    payload = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        execute=True,
        runner=_fake_runner(calls),
    )

    assert payload["ok"] is True
    persisted = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert persisted["identity"] == {
        "mode": "project_bot",
        "sender_profile": "loopx-project-bot",
        "sender_identity": "bot",
        "bot_app_id": "cli_public_fixture",
        "bot_display_name": "LoopX Bot",
        "cli_bin": "custom-lark-cli",
    }
    assert any(
        args[0] == "custom-lark-cli"
        and args[1:3] == ["--profile", "loopx-project-bot"]
        and args[args.index("--as") + 1] == "bot"
        for args in calls
        if "--as" in args
    )
    assert any(
        args[0] == "custom-lark-cli"
        and "--profile" not in args
        and args[args.index("--as") + 1] == "user"
        for args in calls
        if args
        and args[0] == "custom-lark-cli"
        and "--as" in args
        and ("base" in args or "+field-list" in args or "+view-list" in args)
    )
    _assert_public_packet(payload)


def test_setup_retry_reuses_verified_chat_after_later_failure(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    first_calls: list[list[str]] = []
    base_runner = _fake_runner(first_calls)

    def failing_runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        if "+base-create" in args:
            first_calls.append(args)
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "fixture failure",
                "timed_out": False,
            }
        return base_runner(args, cwd, timeout)

    first = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_public_fixture",
        execute=True,
        runner=failing_runner,
    )

    assert first["ok"] is False
    assert first["blocker"] == "provider_api_failed"
    recovery = read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]
    assert recovery["enabled"] is False
    assert recovery["channel"]["chat_id"] == CHAT_ID
    assert any("+chat-create" in args for args in first_calls)

    retry_calls: list[list[str]] = []
    retry = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_public_fixture",
        execute=True,
        runner=_fake_runner(retry_calls),
    )

    assert retry["ok"] is True
    assert not any("+chat-create" in args for args in retry_calls)
    assert (
        read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]["enabled"] is True
    )
    _assert_public_packet(first)
    _assert_public_packet(retry)


def test_setup_retry_adds_missing_bot_without_recreating_chat(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["enabled"] = False
    binding_path.write_text(json.dumps(binding) + "\n", encoding="utf-8")
    calls: list[list[str]] = []
    base_runner = _fake_runner(calls)
    bot_added = False

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        nonlocal bot_added
        if "+chat-members-list" in args:
            calls.append(args)
            return _result(
                {
                    "ok": True,
                    "data": {
                        "users": [{"member_id": "ou_public_fixture"}],
                        "bots": (
                            [{"app_id": "cli_public_fixture"}] if bot_added else []
                        ),
                    },
                }
            )
        if "chat.members" in args and "create" in args:
            calls.append(args)
            bot_added = True
            return _result({"ok": True, "data": {"invalid_id_list": []}})
        return base_runner(args, cwd, timeout)

    payload = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_public_fixture",
        execute=True,
        runner=runner,
    )

    assert payload["ok"] is True
    assert not any("+chat-create" in args for args in calls)
    add_bot = next(
        args for args in calls if "chat.members" in args and "create" in args
    )
    assert add_bot[add_bot.index("--member-id-type") + 1] == "app_id"
    assert json.loads(add_bot[add_bot.index("--data") + 1]) == {
        "id_list": ["cli_public_fixture"]
    }
    assert any(
        "+messages-send" in args and args[args.index("--as") + 1] == "bot"
        for args in calls
    )
    assert (
        read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]["enabled"] is True
    )
    _assert_public_packet(payload)


def test_setup_execute_requires_explicit_matching_bot_app_id(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"

    missing = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        execute=True,
        runner=_fake_runner([]),
    )
    mismatch = setup_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        kanban_config_path=kanban_path,
        bot_app_id="cli_other_fixture",
        execute=True,
        runner=_fake_runner([]),
    )

    assert missing["ok"] is False
    assert missing["blocker"] == "provider_identity_selection_required"
    assert mismatch["ok"] is False
    assert mismatch["blocker"] == "provider_identity_unverified"
    assert binding_path.exists() is False
    _assert_public_packet(missing)
    _assert_public_packet(mismatch)


def test_notify_gate_is_idempotent_after_verified_send(tmp_path: Path) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        base_url="https://example.invalid/base/public-fixture",
    )
    _write_binding(binding_path, kanban_path)
    calls: list[list[str]] = []
    runner = _fake_runner(calls)
    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "Approve the bounded external write.",
        "recommended_action": "Wait for the owner decision.",
        "user_todo_summary": {
            "first_executable_items": [{"text": "Approve the bounded external write."}]
        },
    }

    first = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        execute=True,
        runner=runner,
    )
    send_count = sum("+messages-send" in args for args in calls)
    second = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        execute=True,
        runner=runner,
    )

    assert first["ok"] is True
    assert first["status"] == "sent_verified"
    assert first["readback_verified"] is True
    assert second["ok"] is True
    assert second["status"] == "already_sent"
    assert sum("+messages-send" in args for args in calls) == send_count
    _assert_public_packet(first)
    _assert_public_packet(second)


def test_notify_gate_distinguishes_different_gate_ids_with_same_question(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    calls: list[list[str]] = []
    runner = _fake_runner(calls)

    def quota(todo_id: str) -> dict[str, Any]:
        return {
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
            "user_todo_summary": {
                "gate_open_items": [
                    {
                        "todo_id": todo_id,
                        "task_class": "user_gate",
                        "text": "Approve the bounded external write.",
                    }
                ]
            },
        }

    first = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota("todo_gate_one"),
        runner=runner,
    )
    second = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota("todo_gate_two"),
        runner=runner,
    )

    assert first["status"] == "sent_verified"
    assert second["status"] == "sent_verified"
    assert first["idempotency_key"] != second["idempotency_key"]
    assert sum("+messages-send" in args for args in calls) == 2


def test_notify_gate_uses_material_state_generation_not_projection_copy(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    calls: list[list[str]] = []
    runner = _fake_runner(calls)
    item = {
        "todo_id": "todo_gate_fixture",
        "status": "open",
        "task_class": "user_gate",
        "updated_at": "2026-08-08T00:00:00Z",
        "text": "Approve the bounded external write.",
    }

    def packet(
        *,
        prompt: str,
        recommended_action: str,
        projected_action: str,
    ) -> dict[str, Any]:
        return {
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": prompt,
            "recommended_action": recommended_action,
            "user_todo_summary": {"gate_open_items": [item]},
            "interaction_contract": {
                "user_channel": {
                    "action_required": True,
                    "notify": "NOTIFY",
                    "actions": [projected_action],
                }
            },
        }

    first = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=packet(
            prompt="Approve the bounded external write.",
            recommended_action="Wait for the owner decision.",
            projected_action="- [ ] [P0] Approve the bounded external write.",
        ),
        runner=runner,
    )
    second = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=packet(
            prompt="Current recommendation: approve the bounded external write.",
            recommended_action="Keep waiting for the same owner decision.",
            projected_action="1. Approve the bounded external write.",
        ),
        runner=runner,
    )

    assert first["status"] == "sent_verified"
    assert second["status"] == "already_sent"
    assert first["idempotency_key"] == second["idempotency_key"]
    assert sum("+messages-send" in args for args in calls) == 1


def test_notify_gate_does_not_apply_legacy_receipt_to_unknown_material_state(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["receipts"] = {
        "sha256:legacy": {
            "kind": "gate_notification",
            "gate_identity": "todo_gate_fixture",
            "message_id": GATE_MESSAGE_ID,
            "verified_at": "2026-08-08T00:00:00+00:00",
        }
    }
    write_goal_channel_binding(binding_path, binding)
    calls: list[list[str]] = []
    result = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the changed bounded external write.",
            "user_todo_summary": {
                "gate_open_items": [
                    {
                        "todo_id": "todo_gate_fixture",
                        "status": "open",
                        "task_class": "user_gate",
                        "updated_at": "2026-08-08T01:00:00Z",
                        "text": "Approve the changed bounded external write.",
                    }
                ]
            },
        },
        runner=_fake_runner(calls),
    )

    assert result["status"] == "sent_verified"
    assert sum("+messages-send" in args for args in calls) == 1


def test_notify_gate_preserves_exact_legacy_delivery_for_same_target(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "Approve the bounded external write.",
        "user_todo_summary": {
            "gate_open_items": [
                {
                    "todo_id": "todo_gate_fixture",
                    "task_class": "user_gate",
                    "text": "Approve the bounded external write.",
                }
            ]
        },
    }
    legacy_key = goal_channel_contracts.semantic_key(
        GOAL_ID,
        "lark",
        "notify_gate",
        "todo_gate_fixture",
        "Approve the bounded external write.",
        CHAT_ID,
    )
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["receipts"] = {
        legacy_key: {
            "kind": "gate_notification",
            "gate_identity": "todo_gate_fixture",
            "message_id": GATE_MESSAGE_ID,
            "verified_at": "2026-08-08T00:00:00+00:00",
        }
    }
    write_goal_channel_binding(binding_path, binding)
    calls: list[list[str]] = []
    result = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota_packet,
        runner=_fake_runner(calls),
    )

    assert result["status"] == "already_sent"
    assert sum("+messages-send" in args for args in calls) == 0


def test_notify_gate_sends_same_generation_once_to_replacement_channel(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    calls: list[list[str]] = []
    runner = _fake_runner(calls)
    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "Approve the bounded external write.",
        "user_todo_summary": {
            "gate_open_items": [
                {
                    "todo_id": "todo_gate_fixture",
                    "status": "open",
                    "task_class": "user_gate",
                    "updated_at": "2026-08-08T00:00:00Z",
                    "text": "Approve the bounded external write.",
                }
            ]
        },
    }
    first = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota_packet,
        runner=runner,
    )
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["channel"]["chat_id"] = "oc_second_fixture"
    write_goal_channel_binding(binding_path, binding)
    second = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota_packet,
        runner=runner,
    )

    assert first["status"] == "sent_verified"
    assert second["status"] == "sent_verified"
    assert first["idempotency_key"] != second["idempotency_key"]
    assert sum("+messages-send" in args for args in calls) == 2


def test_notify_gate_resends_for_material_transition_or_one_reminder_window(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    calls: list[list[str]] = []
    runner = _fake_runner(calls)

    def packet(
        *,
        updated_at: str,
        text: str,
        reminder: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = {
            "todo_id": "todo_gate_fixture",
            "status": "open",
            "task_class": "user_gate",
            "updated_at": updated_at,
            "text": text,
        }
        result: dict[str, Any] = {
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": text,
            "user_todo_summary": {"gate_open_items": [item]},
            "interaction_contract": {
                "user_channel": {
                    "action_required": True,
                    "notify": "NOTIFY",
                    "actions": [text],
                }
            },
        }
        if reminder is not None:
            result["user_gate_notification_cooldown"] = reminder
        return result

    initial = packet(
        updated_at="2026-08-08T00:00:00Z",
        text="Approve the bounded external write.",
    )
    changed = packet(
        updated_at="2026-08-08T01:00:00Z",
        text="Approve the bounded external write after reviewing the diff.",
    )
    reminder = {
        "notification_due": True,
        "notification_suppressed": False,
        "policy": "failed_host_update_bounded_reminder_window",
        "failed_at": "2026-08-08T00:00:00Z",
        "next_reminder_at": "2026-08-08T03:00:00Z",
        "cooldown_minutes": 60,
        "reminder_window_minutes": 5,
    }

    first = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=initial,
        runner=runner,
    )
    material = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=changed,
        runner=runner,
    )
    reminded = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=packet(
            updated_at="2026-08-08T01:00:00Z",
            text="Approve the bounded external write after reviewing the diff.",
            reminder=reminder,
        ),
        runner=runner,
    )
    reminder_replay = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=packet(
            updated_at="2026-08-08T01:00:00Z",
            text="Approve the bounded external write after reviewing the diff.",
            reminder=reminder,
        ),
        runner=runner,
    )

    assert first["status"] == "sent_verified"
    assert material["status"] == "sent_verified"
    assert reminded["status"] == "sent_verified"
    assert reminder_replay["status"] == "already_sent"
    assert (
        len(
            {
                first["idempotency_key"],
                material["idempotency_key"],
                reminded["idempotency_key"],
            }
        )
        == 3
    )
    assert sum("+messages-send" in args for args in calls) == 3


def test_notify_gate_honors_admission_and_does_not_present_labels_as_decisions(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    calls: list[list[str]] = []
    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "- Current recommendation: approve. - User todo: revoke key.",
        "recommended_action": "Approve the same gate again.",
        "interaction_contract": {
            "user_channel": {
                "action_required": False,
                "notify": "DONT_NOTIFY",
                "actions": [
                    "- [ ] [P0] Approve the bounded change.",
                    "12. Revoke the test key.",
                ],
            }
        },
    }

    rejected = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota_packet,
        runner=_fake_runner(calls),
    )
    notice = goal_channel_contracts._gate_notice_projection(
        goal_id=GOAL_ID,
        quota_packet={
            **quota_packet,
            "interaction_contract": {
                "user_channel": {
                    "action_required": True,
                    "notify": "NOTIFY",
                    "actions": [
                        "- [ ] [P0] Approve the bounded change.",
                        "12. Revoke the test key.",
                    ],
                }
            },
        },
    )

    assert rejected["status"] == "rejected"
    assert rejected["blocker"] == "state_transition_rejected"
    assert calls == []
    assert notice["source"] == "unavailable"
    assert notice["items"] == []



def test_notify_gate_reports_missing_shared_target_for_direct_caller(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["target_ref"] = "missing-target"
    binding["bindings"][GOAL_ID]["channel"] = {}
    binding["bindings"][GOAL_ID]["identity"] = {}
    write_goal_channel_binding(binding_path, binding)

    payload = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
        execute=True,
        runner=_fake_runner([]),
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["blocker"] == "provider_target_missing"
    _assert_public_packet(payload)


def test_notify_gate_does_not_resend_unchanged_generation_when_readback_is_stale(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
        base_url="https://example.invalid/base/public-fixture",
    )
    _write_binding(binding_path, kanban_path)
    calls: list[list[str]] = []
    base_runner = _fake_runner(calls)
    mget_count = 0

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        nonlocal mget_count
        if "+messages-mget" in args:
            mget_count += 1
            if mget_count == 2:
                calls.append(args)
                return _result({"ok": True, "data": {"items": []}})
        return base_runner(args, cwd, timeout)

    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "Approve the bounded external write.",
    }
    first = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        execute=True,
        runner=runner,
    )
    first_send_count = sum("+messages-send" in args for args in calls)
    second = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet=quota_packet,
        execute=True,
        runner=runner,
    )

    assert first["status"] == "sent_verified"
    assert second["status"] == "already_sent"
    assert second["readback_verified"] is True
    assert sum("+messages-send" in args for args in calls) == first_send_count
    assert mget_count == 1
    _assert_public_packet(first)
    _assert_public_packet(second)


def test_notify_gate_records_sent_unverified_generation_before_returning(
    tmp_path: Path,
) -> None:
    binding_path = _gate_test_binding(tmp_path)
    calls: list[list[str]] = []
    base_runner = _fake_runner(calls)

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        if "+messages-mget" in args:
            calls.append(args)
            return _result({"ok": True, "data": {"items": []}})
        return base_runner(args, cwd, timeout)

    quota_packet = {
        "state": "operator_gate",
        "notify_user_on_gate": True,
        "gate_prompt": "Approve the bounded external write.",
    }
    first = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota_packet,
        runner=runner,
    )
    second = _notify_test_gate(
        tmp_path=tmp_path,
        binding_path=binding_path,
        quota_packet=quota_packet,
        runner=runner,
    )

    assert first["status"] == "sent_unverified"
    assert first["external_write_performed"] is True
    assert second["status"] == "already_sent"
    assert second["readback_verified"] is False
    assert sum("+messages-send" in args for args in calls) == 1
    assert sum("+messages-mget" in args for args in calls) == 1


def test_notify_gate_respects_quota_cooldown_without_external_call(
    tmp_path: Path,
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["receipts"] = {
        "sha256:prior-reminder": {
            "kind": "gate_notification",
            "gate_identity": "todo_gate_fixture",
            "message_id": GATE_MESSAGE_ID,
            "verified_at": "2026-08-08T00:00:00+00:00",
        }
    }
    write_goal_channel_binding(binding_path, binding)
    calls: list[list[str]] = []

    payload = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
            "user_todo_summary": {
                "gate_open_items": [
                    {
                        "todo_id": "todo_gate_fixture",
                        "task_class": "user_gate",
                        "text": "Approve the bounded external write.",
                    }
                ]
            },
            "user_gate_notification_cooldown": {
                "notification_suppressed": True,
            },
        },
        execute=True,
        runner=_fake_runner(calls),
    )

    assert payload["ok"] is False
    assert payload["blocker"] == "notification_cooldown_active"
    assert calls == []
    _assert_public_packet(payload)


def test_doctor_reports_missing_binding_with_typed_blocker(tmp_path: Path) -> None:
    payload = doctor_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=tmp_path / ".loopx" / "registry.json",
        goal_id=GOAL_ID,
        binding_path=tmp_path / ".loopx" / "goal-channel.json",
        runner=_fake_runner([]),
    )

    assert payload["ok"] is False
    assert payload["blocker"] == "channel_binding_missing"
    _assert_public_packet(payload)


def test_doctor_requires_live_control_message_and_pin(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
    )
    _write_binding(binding_path, kanban_path)
    calls: list[list[str]] = []
    base_runner = _fake_runner(calls)

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        if "pins" in args and "list" in args:
            calls.append(args)
            return _result({"ok": True, "data": {"items": []}})
        return base_runner(args, cwd, timeout)

    payload = doctor_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        runner=runner,
    )

    assert payload["ok"] is False
    assert payload["blocker"] == "readback_mismatch"
    assert payload["details"]["control_message_verified"] is False
    _assert_public_packet(payload)


def test_relative_kanban_path_resolves_from_source_registry_across_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    caller = tmp_path / "caller"
    registry_path = source / ".loopx" / "registry.json"
    binding_path = source / ".loopx" / "goal-channel.json"
    kanban_path = source / ".loopx" / "lark-kanban.json"
    binding_path.parent.mkdir(parents=True)
    caller.mkdir()
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
    )
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["kanban"]["config_path"] = ".loopx/lark-kanban.json"
    write_goal_channel_binding(binding_path, binding)
    observed: dict[str, object] = {}

    def sync_todos(
        config: object,
        *,
        config_path: Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        observed["config"] = config
        observed["config_path"] = config_path
        return {
            "ok": True,
            "todo_count": 0,
            "successful_write_count": 0,
            "external_write_performed": False,
            "records": [],
        }

    monkeypatch.setattr(
        "loopx.extensions.lark.goal_channel_runtime.sync_loopx_todos_to_lark_kanban",
        sync_todos,
    )
    monkeypatch.chdir(caller)
    relative_registry_path = Path("..") / registry_path.relative_to(tmp_path)

    doctor = doctor_lark_goal_channel(
        registry=_registry(source),
        registry_path=relative_registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        runner=_fake_runner([]),
    )
    sync = sync_lark_goal_channel(
        registry=_registry(source),
        registry_path=relative_registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=False,
        runner=_fake_runner([]),
    )

    assert doctor["blocker"] == "readback_mismatch"
    assert doctor["details"]["kanban_ready"] is True
    assert sync["ok"] is True
    assert sync["status"] == "preview_ready"
    assert sync["details"]["kanban_ready"] is True
    assert observed["config_path"] == kanban_path
    _assert_public_packet(doctor)
    _assert_public_packet(sync)


def test_partial_binding_blocks_doctor_sync_and_notify(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["enabled"] = False
    binding_path.write_text(json.dumps(binding) + "\n", encoding="utf-8")

    doctor = doctor_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        runner=_fake_runner([]),
    )
    sync = sync_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=False,
        runner=_fake_runner([]),
    )
    notify = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
        execute=False,
        runner=_fake_runner([]),
    )

    for payload in (doctor, sync, notify):
        assert payload["ok"] is False
        assert payload["blocker"] == "channel_binding_incomplete"
        _assert_public_packet(payload)


def test_legacy_user_sender_binding_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
    )
    _write_binding(binding_path, kanban_path)
    binding = read_goal_channel_binding(binding_path)
    binding["bindings"][GOAL_ID]["identity"]["sender_identity"] = "user"
    binding["bindings"][GOAL_ID]["identity"].pop("bot_app_id")
    binding_path.write_text(json.dumps(binding) + "\n", encoding="utf-8")

    doctor = doctor_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        runner=_fake_runner([]),
    )
    notify = notify_lark_goal_channel_gate(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        quota_packet={
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
        execute=True,
        runner=_fake_runner([]),
    )

    assert doctor["ok"] is False
    assert doctor["blocker"] == "provider_identity_unverified"
    assert notify["ok"] is False
    assert notify["blocker"] == "provider_identity_unverified"
    _assert_public_packet(doctor)
    _assert_public_packet(notify)


def test_cli_checks_extension_before_reading_private_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps(_registry(tmp_path)), encoding="utf-8")
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.write_text("not-json", encoding="utf-8")
    captured: dict[str, Any] = {}

    def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("extension unavailable")

    monkeypatch.setattr(goal_channel_cli, "resolve_extension_activation", unavailable)

    result = goal_channel_cli.handle_goal_channel_command(
        argparse.Namespace(
            command="goal-channel",
            goal_channel_command="doctor",
            goal_id=GOAL_ID,
            binding_path=str(binding_path),
            execute=False,
            subcommand_format="json",
            format=None,
        ),
        registry_path=registry_path,
        runtime_root_arg=None,
        print_payload=lambda payload, fmt, renderer: captured.update(payload),
        output_format=lambda args: "json",
    )

    assert result == 1
    assert captured["blocker"] == "extension_unavailable"
    assert "not-json" not in json.dumps(captured)


def test_cli_can_disable_auto_notify_when_extension_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps(_registry(tmp_path)), encoding="utf-8")
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    _write_binding(binding_path, kanban_path)
    configure_lark_goal_channel_automation(
        registry=_registry(tmp_path),
        goal_id=GOAL_ID,
        binding_path=binding_path,
        human_gate_auto_notify=True,
        execute=True,
    )
    captured: dict[str, Any] = {}

    def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("extension unavailable")

    monkeypatch.setattr(goal_channel_cli, "resolve_extension_activation", unavailable)

    result = goal_channel_cli.handle_goal_channel_command(
        argparse.Namespace(
            command="goal-channel",
            goal_channel_command="configure",
            goal_id=GOAL_ID,
            binding_path=str(binding_path),
            auto_notify_human_gates=False,
            execute=True,
            subcommand_format="json",
            format=None,
        ),
        registry_path=registry_path,
        runtime_root_arg=None,
        print_payload=lambda payload, fmt, renderer: captured.update(payload),
        output_format=lambda args: "json",
    )

    assert result == 0
    assert captured["ok"] is True
    assert captured["readback_verified"] is True
    assert (
        read_goal_channel_binding(binding_path)["bindings"][GOAL_ID]["automation"][
            "human_gate_auto_notify_enabled"
        ]
        is False
    )


def test_cli_rejects_custom_binding_path_for_auto_notify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps(_registry(tmp_path)), encoding="utf-8")
    custom_binding = tmp_path / ".loopx" / "custom-goal-channel.json"
    _write_binding(custom_binding, tmp_path / ".loopx" / "lark-kanban.json")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        goal_channel_cli,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )

    result = goal_channel_cli.handle_goal_channel_command(
        argparse.Namespace(
            command="goal-channel",
            goal_channel_command="configure",
            goal_id=GOAL_ID,
            binding_path=str(custom_binding),
            auto_notify_human_gates=True,
            execute=True,
            subcommand_format="json",
            format=None,
        ),
        registry_path=registry_path,
        runtime_root_arg=None,
        print_payload=lambda payload, fmt, renderer: captured.update(payload),
        output_format=lambda args: "json",
    )

    assert result == 1
    assert captured["blocker"] == "noncanonical_binding_path"
    assert (
        read_goal_channel_binding(custom_binding)["bindings"][GOAL_ID].get("automation")
        is None
    )


def test_cli_global_registry_routes_binding_to_source_registry_from_any_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "canonical-project"
    source_registry_path = project / ".loopx" / "registry.json"
    source_registry_path.parent.mkdir(parents=True)
    source_registry = _registry(project)
    source_registry["goals"][0]["repo"] = str(project)
    source_registry_path.write_text(json.dumps(source_registry), encoding="utf-8")
    shared_runtime = tmp_path / "shared-runtime"
    global_registry_path = shared_runtime / "registry.global.json"
    global_registry_path.parent.mkdir(parents=True)
    global_registry = {
        **source_registry,
        "registry_role": "global-local",
        "common_runtime_root": str(shared_runtime),
    }
    global_registry["goals"] = [
        {
            **source_registry["goals"][0],
            "source_registry": str(source_registry_path),
        }
    ]
    global_registry_path.write_text(json.dumps(global_registry), encoding="utf-8")
    captured_paths: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        goal_channel_cli,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"ok": True},
    )

    def capture_doctor(**kwargs: Any) -> dict[str, Any]:
        captured_paths.append((kwargs["registry_path"], kwargs["binding_path"]))
        return {
            "ok": True,
            "goal_id": GOAL_ID,
            "provider": "lark",
            "operation": "doctor",
            "status": "ready",
            "execute": False,
            "external_write_performed": False,
            "readback_verified": True,
            "public_summary": "ready",
        }

    monkeypatch.setattr(goal_channel_cli, "doctor_lark_goal_channel", capture_doctor)
    unrelated_one = tmp_path / "unrelated-one"
    unrelated_two = tmp_path / "unrelated-two"
    unrelated_one.mkdir()
    unrelated_two.mkdir()
    args = argparse.Namespace(
        command="goal-channel",
        goal_channel_command="doctor",
        goal_id=GOAL_ID,
        binding_path=None,
        execute=False,
        subcommand_format="json",
        format=None,
    )

    for cwd in (unrelated_one, unrelated_two):
        monkeypatch.chdir(cwd)
        result = goal_channel_cli.handle_goal_channel_command(
            args,
            registry_path=global_registry_path,
            runtime_root_arg=None,
            print_payload=lambda payload, fmt, renderer: None,
            output_format=lambda namespace: "json",
        )
        assert result == 0

    expected_binding = project / ".loopx" / "goal-channel.json"
    assert captured_paths == [
        (source_registry_path.resolve(), expected_binding),
        (source_registry_path.resolve(), expected_binding),
    ]
    assert not (unrelated_one / ".loopx").exists()
    assert not (unrelated_two / ".loopx").exists()


def test_cli_deliver_operation_uses_source_registry_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "canonical-project"
    source_registry_path = project / ".loopx" / "registry.json"
    source_registry_path.parent.mkdir(parents=True)
    source_registry = _registry(project)
    source_registry["goals"][0]["repo"] = str(project)
    source_registry_path.write_text(json.dumps(source_registry), encoding="utf-8")
    source_runtime = project / "runtime"
    shared_runtime = tmp_path / "shared-runtime"
    global_registry_path = shared_runtime / "registry.global.json"
    global_registry_path.parent.mkdir(parents=True)
    global_registry = {
        **source_registry,
        "registry_role": "global-local",
        "common_runtime_root": str(shared_runtime),
    }
    global_registry["goals"] = [
        {
            **source_registry["goals"][0],
            "source_registry": str(source_registry_path),
        }
    ]
    global_registry_path.write_text(json.dumps(global_registry), encoding="utf-8")
    captured: dict[str, Any] = {}
    printed: dict[str, Any] = {}

    monkeypatch.setattr(
        goal_channel_cli,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"ok": True},
    )

    def capture_delivery(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "ok": True,
            "goal_id": GOAL_ID,
            "provider": "lark",
            "operation": "deliver_operation_card",
            "status": "pending_execution",
            "execute": False,
            "external_write_performed": False,
            "readback_verified": False,
            "public_summary": "validated",
        }

    monkeypatch.setattr(
        goal_channel_operation_cli,
        "deliver_goal_channel_operation_card",
        capture_delivery,
    )
    result = goal_channel_cli.handle_goal_channel_command(
        argparse.Namespace(
            command="goal-channel",
            goal_channel_command="deliver-operation",
            goal_id=GOAL_ID,
            proposal_id="proposal-public-fixture",
            binding_path=None,
            target_path=None,
            execute=False,
            subcommand_format="json",
            format=None,
        ),
        registry_path=global_registry_path,
        runtime_root_arg=None,
        print_payload=lambda payload, fmt, renderer: printed.update(payload),
        output_format=lambda args: "json",
    )

    assert result == 0
    assert printed["ok"] is True
    assert printed["extension_activation"] == {"ok": True}
    assert captured["proposal_id"] == "proposal-public-fixture"
    assert captured["action_store_root"] == source_runtime / "chat" / "actions"
    assert captured["runtime_root"] == source_runtime
    assert captured["binding_path"] == project / ".loopx" / "goal-channel.json"
    assert (
        captured["target_path"]
        == (source_runtime / "goal-channel-targets.json").resolve()
    )
    assert captured["expected_goal_id"] == GOAL_ID


@pytest.mark.parametrize(
    ("remote_record_ids", "expected_ok", "expected_blocker"),
    [
        (["rec_public_one"], True, None),
        ([], False, "readback_mismatch"),
    ],
)
def test_sync_requires_remote_record_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_record_ids: list[str],
    expected_ok: bool,
    expected_blocker: str | None,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
    )
    _write_binding(binding_path, kanban_path)

    monkeypatch.setattr(
        "loopx.extensions.lark.goal_channel_runtime.sync_loopx_todos_to_lark_kanban",
        lambda *args, **kwargs: {
            "ok": True,
            "todo_count": 1,
            "successful_write_count": 1,
            "external_write_performed": True,
            "records": [
                {
                    "todo_id": "todo_public_one",
                    "record_id": "rec_public_one",
                }
            ],
        },
    )

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, object]:
        assert "+record-list" in args
        assert "--view-id" not in args
        return _result(
            {
                "ok": True,
                "data": {"record_id_list": remote_record_ids},
            }
        )

    payload = sync_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        runner=runner,
    )

    assert payload["ok"] is expected_ok
    assert payload.get("blocker") == expected_blocker
    assert payload["external_write_performed"] is True
    assert payload["readback_verified"] is expected_ok
    _assert_public_packet(payload)


def test_sync_preserves_provider_failure_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    kanban_path = tmp_path / ".loopx" / "lark-kanban.json"
    save_lark_kanban_board_config(
        kanban_path,
        base_token="base_public_fixture",
        table_id="tbl_public_fixture",
    )
    _write_binding(binding_path, kanban_path)
    monkeypatch.setattr(
        "loopx.extensions.lark.goal_channel_runtime.sync_loopx_todos_to_lark_kanban",
        lambda *args, **kwargs: {
            "ok": False,
            "todo_count": 2,
            "successful_write_count": 1,
            "external_write_performed": True,
            "records": [
                {
                    "todo_id": "todo_public_one",
                    "record_id": "rec_public_one",
                },
                {
                    "todo_id": "todo_public_two",
                    "record_id": None,
                },
            ],
        },
    )

    payload = sync_lark_goal_channel(
        registry=_registry(tmp_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        runner=_fake_runner([]),
    )

    assert payload["ok"] is False
    assert payload["blocker"] == "provider_api_failed"
    assert payload["public_summary"] == (
        "the Goal Channel Kanban sync failed after partial provider writes"
    )
    assert payload["external_write_performed"] is True
    assert payload["readback_verified"] is False
    assert payload["details"]["successful_write_count"] == 1
    _assert_public_packet(payload)


def test_cli_deliver_operation_projects_typed_stage_blockers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI keeps the typed blocker, stage, and honest write state."""

    project = tmp_path / "typed-stage-project"
    source_registry_path = project / ".loopx" / "registry.json"
    source_registry_path.parent.mkdir(parents=True)
    source_registry = _registry(project)
    source_registry["goals"][0]["repo"] = str(project)
    source_registry_path.write_text(json.dumps(source_registry), encoding="utf-8")

    monkeypatch.setattr(
        goal_channel_cli,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"ok": True},
    )

    def deliver_raises(**kwargs: object) -> object:
        raise GoalChannelDeliveryStageError(
            "Goal Channel delivery send failed",
            blocker="provider_send_rejected",
            failure_stage="send_operation_card",
            external_write_performed=False,
        )

    monkeypatch.setattr(
        goal_channel_operation_cli,
        "deliver_goal_channel_operation_card",
        deliver_raises,
    )
    printed: dict[str, Any] = {}
    result = goal_channel_cli.handle_goal_channel_command(
        argparse.Namespace(
            command="goal-channel",
            goal_channel_command="deliver-operation",
            goal_id=GOAL_ID,
            proposal_id="proposal-typed-stage",
            binding_path=None,
            target_path=None,
            execute=True,
            subcommand_format="json",
            format=None,
        ),
        registry_path=source_registry_path,
        runtime_root_arg=None,
        print_payload=lambda payload, fmt, renderer: printed.update(payload),
        output_format=lambda args: "json",
    )

    assert result == 1
    assert printed["ok"] is False
    assert printed["blocker"] == "provider_send_rejected"
    assert printed["failure_stage"] == "send_operation_card"
    assert printed["external_write_performed"] is False
    assert "extension_activation" not in printed
    assert "provider rejected" not in json.dumps(printed)
    _assert_public_packet(printed)


@pytest.mark.parametrize(
    ("failure_stage", "blocker"),
    [
        # A send with no provider answer may already be live in the chat.
        ("send_operation_card", "delivery_outcome_unknown"),
        # The readback proved the write; only the local receipt failed.
        ("record_delivery_receipt", "delivery_receipt_write_failed"),
    ],
)
def test_cli_deliver_operation_treats_unknown_write_as_performed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    blocker: str,
) -> None:
    """An unknown provider outcome is never projected as a clean receipt."""

    project = tmp_path / "unknown-outcome-project"
    source_registry_path = project / ".loopx" / "registry.json"
    source_registry_path.parent.mkdir(parents=True)
    source_registry = _registry(project)
    source_registry["goals"][0]["repo"] = str(project)
    source_registry_path.write_text(json.dumps(source_registry), encoding="utf-8")

    monkeypatch.setattr(
        goal_channel_cli,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"ok": True},
    )

    def deliver_unknown(**kwargs: object) -> object:
        raise GoalChannelDeliveryStageError(
            "Goal Channel delivery outcome is unknown",
            blocker=blocker,
            failure_stage=failure_stage,
            external_write_performed=None,
        )

    monkeypatch.setattr(
        goal_channel_operation_cli,
        "deliver_goal_channel_operation_card",
        deliver_unknown,
    )
    printed: dict[str, Any] = {}
    result = goal_channel_cli.handle_goal_channel_command(
        argparse.Namespace(
            command="goal-channel",
            goal_channel_command="deliver-operation",
            goal_id=GOAL_ID,
            proposal_id="proposal-unknown-outcome",
            binding_path=None,
            target_path=None,
            execute=True,
            subcommand_format="json",
            format=None,
        ),
        registry_path=source_registry_path,
        runtime_root_arg=None,
        print_payload=lambda payload, fmt, renderer: printed.update(payload),
        output_format=lambda args: "json",
    )

    assert result == 1
    assert printed["ok"] is False
    assert printed["blocker"] == blocker
    assert printed["failure_stage"] == failure_stage
    assert printed["external_write_performed"] is True
    assert printed["details"]["external_write_outcome"] == "unknown"
    assert "extension_activation" not in printed
    _assert_public_packet(printed)


def test_gate_notice_delivers_request_body_instead_of_compact_label(tmp_path: Path) -> None:
    """The same long request survives preview, send, readback and duplicate retry."""
    binding_path = _gate_test_binding(tmp_path)
    body = "Review the public release candidate and its validation evidence. " * 6
    body += "The decision is whether to publish version 2.0 to the stable channel."
    quota = {
        "state": "operator_gate", "notify_user_on_gate": True,
        "interaction_contract": {"user_channel": {
            "action_required": True, "notify": "NOTIFY", "actions": ["[P0] Release review"],
        }},
        "user_todo_summary": {"gate_open_items": [{
            "todo_id": "todo_release_review", "task_class": "user_gate", "status": "open",
            "text": body, "note": "Only the stable-channel publication needs a decision.",
            "evidence": "https://example.org/release/2.0",
        }]},
    }
    notice = goal_channel_contracts._gate_notice_projection(
        goal_id=GOAL_ID, quota_packet=quota,
    )
    message = json.dumps(notice, ensure_ascii=False)
    assert body in message
    assert "todo_release_review" in message
    assert "Only the stable-channel publication needs a decision." in message
    assert "https://example.org/release/2.0" in message
    calls: list[list[str]] = []
    runner = _fake_runner(calls)
    first = _notify_test_gate(tmp_path=tmp_path, binding_path=binding_path, quota_packet=quota, runner=runner)
    again = _notify_test_gate(tmp_path=tmp_path, binding_path=binding_path, quota_packet=quota, runner=runner)
    assert first["status"] == "sent_verified"
    assert first["readback_verified"] is True
    assert again["status"] == "already_sent"
    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 1
    assert body in sends[0][sends[0].index("--text") + 1]


def test_gate_notice_redacts_and_bounds_additional_context() -> None:
    notice = goal_channel_contracts._gate_notice_projection(
        goal_id=GOAL_ID,
        quota_packet={"user_todo_summary": {"gate_open_items": [{
            "todo_id": "todo_release_review", "text": "Review " + "public facts " * 200,
            "note": "See /tmp/private-review.txt and api_key=synthetic_fixture_secret_123456789",
            "evidence": "https://example.org/release/2.0",
        }]}},
    )
    message = json.dumps(notice, ensure_ascii=False)
    assert "/tmp/private-review.txt" not in message
    assert "synthetic_fixture_secret_123456789" not in message
    assert len(message) < 2000
    assert notice["source"] == "unavailable"
    assert notice["incomplete"][0]["reason_code"] == "content_redacted"


def test_notify_cli_compact_quota_joins_same_read_complete_request(tmp_path: Path, monkeypatch) -> None:
    from copy import deepcopy
    from loopx.control_plane.todos.quota_summary import compact_quota_todo_summary_for_payload

    body = "Review public release evidence and independent checks. " * 7
    body += "Learning only. Expires at 2026-10-01T07:00:00Z; do not execute after expiry."
    canonical = {"todo_id": "todo_release_review", "role": "user", "task_class": "user_action",
                 "done": False, "status": "open", "updated_at": "2026-10-01T06:00:00Z",
                 "text": body, "note": "Version 2.0 release review; not execution authority.",
                 "evidence": "https://example.org/release/2.0"}
    status = {"attention_queue": {"items": [{"goal_id": GOAL_ID,
               "user_todos": {"items": [canonical]}}]}}
    before = deepcopy(status)
    reads = []
    monkeypatch.setattr(goal_channel_cli, "registry_project_root", lambda _: tmp_path)
    monkeypatch.setattr(goal_channel_cli, "collect_status", lambda **kwargs: reads.append(kwargs) or status)
    def quota_from_status(observed, **kwargs):
        assert observed is status
        return {"goal_id": GOAL_ID, "state": "operator_gate", "notify_user_on_gate": True,
                "interaction_contract": {"user_channel": {"action_required": True, "notify": "NOTIFY"}},
                "user_todo_summary": compact_quota_todo_summary_for_payload({"gate_open_items": [canonical]})}
    monkeypatch.setattr(goal_channel_cli, "build_quota_should_run", quota_from_status)
    quota = goal_channel_cli._quota_packet(registry_path=tmp_path / "registry.json", runtime_root_arg=None,
                                           goal_id=GOAL_ID, agent_id="fixture-agent")
    assert len(reads) == 1  # no second provider read or snapshot race
    short = quota["user_todo_summary"]["gate_open_items"][0]
    assert len(short["text"]) <= 180 and "note" not in short
    notice = goal_channel_contracts._gate_notice_projection(goal_id=GOAL_ID, quota_packet=quota)
    message = json.dumps(notice, ensure_ascii=False)
    assert body in message and canonical["note"] in message and canonical["evidence"] in message
    assert "Expires at 2026-10-01T07:00:00Z" in message
    calls = []
    binding_path = _gate_test_binding(tmp_path)
    result = _notify_test_gate(tmp_path=tmp_path, binding_path=binding_path, quota_packet=quota, runner=_fake_runner(calls))
    assert result["status"] == "sent_verified" and result["readback_verified"] is True
    sent = next(args for args in calls if "+messages-send" in args)
    expected = goal_channel_contracts._gate_notice_projection(
        goal_id=GOAL_ID,
        quota_packet=quota,
    )
    assert json.loads(sent[sent.index("--text") + 1])["decision_notice"] == expected
    assert status == before
    # A trailing content/evidence change beyond scheduler bounds is material.
    old_generation = goal_channel_contracts.quota_human_gate_state_generation(quota)
    canonical["note"] += " Additional public counterevidence."
    assert goal_channel_contracts.quota_human_gate_state_generation(quota) != old_generation


@pytest.mark.parametrize("mismatch", ["missing", "other_goal", "revision", "lifecycle", "overflow"])
def test_notify_complete_source_mismatch_or_overflow_is_not_decision_ready(mismatch: str) -> None:
    canonical = {"todo_id": "todo_release_review", "role": "user", "task_class": "user_action",
                 "done": False, "status": "open", "updated_at": "2026-10-01T06:00:00Z",
                 "text": "Review public release evidence. " * 10 + "Expiry: 2026-10-01T07:00:00Z."}
    selected = {**canonical, "text": canonical["text"][:177] + "..."}
    snapshot = {"goal_id": GOAL_ID, "items": [canonical]}
    if mismatch == "missing":
        snapshot["items"] = []
    if mismatch == "other_goal":
        snapshot["goal_id"] = "other-goal"
    if mismatch == "revision":
        canonical["updated_at"] = "2026-10-01T06:01:00Z"
    if mismatch == "lifecycle":
        canonical["status"] = "deferred"
    if mismatch == "overflow":
        canonical["note"] = "x" * 451
    notice = goal_channel_contracts._gate_notice_projection(
        goal_id=GOAL_ID,
        quota_packet={"user_todo_summary": {"gate_open_items": [selected]}, "request_snapshot": snapshot},
    )
    assert notice["items"] == []
    assert notice["incomplete"][0]["request_id"] == "todo_release_review"


def test_refresh_auto_notify_uses_same_complete_snapshot(tmp_path: Path, monkeypatch) -> None:
    from loopx.control_plane.todos.quota_summary import compact_quota_todo_summary_for_payload
    _, run = _refresh_gate_fixture(tmp_path, monkeypatch)
    body = "Review public release evidence. " * 12 + "Learning only; expires at 2026-10-01T07:00:00Z."
    todo = {"todo_id": "todo_release_review", "role": "user", "task_class": "user_action",
            "done": False, "status": "open", "updated_at": "2026-10-01T06:00:00Z", "text": body,
            "note": "Independent review, no execution authority."}
    status = {"attention_queue": {"items": [{"goal_id": GOAL_ID, "user_todos": {"items": [todo]}}]}}
    reads = []
    monkeypatch.setattr(goal_channel_lifecycle, "collect_status", lambda **kwargs: reads.append(kwargs) or status)
    monkeypatch.setattr(goal_channel_lifecycle, "build_quota_should_run", lambda *args, **kwargs: {
        "goal_id": GOAL_ID, "state": "operator_gate", "notify_user_on_gate": True,
        "user_todo_summary": compact_quota_todo_summary_for_payload({"gate_open_items": [todo]}),
    })
    calls = []
    result = run(_fake_runner(calls))
    assert result["status"] == "sent_verified" and result["readback_verified"] is True
    assert len(reads) == 1
    sent = next(args for args in calls if "+messages-send" in args)
    assert body in sent[sent.index("--text") + 1] and todo["note"] in sent[sent.index("--text") + 1]
