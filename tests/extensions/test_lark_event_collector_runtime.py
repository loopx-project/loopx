from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

import pytest

from loopx.extensions.lark import event_collector, event_collector_runtime
from loopx.extensions.lark.event_collector import (
    inspect_lark_event_collector,
    plan_lark_event_collector,
)
from loopx.extensions.lark.event_collector_runtime import (
    _callback_event_shape,
    _operation_transport_runner,
    _run_json_with_status,
    enrich_lark_event_reply_context,
    lark_event_requires_reply_context_lookup,
    _reply_source_content,
    run_lark_event_collector,
)


def test_operation_transport_runner_preserves_explicit_node_prefix() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps({"ok": True}),
            stderr="",
        )

    transport = _operation_transport_runner(
        runner,
        command_prefix=["/opt/node", "/opt/lark-cli"],
    )

    result = transport(
        ["/opt/lark-cli", "im", "chats", "get"],
        None,
        30,
    )

    assert result["returncode"] == 0
    assert calls == [["/opt/node", "/opt/lark-cli", "im", "chats", "get"]]


def test_operation_callback_failure_shape_retains_timestamp_width_without_value() -> (
    None
):
    shape = _callback_event_shape(
        {
            "type": "card.action.trigger",
            "timestamp": "1776409469273",
            "action_tag": "button",
            "action_value": "{}",
        }
    )

    assert shape["timestamp_is_digits"] is True
    assert shape["timestamp_digit_count"] == 13
    assert "1776409469273" not in json.dumps(shape)


def test_reply_context_lookup_does_not_trust_unrelated_text_mentions() -> None:
    bot_name = "Context Bot"

    assert lark_event_requires_reply_context_lookup(
        {"content": "@Alice can LoopX handle this?"},
        bot_display_name=bot_name,
    )
    assert lark_event_requires_reply_context_lookup(
        {
            "content": "@Alice can LoopX handle this?",
            "mentions": [{"name": "Alice"}],
            "mentioned": False,
        },
        bot_display_name=bot_name,
    )
    assert not lark_event_requires_reply_context_lookup(
        {
            "mentions": [{"name": bot_name}],
            "mentioned": True,
        },
        bot_display_name=bot_name,
    )
    assert lark_event_requires_reply_context_lookup(
        {"mentions": [{"name": bot_name}], "mentioned": True, "parent_id": "om_parent"},
        bot_display_name=bot_name,
    )


@pytest.mark.parametrize("raw,expected", [
    ("A visible answer", "A visible answer"),
    (json.dumps({"text": "A visible answer"}), "A visible answer"),
    (json.dumps({"zh_cn": {"title": "", "content": [[{"tag": "text", "text": "A visible answer"}]]}}), "A visible answer"),
    (json.dumps({"content": [[{"tag": "img", "image_key": "private_fixture_key"}]]}), ""),
    (json.dumps({"operation": {"callback_token": "private_fixture_token"}}), ""),
    (json.dumps({"schema": "2.0", "header": {"title": {"content": "Review"}},
                 "body": {"elements": [{"tag": "button", "text": {"content": "Open"},
                                           "value": {"callback_token": "private_fixture_token"}}]}}),
     '<card title="Review">\n[Open]\n</card>'),
])
def test_reply_source_reads_visible_text_without_callback_payload(raw: str, expected: str) -> None:
    content = _reply_source_content({"body": {"content": raw}})
    assert content == expected
    assert "private_fixture_" not in content


def test_json_status_reads_nested_provider_code_from_stderr() -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["lark-cli"],
            returncode=1,
            stdout="",
            stderr=json.dumps(
                {
                    "ok": False,
                    "identity": "bot",
                    "error": {"code": 230027, "message": "permission denied"},
                }
            ),
        )

    payload, status = _run_json_with_status(runner, ["lark-cli", "im", "messages"])

    assert payload == {}
    assert status == "message_context_permission_required"


def test_json_status_preserves_success_payload_from_stdout() -> None:
    expected = {"ok": True, "data": {"items": []}}

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["lark-cli"],
            returncode=0,
            stdout=json.dumps(expected),
            stderr="",
        )

    payload, status = _run_json_with_status(runner, ["lark-cli", "im", "messages"])

    assert payload == expected
    assert status == "message_context_available"


def test_reply_context_hydration_preserves_provider_sender_type() -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        message = {
            "message_id": "om_self_fixture",
            "chat_id": "oc_fixture",
            "content": "Bot delivery status",
            "sender": {
                "sender_type": "app",
                "id": "cli_fixture_bot",
            },
        }
        return subprocess.CompletedProcess(
            args=["lark-cli"],
            returncode=0,
            stdout=json.dumps({"ok": True, "data": {"messages": [message]}}),
            stderr="",
        )

    enriched = enrich_lark_event_reply_context(
        {
            "message_id": "om_self_fixture",
            "chat_id": "oc_fixture",
        },
        runner=runner,
        command_prefix=["lark-cli"],
        profile="fixture-bot",
        profile_app_id="cli_fixture_bot",
        configured_chat_id="oc_fixture",
        sleeper=lambda _seconds: None,
    )

    assert enriched["sender_type"] == "app"
    assert enriched["sender_id"] == "cli_fixture_bot"


def test_reply_context_keeps_the_exact_parent_as_context_only() -> None:
    messages = {
        "om_question": {
            "message_id": "om_question",
            "chat_id": "oc_fixture",
            "content": "Where is that card?",
            "parent_id": "om_parent",
            "sender": {"sender_type": "user", "id": "ou_fixture"},
        },
        "om_parent": {
            "message_id": "om_parent",
            "chat_id": "oc_fixture",
            "content": "Dependency review is ready; the request is not approved.",
            "sender": {"sender_type": "app", "id": "cli_fixture_bot"},
        },
    }

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        mid = argv[argv.index("--message-ids") + 1]
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "ok": True,
                    "data": {"messages": [messages[mid]]},
                }
            ),
            "",
        )

    enriched = enrich_lark_event_reply_context(
        {"message_id": "om_question", "chat_id": "oc_fixture"},
        runner=runner,
        command_prefix=["lark-cli"],
        profile="fixture-bot",
        profile_app_id="cli_fixture_bot",
        configured_chat_id="oc_fixture",
        sleeper=lambda _seconds: None,
    )
    assert enriched["reply_to_bot"] is True
    assert enriched["reply_context"] == {
        "message_id": "om_parent",
        "conversation_id": "oc_fixture",
        "content": messages["om_parent"]["content"],
    }


def test_formatted_lookup_preserves_event_ancestry_and_thread_context() -> None:
    root = {"message_id": "om_root", "chat_id": "oc_fixture", "thread_id": "omt_fixture",
            "thread_message_position": "-1", "content": "Review the draft.",
            "sender": {"sender_type": "user", "id": "ou_fixture"}}
    current = {**root, "message_id": "om_current", "thread_message_position": "2",
               "content": "Use the new version."}
    revised = {**root, "message_id": "om_revised", "thread_message_position": "1",
               "content": "Version 3 is ready.", "create_time": "2026-01-01 12:02",
               "sender": {"sender_type": "app", "id": "cli_fixture_bot"}}
    messages = {"om_current": current, "om_root": {**root, "thread_replies": [revised, current]}}
    calls = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        mid = argv[argv.index("--message-ids") + 1]
        calls.append(mid)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {"messages": [messages[mid]]}}), "")

    options = dict(runner=runner, command_prefix=["lark-cli"], profile="fixture-bot",
                   profile_app_id="cli_fixture_bot", configured_chat_id="oc_fixture", sleeper=lambda _: None)
    enriched = enrich_lark_event_reply_context({"message_id": "om_current", "root_id": "om_root"}, **options)
    assert calls == ["om_current", "om_root"]
    assert "parent_id" not in enriched and "reply_context" not in enriched
    assert enriched["reply_to_bot"] is False  # A thread is not a verified direct bot reply.
    assert enriched["thread_context"]["messages"][1] == {
        "message_id": "om_revised", "conversation_id": "oc_fixture", "thread_id": "omt_fixture",
        "position": 1, "content": "Version 3 is ready.", "content_truncated": False,
        "sender": {"id": "cli_fixture_bot", "kind": "app"}, "created_at": "2026-01-01 12:02",
    }
    direct = enrich_lark_event_reply_context({"message_id": "om_current", "parent_id": "om_root"}, **options)
    assert direct["reply_context"]["content"] == "Review the draft."
    assert direct["parent_id"] == "om_root"


def _operation_callback_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    config_root = project / ".loopx" / "config" / "lark"
    config_root.mkdir(parents=True)
    (config_root / "inbox.json").write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": ".loopx/inbox/operation",
                "capture_scope": "configured_chat_all",
                "reply": {
                    "enabled": True,
                    "sender_profile": "operation-bot",
                    "sender_identity": "bot",
                    "bot_display_name": "Operation Bot",
                    "chat_id": "oc_operation_fixture",
                    "placement_policy": "source_context",
                    "editorial_style": "bullet_points_preferred",
                },
            }
        ),
        encoding="utf-8",
    )
    collector = config_root / "collector.json"
    collector.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_collector_config_v1",
                "enabled": True,
                "service_name": "loopx-operation-fixture",
                "event_key": "im.message.receive_v1",
                "identity": "bot",
                "supervisor": "systemd",
                "consume_timeout": "30m",
                "lark_cli_bin": "lark-cli",
                "operation_callbacks": {"enabled": True},
                "routes": [
                    {
                        "route_key": "operation",
                        "chat_id": "oc_operation_fixture",
                        "event_inbox_config": ".loopx/config/lark/inbox.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, collector


def test_operation_callback_plan_requires_pinned_runtime(tmp_path: Path) -> None:
    project, collector = _operation_callback_project(tmp_path)

    plan = plan_lark_event_collector(project=project, config_path=collector)

    assert plan["ok"] is False
    assert plan["status"] == "pinned_runtime_required"
    assert plan["operation_callbacks_enabled"] is True
    assert plan["operation_callback_console_configuration_preflighted"] is False


def test_callback_managed_wake_configuration_is_explicit_and_read_back(tmp_path: Path) -> None:
    project, collector = _operation_callback_project(tmp_path)
    data = json.loads(collector.read_text())
    config = {"registry_path": str(tmp_path / "registry.json"), "goal_id": "goal-fixture",
              "requester_agent_id": "requester", "execution_config": ".loopx/config/delegations.json",
              "binding_id": "operation-worker"}
    data["operation_callbacks"]["managed_turn_wake"] = config
    collector.write_text(json.dumps(data))
    loaded = event_collector.load_lark_event_collector_config(project=project, config_path=collector)
    assert loaded["operation_callbacks"]["managed_turn_wake"] == {**config, "project": str(project)}
    plan = plan_lark_event_collector(project=project, config_path=collector, runtime_root=tmp_path / "runtime")
    assert plan["operation_callback_managed_wake_configured"] is True
    assert "registry_path" not in json.dumps(plan)
    for patch in [{"registry_path": "relative.json"}, {"execution_config": "../outside.json"},
                  {"binding_id": "bad\nvalue"}, {"requester_agent_id": None}, {"unexpected": True}]:
        data["operation_callbacks"]["managed_turn_wake"] = {**config, **patch}
        collector.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            event_collector.load_lark_event_collector_config(project=project, config_path=collector)
    data["operation_callbacks"] = {"enabled": False, "managed_turn_wake": config}
    collector.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="enabled"):
        event_collector.load_lark_event_collector_config(project=project, config_path=collector)


def test_operation_callback_status_separates_readiness_from_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, collector = _operation_callback_project(tmp_path)
    service = tmp_path / "loopx-operation-fixture.service"
    service.write_text("installed", encoding="utf-8")
    monkeypatch.setattr(event_collector, "_service_file", lambda _config: service)
    monkeypatch.setattr(event_collector.shutil, "which", lambda _name: "/usr/bin/true")
    callback_status = (
        project / ".loopx/runtime/lark-collector/operation-callback-status.json"
    )
    callback_status.parent.mkdir(parents=True)
    callback_status.write_text(
        json.dumps(
            {
                "schema_version": "lark_operation_callback_listener_status_v1",
                "listener_active": True,
                "listener_ready": True,
                "callback_delivery_verified": False,
                "last_failure_code": "callback_timestamp_invalid",
                "last_failure_stage": "validate_timestamp",
                "last_failure_event_shape": {
                    "timestamp_is_digits": True,
                    "timestamp_digit_count": 13,
                },
            }
        ),
        encoding="utf-8",
    )

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=0, stdout="active\n", stderr=""
        )

    status = inspect_lark_event_collector(
        project=project,
        config_path=collector,
        runtime_root=tmp_path / "runtime",
        runner=runner,
    )

    assert status["healthy"] is True
    assert status["operation_callback_listener_active"] is True
    assert status["operation_callback_listener_ready"] is True
    assert (
        status["operation_callback_qualification_state"] == "listener_ready_unqualified"
    )
    assert status["operation_callback_last_failure_code"] == (
        "callback_timestamp_invalid"
    )
    assert status["operation_callback_last_failure_stage"] == "validate_timestamp"
    assert status["operation_callback_last_failure_event_shape"] == {
        "timestamp_is_digits": True,
        "timestamp_digit_count": 13,
    }

    payload = json.loads(callback_status.read_text(encoding="utf-8"))
    payload["callback_delivery_verified"] = True
    callback_status.write_text(json.dumps(payload), encoding="utf-8")

    qualified = inspect_lark_event_collector(
        project=project,
        config_path=collector,
        runtime_root=tmp_path / "runtime",
        runner=runner,
    )

    assert qualified["operation_callback_qualification_state"] == "callback_qualified"


@pytest.mark.parametrize("wake_revocation", [False, True])
def test_collector_runs_independent_operation_callback_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wake_revocation: bool,
) -> None:
    project, collector = _operation_callback_project(tmp_path)
    if wake_revocation:
        config = json.loads(collector.read_text())
        config["operation_callbacks"]["managed_turn_wake"] = {
            "registry_path": str(tmp_path / "registry.json"), "goal_id": "fixture-goal",
            "requester_agent_id": "coordinator", "execution_config": "delegations.json",
            "binding_id": "confirmed-operation",
        }
        collector.write_text(json.dumps(config))
    runtime_root = tmp_path / "runtime"
    cli = tmp_path / "lark-cli-fixture"
    cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "import time\n"
        "event_key = sys.argv[sys.argv.index('consume') + 1]\n"
        "if event_key == 'card.action.trigger':\n"
        "    assert '--quiet' not in sys.argv\n"
        "    print('[event] ready event_key=card.action.trigger', flush=True)\n"
        "    print(json.dumps({'type': event_key, 'chat_id': 'oc_operation_fixture'}), flush=True)\n"
        "    print(json.dumps({'type': event_key, 'chat_id': 'oc_operation_fixture'}), flush=True)\n"
        "else:\n"
        "    time.sleep(0.2)\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    captured: list[dict[str, object]] = []

    def handle(payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        captured.append({"payload": payload, **kwargs})
        if wake_revocation and len(captured) == 1:
            config = json.loads(collector.read_text())
            del config["operation_callbacks"]["managed_turn_wake"]
            collector.write_text(json.dumps(config))
        return {
            "ok": len(captured) > 1,
            "schema_version": "lark_operation_callback_receipt_v0",
        }

    monkeypatch.setattr(
        event_collector_runtime,
        "handle_goal_channel_operation_callback",
        handle,
    )

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "whoami" in argv
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps({"appId": "cli_operation_fixture"}),
            stderr="",
        )

    result = run_lark_event_collector(
        project=project,
        config_path=collector,
        lark_cli_executable=str(cli),
        runtime_root=runtime_root,
        runner=runner,
    )

    deadline = time.monotonic() + 1
    while not captured and time.monotonic() < deadline:
        time.sleep(0.01)
    assert result["operation_callback_listener_started"] is True
    assert result["operation_callback_listener_ready"] is True
    assert result["operation_callback_received_count"] == 2
    assert result["operation_callback_verified_count"] == 1
    assert captured[0]["runtime_root"] == runtime_root.resolve()
    assert captured[0]["action_store_root"] == runtime_root / "chat" / "actions"
    assert (captured[0]["managed_turn_wake"] is not None) is wake_revocation
    assert captured[1]["managed_turn_wake"] is None
    status = json.loads(
        (
            project / ".loopx/runtime/lark-collector/operation-callback-status.json"
        ).read_text(encoding="utf-8")
    )
    assert status["callback_delivery_verified"] is True
    assert status["listener_ready"] is False
    assert status["failed_callback_count"] == 1
    assert status["last_failure_code"] == "result_delivery_unverified"
    assert status["last_failure_stage"] == "handle_callback"
    assert status["listener_active"] is False


def test_operation_callback_json_diagnostic_does_not_mark_listener_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, collector = _operation_callback_project(tmp_path)
    runtime_root = tmp_path / "runtime"
    cli = tmp_path / "lark-cli-fixture"
    cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "import time\n"
        "event_key = sys.argv[sys.argv.index('consume') + 1]\n"
        "if event_key == 'card.action.trigger':\n"
        "    print(json.dumps({'error': {'code': 'startup_warning'}}), flush=True)\n"
        "else:\n"
        "    time.sleep(0.2)\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        event_collector_runtime,
        "handle_goal_channel_operation_callback",
        lambda payload, **kwargs: captured.append({"payload": payload, **kwargs}),
    )

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "whoami" in argv
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps({"appId": "cli_operation_fixture"}),
            stderr="",
        )

    result = run_lark_event_collector(
        project=project,
        config_path=collector,
        lark_cli_executable=str(cli),
        runtime_root=runtime_root,
        runner=runner,
    )

    assert result["operation_callback_listener_ready"] is False
    assert result["operation_callback_received_count"] == 0
    assert result["operation_callback_failure_count"] == 0
    assert captured == []
