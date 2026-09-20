from __future__ import annotations

import importlib
import json
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.extensions.lark.manager_reply_parts import (
    MANAGER_REPLY_MAX_PARTS,
    MANAGER_REPLY_OVERFLOW_NOTE,
)
from loopx.extensions.lark.event_collector import _jq_projection
from loopx.extensions.lark.event_inbox import inspect_lark_event_inbox
from loopx.extensions.lark.goal_channel_contracts import (
    binding_for_goal,
    read_goal_channel_binding,
)
from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets
from loopx.extensions.lark.goal_topic_connections import connect_lark_goal_topic


def test_goal_topic_runtime_exposes_the_inbox_bridge() -> None:
    module = importlib.import_module("loopx.extensions.lark.goal_topic_runtime")

    assert callable(getattr(module, "process_lark_goal_topic_event", None))


def test_a_stale_steward_session_names_the_rebind_instead_of_a_generic_failure(
    tmp_path: Path,
) -> None:
    """A machine that changed its steward executor must say what repairs it."""

    from types import SimpleNamespace

    from loopx.extensions.lark.goal_topic_runtime import (
        LarkGoalTopicTurnFailed,
        answer_lark_goal_topic,
    )
    from loopx.extensions.lark.manager_context import manager_failure_reply

    controller = SimpleNamespace(
        steward_executor_defaults=lambda: {
            "schema_version": "steward_executor_effective_defaults_v0",
            "status": "ready",
            "source": "machine_configuration",
            "executor_endpoint": "dsh",
            "executor_model": "deepseek-v4-flash",
            "executor_reasoning_effort": "high",
        },
        store=SimpleNamespace(
            load_session=lambda _session_id: {
                "session_id": "manager-session",
                "agent_id": "codex",
                "channel_id": "manager.external.public_fixture",
                "status": "ready",
            }
        ),
    )
    route = {
        "schema_version": "lark_goal_topic_route_v0",
        "goal_id": "goal-alpha",
        "conversation_kind": "manager",
        "ingress_mode": "session_queue",
        "session_id": "manager-session",
        "manager_channel_id": "manager.external.public_fixture",
        "message_id": "om_manager_stale_endpoint",
        "topic_root_message_id": "om_manager_topic_root",
        "app_ref": "cli_public_fixture",
        "target_ref": "public_fixture_target",
    }

    with pytest.raises(LarkGoalTopicTurnFailed) as failure:
        answer_lark_goal_topic(
            route=route,
            text="@LoopX 管家 status",
            work_dir=str(tmp_path),
            objective="worker objective",
            runtime_controller=controller,
        )

    assert failure.value.error_code == "manager_channel_executor_rebind_required"
    code, text = manager_failure_reply(failure.value)
    assert code == "manager_channel_executor_rebind_required"
    # The reply names the operator action instead of the opaque manager label.
    assert "重新应用" in text
    assert "管家处理失败" not in text


def test_existing_collector_uses_the_real_compact_event_schema() -> None:
    projection = _jq_projection("oc_public_fixture")

    assert "message_id:(.message_id // .id)" in projection
    assert "root_id" not in projection
    assert "parent_id" not in projection
    assert "mentions" not in projection
    assert "mentioned" not in projection


def _connection_runner(state: dict[str, Any]):
    def run(args: list[str], _cwd: object, _timeout: object) -> dict[str, Any]:
        if "auth" in args and "check" in args:
            payload: Any = {
                "ok": True,
                "granted": ["im:message", "im:message:readonly"],
                "missing": [],
            }
        elif "auth" in args and "status" in args:
            payload: Any = {
                "appId": "cli_public_fixture",
                "identities": {
                    "bot": {
                        "available": True,
                        "verified": True,
                        "appName": "linkmacbot",
                    }
                },
            }
        elif "chats" in args and "get" in args:
            payload = {"data": {"chat_id": "oc_public_fixture"}}
        elif "+chat-members-list" in args:
            payload = {"data": {"chats": [{"app_id": "cli_public_fixture"}]}}
        elif "+messages-send" in args:
            payload = {"data": {"message_id": "om_topic_alpha"}}
            state["topic_text"] = args[args.index("--text") + 1]
        elif "+messages-mget" in args:
            payload = {
                "data": {
                    "items": [
                        {
                            "message_id": "om_topic_alpha",
                            "body": {"content": state["topic_text"]},
                        }
                    ]
                }
            }
        else:
            raise AssertionError(args)
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    return run


def _reply_runner(state: dict[str, Any]):
    def run(args: list[str]) -> dict[str, Any]:
        state.setdefault("calls", []).append(list(args))
        if args[3:6] == ["auth", "status", "--verify"]:
            payload: Any = {
                "identities": {
                    "bot": {
                        "available": True,
                        "verified": True,
                        "appName": "linkmacbot",
                    }
                }
            }
        elif args[3:6] == ["im", "chats", "get"]:
            payload = {"data": {"chat_id": "oc_public_fixture"}}
        elif "+messages-reply" in args:
            if "--content" in args:
                state["reply_content"] = args[args.index("--content") + 1]
                state["reply_type"] = "post"
                state["reply_text"] = json.loads(state["reply_content"])["zh_cn"]["content"][0][0]["text"]
            else:
                state["reply_text"] = args[args.index("--text") + 1]
                state["reply_content"] = json.dumps({"text": state["reply_text"]}, ensure_ascii=False)
                state["reply_type"] = "text"
            payload = (
                {
                    "api": [
                        {
                            "body": {
                                "msg_type": state["reply_type"],
                                "content": state["reply_content"]
                            }
                        }
                    ]
                }
                if "--dry-run" in args
                else {"data": {"message_id": "om_reply_fixture"}}
            )
        elif "+messages-mget" in args:
            payload = {
                "data": {
                    "items": [
                        {
                            "message_id": "om_reply_fixture",
                            "msg_type": state["reply_type"],
                            "body": {"content": state["reply_content"]},
                        }
                    ]
                }
            }
        else:
            raise AssertionError(args)
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    return run


def _seed_legacy_topic(target_path: Path, binding_path: Path) -> dict[str, Any]:
    """Old on-disk fixtures remain readable even though new legacy writes are closed."""
    from loopx.extensions.lark.goal_channel_contracts import save_goal_connection
    from loopx.extensions.lark.goal_channel_targets import add_lark_goal_channel_target

    add_lark_goal_channel_target(
        target_path=target_path,
        target_name="fixture",
        chat_id="oc_public_fixture",
        chat_name="Product group",
        identity_mode="local_user",
        sender_profile="mew",
        bot_app_id="cli_public_fixture",
        bot_display_name="linkmacbot",
        execute=True,
    )
    save_goal_connection(
        binding_path=binding_path,
        payload=read_goal_channel_binding(binding_path),
        goal_id="goal-alpha",
        binding={
            "enabled": True,
            "provider": "lark",
            "target_ref": "fixture",
            "topic": {"root_message_id": "om_topic_alpha"},
            "routing": {
                "ingress_mode": "direct_session",
                "incoming_mode": "mentions",
                "reply_mode": "topic_reply",
            },
        },
    )
    return {"ok": True}


def test_mention_uses_existing_inbox_reply_and_ack_path(tmp_path: Path) -> None:
    from loopx.extensions.lark.goal_topic_runtime import process_lark_goal_topic_event

    state: dict[str, Any] = {}
    target_path = tmp_path / "goal-channel-targets.json"
    binding_path = tmp_path / "goal-channel.json"
    connected = _seed_legacy_topic(target_path, binding_path)
    assert connected["ok"] is True

    answers: list[tuple[str, str]] = []
    result = process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={
            "event_id": "evt_incoming",
            "message_id": "om_incoming",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "create_time": "2026-08-14T21:00:00Z",
            "content": "@linkmacbot 你现在 loopx 的版本是什么",
            "mentioned": True,
        },
        runtime_root=tmp_path / "runtime",
        answer=lambda route, text: (
            answers.append((str(route["goal_id"]), text))
            or "当前运行的是 LoopX 开发版。"
        ),
        reply_runner=_reply_runner(state),
    )

    assert result["ok"] is True
    assert result["status"] == "replied_and_acknowledged"
    assert result["goal_id"] == "goal-alpha"
    assert answers == [("goal-alpha", "@linkmacbot 你现在 loopx 的版本是什么")]
    assert state["reply_text"] == "当前运行的是 LoopX 开发版。"
    reply_call = next(call for call in state["calls"] if "+messages-reply" in call)
    assert reply_call[:3] == ["lark-cli", "--profile", "mew"]
    assert reply_call[reply_call.index("--message-id") + 1] == "om_incoming"
    projection = inspect_lark_event_inbox(
        project=tmp_path / "runtime",
        config_path=Path(result["inbox_config_ref"]),
    )
    assert projection["pending_count"] == 0
    assert projection["processed_count"] == 1


def test_manager_captures_unaddressed_context_without_granting_turn_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.extensions.lark import goal_topic_runtime as runtime

    state: dict[str, Any] = {}
    target_path = tmp_path / "goal-channel-targets.json"
    binding_path = tmp_path / "goal-channel.json"
    _seed_legacy_topic(target_path, binding_path)

    def decision(**options: Any) -> dict[str, Any]:
        event = options["event"]
        context_only = event["message_id"] == "om_context_only"
        return {
            "matched": True,
            "reason": "context_only" if context_only else "matched",
            "route": {
                "app_ref": "mew",
                "goal_id": "goal-alpha",
                "message_id": event["message_id"],
                "target_ref": "fixture",
                "topic_root_message_id": "om_topic_alpha",
                "conversation_kind": "manager",
                "capture_scope": "configured_chat_all",
                "authority_mode": (
                    "context_only" if context_only else "turn_authorized"
                ),
                "ingress_mode": "session_queue",
                "reply_mode": "topic_reply",
            },
        }

    monkeypatch.setattr(runtime, "decide_lark_topic_event", decision)
    monkeypatch.setattr(
        runtime,
        "ensure_lark_event_inbox_received_reaction",
        lambda **_options: {"ok": True, "status": "retained"},
    )
    answer_calls: list[tuple[dict[str, Any], str]] = []

    def answer(route: Any, text: str) -> str:
        answer_calls.append((dict(route), text))
        return "收到，已结合上文处理。"

    common = {
        "target_payload": read_goal_channel_targets(target_path),
        "binding_payloads": {
            "goal-alpha": read_goal_channel_binding(binding_path)
        },
        "runtime_root": tmp_path / "runtime",
        "answer": answer,
    }
    captured = runtime.process_lark_goal_topic_event(
        **common,
        event={
            "event_id": "evt_context_only",
            "message_id": "om_context_only",
            "chat_id": "oc_public_fixture",
            "root_id": "om_unrelated_thread",
            "create_time": "2026-09-13T05:59:00Z",
            "content": "先把这个背景放在这里",
            "mentions": [],
            "sender_type": "user",
            "sender_id": "ou_owner_fixture",
        },
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("context-only capture must not call Lark write APIs")
        ),
    )
    assert captured["status"] == "context_only_captured"
    assert captured["turn_authorized"] is False
    assert captured["model_invoked"] is False
    assert answer_calls == []
    duplicate = runtime.process_lark_goal_topic_event(
        **common,
        event={
            "event_id": "evt_context_only",
            "message_id": "om_context_only",
            "chat_id": "oc_public_fixture",
            "root_id": "om_unrelated_thread",
            "create_time": "2026-09-13T05:59:00Z",
            "content": "先把这个背景放在这里",
            "mentions": [],
            "sender_type": "user",
            "sender_id": "ou_owner_fixture",
        },
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("duplicate context must not call Lark write APIs")
        ),
    )
    assert duplicate["status"] == "context_only_already_captured"
    assert answer_calls == []
    pending = inspect_lark_event_inbox(
        project=common["runtime_root"], config_path=captured["inbox_config_ref"]
    )
    assert pending["pending_count"] == 1

    replied = runtime.process_lark_goal_topic_event(
        **common,
        event={
            "event_id": "evt_authorized",
            "message_id": "om_authorized",
            "chat_id": "oc_public_fixture",
            "root_id": "om_unrelated_thread",
            "create_time": "2026-09-13T06:00:00Z",
            "content": "@linkmacbot 结合上文给结论",
            "mentions": [{"id": "cli_public_fixture"}],
            "sender_type": "user",
            "sender_id": "ou_owner_fixture",
        },
        reply_runner=_reply_runner(state),
    )
    assert replied["status"] == "replied_and_acknowledged"
    assert replied["context_material_count"] == 1
    assert replied["context_settled_count"] == 1
    assert len(answer_calls) == 1
    route, text = answer_calls[0]
    assert text == "@linkmacbot 结合上文给结论"
    assert route["context_materials"] == [
        {
            "message_id": "om_context_only",
            "create_time": "2026-09-13T05:59:00Z",
            "content": "先把这个背景放在这里",
        }
    ]
    assert inspect_lark_event_inbox(
        project=common["runtime_root"], config_path=replied["inbox_config_ref"]
    )["pending_count"] == 0


@pytest.mark.parametrize("authority_mode", [None, "future_mode"])
def test_manager_route_rejects_missing_or_unknown_authority_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority_mode: str | None
) -> None:
    from loopx.extensions.lark import goal_topic_runtime as runtime

    target_path = tmp_path / "goal-channel-targets.json"
    binding_path = tmp_path / "goal-channel.json"
    _seed_legacy_topic(target_path, binding_path)

    def decision(**options: Any) -> dict[str, Any]:
        event = options["event"]
        route = {
            "app_ref": "mew",
            "goal_id": "goal-alpha",
            "message_id": event["message_id"],
            "target_ref": "fixture",
            "topic_root_message_id": "om_topic_alpha",
            "conversation_kind": "manager",
            "capture_scope": "configured_chat_all",
            "ingress_mode": "session_queue",
            "reply_mode": "topic_reply",
        }
        if authority_mode is not None:
            route["authority_mode"] = authority_mode
        return {"matched": True, "reason": "matched", "route": route}

    monkeypatch.setattr(runtime, "decide_lark_topic_event", decision)
    answer_calls: list[str] = []
    result = runtime.process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={
            "event_id": "evt_invalid_authority",
            "message_id": "om_invalid_authority",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "create_time": "2026-09-13T06:00:00Z",
            "content": "continue",
            "mentions": [],
            "sender_type": "user",
            "sender_id": "ou_owner_fixture",
        },
        runtime_root=tmp_path / "runtime",
        answer=lambda _route, _text: answer_calls.append("called"),
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("invalid authority must not send a reply")
        ),
    )
    assert result["status"] == "invalid_manager_authority_mode"
    assert result["source_acknowledged"] is False
    assert answer_calls == []


def test_manager_authorized_turn_quietly_recovers_history_as_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.extensions.lark import goal_topic_runtime as runtime

    state: dict[str, Any] = {}
    target_path = tmp_path / "goal-channel-targets.json"
    binding_path = tmp_path / "goal-channel.json"
    _seed_legacy_topic(target_path, binding_path)

    def decision(**options: Any) -> dict[str, Any]:
        event = options["event"]
        return {
            "matched": True,
            "reason": "matched",
            "route": {
                "app_ref": "mew",
                "goal_id": "goal-alpha",
                "message_id": event["message_id"],
                "target_ref": "fixture",
                "topic_root_message_id": "om_topic_alpha",
                "conversation_kind": "manager",
                "capture_scope": "configured_chat_all",
                "authority_mode": "turn_authorized",
                "ingress_mode": "session_queue",
                "reply_mode": "topic_reply",
            },
        }

    monkeypatch.setattr(runtime, "decide_lark_topic_event", decision)
    monkeypatch.setattr(
        runtime,
        "ensure_lark_event_inbox_received_reaction",
        lambda **_options: {"ok": True, "status": "retained"},
    )
    provider_calls: list[list[str]] = []

    def provider_runner(
        args: list[str], **_options: Any
    ) -> subprocess.CompletedProcess[str]:
        provider_calls.append(list(args))
        payload = {
            "ok": True,
            "identity": "bot",
            "data": {
                "messages": [
                    {
                        "message_id": "om_old_authorized",
                        "root_id": "om_topic_alpha",
                        "create_time": "2026-09-13T05:59:00Z",
                        "content": "@linkmacbot 历史请求只作背景",
                        "mentions": [{"id": "cli_public_fixture"}],
                        "sender_type": "user",
                        "deleted": False,
                    }
                ],
                "has_more": False,
                "page_token": "",
            },
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    answer_calls: list[dict[str, Any]] = []

    def answer(route: Mapping[str, Any], _text: str) -> str:
        answer_calls.append(dict(route))
        return "收到，历史内容仅作上下文。"

    result = runtime.process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={
            "goal-alpha": read_goal_channel_binding(binding_path)
        },
        event={
            "event_id": "evt_current_authorized",
            "message_id": "om_current_authorized",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "create_time": "2026-09-13T06:00:00Z",
            "content": "@linkmacbot 结合刚才内容回答",
            "mentions": [{"id": "cli_public_fixture"}],
            "sender_type": "user",
        },
        runtime_root=tmp_path / "runtime",
        answer=answer,
        reply_runner=_reply_runner(state),
        provider_runner=provider_runner,
    )

    assert result["status"] == "replied_and_acknowledged"
    assert result["context_sync_status"] == "observed"
    assert result["context_material_count"] == 1
    assert result["context_settled_count"] == 1
    assert answer_calls[0]["context_materials"][0]["message_id"] == (
        "om_old_authorized"
    )
    assert provider_calls
    assert all("reactions" not in call for call in provider_calls)
    config = json.loads(
        (tmp_path / "runtime" / result["inbox_config_ref"]).read_text()
    )
    assert config["topic_root_message_id"] == "om_topic_alpha"
    projection = inspect_lark_event_inbox(
        project=tmp_path / "runtime",
        config_path=Path(result["inbox_config_ref"]),
    )
    assert projection["pending_count"] == 0


def _connect_agent_session_topic(
    *, tmp_path: Path, state: dict[str, Any]
) -> tuple[Path, Path]:
    target_path = tmp_path / "goal-channel-targets.json"
    binding_path = tmp_path / "goal-channel.json"
    connected = connect_lark_goal_topic(
        registry={
            "goals": [
                {
                    "id": "goal-alpha",
                    "repo": str(tmp_path),
                    "objective": "Alpha delivery",
                    "coordination": {"registered_agents": ["agent-alpha"]},
                }
            ]
        },
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        session_id="session-alpha",
        target_path=target_path,
        binding_path=binding_path,
        app_ref="mew",
        chat_id="oc_public_fixture",
        chat_name="Product group",
        ingress_mode="session_queue",
        runner=_connection_runner(state),
        cli_bin="fake-lark",
    )
    assert connected["ok"] is True
    return target_path, binding_path


def test_agent_session_topic_does_not_reply_or_ack_without_durable_effect(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import process_lark_goal_topic_event

    state: dict[str, Any] = {}
    target_path, binding_path = _connect_agent_session_topic(
        tmp_path=tmp_path,
        state=state,
    )

    result = process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={
            "event_id": "evt_missing_effect",
            "message_id": "om_missing_effect",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "content": "@linkmacbot please continue",
        },
        runtime_root=tmp_path / "runtime",
        answer=lambda _route, _text: "work completed",
        reply_runner=_reply_runner(state),
    )

    assert result["ok"] is False, result
    assert result["status"] == "durable_effect_required"
    assert result["ack_decision"]["ack_allowed"] is False
    assert not any("+messages-reply" in call for call in state.get("calls", []))
    projection = inspect_lark_event_inbox(
        project=tmp_path / "runtime",
        config_path=Path(result["inbox_config_ref"]),
    )
    assert projection["pending_count"] == 1
    assert projection["processed_count"] == 0


def test_agent_session_topic_acks_after_effect_and_verified_reply(
    tmp_path: Path,
) -> None:
    from loopx.extensions.external_connector_runtime import (
        EFFECT_RECEIPT_SCHEMA_VERSION,
    )
    from loopx.extensions.lark.goal_topic_runtime import process_lark_goal_topic_event

    state: dict[str, Any] = {}
    target_path, binding_path = _connect_agent_session_topic(
        tmp_path=tmp_path,
        state=state,
    )

    def answer(route: dict[str, Any], _text: str) -> dict[str, Any]:
        assert route["event_id"] == "evt_effect_committed"
        return {
            "response_text": "work completed\n\n• Evidence verified\n• Next step recorded",
            "effect_receipt": {
                "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
                "event_id": route["event_id"],
                "effect_id": "effect-committed",
                "effect_kind": "todo_update",
                "status": "committed",
            },
        }

    result = process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={
            "event_id": "evt_effect_committed",
            "message_id": "om_effect_committed",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "content": "@linkmacbot please continue",
        },
        runtime_root=tmp_path / "runtime",
        answer=answer,
        reply_runner=_reply_runner(state),
    )

    assert result["ok"] is True
    assert result["status"] == "replied_and_acknowledged", result
    assert state["reply_text"] == "work completed\n\n• Evidence verified\n• Next step recorded"
    projection = inspect_lark_event_inbox(
        project=tmp_path / "runtime",
        config_path=Path(result["inbox_config_ref"]),
    )
    assert projection["pending_count"] == 0
    assert projection["processed_count"] == 1


def test_agent_scoped_async_inbox_queues_without_chat_reply_or_ack(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.extensions.lark.goal_topic_connections as connections
    from loopx.extensions.lark.goal_topic_runtime import process_lark_goal_topic_event

    state: dict[str, Any] = {}
    configured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        connections,
        "configure_goal_with_global_sync",
        lambda **kwargs: configured.append(kwargs) or {"ok": True},
    )
    target_path = tmp_path / "runtime" / "goal-channel-targets.json"
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry = {
        "goals": [
            {
                "id": "goal-alpha",
                "repo": str(tmp_path),
                "objective": "Alpha delivery",
                "coordination": {"registered_agents": ["agent-alpha"]},
            }
        ]
    }
    connected = connect_lark_goal_topic(
        registry=registry,
        registry_path=registry_path,
        goal_id="goal-alpha",
        agent_id="agent-alpha",
        target_path=target_path,
        binding_path=binding_path,
        app_ref="mew",
        chat_id="oc_public_fixture",
        chat_name="Product group",
        incoming_mode="mentions",
        ingress_mode="async_inbox",
        runner=_connection_runner(state),
        cli_bin="fake-lark",
    )

    assert connected["ok"] is True
    assert configured[0]["lark_event_inbox_agent_id"] == "agent-alpha"
    binding = binding_for_goal(read_goal_channel_binding(binding_path), "goal-alpha")
    assert binding is not None
    assert binding["agent_id"] == "agent-alpha"
    assert binding["routing"]["capture_scope"] == "addressed_only"
    assert binding["routing"]["ingress_mode"] == "async_inbox"
    assert binding["connector"]["schema_version"] == "agent_external_connector_v0"
    assert binding["connector"]["agent_ref"] == "agent-alpha"
    assert binding["connector"]["source_kind"] == "group_message"
    assert binding["connector"]["ingress_policy"] == "async_inbox"
    assert binding["connector"]["inbox_ref"] == binding["routing"]["inbox_config_ref"]
    assert binding["connector"]["cursor_ref"].endswith("/processed.json")
    config_ref = Path(binding["routing"]["inbox_config_ref"])
    assert (tmp_path / config_ref).is_file()
    config_payload = json.loads((tmp_path / config_ref).read_text(encoding="utf-8"))
    assert config_payload["capture_scope"] == "addressed_only"

    result = process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={
            "event_id": "evt_async",
            "message_id": "om_async",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "create_time": "2026-08-20T10:00:00Z",
            "content": "@linkmacbot please continue",
        },
        runtime_root=tmp_path / "runtime",
        goal_contexts={"goal-alpha": {"work_dir": str(tmp_path)}},
        answer=lambda *_args: (_ for _ in ()).throw(
            AssertionError("async inbox must not start a Chat turn")
        ),
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("async inbox must not reply before the Agent effect")
        ),
    )

    assert result["status"] == "queued_for_agent"
    assert result["agent_id"] == "agent-alpha"
    projection = inspect_lark_event_inbox(
        project=tmp_path,
        config_path=config_ref,
    )
    assert projection["pending_count"] == 1
    assert projection["processed_count"] == 0
    assert projection["thread_complete"] is False
    assert projection["coverage_warning"]
    assert "periodic-report" not in projection["instruction"]


def test_invalid_persisted_routing_state_never_answers_replies_or_acknowledges(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import process_lark_goal_topic_event

    target_path = tmp_path / "goal-channel-targets.json"
    binding_path = tmp_path / "goal-channel.json"
    connected = _seed_legacy_topic(target_path, binding_path)
    assert connected["ok"] is True
    binding = read_goal_channel_binding(binding_path)
    connection = binding_for_goal(binding, "goal-alpha")
    assert connection is not None
    binding["bindings"]["goal-alpha"]["connections"][connection["connection_id"]][
        "routing"
    ]["ingress_mode"] = "async-inbox"

    result = process_lark_goal_topic_event(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": binding},
        event={
            "event_id": "evt_invalid_routing",
            "message_id": "om_invalid_routing",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "content": "@linkmacbot please continue",
        },
        runtime_root=tmp_path / "runtime",
        answer=lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid routing must not start a Chat turn")
        ),
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("invalid routing must not reply")
        ),
    )

    assert result == {
        "ok": True,
        "status": "ignored",
        "reason": "invalid_routing_state",
    }
    assert not (tmp_path / "runtime" / ".loopx").exists()


def test_profile_stream_stops_when_its_durable_route_disappears(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile

    snapshot = {
        "target_payload": {
            "targets": {
                "mew-product": {
                    "name": "mew-product",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_fixture"},
                    "identity": {"sender_profile": "mew", "cli_bin": "fake-lark"},
                }
            }
        },
        "binding_payloads": {
            "goal-alpha": {
                "bindings": {
                    "goal-alpha": {
                        "goal_id": "goal-alpha",
                        "provider": "lark",
                        "enabled": True,
                        "target_ref": "mew-product",
                    }
                }
            }
        },
    }
    released = threading.Event()
    listening = threading.Event()
    result: dict[str, Any] = {}

    class BlockingLines:
        def __iter__(self):
            yield "[event] ready event_key=im.message.receive_v1\n"
            assert released.wait(5)

    class BlockingConsumer:
        stdout = BlockingLines()

        def poll(self):
            return 0 if released.is_set() else None

        def wait(self, timeout=None):
            assert released.wait(timeout or 5)
            return 0

        def terminate(self):
            released.set()

        def kill(self):
            released.set()

    stop = threading.Event()

    def run() -> None:
        result.update(
            stream_lark_goal_topic_profile(
                profile="mew",
                snapshot_provider=lambda: snapshot,
                stop=stop,
                runtime_root=tmp_path,
                answer=lambda _route, _text: "ok",
                process_factory=lambda _args: BlockingConsumer(),
                health_sink=lambda update: (
                    listening.set() if update.get("status") == "listening" else None
                ),
            )
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert listening.wait(2)
    snapshot["binding_payloads"] = {}
    worker.join(4)
    assert not worker.is_alive()
    assert stop.is_set()
    assert result == {
        "ok": True,
        "status": "configuration_removed",
        "event_count": 0,
        "replied_count": 0,
    }


def test_bound_topic_reuses_one_goal_chat_session(tmp_path: Path) -> None:
    from loopx.extensions.lark.goal_topic_runtime import answer_lark_goal_topic

    class FakeRuntime:
        def __init__(self) -> None:
            self.open_calls: list[dict[str, Any]] = []
            self.submit_calls: list[dict[str, Any]] = []

        def open_session(self, **kwargs: Any):
            self.open_calls.append(kwargs)
            return ({"session_id": "session-alpha"}, bool(len(self.open_calls) > 1))

        def submit_turn(self, **kwargs: Any):
            self.submit_calls.append(kwargs)
            return ({"turn_id": "turn-alpha"}, True)

        def wait_for_turn(self, **_kwargs: Any):
            return {
                "status": "completed",
                "response": {"message": "当前运行的是 LoopX 开发版。"},
            }

    runtime = FakeRuntime()
    route = {
        "app_ref": "mew",
        "goal_id": "goal-alpha",
        "target_ref": "mew-product",
        "topic_root_message_id": "om_topic_alpha",
        "message_id": "om_incoming",
    }
    first = answer_lark_goal_topic(
        route=route,
        text="@linkmacbot 你现在 loopx 的版本是什么",
        work_dir=tmp_path,
        objective="Alpha delivery",
        runtime_controller=runtime,
    )
    second = answer_lark_goal_topic(
        route={**route, "message_id": "om_incoming_2"},
        text="再确认一下",
        work_dir=tmp_path,
        objective="Alpha delivery",
        runtime_controller=runtime,
    )

    assert first == "当前运行的是 LoopX 开发版。"
    assert second == first
    assert runtime.open_calls[0]["mode"] == "resume_latest"
    assert runtime.open_calls[0]["channel_id"].startswith("lark.")
    assert runtime.open_calls[0]["channel_id"] == runtime.open_calls[1]["channel_id"]
    assert runtime.submit_calls[0]["client_turn_id"].startswith("lark.")
    assert "只生成预览" in runtime.submit_calls[0]["message"]


def test_runtime_service_uses_one_consumer_for_reused_app_profile(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import LarkGoalTopicRuntimeService

    started = threading.Event()
    stopped = threading.Event()
    profiles: list[str] = []
    target_payload = {
        "schema_version": "loopx_goal_channel_provider_targets_v0",
        "targets": {
            "mew-product": {
                "name": "mew-product",
                "provider": "lark",
                "enabled": True,
                "channel": {"chat_id": "oc_public_fixture", "chat_name": "Product"},
                "identity": {
                    "sender_profile": "mew",
                    "sender_identity": "bot",
                    "bot_app_id": "cli_public_fixture",
                    "bot_display_name": "linkmacbot",
                    "cli_bin": "fake-lark",
                },
            }
        },
    }
    binding_payloads = {
        goal_id: {
            "schema_version": "loopx_goal_channel_lark_binding_v0",
            "bindings": {
                goal_id: {
                    "goal_id": goal_id,
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": "mew-product",
                    "topic": {"root_message_id": f"om_{goal_id}"},
                    "routing": {
                        "incoming_mode": "mentions",
                        "reply_mode": "topic_reply",
                    },
                }
            },
        }
        for goal_id in ("goal-alpha", "goal-beta")
    }

    def poller(profile: str, _stop: threading.Event) -> None:
        profiles.append(profile)
        started.set()
        _stop.wait(2)
        stopped.set()

    service = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: {
            "target_payload": target_payload,
            "binding_payloads": binding_payloads,
            "goal_contexts": {},
        },
        runtime_root=tmp_path,
        runtime_controller=object(),
        profile_poller=poller,
    )
    service.refresh()

    assert started.wait(1)
    assert service.active_profiles() == ["mew"]
    assert profiles == ["mew"]

    service.close()
    assert stopped.wait(1)
    assert service.active_profiles() == []


def test_runtime_service_exposes_content_free_listener_health(tmp_path: Path) -> None:
    from loopx.extensions.lark.goal_topic_runtime import LarkGoalTopicRuntimeService

    started = threading.Event()
    release = threading.Event()
    snapshot = {
        "target_payload": {
            "schema_version": "loopx_goal_channel_provider_targets_v0",
            "targets": {
                "workspace-bot": {
                    "name": "workspace-bot",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_fixture"},
                    "identity": {
                        "sender_profile": "workspace-bot",
                        "sender_identity": "bot",
                        "bot_app_id": "cli_public_fixture",
                        "cli_bin": "fake-lark",
                    },
                }
            },
        },
        "binding_payloads": {
            "goal-alpha": {
                "schema_version": "loopx_goal_channel_lark_binding_v0",
                "bindings": {
                    "goal-alpha": {
                        "goal_id": "goal-alpha",
                        "provider": "lark",
                        "enabled": True,
                        "target_ref": "workspace-bot",
                        "topic": {"root_message_id": "om_topic_alpha"},
                    }
                },
            }
        },
    }

    def poller(_profile: str, stop: threading.Event) -> None:
        started.set()
        while not stop.is_set() and not release.wait(0.01):
            pass

    service = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path,
        runtime_controller=object(),
        profile_poller=poller,
    )
    service.refresh()

    assert started.wait(1)
    health = service.health_snapshot()["workspace-bot"]
    assert health["status"] in {"starting", "listening"}
    assert health["event_count"] == 0
    assert health["replied_count"] == 0
    assert health["last_event_status"] is None
    assert "path" not in health
    assert "message" not in health

    release.set()
    service.close()


def test_runtime_service_reconciles_manager_route_before_answer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import loopx.extensions.lark.goal_topic_runtime as runtime

    observed: list[str] = []
    stop = threading.Event()
    snapshot = {
        "target_payload": {},
        "binding_payloads": {},
        "goal_contexts": {"goal-alpha": {"work_dir": str(tmp_path)}},
    }

    def fake_stream(**kwargs: Any) -> dict[str, Any]:
        receipt = kwargs["answer"](
            {
                "goal_id": "goal-alpha",
                "conversation_kind": "manager",
                "session_id": "old-session",
            },
            "status",
        )
        assert receipt["response_text"] == "done"
        stop.set()
        return {"ok": True, "status": "stopped"}

    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", fake_stream)
    monkeypatch.setattr(
        runtime,
        "answer_lark_goal_topic",
        lambda **kwargs: observed.append(str(kwargs["route"]["session_id"])) or "done",
    )
    service = runtime.LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path,
        runtime_controller=object(),
        manager_route_reconciler=lambda route: {
            **route,
            "session_id": "new-session",
        },
    )
    service._poll_profile("mew", stop)
    assert observed == ["new-session"]


def test_runtime_service_records_safe_failure_code_for_listener_exception(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import loopx.extensions.lark.goal_topic_runtime as runtime

    attempted = threading.Event()
    release = threading.Event()
    snapshot = {
        "target_payload": {
            "schema_version": "loopx_goal_channel_provider_targets_v0",
            "targets": {
                "workspace-bot": {
                    "name": "workspace-bot",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_fixture"},
                    "identity": {
                        "sender_profile": "workspace-bot",
                        "sender_identity": "bot",
                        "bot_app_id": "cli_public_fixture",
                        "cli_bin": "fake-lark",
                    },
                }
            },
        },
        "binding_payloads": {
            "goal-alpha": {
                "bindings": {
                    "goal-alpha": {
                        "goal_id": "goal-alpha",
                        "provider": "lark",
                        "enabled": True,
                        "target_ref": "workspace-bot",
                        "topic": {"root_message_id": "om_topic_alpha"},
                    }
                }
            }
        },
    }

    def fail_stream(**_kwargs: Any) -> dict[str, Any]:
        attempted.set()
        release.wait(1)
        raise RuntimeError("private message and local path must not escape")

    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", fail_stream)
    service = runtime.LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path,
        runtime_controller=object(),
    )
    service.refresh()

    assert attempted.wait(1)
    release.set()
    for _ in range(100):
        health = service.health_snapshot().get("workspace-bot", {})
        if health.get("error_code") == "lark_event_listener_failed":
            break
        threading.Event().wait(0.01)
    assert health["status"] == "retrying"
    assert health["error_code"] == "lark_event_listener_failed"
    assert "private" not in json.dumps(health)
    assert str(tmp_path) not in json.dumps(health)
    service.close()


def test_profile_stream_keeps_one_consumer_open_between_messages(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile

    captured: dict[str, Any] = {}
    snapshot = {
        "target_payload": {
            "schema_version": "loopx_goal_channel_provider_targets_v0",
            "targets": {
                "mew-product": {
                    "name": "mew-product",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_fixture"},
                    "identity": {
                        "sender_profile": "mew",
                        "sender_identity": "bot",
                        "bot_app_id": "cli_public_fixture",
                        "cli_bin": "fake-lark",
                    },
                }
            },
        },
        "binding_payloads": {
            "goal-alpha": {
                "schema_version": "loopx_goal_channel_lark_binding_v0",
                "bindings": {
                    "goal-alpha": {
                        "goal_id": "goal-alpha",
                        "provider": "lark",
                        "enabled": True,
                        "target_ref": "mew-product",
                        "topic": {"root_message_id": "om_topic_alpha"},
                        "routing": {
                            "incoming_mode": "mentions",
                            "reply_mode": "topic_reply",
                        },
                    }
                },
            }
        },
    }

    class FinishedConsumer:
        stdout = iter(())

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("a completed consumer must not be terminated")

        def kill(self) -> None:
            raise AssertionError("a completed consumer must not be killed")

    def process_factory(args: list[str]) -> FinishedConsumer:
        captured["args"] = list(args)
        return FinishedConsumer()

    result = stream_lark_goal_topic_profile(
        profile="mew",
        snapshot_provider=lambda: snapshot,
        stop=threading.Event(),
        runtime_root=tmp_path,
        answer=lambda _route, _text: "ok",
        process_factory=process_factory,
    )

    timeout_index = captured["args"].index("--timeout")
    assert captured["args"][timeout_index + 1] == "30m"
    assert "--quiet" not in captured["args"]
    assert result == {
        "ok": False,
        "status": "stream_not_ready",
        "event_count": 0,
        "replied_count": 0,
    }


@pytest.mark.parametrize(
    "exit_reason,stop_requested,returncode",
    [("timeout", False, 0), ("limit", False, 0), ("signal", False, 0),
     ("unknown", False, 0), ("", False, 0), ("signal", True, 0),
     ("timeout", False, 1)],
)
def test_profile_stream_waits_for_provider_ready_before_reporting_listening(
    tmp_path: Path, exit_reason: str, stop_requested: bool, returncode: int,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile

    health: list[dict[str, Any]] = []
    snapshot = {
        "target_payload": {
            "targets": {
                "mew-product": {
                    "name": "mew-product",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_fixture"},
                    "identity": {
                        "sender_profile": "mew",
                        "sender_identity": "bot",
                        "bot_app_id": "cli_public_fixture",
                        "cli_bin": "fake-lark",
                    },
                }
            }
        },
        "binding_payloads": {
            "goal-alpha": {
                "bindings": {
                    "goal-alpha": {
                        "goal_id": "goal-alpha",
                        "provider": "lark",
                        "enabled": True,
                        "target_ref": "mew-product",
                        "topic": {"root_message_id": "om_topic_alpha"},
                    }
                }
            }
        },
    }

    class ReadyConsumer:
        stdout = iter(
            (
                "[event] local bus not found; checking remote connections...\n",
                "[event] ready event_key=im.message.receive_v1\n",
                f"[event] exited — received 0 event(s) in 2s (reason: {exit_reason})\n",
            )
        )

        def poll(self) -> int:
            return returncode

        def wait(self, timeout: float | None = None) -> int:
            return returncode

        def terminate(self) -> None:
            raise AssertionError("a completed consumer must not be terminated")

        def kill(self) -> None:
            raise AssertionError("a completed consumer must not be killed")

    stop = threading.Event()
    if stop_requested:
        stop.set()
    result = stream_lark_goal_topic_profile(
        profile="mew",
        snapshot_provider=lambda: snapshot,
        stop=stop,
        runtime_root=tmp_path,
        answer=lambda _route, _text: "ok",
        process_factory=lambda _args: ReadyConsumer(),
        health_sink=lambda update: health.append(dict(update)),
    )

    planned = stop_requested or (exit_reason in {"timeout", "limit"} and returncode == 0)
    assert result == {
        "ok": planned,
        "status": "stopped" if stop_requested else "stream_ended" if planned else "source_disconnected",
        **({} if planned else {"error_code": "lark_event_source_disconnected"}),
        "event_count": 0,
        "replied_count": 0,
    }
    assert [item["status"] for item in health] == (["starting"] if stop_requested else ["starting", "listening"])


def test_profile_poll_routes_provider_event_through_existing_reply_path(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import (
        poll_lark_goal_topic_profile_once,
    )

    state: dict[str, Any] = {}
    target_payload = {
        "schema_version": "loopx_goal_channel_provider_targets_v0",
        "targets": {
            "mew-product": {
                "name": "mew-product",
                "provider": "lark",
                "enabled": True,
                "channel": {"chat_id": "oc_public_fixture", "chat_name": "Product"},
                "identity": {
                    "sender_profile": "mew",
                    "sender_identity": "bot",
                    "bot_app_id": "cli_public_fixture",
                    "bot_display_name": "linkmacbot",
                    "cli_bin": "fake-lark",
                },
            }
        },
    }
    binding_payloads = {
        "goal-alpha": {
            "schema_version": "loopx_goal_channel_lark_binding_v0",
            "bindings": {
                "goal-alpha": {
                    "goal_id": "goal-alpha",
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": "mew-product",
                    "topic": {"root_message_id": "om_topic_alpha"},
                    "routing": {
                        "incoming_mode": "mentions",
                        "reply_mode": "topic_reply",
                    },
                }
            },
        }
    }
    event = {
        "event_id": "evt_incoming",
        "message_id": "om_incoming",
        "chat_id": "oc_public_fixture",
        "root_id": "om_topic_alpha",
        "parent_id": "om_topic_alpha",
        "thread_id": "omt_topic_alpha",
        "create_time": "2026-08-14T21:00:00Z",
        "content": "@_user_1 你现在 loopx 的版本是什么",
        "mentions": [
            {
                "key": "@_user_1",
                "id": "cli_public_fixture",
                "name": "linkmacbot",
            }
        ],
    }

    def consume_runner(args: list[str]) -> dict[str, Any]:
        state["consume_args"] = list(args)
        return {"returncode": 0, "stdout": json.dumps(event) + "\n", "stderr": ""}

    def provider_runner(
        args: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"event routing must not query message history: {args}")

    result = poll_lark_goal_topic_profile_once(
        profile="mew",
        snapshot={
            "target_payload": target_payload,
            "binding_payloads": binding_payloads,
            "goal_contexts": {},
        },
        runtime_root=tmp_path,
        answer=lambda _route, _text: "当前运行的是 LoopX 开发版。",
        consume_runner=consume_runner,
        provider_runner=provider_runner,
        reply_runner=_reply_runner(state),
    )

    assert result == {
        "ok": True,
        "status": "polled",
        "event_count": 1,
        "replied_count": 1,
        "event_statuses": ["replied_and_acknowledged"],
        "event_reasons": [None],
    }
    assert state["consume_args"][:3] == ["fake-lark", "--profile", "mew"]
    assert "event" in state["consume_args"]
    projection = state["consume_args"][state["consume_args"].index("--jq") + 1]
    assert "message_id:(.message_id // .id)" in projection
    assert "root_id:(.root_id // .message.root_id" in projection
    assert "parent_id:(.parent_id // .reply_to" in projection
    assert "thread_id" in projection
    assert "sender_id:(.sender_id // .sender.id // .sender.sender_id" in projection
    assert state["reply_text"] == "当前运行的是 LoopX 开发版。"


def test_profile_poll_scopes_chat_wide_capture_to_the_consuming_bot_target(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import (
        poll_lark_goal_topic_profile_once,
    )

    target_payload = {
        "schema_version": "loopx_goal_channel_provider_targets_v0",
        "targets": {
            "mew-product": {
                "name": "mew-product",
                "provider": "lark",
                "enabled": True,
                "channel": {"chat_id": "oc_public_fixture"},
                "identity": {
                    "sender_profile": "mew",
                    "bot_app_id": "cli_public_fixture",
                    "bot_display_name": "linkmacbot",
                    "cli_bin": "fake-lark",
                },
            },
            "owl-product": {
                "name": "owl-product",
                "provider": "lark",
                "enabled": True,
                "channel": {"chat_id": "oc_public_fixture"},
                "identity": {
                    "sender_profile": "owl",
                    "bot_app_id": "cli_other_fixture",
                    "bot_display_name": "owlbot",
                    "cli_bin": "fake-lark",
                },
            },
        },
    }
    binding_payloads: dict[str, Any] = {}
    for goal_id, target_ref, topic_root in (
        ("goal-alpha", "mew-product", "om_topic_alpha"),
        ("goal-beta", "owl-product", "om_topic_beta"),
    ):
        binding_payloads[goal_id] = {
            "schema_version": "loopx_goal_channel_lark_binding_v0",
            "bindings": {
                goal_id: {
                    "goal_id": goal_id,
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": target_ref,
                    "topic": {"root_message_id": topic_root},
                    "routing": {
                        "incoming_mode": "all",
                        "reply_mode": "topic_reply",
                    },
                }
            },
        }
    state: dict[str, Any] = {}
    event = {
        "event_id": "evt_cross_topic",
        "message_id": "om_cross_topic",
        "chat_id": "oc_public_fixture",
        "root_id": "om_new_topic",
        "content": "new topic message",
    }

    result = poll_lark_goal_topic_profile_once(
        profile="mew",
        snapshot={
            "target_payload": target_payload,
            "binding_payloads": binding_payloads,
            "goal_contexts": {},
        },
        runtime_root=tmp_path,
        answer=lambda route, _text: f"routed to {route['goal_id']}",
        consume_runner=lambda _args: {
            "returncode": 0,
            "stdout": json.dumps(event) + "\n",
            "stderr": "",
        },
        provider_runner=lambda _args: subprocess.CompletedProcess([], 0, "", ""),
        reply_runner=_reply_runner(state),
    )

    assert result["event_statuses"] == ["replied_and_acknowledged"]
    assert state["reply_text"] == "routed to goal-alpha"


def test_profile_poll_routes_the_only_goal_in_a_chat_without_querying_message_history(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import (
        poll_lark_goal_topic_profile_once,
    )

    snapshot = {
        "target_payload": {
            "schema_version": "loopx_goal_channel_provider_targets_v0",
            "targets": {
                "mew-product": {
                    "name": "mew-product",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_fixture"},
                    "identity": {
                        "sender_profile": "mew",
                        "sender_identity": "bot",
                        "bot_app_id": "cli_public_fixture",
                        "bot_display_name": "linkmacbot",
                        "cli_bin": "fake-lark",
                    },
                }
            },
        },
        "binding_payloads": {
            "goal-alpha": {
                "schema_version": "loopx_goal_channel_lark_binding_v0",
                "bindings": {
                    "goal-alpha": {
                        "goal_id": "goal-alpha",
                        "provider": "lark",
                        "enabled": True,
                        "target_ref": "mew-product",
                        "topic": {"root_message_id": "om_topic_alpha"},
                        "routing": {
                            "incoming_mode": "all",
                            "reply_mode": "topic_reply",
                        },
                    }
                },
            }
        },
    }
    event = {
        "event_id": "evt_incoming",
        "message_id": "om_incoming",
        "chat_id": "oc_public_fixture",
        "content": "hello",
    }

    def provider_runner(
        args: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"event routing must not query message history: {args}")

    result = poll_lark_goal_topic_profile_once(
        profile="mew",
        snapshot=snapshot,
        runtime_root=tmp_path / "runtime",
        answer=lambda route, text: (
            "goal-alpha received hello"
            if route.get("goal_id") == "goal-alpha" and text == "hello"
            else "unexpected route"
        ),
        consume_runner=lambda _args: {
            "returncode": 0,
            "stdout": json.dumps(event) + "\n",
            "stderr": "",
        },
        provider_runner=provider_runner,
        reply_runner=_reply_runner({}),
    )

    assert result["event_count"] == 1
    assert result["replied_count"] == 1, result
    assert result["event_statuses"] == ["replied_and_acknowledged"]


def test_profile_poll_reports_ambiguous_topic_context_without_querying_message_history(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import (
        poll_lark_goal_topic_profile_once,
    )

    target = {
        "name": "mew-product",
        "provider": "lark",
        "enabled": True,
        "channel": {"chat_id": "oc_public_fixture"},
        "identity": {
            "sender_profile": "mew",
            "bot_app_id": "cli_public_fixture",
            "cli_bin": "fake-lark",
        },
    }
    binding_payloads: dict[str, Any] = {}
    for goal_id, topic_root in (
        ("goal-alpha", "om_topic_alpha"),
        ("goal-beta", "om_topic_beta"),
    ):
        binding_payloads[goal_id] = {
            "schema_version": "loopx_goal_channel_lark_binding_v0",
            "bindings": {
                goal_id: {
                    "goal_id": goal_id,
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": "mew-product",
                    "topic": {"root_message_id": topic_root},
                    "routing": {
                        "incoming_mode": "mentions",
                        "reply_mode": "topic_reply",
                    },
                }
            },
        }
    event = {
        "event_id": "evt_incoming",
        "message_id": "om_incoming",
        "chat_id": "oc_public_fixture",
        "content": "hello",
    }

    def provider_runner(
        args: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"event routing must not query message history: {args}")

    result = poll_lark_goal_topic_profile_once(
        profile="mew",
        snapshot={
            "target_payload": {
                "schema_version": "loopx_goal_channel_provider_targets_v0",
                "targets": {"mew-product": target},
            },
            "binding_payloads": binding_payloads,
        },
        runtime_root=tmp_path / "runtime",
        answer=lambda _route, _text: "must not reply",
        consume_runner=lambda _args: {
            "returncode": 0,
            "stdout": json.dumps(event) + "\n",
            "stderr": "",
        },
        provider_runner=provider_runner,
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("must not reply")
        ),
    )

    assert result["event_count"] == 1
    assert result["replied_count"] == 0
    assert result["event_statuses"] == ["topic_context_ambiguous"]
    assert not (tmp_path / "runtime").exists()


def test_profile_poll_does_not_reply_or_invoke_agent_when_message_mentions_other_user_or_all(
    tmp_path: Path,
) -> None:
    from loopx.extensions.lark.goal_topic_runtime import (
        poll_lark_goal_topic_profile_once,
    )

    target = {
        "name": "mew-product",
        "provider": "lark",
        "enabled": True,
        "channel": {"chat_id": "oc_public_fixture"},
        "identity": {
            "sender_profile": "mew",
            "sender_identity": "bot",
            "bot_app_id": "cli_public_fixture",
            "bot_display_name": "linkmacbot",
            "cli_bin": "fake-lark",
        },
    }
    binding_payloads = {
        "goal-alpha": {
            "schema_version": "loopx_goal_channel_lark_binding_v0",
            "bindings": {
                "goal-alpha": {
                    "goal_id": "goal-alpha",
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": "mew-product",
                    "topic": {"root_message_id": "om_topic_alpha"},
                    "routing": {
                        "incoming_mode": "mentions",
                        "reply_mode": "topic_reply",
                    },
                }
            },
        }
    }

    # Case A: Message mentions another user
    other_user_event = {
        "event_id": "evt_other_user",
        "message_id": "om_other_user",
        "chat_id": "oc_public_fixture",
        "root_id": "om_topic_alpha",
        "content": "@Alice 请看一下这个 PR",
        "mentions": [{"name": "Alice", "id": "ou_alice_999"}],
    }

    answer_called = False

    def answer_spy(_route: Any, _text: Any) -> str:
        nonlocal answer_called
        answer_called = True
        return "must not reply"

    result_other = poll_lark_goal_topic_profile_once(
        profile="mew",
        snapshot={
            "target_payload": {
                "schema_version": "loopx_goal_channel_provider_targets_v0",
                "targets": {"mew-product": target},
            },
            "binding_payloads": binding_payloads,
        },
        runtime_root=tmp_path / "runtime-other",
        answer=answer_spy,
        consume_runner=lambda _args: {
            "returncode": 0,
            "stdout": json.dumps(other_user_event) + "\n",
            "stderr": "",
        },
        provider_runner=lambda _args: subprocess.CompletedProcess([], 0, "", ""),
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("must not reply")
        ),
    )

    assert result_other["event_count"] == 1
    assert result_other["replied_count"] == 0
    assert result_other["event_statuses"] == ["ignored"]
    assert not answer_called

    # Case B: Message mentions @_all
    all_event = {
        "event_id": "evt_all",
        "message_id": "om_all",
        "chat_id": "oc_public_fixture",
        "root_id": "om_topic_alpha",
        "content": "@_all 大家同步一下进度",
        "mentions": [{"key": "@_all", "name": "所有人"}],
    }

    result_all = poll_lark_goal_topic_profile_once(
        profile="mew",
        snapshot={
            "target_payload": {
                "schema_version": "loopx_goal_channel_provider_targets_v0",
                "targets": {"mew-product": target},
            },
            "binding_payloads": binding_payloads,
        },
        runtime_root=tmp_path / "runtime-all",
        answer=answer_spy,
        consume_runner=lambda _args: {
            "returncode": 0,
            "stdout": json.dumps(all_event) + "\n",
            "stderr": "",
        },
        provider_runner=lambda _args: subprocess.CompletedProcess([], 0, "", ""),
        reply_runner=lambda _args: (_ for _ in ()).throw(
            AssertionError("must not reply")
        ),
    )

    assert result_all["event_count"] == 1
    assert result_all["replied_count"] == 0
    assert result_all["event_statuses"] == ["ignored"]
    assert not answer_called


@pytest.mark.parametrize("reaction_ok", [True, False, None])
def test_manager_receives_reaction_before_answer_and_preserves_sender(tmp_path, monkeypatch, reaction_ok):
    from loopx.extensions.lark import goal_topic_runtime as runtime
    target_path, binding_path = tmp_path / "targets.json", tmp_path / "bindings.json"
    _seed_legacy_topic(target_path, binding_path)
    original_decide = runtime.decide_lark_topic_event
    def manager_decision(**kw):
        result = original_decide(**kw)
        result["route"] = {
            **result["route"],
            "conversation_kind": "manager",
            "authority_mode": "turn_authorized",
            "ingress_mode": "session_queue",
        }
        return result
    monkeypatch.setattr(runtime, "decide_lark_topic_event", manager_decision)
    state = {}
    runner = _reply_runner(state)
    stages = []
    if reaction_ok is None:
        def unavailable(**kw):
            raise OSError("private receipt unavailable")
        monkeypatch.setattr(runtime, "ensure_lark_event_inbox_received_reaction", unavailable)
    def with_reactions(args):
        if args[3:6] == ["im", "reactions", "create"]:
            stages.append("received")
            assert args[args.index("--message-id") + 1] == "om_incoming"
            assert json.loads(args[args.index("--data") + 1])["reaction_type"]["emoji_type"] == "Get"
            return {"returncode": 0 if reaction_ok else 1, "stdout": json.dumps({"ok": reaction_ok, "data": {"reaction_id": "reaction_fixture"}})}
        if args[3:6] == ["im", "reactions", "delete"]:
            stages.append("cleanup")
            return {"returncode": 0, "stdout": json.dumps({"ok": True})}
        return runner(args)
    def answer(route, text):
        stages.append("answer")
        assert stages == (["answer"] if reaction_ok is None else ["received", "answer"])
        assert route["source_sender_id"] == "ou_owner_fixture"
        return "Received the original intent."
    kwargs = dict(target_payload=read_goal_channel_targets(target_path),
                  binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
                  event={"event_id": "evt_incoming", "message_id": "om_incoming", "chat_id": "oc_public_fixture",
                         "root_id": "om_topic_alpha", "create_time": "2026-08-14T21:00:00Z",
                         "content": "@linkmacbot forward this", "mentioned": True, "sender_type": "user", "sender_id": "ou_owner_fixture"},
                  runtime_root=tmp_path / "runtime", answer=answer, reply_runner=with_reactions)
    result = runtime.process_lark_goal_topic_event(**kwargs)
    assert result["ok"], result
    assert result["status"] == "replied_and_acknowledged"
    assert "cleanup" not in stages  # Manager receipt ACK survives its answer.
    if reaction_ok:
        from loopx.extensions.lark.event_inbox import load_lark_event_inbox_config
        from loopx.extensions.lark.inbox_reactions import lark_inbox_reaction_receipts
        config = load_lark_event_inbox_config(
            project=kwargs["runtime_root"], config_path=result["inbox_config_ref"])
        assert config["reply"]["received_reaction_policy"] == "retain"
        assert lark_inbox_reaction_receipts(
            inbox=config["inbox_path"], message_id="om_incoming")["received"]["emoji_type"] == "Get"
    before = list(stages)
    assert runtime.process_lark_goal_topic_event(**kwargs)["status"] == "already_acknowledged"
    assert stages == before


@pytest.mark.parametrize("error_code,label", [
    ("cyber_policy", "安全策略拦截"),
    ("rate_limit_exceeded", "请求频率限制"),
    ("private-upstream-detail", "管家处理失败"),
])
@pytest.mark.parametrize("reply_ok", [True, False])
def test_manager_terminal_failure_replies_once_before_ack(
    tmp_path, monkeypatch, error_code, label, reply_ok,
):
    from loopx.extensions.lark import goal_topic_runtime as runtime

    target_path, binding_path = tmp_path / "targets.json", tmp_path / "bindings.json"
    _seed_legacy_topic(target_path, binding_path)
    original_decide = runtime.decide_lark_topic_event
    def manager_decision(**kw):
        result = original_decide(**kw)
        result["route"].update(
            conversation_kind="manager", ingress_mode="session_queue",
            authority_mode="turn_authorized",
            event_id=kw["event"]["event_id"],
            connector={"response_policy": "topic_reply"},
        )
        return result
    monkeypatch.setattr(runtime, "decide_lark_topic_event", manager_decision)
    monkeypatch.setattr(runtime, "ensure_lark_event_inbox_received_reaction",
                        lambda **kw: {"ok": True, "status": "already_received"})
    state = {}
    runner = _reply_runner(state)
    calls = []
    def answer(route, text):
        calls.append(text)
        raise runtime.LarkGoalTopicTurnFailed(error_code, runtime._session_turn_effect(route))
    def reply(args):
        if not reply_ok and "+messages-reply" in args and "--dry-run" not in args:
            return {"returncode": 1, "stdout": "", "stderr": "private-transport-detail"}
        return runner(args)
    kwargs = dict(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={"event_id": "evt_incoming", "message_id": "om_incoming",
               "chat_id": "oc_public_fixture", "root_id": "om_topic_alpha",
               "create_time": "2026-08-14T21:00:00Z", "content": "@linkmacbot report",
               "mentioned": True, "sender_type": "user"},
        runtime_root=tmp_path / "runtime", answer=answer, reply_runner=reply,
    )
    result = runtime.process_lark_goal_topic_event(**kwargs)
    assert result["ok"] is False
    assert label in state["reply_text"]
    assert "private-" not in state["reply_text"] + str(result)
    assert "不会自动重放" in state["reply_text"]
    pending = inspect_lark_event_inbox(project=kwargs["runtime_root"],
                                      config_path=Path(result["inbox_config_ref"]))
    if reply_ok:
        assert result["status"] == "processing_failed"
        assert result["failure_reply_verified"] and result["source_acknowledged"]
        assert pending["items"] == []
        assert runtime.process_lark_goal_topic_event(**kwargs)["status"] == "already_acknowledged"
        assert len(calls) == 1
    else:
        assert any(x["message_id"] == "om_incoming" for x in pending["items"])
        assert not result.get("source_acknowledged")


@pytest.mark.parametrize("answer_value", ["raise", "empty"])
def test_manager_untyped_or_empty_answer_gets_bounded_failure_receipt(
    tmp_path, monkeypatch, answer_value,
):
    from loopx.extensions.lark import goal_topic_runtime as runtime

    target_path, binding_path = tmp_path / "targets.json", tmp_path / "bindings.json"
    _seed_legacy_topic(target_path, binding_path)
    original_decide = runtime.decide_lark_topic_event

    def manager_decision(**kw):
        result = original_decide(**kw)
        result["route"].update(
            conversation_kind="manager", ingress_mode="session_queue",
            authority_mode="turn_authorized",
            event_id=kw["event"]["event_id"],
            connector={"response_policy": "topic_reply"},
        )
        return result

    monkeypatch.setattr(runtime, "decide_lark_topic_event", manager_decision)
    monkeypatch.setattr(
        runtime,
        "ensure_lark_event_inbox_received_reaction",
        lambda **kw: {"ok": True, "status": "already_received"},
    )
    state, answer_calls = {}, []

    def answer(route, text):
        answer_calls.append(text)
        if answer_value == "raise":
            raise ValueError("private provider detail")
        return ""

    kwargs = {
        "target_payload": read_goal_channel_targets(target_path),
        "binding_payloads": {"goal-alpha": read_goal_channel_binding(binding_path)},
        "event": {
            "event_id": "evt_incoming",
            "message_id": "om_incoming",
            "chat_id": "oc_public_fixture",
            "root_id": "om_topic_alpha",
            "create_time": "2026-08-14T21:00:00Z",
            "content": "@linkmacbot report",
            "mentioned": True,
            "sender_type": "user",
        },
        "runtime_root": tmp_path / "runtime",
        "answer": answer,
        "reply_runner": _reply_runner(state),
    }

    result = runtime.process_lark_goal_topic_event(**kwargs)

    assert result["ok"] is False
    assert result["status"] == "processing_failed"
    assert result["failure_reply_verified"] is True
    assert result["source_acknowledged"] is True
    assert "不会自动重放" in state["reply_text"]
    assert "private provider detail" not in state["reply_text"]
    assert len(answer_calls) == 1
    pending = inspect_lark_event_inbox(
        project=kwargs["runtime_root"],
        config_path=Path(result["inbox_config_ref"]),
    )
    assert pending["items"] == []
    assert runtime.process_lark_goal_topic_event(**kwargs)["status"] == "already_acknowledged"
    assert len(answer_calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "完整报告" * 400,
        "x" * 6001,
        "测" * 40000,
        "测" * 50000,
        r"private\nformat",
        r"{new_description}\n- 条目",
    ],
    ids=[
        "report",
        "long-ascii",
        "long-unicode",
        "oversize",
        "repaired-newlines",
        "unresolved-placeholder",
    ],
)
@pytest.mark.parametrize("reply_ok", [True, False])
def test_manager_report_delivery_recovers_safe_format_and_keeps_pending_body(
    tmp_path, monkeypatch, body, reply_ok,
):
    from loopx.extensions.lark import goal_topic_runtime as runtime
    from loopx.extensions.lark.inbox_reply import send_lark_inbox_message
    from loopx.extensions.lark.outbound import LarkOutboundTextError

    target_path, binding_path = tmp_path / "targets.json", tmp_path / "bindings.json"
    _seed_legacy_topic(target_path, binding_path)
    original_decide = runtime.decide_lark_topic_event

    def manager_decision(**kw):
        result = original_decide(**kw)
        result["route"].update(
            conversation_kind="manager", ingress_mode="session_queue",
            authority_mode="turn_authorized",
            event_id=kw["event"]["event_id"],
            connector={"response_policy": "topic_reply"},
        )
        return result

    monkeypatch.setattr(runtime, "decide_lark_topic_event", manager_decision)
    monkeypatch.setattr(runtime, "ensure_lark_event_inbox_received_reaction",
                        lambda **kw: {"ok": True, "status": "already_received"})
    state, answered = {}, []
    runner = _reply_runner(state)

    def answer(route, text):
        answered.append(text)
        return {"response_text": body, "effect_receipt": runtime._session_turn_effect(route)}

    def reply(args):
        if not reply_ok and "+messages-reply" in args and "--dry-run" not in args:
            return {"returncode": 1}
        return runner(args)

    kwargs = dict(
        target_payload=read_goal_channel_targets(target_path),
        binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)},
        event={"event_id": "evt_incoming", "message_id": "om_incoming",
               "chat_id": "oc_public_fixture", "root_id": "om_topic_alpha",
               "create_time": "2026-08-14T21:00:00Z", "content": "@linkmacbot report",
               "mentioned": True, "sender_type": "user"},
        runtime_root=tmp_path / "runtime", answer=answer, reply_runner=reply,
    )
    result = runtime.process_lark_goal_topic_event(**kwargs)
    sendable = len(body.encode("utf-8")) < 150_000
    if body == r"private\nformat":
        # Escaped newlines are repaired before the first send, so the answer is
        # delivered as markdown (a real bullet list) and is not reported as a
        # degraded delivery.
        expected = "private\nformat"
        expected_repairs = ["escaped_newline"]
    elif body == r"{new_description}\n- 条目":
        # A placeholder the template never filled reaches the reader as a typed
        # marker rather than as raw braces, and the escaped bullet list is a
        # real list.
        expected = "[未解析占位符: new_description]\n- 条目"
        expected_repairs = ["escaped_newline", "unresolved_template_placeholder"]
    else:
        expected = body
        expected_repairs = []
    assert [
        incident["code"] for incident in result.get("rich_text_repairs") or []
    ] == expected_repairs
    if sendable:
        assert state["reply_text"] == expected
        assert result["ok"] is reply_ok
        assert result.get("format_degraded") is False
    else:
        # Longer than one deliverable message: the persisted answer is sent as a
        # bounded, ordered sequence of parts that ends with the overflow note,
        # instead of being left undelivered as `reply_format_invalid`.
        assert result["ok"] is reply_ok
        assert result.get("format_degraded") is True
        if reply_ok:
            assert result["delivery_part_count"] == MANAGER_REPLY_MAX_PARTS
            assert result["delivery_parts_sent"] == MANAGER_REPLY_MAX_PARTS
            assert state["reply_text"].startswith("(8/8) ")
            assert MANAGER_REPLY_OVERFLOW_NOTE in state["reply_text"]
        else:
            assert result["status"] == "reply_delivery_pending"
            assert result["reason"] == "reply_part_delivery_incomplete"
            assert result["delivery_parts_sent"] == 0
            assert result["source_acknowledged"] is False
    pending = inspect_lark_event_inbox(project=kwargs["runtime_root"],
                                      config_path=Path(result["inbox_config_ref"]))
    if reply_ok:
        assert pending["items"] == []
        assert runtime.process_lark_goal_topic_event(**kwargs)["status"] == "already_acknowledged"
        assert len(answered) == 1
    else:
        assert any(x["message_id"] == "om_incoming" for x in pending["items"])
        retried = runtime.process_lark_goal_topic_event(**kwargs)
        assert retried["status"] == "reply_delivery_pending"
        assert len(answered) == 1
    # Root notifications retain their existing compact limit.
    with pytest.raises(LarkOutboundTextError):
        send_lark_inbox_message(project=kwargs["runtime_root"],
                               config_path=result["inbox_config_ref"], text="x" * 1201)


def test_manager_delivery_reuses_saved_answer_after_transport_restart(
    tmp_path, monkeypatch,
):
    from loopx.extensions.lark import goal_topic_runtime as runtime

    target_path, binding_path = tmp_path / "targets.json", tmp_path / "bindings.json"
    _seed_legacy_topic(target_path, binding_path)
    original_decide = runtime.decide_lark_topic_event
    original_acknowledge = runtime.acknowledge_lark_event_inbox

    def manager_decision(**kwargs):
        result = original_decide(**kwargs)
        result["route"].update(
            conversation_kind="manager",
            ingress_mode="session_queue",
            authority_mode="turn_authorized",
            event_id=kwargs["event"]["event_id"],
            connector={"response_policy": "topic_reply"},
        )
        return result

    monkeypatch.setattr(runtime, "decide_lark_topic_event", manager_decision)
    monkeypatch.setattr(
        runtime,
        "ensure_lark_event_inbox_received_reaction",
        lambda **_kwargs: {"ok": True, "status": "already_received"},
    )
    event = {
        "event_id": "evt_incoming",
        "message_id": "om_incoming",
        "chat_id": "oc_public_fixture",
        "root_id": "om_topic_alpha",
        "create_time": "2026-08-14T21:00:00Z",
        "content": "@linkmacbot report",
        "mentioned": True,
        "sender_type": "user",
    }
    answer_calls = []

    def answer(route, text):
        answer_calls.append(text)
        return {
            "response_text": r"完整答复\n@LoopX 管家仅为显示文本",
            "effect_receipt": runtime._session_turn_effect(route),
        }

    first_state = {}
    working_runner = _reply_runner(first_state)

    def unavailable_runner(args):
        if "+messages-reply" in args and "--dry-run" not in args:
            return {"returncode": 1, "stdout": "", "stderr": "unavailable"}
        return working_runner(args)

    kwargs = {
        "target_payload": read_goal_channel_targets(target_path),
        "binding_payloads": {
            "goal-alpha": read_goal_channel_binding(binding_path)
        },
        "event": event,
        "runtime_root": tmp_path / "runtime",
        "answer": answer,
        "reply_runner": unavailable_runner,
    }

    first = runtime.process_lark_goal_topic_event(**kwargs)

    assert first["status"] == "reply_delivery_pending"
    assert first["source_acknowledged"] is False
    assert len(answer_calls) == 1
    delivery_files = list(
        (tmp_path / "runtime" / ".loopx" / "inbox").glob(
            "**/manager-delivery/om_incoming.json"
        )
    )
    assert len(delivery_files) == 1
    assert json.loads(delivery_files[0].read_text())["status"] == "pending"

    second_state = {}
    kwargs["reply_runner"] = _reply_runner(second_state)
    kwargs["answer"] = lambda *_args: (_ for _ in ()).throw(
        AssertionError("saved answer must be reused")
    )
    monkeypatch.setattr(
        runtime,
        "acknowledge_lark_event_inbox",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("ack unavailable")),
    )
    with pytest.raises(OSError, match="ack unavailable"):
        runtime.process_lark_goal_topic_event(**kwargs)

    assert second_state["reply_text"] == "完整答复\n＠LoopX 管家仅为显示文本"
    assert json.loads(delivery_files[0].read_text())["status"] == "sent_verified"

    monkeypatch.setattr(
        runtime, "acknowledge_lark_event_inbox", original_acknowledge
    )
    third_state = {}
    third_runner = _reply_runner(third_state)

    def no_duplicate_send(args):
        if "+messages-reply" in args:
            raise AssertionError("verified reply must not be sent twice")
        return third_runner(args)

    kwargs["reply_runner"] = no_duplicate_send
    second = runtime.process_lark_goal_topic_event(**kwargs)

    assert second["ok"] is True
    assert second["status"] == "replied_and_acknowledged"
    assert second["saved_response_reused"] is True
    assert second["format_degraded"] is True
    receipt = json.loads(delivery_files[0].read_text())
    assert receipt["status"] == "acknowledged"
    assert "delivery_text" not in receipt
    assert inspect_lark_event_inbox(
        project=kwargs["runtime_root"], config_path=Path(second["inbox_config_ref"])
    )["items"] == []
