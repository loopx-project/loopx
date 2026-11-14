from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from loopx.cli import build_parser
from loopx.control_plane.goals.botmux_runtime import (
    BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION,
    BotmuxApiError,
    BotmuxTransportError,
    botmux_binding_for_goal,
    disable_botmux_runtime,
    doctor_botmux_runtime,
    read_botmux_runtime_binding,
    setup_botmux_runtime,
    status_botmux_runtime,
    trigger_botmux_runtime,
)
from loopx.file_lock import LOCK_POLICIES, LockAcquisitionPolicy, LockPolicy


GOAL_ID = "goal-public-fixture"
BOT_ID = "cli_public_fixture"
CHAT_ID = "oc_public_fixture"
SESSION_ID = "session-public-fixture"


def _registry(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text("# Active Goal\n\n- next: validate botmux\n", encoding="utf-8")
    registry = {
        "goals": [
            {
                "id": GOAL_ID,
                "objective": "Validate a public-safe botmux runtime binding.",
                "repo": str(tmp_path),
                "state_file": str(state_path),
            }
        ]
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return registry, registry_path


def _relative_registry(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    state_path = tmp_path / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("# Active Goal\n\n- next: relative state\n", encoding="utf-8")
    registry = {
        "goals": [
            {
                "id": GOAL_ID,
                "objective": "Validate a relative project state path.",
                "repo": str(tmp_path),
                "state_file": str(state_path.relative_to(tmp_path)),
            }
        ]
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return registry, registry_path


def _requester(
    calls: list[dict[str, Any]],
    working_dir: Path,
):
    def request(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        path = str(kwargs["path"])
        if path == "/__health":
            return {"ok": True}
        if path == "/api/bots":
            return {
                "bots": [
                    {
                        "larkAppId": BOT_ID,
                        "online": True,
                        "defaultWorkingDir": str(working_dir),
                    }
                ]
            }
        if path == "/api/groups":
            return {
                "chats": [
                    {
                        "chatId": chat_id,
                        "memberBots": [
                            {
                                "larkAppId": BOT_ID,
                                "inChat": True,
                                "oncallChat": None,
                            }
                        ],
                    }
                    for chat_id in (CHAT_ID, "oc_second_public_fixture")
                ]
            }
        if path == "/api/trigger":
            body = kwargs["payload"]
            if body["options"].get("dryRun") is True:
                return {"ok": True, "action": "dry_run"}
            return {
                "ok": True,
                "action": "queued",
                "triggerId": "trigger-public-fixture",
                "target": {"sessionId": SESSION_ID},
            }
        if "/trigger-result" in path:
            # Like the provider: a pinned trigger id selects that turn and is
            # echoed back; without one the answer is for the session's latest.
            pinned = parse_qs(urlsplit(path).query).get("triggerId", [None])[0]
            return {
                "ok": True,
                "state": "completed",
                "triggerId": pinned or "trigger-public-fixture",
                "output": {"content": "validated"},
            }
        raise AssertionError(f"unexpected botmux request: {kwargs}")

    return request


def _assert_public_packet(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    assert BOT_ID not in serialized
    assert CHAT_ID not in serialized
    assert SESSION_ID not in serialized
    assert "BOTMUX_TEST_TOKEN" not in serialized
    assert str(Path.home()) not in serialized


def test_runtime_cli_parses_setup_doctor_trigger_and_status() -> None:
    parser = build_parser()

    setup = parser.parse_args(
        [
            "goal-channel",
            "runtime",
            "setup",
            "--goal-id",
            GOAL_ID,
            "--bot-id",
            BOT_ID,
            "--chat-id",
            CHAT_ID,
        ]
    )
    doctor = parser.parse_args(
        ["goal-channel", "runtime", "doctor", "--goal-id", GOAL_ID]
    )
    trigger = parser.parse_args(
        ["goal-channel", "runtime", "trigger", "--goal-id", GOAL_ID]
    )
    status = parser.parse_args(
        ["goal-channel", "runtime", "status", "--goal-id", GOAL_ID]
    )
    disable = parser.parse_args(
        ["goal-channel", "runtime", "disable", "--goal-id", GOAL_ID]
    )

    assert setup.goal_channel_runtime_command == "setup"
    assert setup.endpoint == "http://127.0.0.1:7891"
    assert doctor.goal_channel_runtime_command == "doctor"
    assert trigger.goal_channel_runtime_command == "trigger"
    assert status.goal_channel_runtime_command == "status"
    assert disable.goal_channel_runtime_command == "disable"


def test_setup_is_dry_run_then_writes_owner_private_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")

    preview = setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        requester=_requester(calls, tmp_path),
    )

    assert preview["ok"] is True
    assert preview["status"] == "preview_ready"
    assert not binding_path.exists()
    assert calls[-1]["payload"]["options"] == {"dryRun": True}

    configured = setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester(calls, tmp_path),
    )

    assert configured["ok"] is True
    assert configured["status"] == "configured"
    assert configured["readback_verified"] is True
    assert binding_path.stat().st_mode & 0o777 == 0o600
    stored = read_botmux_runtime_binding(binding_path)
    assert stored["schema_version"] == BOTMUX_RUNTIME_BINDING_SCHEMA_VERSION
    binding = botmux_binding_for_goal(stored, GOAL_ID)
    assert binding is not None
    assert binding["bot_id"] == BOT_ID
    assert binding["chat_id"] == CHAT_ID
    assert binding["token_env"] == "BOTMUX_TEST_TOKEN"
    _assert_public_packet(configured)


def test_doctor_accepts_runtime_agnostic_bot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )

    def another_runtime(**kwargs: Any) -> dict[str, Any]:
        if kwargs["path"] == "/__health":
            return {"ok": True}
        if kwargs["path"] == "/api/bots":
            return {
                "bots": [
                    {
                        "larkAppId": BOT_ID,
                        "cliId": "codex",
                        "online": True,
                        "defaultWorkingDir": str(tmp_path),
                    }
                ]
            }
        if kwargs["path"] == "/api/groups":
            return {
                "chats": [
                    {
                        "chatId": CHAT_ID,
                        "memberBots": [
                            {"larkAppId": BOT_ID, "inChat": True, "oncallChat": None}
                        ],
                    }
                ]
            }
        if kwargs["path"] == "/api/trigger":
            return {"ok": True, "action": "dry_run"}
        raise AssertionError(f"unexpected request: {kwargs}")

    result = doctor_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        requester=another_runtime,
    )

    assert result["ok"] is True
    assert result["status"] == "ready"
    _assert_public_packet(result)


def test_setup_rejects_explicitly_offline_bot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")

    def offline(**kwargs: Any) -> dict[str, Any]:
        if kwargs["path"] == "/__health":
            return {"ok": True}
        if kwargs["path"] == "/api/bots":
            return {
                "bots": [
                    {
                        "larkAppId": BOT_ID,
                        "online": False,
                        "defaultWorkingDir": str(tmp_path),
                    }
                ]
            }
        raise AssertionError("offline bot must stop readiness before chat probing")

    result = setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=offline,
    )

    assert result["ok"] is False
    assert result["blocker"] == "botmux_configuration_invalid"
    assert not binding_path.exists()


def test_disable_is_goal_scoped_and_previewable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )

    preview = disable_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
    )
    before = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )
    disabled = disable_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
    )
    after = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )

    assert preview["status"] == "preview_ready"
    assert before is not None and before["enabled"] is True
    assert disabled["status"] == "disabled"
    assert after is not None
    assert after["enabled"] is False
    assert after["session"] == {}
    _assert_public_packet(disabled)


def test_doctor_rejects_failed_health_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )

    result = doctor_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        requester=lambda **kwargs: {"ok": False},
    )

    assert result["ok"] is False
    assert result["blocker"] == "botmux_configuration_invalid"


def test_setup_rejects_botmux_route_for_another_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")

    result = setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path / "another-project"),
    )

    assert result["ok"] is False
    assert result["blocker"] == "botmux_configuration_invalid"
    assert not binding_path.exists()


def test_trigger_is_previewable_idempotent_and_reuses_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    calls: list[dict[str, Any]] = []

    preview = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        requester=_requester(calls, tmp_path),
    )
    queued = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester(calls, tmp_path),
    )
    duplicate = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester(calls, tmp_path),
    )

    assert preview["status"] == "preview_ready"
    assert preview["external_write_performed"] is False
    assert queued["status"] == "queued"
    assert queued["external_write_performed"] is True
    assert duplicate["status"] == "already_dispatched"
    trigger_calls = [call for call in calls if call["path"] == "/api/trigger"]
    assert len(trigger_calls) == 1
    first_target = trigger_calls[0]["payload"]["target"]
    assert first_target == {
        "kind": "turn",
        "botId": BOT_ID,
        "chatId": CHAT_ID,
    }
    _assert_public_packet(queued)
    _assert_public_packet(duplicate)

    followup_calls: list[dict[str, Any]] = []
    followup = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        turn_key="explicit-next-segment",
        execute=True,
        requester=_requester(followup_calls, tmp_path),
    )
    followup_body = followup_calls[0]["payload"]
    assert followup["status"] == "queued"
    assert followup_body["target"] == {
        "kind": "turn",
        "botId": BOT_ID,
        "sessionId": SESSION_ID,
    }
    assert "turnIdempotencyKey" in followup_body["options"]


def test_relative_state_file_changes_default_dispatch_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _relative_registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )

    first = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        requester=_requester([], tmp_path),
    )
    state_path = tmp_path / registry["goals"][0]["state_file"]
    state_path.write_text("# Active Goal\n\n- next: changed state\n", encoding="utf-8")
    second = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        requester=_requester([], tmp_path),
    )

    assert first["idempotency_key"] != second["idempotency_key"]


def test_rebinding_to_another_chat_drops_prior_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    new_chat = "oc_second_public_fixture"
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=new_chat,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )

    binding = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )
    assert binding is not None
    assert binding["chat_id"] == new_chat
    assert binding["session"] == {}
    assert binding["receipts"] == {}


def test_status_reads_typed_lifecycle_and_updates_local_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    result = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["readback_verified"] is True
    assert result["details"] == {
        "dispatch_state": "completed",
        "output_available": True,
        "local_receipt_updated": True,
    }
    _assert_public_packet(result)


def test_status_transport_failure_does_not_regress_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )
    status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    def unavailable(**kwargs: Any) -> dict[str, Any]:
        raise BotmuxTransportError("fixture status failure")

    observed = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=unavailable,
    )
    binding = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )
    assert binding is not None
    dispatch_key = binding["session"]["active_dispatch_key"]

    assert observed["status"] == "unknown"
    assert binding["receipts"][dispatch_key]["state"] == "completed"


@pytest.mark.parametrize(
    ("local_state", "provider_state"),
    [
        ("completed", "running"),
        ("failed", "unknown"),
        ("not_found", None),
    ],
)
def test_status_ignores_provider_regression_after_local_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_state: str,
    provider_state: str | None,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    def terminal(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "state": local_state, "triggerId": "trigger-public-fixture"}

    status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=terminal,
    )

    def stale(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "state": provider_state, "triggerId": "trigger-public-fixture"}

    observed = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=stale,
    )
    binding = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )
    assert binding is not None
    dispatch_key = binding["session"]["active_dispatch_key"]

    assert observed["ok"] is True
    assert observed["status"] == local_state
    assert "blocker" not in observed
    assert observed["details"] == {
        "dispatch_state": local_state,
        "output_available": False,
        "local_receipt_updated": True,
        "provider_observation": provider_state or "unknown",
        "provider_observation_ignored": True,
    }
    assert binding["receipts"][dispatch_key]["state"] == local_state
    assert (
        binding["receipts"][dispatch_key]["provider_observation"]
        == (provider_state or "unknown")
    )
    assert (
        binding["receipts"][dispatch_key]["provider_observation_ignored"]
        is True
    )
    _assert_public_packet(observed)


def test_status_maps_confirmed_unknown_session_to_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    def missing(**kwargs: Any) -> dict[str, Any]:
        raise BotmuxApiError(404, {"error": "unknown_session"})

    result = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        requester=missing,
    )

    assert result["ok"] is True
    assert result["status"] == "not_found"


def test_terminal_failure_requires_new_turn_key_before_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    def failed(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "state": "failed"}

    status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=failed,
    )
    calls: list[dict[str, Any]] = []
    duplicate = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester(calls, tmp_path),
    )

    assert duplicate["status"] == "already_dispatched"
    assert calls == []


@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        ("transport", "transport_failure"),
        ("invalid_receipt", "invalid_provider_receipt"),
    ],
)
def test_unknown_dispatch_is_persisted_and_status_never_reports_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_reason: str,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    provider_calls = 0

    def uncertain_provider(**kwargs: Any) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        if failure_kind == "transport":
            raise BotmuxTransportError("fixture transport failure")
        return {"ok": True, "action": "queued"}

    dispatched = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=uncertain_provider,
    )
    binding = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )
    assert binding is not None
    active_key = str(binding["session"]["active_dispatch_key"])
    receipt = binding["receipts"][active_key]

    assert dispatched["status"] == "dispatch_unknown"
    assert receipt["state"] == "unknown"
    assert receipt["unknown_reason"] == expected_reason
    assert "attempted_at" in receipt

    status = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
    )
    assert status["ok"] is False
    assert status["status"] == "unknown"
    assert status["blocker"] == "botmux_dispatch_outcome_unknown"
    assert status["details"]["dispatch_state"] == "unknown"

    duplicate = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=uncertain_provider,
    )
    assert duplicate["status"] == "already_dispatched"
    assert duplicate["details"]["dispatch_state"] == "unknown"
    assert provider_calls == 1


def test_status_promotes_unresolved_attempt_to_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, GOAL_ID)
    assert binding is not None
    dispatch_key = "sha256:" + ("a" * 64)
    binding["receipts"] = {
        dispatch_key: {
            "state": "attempting",
            "attempted_at": "2026-01-01T00:00:00+00:00",
            "session_reused": False,
        }
    }
    binding["session"] = {"active_dispatch_key": dispatch_key}
    payload["bindings"][GOAL_ID] = binding
    binding_path.write_text(json.dumps(payload), encoding="utf-8")

    status = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
    )
    persisted = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path),
        GOAL_ID,
    )

    assert status["status"] == "unknown"
    assert status["details"]["local_receipt_updated"] is True
    assert persisted is not None
    assert persisted["receipts"][dispatch_key]["state"] == "unknown"
    assert (
        persisted["receipts"][dispatch_key]["unknown_reason"]
        == "unresolved_attempt"
    )


def test_concurrent_trigger_dispatches_provider_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    provider_call_count = 0
    provider_call_lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def delayed_provider(**kwargs: Any) -> dict[str, Any]:
        nonlocal provider_call_count
        with provider_call_lock:
            provider_call_count += 1
        time.sleep(0.05)
        return {
            "ok": True,
            "action": "queued",
            "triggerId": "trigger-public-fixture",
            "target": {"sessionId": SESSION_ID},
        }

    def run_trigger() -> None:
        results.append(
            trigger_botmux_runtime(
                registry=registry,
                registry_path=registry_path,
                goal_id=GOAL_ID,
                binding_path=binding_path,
                execute=True,
                requester=delayed_provider,
            )
        )

    threads = [threading.Thread(target=run_trigger) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert provider_call_count == 1
    assert sorted(result["status"] for result in results) == [
        "already_dispatched",
        "queued",
    ]


def test_doctor_waits_for_concurrent_setup_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    ready_to_probe = threading.Event()
    allow_setup_to_finish = threading.Event()
    doctor_result: dict[str, Any] = {}

    def delayed_requester(**kwargs: Any) -> dict[str, Any]:
        if kwargs["path"] == "/api/trigger" and kwargs["payload"]["options"].get("dryRun") is True:
            ready_to_probe.set()
            allow_setup_to_finish.wait(timeout=2.0)
        return _requester([], tmp_path)(**kwargs)

    def run_setup() -> None:
        setup_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id=GOAL_ID,
            binding_path=binding_path,
            endpoint="http://127.0.0.1:7891",
            bot_id=BOT_ID,
            chat_id=CHAT_ID,
            token_env="BOTMUX_TEST_TOKEN",
            execute=True,
            requester=delayed_requester,
        )

    def run_doctor() -> None:
        ready_to_probe.wait(timeout=2.0)
        doctor_result.update(
            doctor_botmux_runtime(
                goal_id=GOAL_ID,
                binding_path=binding_path,
                requester=_requester([], tmp_path),
            )
        )

    setup_thread = threading.Thread(target=run_setup)
    doctor_thread = threading.Thread(target=run_doctor)
    setup_thread.start()
    doctor_thread.start()
    ready_to_probe.wait(timeout=2.0)
    time.sleep(0.05)
    allow_setup_to_finish.set()
    setup_thread.join()
    doctor_thread.join()

    assert doctor_result["ok"] is True
    assert doctor_result["status"] == "ready"


@pytest.mark.parametrize(
    ("runtime_operation", "waiter_operation"),
    [
        ("runtime_doctor", "botmux_runtime_doctor"),
        ("runtime_status", "botmux_runtime_status"),
    ],
)
def test_runtime_reads_return_typed_packet_when_setup_lock_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_operation: str,
    waiter_operation: str,
) -> None:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    monkeypatch.setitem(
        LOCK_POLICIES,
        LockAcquisitionPolicy.MUTATION,
        LockPolicy(
            timeout_seconds=0.05,
            poll_interval_seconds=0.005,
            retry_mode="manual_after_holder_inspection",
        ),
    )
    setup_holds_lock = threading.Event()
    allow_setup_to_finish = threading.Event()
    setup_result: dict[str, Any] = {}

    def delayed_requester(**kwargs: Any) -> dict[str, Any]:
        if (
            kwargs["path"] == "/api/trigger"
            and kwargs["payload"]["options"].get("dryRun") is True
        ):
            setup_holds_lock.set()
            allow_setup_to_finish.wait(timeout=2.0)
        return _requester([], tmp_path)(**kwargs)

    def run_setup() -> None:
        setup_result.update(
            setup_botmux_runtime(
                registry=registry,
                registry_path=registry_path,
                goal_id=GOAL_ID,
                binding_path=binding_path,
                endpoint="http://127.0.0.1:7891",
                bot_id=BOT_ID,
                chat_id=CHAT_ID,
                token_env="BOTMUX_TEST_TOKEN",
                execute=True,
                requester=delayed_requester,
            )
        )

    setup_thread = threading.Thread(target=run_setup)
    setup_thread.start()
    assert setup_holds_lock.wait(timeout=2.0)

    try:
        if runtime_operation == "runtime_doctor":
            result = doctor_botmux_runtime(
                goal_id=GOAL_ID,
                binding_path=binding_path,
                requester=_requester([], tmp_path),
            )
        else:
            result = status_botmux_runtime(
                goal_id=GOAL_ID,
                binding_path=binding_path,
                requester=_requester([], tmp_path),
            )
    finally:
        allow_setup_to_finish.set()
        setup_thread.join(timeout=2.0)

    assert not setup_thread.is_alive()
    assert setup_result["status"] == "configured"
    assert result["ok"] is False
    assert result["operation"] == runtime_operation
    assert result["status"] == "blocked"
    assert result["blocker"] == "botmux_runtime_lock_timeout"
    assert result["error_code"] == "lock_acquire_timeout"
    assert result["lock_timeout"]["holder"]["operation"] == "botmux_runtime_setup"
    assert result["lock_timeout"]["waiter"]["operation"] == waiter_operation
    assert result["operator_action"]["action"] == "inspect_lock_holder"
    assert json.loads(json.dumps(result)) == result
    _assert_public_packet(result)


def _setup_reused_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Path, Path]:
    registry, registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "secret-public-fixture")
    common = {
        "registry": registry,
        "registry_path": registry_path,
        "goal_id": GOAL_ID,
        "binding_path": binding_path,
    }
    setup_botmux_runtime(
        **common,
        endpoint="http://127.0.0.1:7891",
        bot_id=BOT_ID,
        chat_id=CHAT_ID,
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
        requester=_requester([], tmp_path),
    )
    first = trigger_botmux_runtime(
        **common, execute=True, requester=_requester([], tmp_path)
    )
    assert first["status"] == "queued"
    return registry, registry_path, binding_path


def test_status_never_vouches_for_rejected_dispatch_on_reused_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path, binding_path = _setup_reused_session(tmp_path, monkeypatch)
    ok = _requester([], tmp_path)

    def reject_trigger(**kwargs: Any) -> dict[str, Any]:
        if kwargs["path"] == "/api/trigger":
            raise BotmuxApiError(409, {"error": "busy"})
        return ok(**kwargs)

    rejected = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        turn_key="second-segment",
        execute=True,
        requester=reject_trigger,
    )
    assert rejected["status"] == "rejected"

    # The session's trigger-result still describes the first, completed turn.
    for execute in (False, True):
        status = status_botmux_runtime(
            goal_id=GOAL_ID,
            binding_path=binding_path,
            execute=execute,
            requester=ok,
        )
        assert status["ok"] is False
        assert status["status"] == "rejected"
        assert status["readback_verified"] is False
        assert status["blocker"] == "botmux_api_rejected"


def test_status_promotes_unresolved_attempt_on_reused_session_without_provider_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registry_value, _registry_path, binding_path = _setup_reused_session(
        tmp_path, monkeypatch
    )
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, GOAL_ID)
    assert binding is not None
    dispatch_key = "sha256:" + ("b" * 64)
    binding["receipts"][dispatch_key] = {
        "state": "attempting",
        "attempted_at": "2026-01-01T00:00:00+00:00",
        "session_reused": True,
    }
    binding["session"] = {
        "session_id": SESSION_ID,
        "active_dispatch_key": dispatch_key,
    }
    payload["bindings"][GOAL_ID] = binding
    binding_path.write_text(json.dumps(payload), encoding="utf-8")

    status = status_botmux_runtime(
        goal_id=GOAL_ID,
        binding_path=binding_path,
        execute=True,
        requester=_requester([], tmp_path),
    )

    assert status["ok"] is False
    assert status["status"] == "unknown"
    assert status["readback_verified"] is False
    persisted = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path), GOAL_ID
    )
    assert persisted is not None
    assert persisted["receipts"][dispatch_key]["state"] == "unknown"


def test_repeated_status_never_adopts_previous_result_for_interrupted_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path, binding_path = _setup_reused_session(tmp_path, monkeypatch)
    ok = _requester([], tmp_path)

    def interrupt_trigger(**kwargs: Any) -> dict[str, Any]:
        if kwargs["path"] == "/api/trigger":
            raise BotmuxTransportError("connection reset before acceptance")
        return ok(**kwargs)

    interrupted = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        turn_key="second-segment",
        execute=True,
        requester=interrupt_trigger,
    )
    assert interrupted["status"] == "dispatch_unknown"
    provider_reads: list[dict[str, Any]] = []
    status_requester = _requester(provider_reads, tmp_path)

    # The session's trigger-result still describes the first, completed turn.
    for _ in range(3):
        status = status_botmux_runtime(
            goal_id=GOAL_ID,
            binding_path=binding_path,
            execute=True,
            requester=status_requester,
        )
        assert status["ok"] is False
        assert status["status"] == "unknown"
        assert status["readback_verified"] is False

    persisted = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path), GOAL_ID
    )
    assert persisted is not None
    active_key = persisted["session"]["active_dispatch_key"]
    assert persisted["receipts"][active_key]["state"] == "unknown"
    assert provider_reads == []


def test_status_recovers_accepted_dispatch_after_a_status_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registry_value, _registry_path, binding_path = _setup_reused_session(
        tmp_path, monkeypatch
    )
    ok = _requester([], tmp_path)

    def unreachable(**kwargs: Any) -> dict[str, Any]:
        if "/trigger-result" in str(kwargs["path"]):
            raise BotmuxTransportError("status read failed after acceptance")
        return ok(**kwargs)

    lost = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True, requester=unreachable
    )
    assert lost["status"] == "unknown"
    persisted = botmux_binding_for_goal(
        read_botmux_runtime_binding(binding_path), GOAL_ID
    )
    assert persisted is not None
    active_key = persisted["session"]["active_dispatch_key"]
    assert persisted["receipts"][active_key]["state"] == "unknown"

    recovered = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True, requester=ok
    )
    assert recovered["ok"] is True
    assert recovered["status"] == "completed"


def _second_accepted_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trigger_id: str
) -> tuple[Path, Any]:
    """A second turn accepted on the reused session under its own trigger id."""
    registry, registry_path, binding_path = _setup_reused_session(tmp_path, monkeypatch)
    base = _requester([], tmp_path)

    def accept_second(**kwargs: Any) -> dict[str, Any]:
        if kwargs["path"] == "/api/trigger":
            return {
                "ok": True,
                "action": "queued",
                "triggerId": trigger_id,
                "target": {"sessionId": SESSION_ID},
            }
        return base(**kwargs)

    second = trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        binding_path=binding_path,
        turn_key="second-segment",
        execute=True,
        requester=accept_second,
    )
    assert second["status"] == "queued"
    return binding_path, base


def _persisted_active_receipt(binding_path: Path) -> dict[str, Any]:
    persisted = botmux_binding_for_goal(read_botmux_runtime_binding(binding_path), GOAL_ID)
    assert persisted is not None
    return persisted["receipts"][persisted["session"]["active_dispatch_key"]]


def test_status_reads_the_turn_this_receipt_was_queued_under(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, base = _second_accepted_trigger(tmp_path, monkeypatch, "trigger-two")
    calls: list[dict[str, Any]] = []

    def provider(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        pinned = parse_qs(urlsplit(str(kwargs["path"])).query).get("triggerId", [None])[0]
        # Turn two failed; another client has since completed a third turn.
        if pinned == "trigger-two":
            return {"ok": True, "state": "failed", "triggerId": "trigger-two"}
        return {
            "ok": True, "state": "completed", "triggerId": "trigger-three",
            "output": {"content": "someone else"},
        }

    for execute in (False, True):
        status = status_botmux_runtime(
            goal_id=GOAL_ID, binding_path=binding_path, execute=execute, requester=provider
        )
        assert status["status"] == "failed"
        assert status["readback_verified"] is False
    assert all("triggerId=trigger-two" in str(call["path"]) for call in calls)
    assert _persisted_active_receipt(binding_path)["state"] == "failed"


def test_status_does_not_promote_a_result_attributed_to_another_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, _base = _second_accepted_trigger(tmp_path, monkeypatch, "trigger-two")

    def ignores_the_pin(**kwargs: Any) -> dict[str, Any]:
        # A provider that answers with the session's latest turn whatever is asked.
        return {
            "ok": True, "state": "completed", "triggerId": "trigger-three",
            "output": {"content": "someone else"},
        }

    for execute in (False, True):
        status = status_botmux_runtime(
            goal_id=GOAL_ID, binding_path=binding_path, execute=execute, requester=ignores_the_pin
        )
        assert status["ok"] is False and status["status"] == "unknown"
        assert status["readback_verified"] is False
        assert status["details"]["output_available"] is False
    receipt = _persisted_active_receipt(binding_path)
    assert receipt["state"] == "unknown"
    assert receipt["unknown_reason"] == "trigger_identity_unverified"


def test_status_without_a_stored_trigger_id_asks_the_provider_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry_value, _registry_path, binding_path = _setup_reused_session(tmp_path, monkeypatch)
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, GOAL_ID)
    assert binding is not None
    receipt = binding["receipts"][binding["session"]["active_dispatch_key"]]
    receipt.pop("trigger_id")
    payload["bindings"][GOAL_ID] = binding
    binding_path.write_text(json.dumps(payload), encoding="utf-8")
    calls: list[dict[str, Any]] = []

    status = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True,
        requester=_requester(calls, tmp_path),
    )

    assert status["status"] == "unknown" and status["readback_verified"] is False
    assert calls == []


def test_a_not_found_naming_another_turn_cannot_seal_this_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, _base = _second_accepted_trigger(tmp_path, monkeypatch, "trigger-two")

    def wrong_turn_missing(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True, "state": "not_found", "triggerId": "trigger-three",
            "output": {"content": "someone else"},
        }

    for execute in (False, True):
        status = status_botmux_runtime(
            goal_id=GOAL_ID, binding_path=binding_path, execute=execute,
            requester=wrong_turn_missing,
        )
        assert status["status"] == "unknown" and status["ok"] is False
        assert status["details"]["output_available"] is False
    assert _persisted_active_receipt(binding_path)["state"] == "unknown"

    def own_completion(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True, "state": "completed", "triggerId": "trigger-two",
            "output": {"content": "mine"},
        }

    # The receipt is not sealed: this request's real completion still lands.
    done = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True, requester=own_completion
    )
    assert done["status"] == "completed" and done["readback_verified"] is True
    assert _persisted_active_receipt(binding_path)["state"] == "completed"


def test_a_not_found_for_this_turn_is_accepted_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, _base = _second_accepted_trigger(tmp_path, monkeypatch, "trigger-two")

    def own_missing(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "state": "not_found", "triggerId": "trigger-two"}

    status = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True, requester=own_missing
    )

    assert status["status"] == "not_found"
    assert status["details"]["output_available"] is False
    assert _persisted_active_receipt(binding_path)["state"] == "not_found"


def test_a_generic_404_not_found_cannot_seal_an_accepted_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, _base = _second_accepted_trigger(tmp_path, monkeypatch, "trigger-two")

    def generic_404(**kwargs: Any) -> dict[str, Any]:
        # Not-found state, but neither a named turn nor an explicit unknown_session.
        raise BotmuxApiError(404, {"state": "not_found"})

    for execute in (False, True):
        status = status_botmux_runtime(
            goal_id=GOAL_ID, binding_path=binding_path, execute=execute, requester=generic_404
        )
        assert status["status"] == "unknown" and status["ok"] is False
    assert _persisted_active_receipt(binding_path)["state"] == "unknown"

    def own_completion(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "state": "completed", "triggerId": "trigger-two",
                "output": {"content": "mine"}}

    done = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True, requester=own_completion
    )
    assert done["status"] == "completed" and done["readback_verified"] is True


def test_a_404_naming_another_turn_is_not_a_session_level_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, _base = _second_accepted_trigger(tmp_path, monkeypatch, "trigger-two")

    def other_turn_404(**kwargs: Any) -> dict[str, Any]:
        raise BotmuxApiError(404, {"error": "unknown_session", "triggerId": "trigger-three"})

    status = status_botmux_runtime(
        goal_id=GOAL_ID, binding_path=binding_path, execute=True, requester=other_turn_404
    )
    assert status["status"] == "unknown"
    assert _persisted_active_receipt(binding_path)["state"] == "unknown"
