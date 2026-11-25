"""Modularization, Zero-Any, and compatibility facade contracts for botmux runtime."""

from __future__ import annotations

import ast
import json
from http.client import HTTPMessage
import io
from pathlib import Path
from urllib.request import Request

import pytest

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
