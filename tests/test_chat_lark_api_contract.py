from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.request import urlopen
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import loopx.chat_server as chat_server
from loopx.chat_server import (
    CHAT_GOAL_CONTEXTS_PATH,
    CHAT_LARK_APP_SETUPS_PATH,
    CHAT_LARK_APPS_PATH,
    CHAT_LARK_CHATS_PATH,
    CHAT_LARK_CONNECTIONS_PATH,
    ChatHTTPServer,
    build_goal_repository_contexts,
    configured_lark_cli_bin,
    resolve_lark_cli_for_runtime,
)
from loopx.chat_lark_api import LarkChatRequestMixin
from loopx.extensions.lark.cli_resolution import LarkCliResolution
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding
from loopx.extensions.lark.goal_channel_targets import add_lark_goal_channel_target


def test_lark_catalog_and_bounded_health_use_real_http_without_provider_writes() -> (
    None
):
    calls: list[tuple[list[str], float]] = []
    broken = False
    slow = False

    def runner(args: list[str], _cwd: Any, timeout: float) -> dict[str, Any]:
        calls.append((args, timeout))
        if args[-2:] == ["profile", "list"]:
            return {
                "returncode": 0,
                "stdout": "{}"
                if broken
                else json.dumps(
                    [
                        {
                            "name": "demo",
                            "appId": "cli_demo",
                            "brand": "feishu",
                            "active": True,
                        },
                        {
                            "name": "other",
                            "appId": "cli_other",
                            "brand": "feishu",
                            "active": False,
                        },
                    ]
                ),
            }
        assert args[-4:] == ["auth", "status", "--verify", "--json"]
        if slow:
            raise subprocess.TimeoutExpired(args, timeout)
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "appId": "cli_demo",
                    "identities": {
                        "bot": {"available": True, "verified": True, "appName": "Demo"}
                    },
                }
            ),
        }

    class Handler(LarkChatRequestMixin, BaseHTTPRequestHandler):
        def _require_lark_cli(self):
            return "lark-cli"

        def _lark_runner(self):
            return runner

        def do_GET(self):
            self._lark_apps()

        def log_message(self, *_args):
            pass

        def _send_json(self, payload, *, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def _send_error(self, message, *, status, error_code):
            self._send_json(
                {"ok": False, "error": message, "error_code": error_code}, status=status
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read(query):
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/chat/lark/apps{query}",
            timeout=10,
        ) as response:
            return json.load(response)

    try:
        catalog = read("?verify=false")
        assert len(catalog["apps"]) == 2 and len(calls) == 1
        assert all(not row["ready"] for row in catalog["apps"])
        calls.clear()
        health = read("?verify=true&app_ref=demo")
        assert health["apps"][0]["ready"] is True
        assert len(calls) == 2 and all(timeout == 5 for _, timeout in calls)
        slow = True
        assert (
            read("?verify=true&app_ref=demo")["apps"][0]["health_error_code"]
            == "lark_app_unverified"
        )
        for query in [
            "?verify=unknown",
            "?verify=false&verify=true",
            "?app_ref=removed",
            "?app_ref=../outside",
        ]:
            with pytest.raises(HTTPError) as error:
                read(query)
            assert error.value.code == 400
        broken = True
        with pytest.raises(HTTPError) as error:
            read("?verify=false")
        assert error.value.code == 502
        assert "private" not in error.value.read().decode()
        broken = False
        slow = False
        assert len(read("?verify=false")["apps"]) == 2
        assert all("profile" in args or "status" in args for args, _ in calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_lark_management_uses_dedicated_local_api_routes() -> None:
    assert CHAT_GOAL_CONTEXTS_PATH == "/api/chat/goals/contexts"
    assert CHAT_LARK_APPS_PATH == "/api/chat/lark/apps"
    assert CHAT_LARK_APP_SETUPS_PATH == "/api/chat/lark/app-setups"
    assert CHAT_LARK_CHATS_PATH == "/api/chat/lark/chats"
    assert CHAT_LARK_CONNECTIONS_PATH == "/api/chat/lark/connections"


def test_configured_lark_cli_bin_uses_deterministic_target_priority() -> None:
    payload = {
        "targets": {
            "z-target": {"identity": {"cli_bin": "z-lark-cli"}},
            "a-target": {"identity": {"cli_bin": "a-lark-cli"}},
        }
    }

    assert configured_lark_cli_bin(payload) == "a-lark-cli"


def test_runtime_resolution_reads_the_runtime_target_before_server_binding(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: dict[str, object] = {}

    def fake_read(path: Path) -> dict[str, object]:
        calls["target_path"] = path
        return {"targets": {"shared": {"identity": {"cli_bin": "target-lark-cli"}}}}

    def fake_resolve(
        *, explicit: str | None, target_cli_bin: str | None
    ) -> LarkCliResolution:
        calls["explicit"] = explicit
        calls["target_cli_bin"] = target_cli_bin
        return LarkCliResolution(
            command="explicit-lark-cli",
            available=True,
            source="explicit",
            version="test",
            error_code=None,
        )

    monkeypatch.setattr(chat_server, "read_goal_channel_targets", fake_read)
    monkeypatch.setattr(chat_server, "resolve_lark_cli", fake_resolve)

    resolution = resolve_lark_cli_for_runtime(
        runtime_root=tmp_path,
        explicit="explicit-lark-cli",
    )

    assert resolution.command == "explicit-lark-cli"
    assert calls["target_path"] == chat_server.default_goal_channel_target_path(
        tmp_path
    )
    assert calls["explicit"] == "explicit-lark-cli"
    assert calls["target_cli_bin"] == "target-lark-cli"


def test_goal_repository_context_is_credential_free_and_path_free(
    tmp_path: Path,
) -> None:
    project = tmp_path / "private" / "loopx"
    project.mkdir(parents=True)
    calls: list[list[str]] = []

    def git_runner(args: list[str]) -> dict[str, Any]:
        calls.append(args)
        if args[-3:] == ["config", "--get", "remote.origin.url"]:
            return {"returncode": 0, "stdout": "git@github.com:loopx-ai/loopx.git\n"}
        if args[-2:] == ["branch", "--show-current"]:
            return {"returncode": 0, "stdout": "codex/lark-goal-topic-binding\n"}
        return {"returncode": 1, "stdout": ""}

    rows = build_goal_repository_contexts(
        registry={"goals": [{"id": "goal-alpha", "repo": str(project)}]},
        git_runner=git_runner,
    )

    assert rows == [
        {
            "goal_id": "goal-alpha",
            "repository": {
                "branch": "codex/lark-goal-topic-binding",
                "identity": "git:github.com/loopx-ai/loopx",
                "label": "loopx-ai/loopx",
                "read_only": True,
            },
        }
    ]
    encoded = json.dumps(rows)
    assert str(tmp_path) not in encoded
    assert "git@" not in encoded
    assert calls


def test_runtime_snapshot_reuses_private_goal_channel_bindings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.chat_lark_api as api

    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    source_registry = project / ".loopx" / "registry.json"
    source_registry.parent.mkdir(parents=True)
    source_registry.write_text("{}\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry = {
        "goals": [
            {
                "id": "goal-alpha",
                "repo": str(project),
                "objective": "Alpha delivery",
            }
        ]
    }
    monkeypatch.setattr(api, "load_registry", lambda _path: registry)
    monkeypatch.setattr(
        api, "resolve_runtime_root", lambda *_args, **_kwargs: runtime_root
    )
    monkeypatch.setattr(
        api,
        "resolve_goal_source_runtime_route",
        lambda **_kwargs: {"source_registry": str(source_registry)},
    )
    target_path = runtime_root / "goal-channel-targets.json"
    add_lark_goal_channel_target(
        target_path=target_path,
        target_name="mew-product",
        chat_id="oc_public_fixture",
        chat_name="Product",
        identity_mode="local_user",
        sender_profile="mew",
        sender_identity="bot",
        bot_app_id="cli_public_fixture",
        bot_display_name="linkmacbot",
        cli_bin="fake-lark",
        execute=True,
    )
    write_goal_channel_binding(
        source_registry.parent / "goal-channel.json",
        {
            "schema_version": "loopx_goal_channel_lark_binding_v0",
            "bindings": {
                "goal-alpha": {
                    "goal_id": "goal-alpha",
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": "mew-product",
                    "topic": {"root_message_id": "om_topic_alpha"},
                }
            },
        },
    )

    snapshot = api.build_lark_goal_topic_runtime_snapshot(
        registry_path=registry_path,
        runtime_root_override=None,
    )

    assert set(snapshot["binding_payloads"]) == {"goal-alpha"}
    assert snapshot["goal_contexts"] == {
        "goal-alpha": {
            "work_dir": str(project.resolve()),
            "objective": "Alpha delivery",
        }
    }
    assert (
        snapshot["target_payload"]["targets"]["mew-product"]["identity"][
            "sender_profile"
        ]
        == "mew"
    )


def test_chat_server_closes_lark_goal_topic_runtime(monkeypatch: Any) -> None:
    closed: list[str] = []

    class Closer:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    monkeypatch.setattr(ThreadingHTTPServer, "server_close", lambda _self: None)
    server = object.__new__(ChatHTTPServer)
    server.lark_app_setup_manager = Closer("setup")  # type: ignore[assignment]
    server.lark_goal_topic_runtime = Closer("topics")  # type: ignore[attr-defined]
    server.runtime_controller = Closer("chat")  # type: ignore[assignment]
    server.server_close()

    assert closed == ["setup", "topics", "chat"]


def test_lark_apps_reports_safe_missing_cli_diagnostic() -> None:
    responses: list[dict[str, Any]] = []

    class Handler(LarkChatRequestMixin):
        server = SimpleNamespace(
            lark_cli_resolution=LarkCliResolution(
                command=None,
                available=False,
                source="missing",
                version=None,
                error_code="lark_cli_not_installed",
            )
        )

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            responses.append({**payload, "http_status": status})

        def _send_error(
            self,
            message: str,
            *,
            status: int,
            error_code: str,
            **_kwargs: Any,
        ) -> None:
            responses.append(
                {
                    "error": message,
                    "error_code": error_code,
                    "http_status": status,
                }
            )

    Handler()._lark_apps()

    assert responses == [
        {
            "error": "Install lark-cli, then restart the LoopX Chat service.",
            "error_code": "lark_cli_not_installed",
            "http_status": 503,
        }
    ]


def test_lark_chats_reports_lookup_failure_instead_of_an_empty_list(
    monkeypatch: Any,
) -> None:
    import loopx.chat_lark_api as api

    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api,
        "list_lark_group_chats",
        lambda **_kwargs: (_ for _ in ()).throw(api.LarkGroupChatLookupError()),
    )

    class Handler(LarkChatRequestMixin):
        path = "/api/chat/lark/chats?app_ref=mew"
        server = SimpleNamespace(
            lark_cli_resolution=LarkCliResolution(
                command="fake-lark",
                available=True,
                source="explicit",
                version="test",
                error_code=None,
            )
        )

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            responses.append({**payload, "http_status": status})

        def _send_error(
            self,
            message: str,
            *,
            status: int,
            error_code: str,
            **_kwargs: Any,
        ) -> None:
            responses.append(
                {
                    "error": message,
                    "error_code": error_code,
                    "http_status": status,
                }
            )

    Handler()._lark_chats()

    assert responses == [
        {
            "error": "Unable to list groups joined by the selected Lark App",
            "error_code": "lark_group_lookup_failed",
            "http_status": 502,
        }
    ]


def test_lark_connections_include_app_reply_health(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import loopx.chat_lark_api as api

    calls: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(api, "load_registry", lambda _path: {"goals": []})

    def fake_list(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [
            {
                "app_label": "Workspace Bot",
                "app_ref": "workspace-bot",
                "chat_name": "Product",
                "enabled": True,
                "goal_id": "goal-alpha",
                "goal_title": "Goal Alpha",
                "health_error_code": "lark_message_permissions_required",
                "incoming_mode": "mentions",
                "last_event_reason": "topic_mismatch",
                "reply_mode": "topic_reply",
                "reply_ready": False,
                "target_ref": "target-alpha",
                "topic_name": "goal-alpha",
                "topic_setup_required": False,
            }
        ]

    monkeypatch.setattr(api, "list_lark_connections", fake_list)

    def runner(*_args):
        return {"returncode": 0, "stdout": "{}", "stderr": ""}

    class Handler(LarkChatRequestMixin):
        server = SimpleNamespace(
            registry_path=tmp_path / "registry.json",
            lark_goal_topic_runtime=SimpleNamespace(
                health_snapshot=lambda: {
                    "workspace-bot": {
                        "status": "listening",
                        "event_count": 0,
                        "replied_count": 0,
                    }
                }
            ),
            lark_cli_resolution=LarkCliResolution(
                command="fake-lark",
                available=True,
                source="explicit",
                version="test",
                error_code=None,
            ),
        )

        def _goal_channel_target_path(self) -> Path:
            return tmp_path / "goal-channel-targets.json"

        def _lark_binding_paths(self, _registry: dict[str, Any]) -> dict[str, Path]:
            return {"goal-alpha": tmp_path / "goal-alpha.json"}

        def _lark_runner(self):
            return runner

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            responses.append({**payload, "http_status": status})

        def _send_error(self, message: str, **_kwargs: Any) -> None:
            raise AssertionError(message)

    Handler()._lark_connections()

    assert calls[0]["runner"] is runner
    assert calls[0]["cli_bin"] == "fake-lark"
    assert calls[0]["runtime_health"]["workspace-bot"]["status"] == "listening"
    assert responses[0]["connections"][0]["reply_ready"] is False
    assert (
        responses[0]["connections"][0]["health_error_code"]
        == "lark_message_permissions_required"
    )
    assert responses[0]["connections"][0]["last_event_reason"] == "topic_mismatch"


@pytest.mark.parametrize("editing", [False, True])
def test_connect_refreshes_the_app_level_event_consumer(
    monkeypatch: Any, tmp_path: Path, editing: bool
) -> None:
    import loopx.chat_lark_api as api

    refreshed: list[bool] = []
    connect_calls: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api,
        "connect_lark_goal_topic",
        lambda **kwargs: (
            connect_calls.append(kwargs) or {"ok": True, "status": "connected"}
        ),
    )

    class Handler(LarkChatRequestMixin):
        path = "/api/chat/lark/connections"
        server = SimpleNamespace(
            lark_goal_topic_runtime=SimpleNamespace(
                refresh=lambda: refreshed.append(True)
            )
        )

        def _read_json(self) -> dict[str, Any]:
            body = {
                "goal_id": "goal-alpha",
                "app_ref": "mew",
                "chat_id": "oc_public_fixture",
                "chat_name": "Product",
                "incoming_mode": "mentions",
                "agent_id": "agent-alpha",
                "capture_scope": "addressed_only",
                "ingress_mode": "async_inbox",
                "reply_mode": "topic_reply",
                "execute": True,
            }

            if editing:
                for field in ("app_ref", "chat_id", "chat_name"):
                    body.pop(field)
                body["connection_id"] = "lark_existing"
            return body

        def _goal_channel_context(self, _goal_id: str):
            return ({"goals": [{"id": "goal-alpha"}]}, tmp_path / "goal-channel.json")

        def _goal_channel_target_path(self) -> Path:
            return tmp_path / "goal-channel-targets.json"

        def _lark_runner(self):
            return lambda *_args: {"returncode": 0, "stdout": "{}", "stderr": ""}

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            responses.append({**payload, "http_status": status})

        def _send_error(self, message: str, **_kwargs: Any) -> None:
            raise AssertionError(message)

    Handler()._lark_connect()

    assert connect_calls[0]["connection_id"] == ("lark_existing" if editing else None)
    assert refreshed == [True]
    assert connect_calls[0]["agent_id"] == "agent-alpha"
    assert connect_calls[0]["capture_scope"] == "addressed_only"
    assert connect_calls[0]["ingress_mode"] == "async_inbox"
    assert connect_calls[0]["registry_path"] == tmp_path / "registry.json"
    assert responses == [{"ok": True, "status": "connected", "http_status": 200}]
    assert refreshed == [True]


def test_connect_batch_preserves_each_explicit_agent_app_pair(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import loopx.chat_lark_api as api

    batch_calls: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api,
        "connect_lark_goal_topics",
        lambda **kwargs: (
            batch_calls.append(kwargs) or {"ok": True, "status": "connected"}
        ),
    )

    class Handler(LarkChatRequestMixin):
        path = "/api/chat/lark/connections"
        server = SimpleNamespace(
            lark_goal_topic_runtime=SimpleNamespace(refresh=lambda: None)
        )

        def _read_json(self) -> dict[str, Any]:
            return {
                "goal_id": "goal-alpha",
                "agent_bindings": [
                    {"agent_id": "agent-alpha", "app_ref": "mew-alpha"},
                    {"agent_id": "agent-beta", "app_ref": "mew-beta"},
                ],
                "chat_id": "oc_public_fixture",
                "chat_name": "Product",
                "ingress_mode": "async_inbox",
                "execute": True,
            }

        def _goal_channel_context(self, _goal_id: str):
            return ({"goals": [{"id": "goal-alpha"}]}, tmp_path / "goal-channel.json")

        def _goal_channel_target_path(self) -> Path:
            return tmp_path / "goal-channel-targets.json"

        def _lark_runner(self):
            return lambda *_args: {"returncode": 0, "stdout": "{}", "stderr": ""}

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            responses.append({**payload, "http_status": status})

        def _send_error(self, message: str, **_kwargs: Any) -> None:
            raise AssertionError(message)

    Handler()._lark_connect()

    assert batch_calls[0]["app_refs_by_agent"] == {
        "agent-alpha": "mew-alpha",
        "agent-beta": "mew-beta",
    }
    assert batch_calls[0]["session_ids_by_agent"] == {}
    assert responses == [{"ok": True, "status": "connected", "http_status": 200}]


def test_connect_batch_preview_and_full_failure_do_not_refresh_runtime(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import loopx.chat_lark_api as api

    packets = [
        {"ok": True, "status": "preview_ready"},
        {
            "ok": False,
            "status": "failed",
            "details": {"completed_agent_ids": []},
        },
    ]
    executions = iter([False, True])
    refreshed: list[bool] = []
    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api,
        "connect_lark_goal_topics",
        lambda **_kwargs: packets.pop(0),
    )

    class Handler(LarkChatRequestMixin):
        path = "/api/chat/lark/connections"
        server = SimpleNamespace(
            lark_goal_topic_runtime=SimpleNamespace(
                refresh=lambda: refreshed.append(True)
            )
        )

        def _read_json(self) -> dict[str, Any]:
            return {
                "goal_id": "goal-alpha",
                "agent_bindings": [
                    {"agent_id": "agent-alpha", "app_ref": "mew-alpha"},
                    {"agent_id": "agent-beta", "app_ref": "mew-beta"},
                ],
                "chat_id": "oc_public_fixture",
                "chat_name": "Product",
                "ingress_mode": "async_inbox",
                "execute": next(executions),
            }

        def _goal_channel_context(self, _goal_id: str):
            return ({"goals": [{"id": "goal-alpha"}]}, tmp_path / "goal-channel.json")

        def _goal_channel_target_path(self) -> Path:
            return tmp_path / "goal-channel-targets.json"

        def _lark_runner(self):
            return lambda *_args: {"returncode": 0, "stdout": "{}", "stderr": ""}

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            responses.append({**payload, "http_status": status})

        def _send_error(self, message: str, **_kwargs: Any) -> None:
            raise AssertionError(message)

    Handler()._lark_connect()
    Handler()._lark_connect()

    assert refreshed == []
    assert [response["http_status"] for response in responses] == [200, 400]


def test_session_ingress_resolves_the_exact_goal_agent_session(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    import loopx.chat_lark_api as api

    connect_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api,
        "connect_lark_goal_topic",
        lambda **kwargs: (
            connect_calls.append(kwargs) or {"ok": True, "status": "connected"}
        ),
    )
    latest_calls: list[dict[str, Any]] = []

    class Handler(LarkChatRequestMixin):
        server = SimpleNamespace(
            chat_store=SimpleNamespace(
                latest_session=lambda **kwargs: (
                    latest_calls.append(kwargs) or {"session_id": "session-alpha"}
                )
            ),
            lark_goal_topic_runtime=SimpleNamespace(refresh=lambda: None),
        )

        def _read_json(self) -> dict[str, Any]:
            return {
                "goal_id": "goal-alpha",
                "app_ref": "mew",
                "chat_id": "oc_public_fixture",
                "chat_name": "Product",
                "agent_id": "agent-alpha",
                "ingress_mode": "session_queue",
                "execute": True,
            }

        def _goal_channel_context(self, _goal_id: str):
            return ({"goals": [{"id": "goal-alpha"}]}, tmp_path / "goal-channel.json")

        def _goal_channel_target_path(self) -> Path:
            return tmp_path / "goal-channel-targets.json"

        def _lark_runner(self):
            return lambda *_args: {"returncode": 0, "stdout": "{}", "stderr": ""}

        def _send_json(self, _payload: dict[str, Any], *, status: int = 200) -> None:
            assert status == 200

        def _send_error(self, message: str, **_kwargs: Any) -> None:
            raise AssertionError(message)

    Handler()._lark_connect()

    assert latest_calls == [
        {
            "goal_id": "goal-alpha",
            "agent_id": "agent-alpha",
            "channel_id": "goal.goal-alpha",
        }
    ]
    assert connect_calls[0]["session_id"] == "session-alpha"
    assert connect_calls[0]["ingress_mode"] == "session_queue"


def test_manager_route_reconciliation_opens_before_atomic_binding_swap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.chat_lark_api as api

    project = tmp_path / "project"
    project.mkdir()
    source_registry = project / ".loopx" / "registry.json"
    source_registry.parent.mkdir()
    source_registry.write_text("{}\n")
    registry = {"goals": [{"id": "goal-alpha", "repo": str(project)}]}
    binding = {
        "goal_id": "goal-alpha",
        "connection_id": "lark-manager",
        "agent_id": "loopx-manager",
        "session_id": "old-session",
        "enabled": True,
        "target_ref": "manager-target",
        "routing": {"conversation_kind": "manager"},
        "connector": {"session_ref": "old-session"},
    }
    calls: list[str] = []
    monkeypatch.setattr(api, "load_registry", lambda _path: registry)
    monkeypatch.setattr(
        api,
        "resolve_goal_source_runtime_route",
        lambda **_kwargs: {"source_registry": str(source_registry)},
    )
    monkeypatch.setattr(api, "resolve_runtime_root", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(api, "read_goal_channel_binding", lambda _path: {})
    monkeypatch.setattr(
        api, "binding_for_goal", lambda *_args, **_kwargs: dict(binding)
    )
    monkeypatch.setattr(
        api,
        "read_goal_channel_targets",
        lambda _path: {
            "targets": {
                "manager-target": {
                    "name": "manager-target",
                    "enabled": True,
                    "identity": {"sender_profile": "mew"},
                    "channel": {"chat_id": "oc_public_fixture"},
                }
            }
        },
    )
    monkeypatch.setattr(
        api,
        "manager_connection_executor_endpoint",
        lambda _root: ("dsh", "machine_configuration"),
    )

    def open_session(**_kwargs: Any):
        calls.append("open")
        return {"session_id": "new-session"}, True

    def rebind(**_kwargs: Any):
        calls.append("rebind")
        return {
            **binding,
            "session_id": "new-session",
            "routing": {
                "conversation_kind": "manager",
                "executor_endpoint_id": "dsh",
            },
            "connector": {"session_ref": "new-session"},
        }

    monkeypatch.setattr(api, "open_manager_session", open_session)
    monkeypatch.setattr(api, "rebind_lark_manager_session", rebind)
    controller = SimpleNamespace(
        store=SimpleNamespace(
            load_session=lambda session_id: {
                "session_id": session_id,
                "agent_id": "codex" if session_id == "old-session" else "dsh",
                "channel_id": api.manager_channel(
                    provider="lark", audience="mew\0oc_public_fixture"
                ),
                "status": "ready",
            }
        )
    )
    reconciled = api.reconcile_lark_manager_route(
        route={
            "goal_id": "goal-alpha",
            "connection_id": "lark-manager",
            "conversation_kind": "manager",
            "session_id": "old-session",
        },
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        runtime_controller=controller,
    )
    assert calls == ["open", "rebind"]
    assert reconciled["session_id"] == "new-session"
    assert reconciled["executor_endpoint_id"] == "dsh"
    assert reconciled["connector"]["session_ref"] == "new-session"


@pytest.mark.parametrize("execute, scope_failure", [(False, False), (True, False), (True, True)])
def test_manager_connection_opens_audience_session_only_on_execute(
    monkeypatch: Any, tmp_path: Path, execute: bool, scope_failure: bool
) -> None:
    import loopx.chat_lark_api as api
    import loopx.capabilities.manager_context as manager_context
    from loopx.chat_manager import manager_channel

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"goals": [{"id": "goal-alpha", "repo": str(tmp_path)}]}))
    calls: list[dict[str, Any]] = []
    opened: list[dict[str, Any]] = []
    responses: list[tuple[dict[str, Any], int]] = []
    refreshed: list[bool] = []
    configure = manager_context.configure_evidence_scope

    def configure_scope(*args, **kwargs):
        if scope_failure and kwargs.get("execute"):
            raise OSError("synthetic policy write failure")
        return configure(*args, **kwargs)

    monkeypatch.setattr(manager_context, "configure_evidence_scope", configure_scope)
    monkeypatch.setattr(
        api,
        "connect_lark_goal_topic",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "connected"},
    )

    class Handler(LarkChatRequestMixin):
        path = "/api/chat/lark/connections"
        server = SimpleNamespace(
            registry_path=registry_path,
            chat_store=SimpleNamespace(latest_session=lambda **kwargs: None),
            runtime_controller=SimpleNamespace(
                coordination_runtime_root=tmp_path / "runtime",
                open_session=lambda **kwargs: (
                    opened.append(kwargs) or {"session_id": "manager-session"},
                    False,
                )
            ),
            lark_goal_topic_runtime=SimpleNamespace(refresh=lambda: refreshed.append(True)),
        )

        def _require_lark_cli(self):
            return "fake-lark"

        def _read_json(self):
            return {
                "goal_id": "goal-alpha",
                "app_ref": "mew",
                "chat_id": "oc_public_fixture",
                "chat_name": "Product",
                "conversation_kind": "manager",
                "turn_trigger": "human_messages",
                "execute": execute,
            }

        def _goal_channel_context(self, _goal_id):
            return {
                "goals": [{"id": "goal-alpha", "repo": str(tmp_path)}]
            }, tmp_path / "binding.json"

        def _goal_channel_target_path(self):
            return tmp_path / "targets.json"

        def _lark_runner(self):
            return lambda *_args: {"returncode": 0, "stdout": "{}", "stderr": ""}

        def _send_json(self, payload, *, status=200):
            responses.append((payload, status))

        def _send_error(self, message, **kwargs):
            raise AssertionError(message)

    Handler()._lark_connect()
    assert responses[0][0]["ok"] is not scope_failure
    assert responses[0][1] == (400 if scope_failure else 200)
    if scope_failure:
        assert responses[0][0]["status"] == "partially_connected"
        assert "read scope" in responses[0][0]["error"]
    assert len(refreshed) == int(execute)
    assert calls[0]["conversation_kind"] == "manager"
    assert calls[0]["turn_trigger"] == "human_messages"
    assert calls[0]["ingress_mode"] == "session_queue"
    assert calls[0]["session_id"] == ("manager-session" if execute else None)
    assert len(opened) == int(execute)
    if execute:
        assert opened[0]["agent_goal_id"] == "loopx-manager"
        assert opened[0]["agent_id"] == "codex"
        assert opened[0]["channel_id"] == manager_channel(
            provider="lark", audience="mew\0oc_public_fixture"
        )
        assert opened[0]["channel_id"] != "manager"

    from loopx.capabilities.manager_context import evidence_goal_scope
    observed = evidence_goal_scope(tmp_path / "runtime", manager_channel(provider="lark", audience="mew\0oc_public_fixture"), registry_path=registry_path)
    assert observed == (["goal-alpha"] if execute and not scope_failure else None)


def test_manager_scope_reads_policy_from_coordination_root_when_chat_root_overrides(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from loopx.capabilities.machine_configuration.builtins import (
        build_builtin_machine_configuration_registry,
    )
    from loopx.capabilities.machine_configuration.store import (
        configure_machine_configuration,
    )
    from loopx.capabilities.manager_context import configure_evidence_scope
    from loopx.chat_manager import (
        manager_channel,
        manager_connection_executor_endpoint,
    )

    chat_root = tmp_path / "chat-runtime"
    coordination_root = tmp_path / "registered-runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(coordination_root),
                "goals": [
                    {"id": "goal-alpha", "repo": str(tmp_path)},
                    {"id": "goal-beta", "repo": str(tmp_path)},
                ],
            }
        ),
        encoding="utf-8",
    )
    channel = manager_channel(
        provider="lark", audience="mew\0oc_public_fixture"
    )
    machine_configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "steward_executor": {
                "schema_version": "steward_executor_machine_defaults_v0",
                "executor_endpoint": "dsh",
                "executor_model": None,
                "executor_reasoning_effort": None,
            }
        },
    }
    machine_registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=chat_root,
        configuration=machine_configuration,
        registry=machine_registry,
        execute=False,
    )
    configure_machine_configuration(
        runtime_root=chat_root,
        configuration=machine_configuration,
        registry=machine_registry,
        execute=True,
        expected_plan_revision=str(preview["plan_revision"]),
    )
    configure_evidence_scope(
        coordination_root,
        registry_path,
        channel=channel,
        goal_ids=["goal-alpha", "goal-beta"],
        goal_scope="all_registered",
        execute=True,
    )

    executor_endpoint, _source = manager_connection_executor_endpoint(chat_root)
    assert executor_endpoint == "dsh"
    target_payload = {
        "schema_version": "loopx_goal_channel_provider_targets_v0",
        "targets": {
            "manager-target": {
                "name": "manager-target",
                "enabled": True,
                "identity": {"sender_profile": "mew"},
                "channel": {"chat_id": "oc_public_fixture"},
            }
        },
    }
    binding_payloads = {
        "goal-alpha": {
            "bindings": {
                "goal-alpha": {
                    "goal_id": "goal-alpha",
                    "agent_id": executor_endpoint,
                    "session_id": "manager-session",
                    "enabled": True,
                    "target_ref": "manager-target",
                    "routing": {
                        "conversation_kind": "manager",
                        "ingress_mode": "session_queue",
                    },
                    "connector": {
                        "schema_version": "agent_external_connector_v0",
                        "goal_ref": "goal-alpha",
                        "agent_ref": executor_endpoint,
                        "provider_kind": "lark",
                        "source_kind": "group_message",
                        "source_ref": "oc_public_fixture",
                        "capture_policy": "configured_source_all",
                        "ingress_policy": "session_queue",
                        "response_policy": "topic_reply",
                        "lifecycle": "connected",
                        "capabilities": ["response_write", "response_readback"],
                        "session_ref": "manager-session",
                    },
                }
            }
        }
    }
    snapshot = {
        "target_payload": target_payload,
        "binding_payloads": binding_payloads,
    }
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("fixture", encoding="utf-8")
    from loopx import chat_lark_api

    monkeypatch.setattr(chat_lark_api, "build_lark_goal_topic_runtime_snapshot", lambda **_: snapshot)
    monkeypatch.setattr(
        chat_server,
        "resolve_lark_cli_for_runtime",
        lambda **_: LarkCliResolution(
            None, False, "missing", None, "lark_cli_not_installed"
        ),
    )
    entered = threading.Event()
    captured: list[ChatHTTPServer] = []

    class Server(ChatHTTPServer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            captured.append(self)

        def serve_forever(self, *args: Any, **kwargs: Any) -> None:
            entered.set()
            super().serve_forever(*args, **kwargs)

    monkeypatch.setattr(chat_server, "ChatHTTPServer", Server)
    worker = threading.Thread(
        target=chat_server.serve_chat,
        kwargs={
            "registry_path": registry_path,
            "runtime_root_override": chat_root,
            "scan_roots": [tmp_path],
            "host": "127.0.0.1",
            "port": 0,
            "assets_dir": assets,
            "open_browser": False,
        },
        daemon=True,
    )
    worker.start()
    try:
        assert entered.wait(8)
        server = captured[0]
        assert server.runtime_root == chat_root
        assert server.runtime_controller.coordination_runtime_root == coordination_root
        session = {
            "session_id": "manager-session",
            "goal_id": "goal-alpha",
            "agent_id": executor_endpoint,
            "channel_id": channel,
        }
        resolver = server.runtime_controller.manager_scope_resolver
        assert resolver(session) == ["goal-alpha", "goal-beta"]
        configure_evidence_scope(
            chat_root,
            registry_path,
            channel=channel,
            goal_ids=["goal-alpha", "goal-beta"],
            goal_scope="all_registered",
            execute=True,
        )
        configure_evidence_scope(
            coordination_root,
            registry_path,
            channel=channel,
            goal_ids=["goal-alpha"],
            execute=True,
        )
        assert resolver(session) == ["goal-alpha"]
    finally:
        if captured:
            captured[0].shutdown()
        worker.join(8)
    assert not worker.is_alive()
