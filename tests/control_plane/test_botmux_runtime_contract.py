"""Modularization, Zero-Any, and compatibility facade contracts for botmux runtime."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Mapping
import io
import json
from http.client import HTTPMessage
from pathlib import Path
from typing import cast
from urllib.request import Request

import pytest

from loopx.cli_commands import goal_channel_runtime
from loopx.control_plane.goals import botmux_runtime
from loopx.control_plane.goals.botmux_transport import _decode_json_object

REPO_ROOT = Path(__file__).resolve().parents[2]
BOTMUX_MODULES = (
    "loopx/control_plane/goals/botmux_binding_store.py",
    "loopx/control_plane/goals/botmux_transport.py",
    "loopx/control_plane/goals/botmux_lifecycle.py",
    "loopx/control_plane/goals/botmux_trigger.py",
    "loopx/control_plane/goals/botmux_status.py",
    "loopx/control_plane/goals/botmux_runtime.py",
    "loopx/cli_commands/goal_channel_runtime.py",
)
MODULE_LINE_BUDGET = 750


def test_botmux_modules_stay_modular_and_zero_any() -> None:
    for rel_path in BOTMUX_MODULES:
        source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        lines = source.splitlines()
        assert len(lines) < MODULE_LINE_BUDGET, f"{rel_path} has {len(lines)} lines"
        assert ("type:" + " ignore") not in source, f"{rel_path} must not suppress mypy"
        tree = ast.parse(source, filename=rel_path)
        any_nodes = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        assert any_nodes == [], f"{rel_path} uses Any at lines {any_nodes}"


def test_botmux_runtime_facade_preserves_monkeypatch_seams_and_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    state_path = tmp_path / "STATE.md"
    state_path.write_text("# State\n", encoding="utf-8")
    registry = {
        "goals": [
            {
                "id": "goal-fixture",
                "repo": str(tmp_path),
                "state_file": str(state_path),
            }
        ]
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "fixture-token")

    requested_paths: list[str] = []

    def fake_request_json(**kwargs: object) -> dict[str, object]:
        path = str(kwargs["path"])
        requested_paths.append(path)
        if path == "/__health":
            return {"ok": True}
        if path == "/api/bots":
            return {
                "bots": [
                    {
                        "larkAppId": "bot-fixture",
                        "online": True,
                        "defaultWorkingDir": str(tmp_path.resolve()),
                    }
                ]
            }
        if path == "/api/groups":
            return {
                "chats": [
                    {
                        "chatId": "chat-fixture",
                        "memberBots": [
                            {"larkAppId": "bot-fixture", "inChat": True, "oncallChat": None}
                        ],
                    }
                ]
            }
        if path == "/api/trigger":
            return {"ok": True, "action": "dry_run"}
        raise AssertionError(f"unexpected path: {path}")

    fixed_iso = "2026-11-26T00:00:00+00:00"
    monkeypatch.setattr(botmux_runtime, "_request_json", fake_request_json)
    monkeypatch.setattr(botmux_runtime, "_now_iso", lambda: fixed_iso)

    result = botmux_runtime.setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-fixture",
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id="bot-fixture",
        chat_id="chat-fixture",
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
    )

    assert result["status"] == "configured"
    assert "/__health" in requested_paths
    persisted = botmux_runtime.botmux_binding_for_goal(
        botmux_runtime.read_botmux_runtime_binding(binding_path),
        "goal-fixture",
    )
    assert persisted is not None
    assert persisted["verified_at"] == fixed_iso
    for symbol in botmux_runtime.__all__:
        assert hasattr(botmux_runtime, symbol)


def test_botmux_transport_rejects_redirects_and_non_dict_json() -> None:
    handler = botmux_runtime._NoRedirectHandler()
    redirected = handler.redirect_request(
        Request("http://127.0.0.1:7891/__health"),
        io.BytesIO(b""),
        302,
        "Found",
        HTTPMessage(),
        "https://example.invalid/redirect",
    )
    assert redirected is None

    with pytest.raises(botmux_runtime.BotmuxTransportError, match="non-JSON"):
        _decode_json_object(b"not-json")
    with pytest.raises(botmux_runtime.BotmuxTransportError, match="invalid JSON"):
        _decode_json_object(b'["array"]')


def _setup_fixture_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], Path, Path, list[dict[str, object]]]:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "STATE.md"
    state_path.write_text("# State\n", encoding="utf-8")
    registry: dict[str, object] = {
        "goals": [
            {
                "id": "goal-fixture",
                "repo": str(tmp_path),
                "state_file": str(state_path),
            }
        ]
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    binding_path = tmp_path / ".loopx" / "goal-channel-runtime.json"
    monkeypatch.setenv("BOTMUX_TEST_TOKEN", "fixture-token")
    calls: list[dict[str, object]] = []

    def requester(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        path = str(kwargs["path"])
        if path == "/__health":
            return {"ok": True}
        if path == "/api/bots":
            return {
                "bots": [
                    {
                        "larkAppId": "bot-fixture",
                        "online": True,
                        "defaultWorkingDir": str(tmp_path.resolve()),
                    }
                ]
            }
        if path == "/api/groups":
            return {
                "chats": [
                    {
                        "chatId": "chat-fixture",
                        "memberBots": [
                            {
                                "larkAppId": "bot-fixture",
                                "inChat": True,
                                "oncallChat": None,
                            }
                        ],
                    }
                ]
            }
        if path == "/api/trigger":
            body = cast(Mapping[str, object], kwargs["payload"])
            options = cast(Mapping[str, object], body["options"])
            if options.get("dryRun") is True:
                return {"ok": True, "action": "dry_run"}
            return {
                "ok": True,
                "action": "queued",
                "triggerId": "trigger-fixture",
                "target": {"sessionId": "session-fixture"},
            }
        if "/trigger-result" in path:
            return {
                "ok": True,
                "state": "completed",
                "triggerId": "trigger-fixture",
                "output": {"content": "done"},
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(botmux_runtime, "_request_json", requester)
    setup_res = botmux_runtime.setup_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-fixture",
        binding_path=binding_path,
        endpoint="http://127.0.0.1:7891",
        bot_id="bot-fixture",
        chat_id="chat-fixture",
        token_env="BOTMUX_TEST_TOKEN",
        execute=True,
    )
    assert setup_res["status"] == "configured"
    calls.clear()
    return registry, registry_path, binding_path, calls


def test_botmux_binding_handles_missing_and_recoverable_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, registry_path, binding_path, calls = _setup_fixture_binding(
        tmp_path, monkeypatch
    )

    payload = botmux_runtime.read_botmux_runtime_binding(binding_path)
    bindings = cast(dict[str, object], payload["bindings"])
    binding = cast(dict[str, object], bindings["goal-fixture"])
    binding["receipts"] = None
    binding["session"] = None
    binding_path.write_text(json.dumps(payload), encoding="utf-8")

    queued = botmux_runtime.trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-fixture",
        binding_path=binding_path,
        turn_key="turn-1",
        execute=True,
    )
    assert queued["status"] == "queued"
    assert len(calls) == 1
    first_target = cast(
        Mapping[str, object], cast(Mapping[str, object], calls[0]["payload"])["target"]
    )
    assert first_target.get("chatId") == "chat-fixture"
    calls.clear()

    completed = botmux_runtime.status_botmux_runtime(
        goal_id="goal-fixture",
        binding_path=binding_path,
        execute=True,
    )
    assert completed["status"] == "completed"
    calls.clear()

    payload = botmux_runtime.read_botmux_runtime_binding(binding_path)
    bindings = cast(dict[str, object], payload["bindings"])
    binding = cast(dict[str, object], bindings["goal-fixture"])
    stored_receipts = cast(dict[str, object], binding["receipts"])
    stored_session = cast(dict[str, object], binding["session"])
    dispatch_key = str(stored_session["active_dispatch_key"])
    binding["receipts"] = [[dispatch_key, stored_receipts[dispatch_key]]]
    binding["session"] = [
        ["session_id", stored_session["session_id"]],
        ["active_dispatch_key", dispatch_key],
    ]
    binding_path.write_text(json.dumps(payload), encoding="utf-8")
    kv_bytes = binding_path.read_bytes()

    duplicate = botmux_runtime.trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-fixture",
        binding_path=binding_path,
        turn_key="turn-1",
        execute=True,
    )
    assert duplicate["status"] == "already_dispatched"
    assert calls == []
    assert binding_path.read_bytes() == kv_bytes

    status_kv = botmux_runtime.status_botmux_runtime(
        goal_id="goal-fixture",
        binding_path=binding_path,
        execute=False,
    )
    assert status_kv["status"] == "completed"
    calls.clear()

    followup = botmux_runtime.trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-fixture",
        binding_path=binding_path,
        turn_key="turn-2",
        execute=True,
    )
    assert followup["status"] == "queued"
    assert len(calls) == 1
    reused_payload = cast(Mapping[str, object], calls[0]["payload"])
    assert cast(Mapping[str, object], reused_payload["target"]).get("sessionId") == (
        "session-fixture"
    )
    assert "turnIdempotencyKey" in cast(Mapping[str, object], reused_payload["options"])


@pytest.mark.parametrize("field", ["receipts", "session"])
@pytest.mark.parametrize(
    "corrupt_value",
    ["corrupted-section", ["bad-item"], ["ab"], 123, True, [[123, {}]]],
)
def test_botmux_rejects_corrupt_sections_before_provider_post_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    corrupt_value: object,
) -> None:
    registry, registry_path, binding_path, calls = _setup_fixture_binding(
        tmp_path, monkeypatch
    )
    first = botmux_runtime.trigger_botmux_runtime(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-fixture",
        binding_path=binding_path,
        turn_key="turn-1",
        execute=True,
    )
    assert first["status"] == "queued"
    calls.clear()

    payload = botmux_runtime.read_botmux_runtime_binding(binding_path)
    bindings = cast(dict[str, object], payload["bindings"])
    binding = cast(dict[str, object], bindings["goal-fixture"])
    binding[field] = corrupt_value
    binding_path.write_text(json.dumps(payload), encoding="utf-8")
    preserved = binding_path.read_bytes()

    with pytest.raises(ValueError, match=field):
        botmux_runtime.trigger_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id="goal-fixture",
            binding_path=binding_path,
            turn_key="turn-2",
            execute=True,
        )
    assert calls == []
    assert binding_path.read_bytes() == preserved

    with pytest.raises(ValueError, match=field):
        botmux_runtime.status_botmux_runtime(
            goal_id="goal-fixture",
            binding_path=binding_path,
            execute=True,
        )
    assert calls == []
    assert binding_path.read_bytes() == preserved

    with pytest.raises(ValueError, match=field):
        botmux_runtime.setup_botmux_runtime(
            registry=registry,
            registry_path=registry_path,
            goal_id="goal-fixture",
            binding_path=binding_path,
            endpoint="http://127.0.0.1:7891",
            bot_id="bot-fixture",
            chat_id="chat-fixture",
            token_env="BOTMUX_TEST_TOKEN",
            execute=True,
        )
    assert calls == []
    assert binding_path.read_bytes() == preserved

    cli_trigger = goal_channel_runtime.run_goal_channel_runtime(
        argparse.Namespace(
            goal_id="goal-fixture",
            runtime_binding_path=str(binding_path),
            goal_channel_runtime_command="trigger",
            instruction=None,
            turn_key="turn-2",
            execute=True,
        ),
        registry=registry,
        registry_path=registry_path,
    )
    assert cli_trigger["ok"] is False
    assert cli_trigger["status"] == "blocked"
    assert cli_trigger["blocker"] == "invalid_configuration"
    assert cli_trigger["external_write_performed"] is False
    assert calls == []
    assert binding_path.read_bytes() == preserved
