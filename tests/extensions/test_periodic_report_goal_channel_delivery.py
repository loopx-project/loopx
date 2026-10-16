from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.periodic_report import (
    build_periodic_report_document,
    build_periodic_report_generation_bundle,
    build_periodic_report_source_result,
)
from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    write_goal_channel_binding,
)
from loopx.extensions.lark.goal_channel_targets import add_lark_goal_channel_target
from loopx.capabilities.periodic_report.machine_store import (
    configure_periodic_report_machine_defaults,
    read_periodic_report_machine_defaults,
)
from loopx.capabilities.periodic_report.machine_defaults import (
    build_periodic_report_delivery_authority,
    resolve_goal_periodic_report_subscription,
)
from loopx.extensions.lark.periodic_report_delivery import (
    ANNOUNCEMENT_IDEMPOTENCY_SCHEMA,
    DELIVERY_INTENT_SCHEMA,
    GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA,
    deliver_periodic_report_to_goal_channel,
)
from loopx.extensions.lark.presentation.message_card import (
    build_lark_markdown_reply_card,
)
from loopx.capabilities.periodic_report.incremental import (
    build_periodic_report_publication_candidate,
    read_periodic_report_publication_cursor,
    write_periodic_report_publication_candidate,
)
from loopx.extensions.lark import periodic_report_cli
from loopx.extensions.manifest import load_extension_manifest
from loopx.presentation.renderers.periodic_report_markdown import (
    periodic_report_markdown_renderer_adapter,
)


ROOT = Path(__file__).resolve().parents[2]
LARK_EXTENSION_VERSION = str(
    load_extension_manifest(ROOT / "loopx/extensions/lark/extension.toml")["provider"][
        "version"
    ]
)
GOAL_ID = "goal-public-fixture"
CHAT_ID = "oc_public_fixture"
APP_ID = "cli_public_fixture"
MESSAGE_ID = "om_periodic_report_fixture"


def _generation_bundle(*, period_window: dict[str, str] | None = None) -> dict[str, Any]:
    source = build_periodic_report_source_result(
        source_id="project_progress",
        source_kind="project_progress",
        status="complete",
        observed_at="2026-08-30T09:00:00Z",
        sections=[
            {
                "section_id": "next_actions",
                "title": "下一步",
                "order": 40,
                "items": [
                    {
                        "item_id": "next_action",
                        "title": "推进下一个已验证步骤",
                        "summary": "完成事实复核后推进下一个明确步骤。",
                        "content_kind": "next_action",
                        "value_rank": 90,
                    }
                ],
            }
        ],
    )
    document = build_periodic_report_document(
        title="阶段分析周报",
        generated_at="2026-08-30T09:00:00Z",
        period_window=period_window or {
            "start_at": "2026-08-29T11:24:00Z",
            "end_at": "2026-08-30T01:55:00Z",
        },
        profile={"profile_id": "weekly_progress", "profile_version": "v1"},
        sources=[source],
    )
    return build_periodic_report_generation_bundle(
        document=document,
        artifacts=[periodic_report_markdown_renderer_adapter().render(document)],
    )


def _goal(*, route_ref: str = "loopx-concierge") -> dict[str, Any]:
    return {
        "id": GOAL_ID,
        "control_plane": {
            "periodic_report": {
                "enabled": True,
                "profile_preset": "weekly-progress",
                "route_ref": route_ref,
                "timezone": "Asia/Shanghai",
            }
        },
    }


def _authority(
    *,
    goal: dict[str, Any] | None = None,
    machine_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_periodic_report_delivery_authority(
        resolve_goal_periodic_report_subscription(
            goal or _goal(),
            machine_defaults,
        )
    )


def _request(*, delivery_authority: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA,
        "generation_bundle": _generation_bundle(),
        "delivery_authority": delivery_authority or _authority(),
        "delivery_intent": {
            "schema_version": DELIVERY_INTENT_SCHEMA,
            "kind": "goal_channel",
            "sink_id": "lark_delivery",
            "sink_kind": "lark_message",
            "idempotency_key": "periodic-report:goal-public-fixture:stage-1",
            "announcements": [
                {
                    "kind": "hosted_report",
                    "title": "阶段周报",
                    "url": "https://example.com/reports/stage-1",
                },
                {
                    "kind": "lark_document",
                    "title": "配套 Lark 文档",
                    "url": "https://example.larksuite.com/docx/stage-1",
                },
            ],
        },
    }


def _write_registry(
    registry_path: Path,
    *,
    goal: dict[str, Any] | None = None,
    runtime_root: Path | None = None,
) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"goals": [goal or _goal()]}
    if runtime_root is not None:
        payload["common_runtime_root"] = str(runtime_root)
    registry_path.write_text(json.dumps(payload), encoding="utf-8")


def _extension_activation() -> dict[str, Any]:
    return {
        "schema_version": "loopx_extension_activation_v0",
        "extension_id": "loopx-lark",
        "provider_version": LARK_EXTENSION_VERSION,
        "revision": "publicfixture123",
        "enabled": True,
        "doctor_verified": True,
        "required_permissions": ["lark.goal_channel.manage"],
    }


def _write_publication_candidate(
    runtime_root: Path, generation_bundle: dict[str, Any], *, trigger_ids: list[str] | None = None
) -> None:
    candidate = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id="example-agent",
        generation_id=generation_bundle["generation_receipt"]["generation_id"],
        trigger_receipt={"coalesced_trigger_ids": trigger_ids or ["trigger_stage_1"]},
        facts=[
            {
                "source_ref": "todo:stage_1",
                "title": "Stage 1 completed",
                "summary": "The stage outcome was validated.",
                "content_kind": "outcome",
                "status": "done",
            }
        ],
        baseline=None,
    )
    path = (
        runtime_root
        / "goals"
        / GOAL_ID
        / "periodic_reports"
        / "fixture"
        / "publication-candidate.json"
    )
    write_periodic_report_publication_candidate(path=path, candidate=candidate)


def test_goal_channel_readback_advances_publication_cursor_only_on_success(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    runtime_root = tmp_path / "runtime"
    _write_binding(registry_path)
    request = _request()
    _write_publication_candidate(runtime_root, request["generation_bundle"])

    preview = deliver_periodic_report_to_goal_channel(
        request,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
    )
    assert preview["publication_cursor"] is None
    assert (
        read_periodic_report_publication_cursor(
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            agent_id="example-agent",
        )
        is None
    )

    delivered = deliver_periodic_report_to_goal_channel(
        request,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=_runner([]),
    )
    cursor = delivered["publication_cursor"]
    assert (
        cursor["generation_id"]
        == request["generation_bundle"]["generation_receipt"]["generation_id"]
    )
    assert cursor["covered_trigger_ids"] == ["trigger_stage_1"]


def _write_binding(
    registry_path: Path,
    *,
    mode: str = "project_bot",
    app_id: str = APP_ID,
    target_ref: str = "loopx-concierge",
) -> None:
    add_lark_goal_channel_target(
        target_path=registry_path.parent.parent
        / "runtime"
        / "goal-channel-targets.json",
        target_name=target_ref,
        chat_id=CHAT_ID,
        chat_name="LoopX Concierge",
        identity_mode=mode,
        sender_profile="project-reporter",
        sender_identity="bot",
        bot_app_id=app_id,
        bot_display_name="Project Reporter",
        cli_bin="lark-cli",
        execute=True,
    )
    write_goal_channel_binding(
        registry_path.parent / "goal-channel.json",
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {
                GOAL_ID: {
                    "goal_id": GOAL_ID,
                    "provider": "lark",
                    "enabled": True,
                    "target_ref": target_ref,
                    "channel": {"chat_id": CHAT_ID},
                    "identity": {
                        "mode": mode,
                        "sender_profile": "project-reporter",
                        "sender_identity": "bot",
                        "bot_app_id": app_id,
                        "bot_display_name": "Project Reporter",
                        "cli_bin": "lark-cli",
                    },
                }
            },
        },
    )


def _write_machine_default_route(
    runtime_root: Path, *, route_ref: str = "loopx-concierge"
) -> None:
    target_path = runtime_root / "goal-channel-targets.json"
    add_lark_goal_channel_target(
        target_path=target_path,
        target_name=route_ref,
        chat_id=CHAT_ID,
        chat_name="LoopX Concierge",
        identity_mode="project_bot",
        sender_profile="project-reporter",
        sender_identity="bot",
        bot_app_id=APP_ID,
        bot_display_name="Project Reporter",
        cli_bin="lark-cli",
        execute=True,
    )
    defaults = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "periodic_report": {
                "schema_version": "periodic_report_machine_defaults_v0",
                "enabled": True,
                "inheritance": "live_machine_default",
                "profile_preset": "weekly-progress",
                "route_ref": route_ref,
                "timezone": "Asia/Shanghai",
            }
        },
    }
    preview = configure_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        machine_defaults=defaults,
    )
    configure_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        machine_defaults=defaults,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )


def test_unbound_goal_uses_live_machine_default_shared_target(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    _write_registry(registry_path, goal={"id": GOAL_ID})
    _write_machine_default_route(runtime_root)
    request = _request(
        delivery_authority=_authority(
            goal={"id": GOAL_ID},
            machine_defaults=read_periodic_report_machine_defaults(runtime_root),
        )
    )
    calls: list[list[str]] = []

    result = deliver_periodic_report_to_goal_channel(
        request,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=_runner(calls),
    )

    assert result["status"] == "satisfied"
    assert result["boundary"]["machine_default_route_allowed_when_unbound"] is True
    assert not (registry_path.parent / "goal-channel.json").exists()


def test_explicit_goal_channel_binding_uses_the_authorized_route(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    _write_registry(registry_path)
    _write_binding(registry_path)
    _write_machine_default_route(runtime_root)
    calls: list[list[str]] = []

    result = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=_runner(calls),
    )

    assert result["status"] == "satisfied"
    assert all("project-reporter" in args for args in calls)


def test_explicit_goal_channel_binding_cannot_redirect_authorized_route(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path, target_ref="different-route")
    calls: list[list[str]] = []

    with pytest.raises(ValueError, match="does not match the authorized route"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )

    assert calls == []


def _runner(
    calls: list[list[str]],
    *,
    normalized_readback: bool = False,
    initial_cards: dict[str, dict[str, Any]] | None = None,
    provider_messages_by_key: dict[str, str] | None = None,
):
    sent_cards: dict[str, dict[str, Any]] = dict(initial_cards or {})
    provider_cache = dict(provider_messages_by_key or {})

    def run(
        args: list[str],
        _cwd: Path | None,
        _timeout: float | None,
    ) -> dict[str, Any]:
        calls.append(args)
        assert args[:3] == ["lark-cli", "--profile", "project-reporter"]
        if "auth" in args and "status" in args:
            payload = {
                "ok": True,
                "appId": APP_ID,
                "identities": {
                    "bot": {
                        "available": True,
                        "verified": True,
                        "appName": "Project Reporter",
                    }
                },
            }
        elif "chats" in args and "get" in args:
            payload = {"ok": True, "data": {"chat_id": CHAT_ID}}
        elif "+chat-members-list" in args:
            payload = {
                "ok": True,
                "data": {"bots": [{"app_id": APP_ID}]},
            }
        elif "+chat-messages-list" in args:
            payload = {
                "ok": True,
                "messages": [
                    {
                        "message_id": message_id,
                        "chat_id": CHAT_ID,
                        "sender": {"sender_type": "app", "id": APP_ID},
                        "msg_type": "interactive",
                        "deleted": False,
                        "body": {"content": json.dumps(card)},
                    }
                    for message_id, card in sent_cards.items()
                ],
                "has_more": False,
            }
        elif "+messages-send" in args:
            provider_key = args[args.index("--idempotency-key") + 1]
            message_id = provider_cache.get(provider_key)
            if message_id is None:
                message_id = f"{MESSAGE_ID}_{len(sent_cards) + 1}"
                sent_cards[message_id] = json.loads(args[args.index("--content") + 1])
                provider_cache[provider_key] = message_id
            payload = {"ok": True, "data": {"message_id": message_id}}
        elif "+messages-mget" in args:
            message_id = args[args.index("--message-ids") + 1]
            card = sent_cards[message_id]
            if normalized_readback:
                title = card["header"]["title"]["content"]
                markdown = card["elements"][0]["text"]["content"]
                footer = card["elements"][2]["elements"][0]["content"]
                message_content = (
                    f'<card title="{title}">\n{markdown}\n---\n📝 {footer}\n</card>'
                )
            payload = {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "message_id": message_id,
                            "chat_id": CHAT_ID,
                            "sender": {
                                "sender_type": "app",
                                "id": APP_ID,
                            },
                            "msg_type": "interactive",
                            **(
                                {"content": message_content}
                                if normalized_readback
                                else {
                                    "body": {
                                        "content": json.dumps(sent_cards[message_id])
                                    }
                                }
                            ),
                        }
                    ]
                },
            }
        else:  # pragma: no cover - makes new provider calls fail loudly
            raise AssertionError(args)
        return {
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    return run


def test_goal_channel_delivery_accepts_normalized_cli_card_readback(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []

    result = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=_runner(calls, normalized_readback=True),
    )

    assert result["ok"] is True
    assert result["status"] == "satisfied"
    assert result["sink_result"]["readback_verified"] is True
    send_calls = [args for args in calls if "+messages-send" in args]
    hosted_card = json.loads(send_calls[0][send_calls[0].index("--content") + 1])
    hosted_markdown = hosted_card["elements"][0]["text"]["content"]
    assert "下一步：完成事实复核后推进下一个明确步骤。" in hosted_markdown
    assert "https://example.com/reports/stage-1" in hosted_markdown
    assert len(result["sink_result"]["message_results"]) == 2


def test_goal_channel_delivery_uses_only_the_bound_project_bot(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []

    preview = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
    )
    sent = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=_runner(calls),
    )

    assert preview["status"] == "pending_execution"
    assert preview["boundary"]["caller_identity_override_allowed"] is False
    assert sent["status"] == "satisfied"
    assert sent["sink_result"]["sender_identity_verified"] is True
    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 2
    assert len(sent["sink_result"]["message_results"]) == 2
    assert [item["kind"] for item in sent["sink_result"]["message_results"]] == [
        "hosted_report",
        "lark_document",
    ]
    for send in sends:
        assert send[send.index("--chat-id") + 1] == CHAT_ID
        assert send[send.index("--as") + 1] == "bot"
        assert "--profile" in send
        assert "project-reporter" in send
        provider_key = send[send.index("--idempotency-key") + 1]
        assert provider_key.startswith("loopx-")
    assert sent["boundary"]["rendered_announcement_idempotency_bound"] is True


def test_goal_channel_provider_keys_are_bound_to_rendered_announcement(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    runner = _runner(calls)

    deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )
    first_keys = [
        args[args.index("--idempotency-key") + 1]
        for args in calls
        if "+messages-send" in args
    ]

    changed = _request()
    next_action = changed["generation_bundle"]["document"]["sections"][0]["items"][0]
    next_action["summary"] = "完成事实复核后执行新版明确步骤。"
    changed["generation_bundle"] = build_periodic_report_generation_bundle(
        document=changed["generation_bundle"]["document"],
        artifacts=[
            periodic_report_markdown_renderer_adapter().render(
                changed["generation_bundle"]["document"]
            )
        ],
    )
    deliver_periodic_report_to_goal_channel(
        changed,
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )
    all_keys = [
        args[args.index("--idempotency-key") + 1]
        for args in calls
        if "+messages-send" in args
    ]

    assert ANNOUNCEMENT_IDEMPOTENCY_SCHEMA.endswith("_v1")
    assert len(first_keys) == 2
    assert len(all_keys) == 3
    assert all_keys[2] != first_keys[0]
    assert len(set(all_keys)) == 3


def test_upgraded_retry_does_not_reuse_legacy_key_for_changed_hosted_card(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    footer = "LoopX periodic report · Goal Channel"
    old_hosted_id = f"{MESSAGE_ID}_legacy_hosted"
    old_lark_id = f"{MESSAGE_ID}_legacy_lark"
    old_cards = {
        old_hosted_id: build_lark_markdown_reply_card(
            "本期阶段周报已发布。\n\n[查看周报](https://example.com/reports/stage-1)",
            title="阶段周报",
            footer=footer,
        ),
        old_lark_id: build_lark_markdown_reply_card(
            "配套 Lark 文档已同步。\n\n"
            "[查看 Lark 文档](https://example.larksuite.com/docx/stage-1)",
            title="配套 Lark 文档",
            footer=footer,
        ),
    }
    legacy_semantic_key = "periodic-report:goal-public-fixture:stage-1:hosted_report"
    legacy_provider_key = (
        "loopx-" + hashlib.sha256(legacy_semantic_key.encode()).hexdigest()[:32]
    )
    runner = _runner(
        calls,
        initial_cards=old_cards,
        provider_messages_by_key={legacy_provider_key: old_hosted_id},
    )

    recovered = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )

    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 1
    new_provider_key = sends[0][sends[0].index("--idempotency-key") + 1]
    assert new_provider_key != legacy_provider_key
    assert recovered["status"] == "satisfied"
    assert [
        item["semantic_dedupe_status"]
        for item in recovered["sink_result"]["message_results"]
    ] == ["no_existing_exact_message", "existing_exact_message"]


def test_goal_channel_delivery_reuses_exact_messages_after_interrupted_readback(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    base_runner = _runner(calls)
    interrupt_next_readback = True

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        nonlocal interrupt_next_readback
        result = base_runner(args, cwd, timeout)
        if "+messages-mget" in args and interrupt_next_readback:
            interrupt_next_readback = False
            raise RuntimeError("simulated process interruption after provider send")
        return result

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=runner,
        )

    recovered = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )

    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 2
    assert recovered["status"] == "satisfied"
    assert [
        item["semantic_dedupe_status"]
        for item in recovered["sink_result"]["message_results"]
    ] == ["existing_exact_message", "no_existing_exact_message"]


def test_goal_channel_delivery_exact_replay_performs_no_external_write(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    runner = _runner(calls)

    first = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )
    replay = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )

    assert first["sink_result"]["external_writes_performed"] is True
    assert replay["sink_result"]["external_writes_performed"] is False
    assert len([args for args in calls if "+messages-send" in args]) == 2
    assert {
        item["semantic_dedupe_status"]
        for item in replay["sink_result"]["message_results"]
    } == {"existing_exact_message"}


def test_goal_channel_delivery_fails_closed_when_dedupe_history_is_incomplete(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    base_runner = _runner(calls)

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        if "+chat-messages-list" in args:
            calls.append(args)
            return {
                "returncode": 0,
                "stdout": json.dumps({"ok": True, "messages": [], "has_more": True}),
                "stderr": "",
            }
        return base_runner(args, cwd, timeout)

    with pytest.raises(ValueError, match="dedupe history is incomplete"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=runner,
        )
    assert not any("+messages-send" in args for args in calls)


def test_goal_channel_delivery_revalidates_subscription_before_explicit_binding_send(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    request = _request()
    calls: list[list[str]] = []

    disabled = _goal()
    disabled["control_plane"]["periodic_report"]["enabled"] = False
    _write_registry(registry_path, goal=disabled)
    with pytest.raises(ValueError, match="subscription is disabled"):
        deliver_periodic_report_to_goal_channel(
            request,
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )

    _write_registry(registry_path, goal=_goal(route_ref="different-route"))
    with pytest.raises(ValueError, match="subscription authority drifted"):
        deliver_periodic_report_to_goal_channel(
            request,
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )
    assert calls == []


def test_goal_channel_delivery_rejects_canonical_stop_before_send(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    stopped = _goal()
    stopped["activation_state"] = "stopped"
    _write_registry(registry_path, goal=stopped)
    _write_binding(registry_path)
    calls: list[list[str]] = []

    with pytest.raises(ValueError, match="subscription is disabled"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )

    assert not any("+messages-send" in args for args in calls)


def test_goal_channel_delivery_revalidates_live_machine_default_before_send(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    inherited_goal = {"id": GOAL_ID}
    _write_registry(registry_path, goal=inherited_goal)
    _write_machine_default_route(runtime_root)
    request = _request(
        delivery_authority=_authority(
            goal=inherited_goal,
            machine_defaults=read_periodic_report_machine_defaults(runtime_root),
        )
    )
    _write_machine_default_route(runtime_root, route_ref="different-route")
    calls: list[list[str]] = []

    with pytest.raises(ValueError, match="subscription authority drifted"):
        deliver_periodic_report_to_goal_channel(
            request,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )

    assert calls == []


def test_goal_channel_delivery_revalidates_authority_between_message_writes(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    base_runner = _runner(calls)
    revoked = False

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        nonlocal revoked
        result = base_runner(args, cwd, timeout)
        if "+messages-send" in args and not revoked:
            revoked = True
            disabled = _goal()
            disabled["control_plane"]["periodic_report"]["enabled"] = False
            _write_registry(registry_path, goal=disabled)
        return result

    with pytest.raises(ValueError, match="subscription is disabled"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=runner,
        )

    assert len([args for args in calls if "+messages-send" in args]) == 1


def test_goal_channel_delivery_stops_between_writes_then_resumes_without_resend(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    base_runner = _runner(calls)
    stopped = False

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        nonlocal stopped
        result = base_runner(args, cwd, timeout)
        if "+messages-send" in args and not stopped:
            stopped = True
            stopped_goal = _goal()
            stopped_goal["activation_state"] = "stopped"
            _write_registry(registry_path, goal=stopped_goal)
        return result

    with pytest.raises(ValueError, match="subscription is disabled"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=runner,
        )

    assert len([args for args in calls if "+messages-send" in args]) == 1

    _write_registry(registry_path)
    resumed = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )

    assert resumed["status"] == "satisfied"
    assert len([args for args in calls if "+messages-send" in args]) == 2
    assert [
        item["semantic_dedupe_status"]
        for item in resumed["sink_result"]["message_results"]
    ] == ["existing_exact_message", "no_existing_exact_message"]


def test_goal_channel_delivery_requires_native_message_sender_readback(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    base_runner = _runner(calls)

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        result = base_runner(args, cwd, timeout)
        if "+messages-mget" not in args:
            return result
        payload = json.loads(result["stdout"])
        payload["data"]["items"][0]["sender"]["id"] = "cli_other_fixture"
        return {**result, "stdout": json.dumps(payload)}

    result = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )

    assert result["ok"] is False
    assert result["status"] == "readback_unverified"
    assert result["sink_result"]["sender_identity_verified"] is False


def test_goal_channel_delivery_requires_two_distinct_message_receipts(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    calls: list[list[str]] = []
    base_runner = _runner(calls)
    last_send_receipts: list[str] = []

    def runner(
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        if "+messages-send" in args:
            result = base_runner(args, cwd, timeout)
            payload = json.loads(result["stdout"])
            last_send_receipts.append(payload["data"]["message_id"])
            payload["data"]["message_id"] = f"{MESSAGE_ID}_same"
            return {**result, "stdout": json.dumps(payload)}
        if "+messages-mget" in args:
            original = list(args)
            original[original.index("--message-ids") + 1] = last_send_receipts.pop(0)
            result = base_runner(original, cwd, timeout)
            payload = json.loads(result["stdout"])
            payload["data"]["items"][0]["message_id"] = f"{MESSAGE_ID}_same"
            return {**result, "stdout": json.dumps(payload)}
        return base_runner(args, cwd, timeout)

    result = deliver_periodic_report_to_goal_channel(
        _request(),
        registry_path=registry_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        extension_activation=_extension_activation(),
        execute=True,
        runner=runner,
    )

    assert result["ok"] is False
    assert result["status"] == "readback_unverified"


def test_goal_channel_delivery_fails_closed_before_send_on_identity_drift(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    calls: list[list[str]] = []

    _write_binding(registry_path, mode="local_user")
    with pytest.raises(ValueError, match="project_bot Goal Channel identity"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )
    assert calls == []

    _write_binding(registry_path, app_id="cli_different_fixture")
    with pytest.raises(ValueError, match="sender identity could not be verified"):
        deliver_periodic_report_to_goal_channel(
            _request(),
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
            execute=True,
            runner=_runner(calls),
        )
    assert not any("+messages-send" in args for args in calls)


def test_goal_channel_delivery_rejects_caller_route_overrides(tmp_path: Path) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    request = _request()
    request["delivery_intent"]["sender_profile"] = "environment-default"

    with pytest.raises(ValueError, match="caller overrides are forbidden"):
        deliver_periodic_report_to_goal_channel(
            request,
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
        )


def test_goal_channel_delivery_requires_two_ordered_https_announcements(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    _write_registry(registry_path)
    _write_binding(registry_path)
    request = _request()
    request["delivery_intent"]["announcements"] = request["delivery_intent"][
        "announcements"
    ][:1]

    with pytest.raises(ValueError, match="exactly two announcements"):
        deliver_periodic_report_to_goal_channel(
            request,
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
        )

    request = _request()
    request["delivery_intent"]["announcements"][1]["url"] = "file:///tmp/report"
    with pytest.raises(ValueError, match="must be an https URL"):
        deliver_periodic_report_to_goal_channel(
            request,
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            extension_activation=_extension_activation(),
        )


def test_goal_channel_delivery_cli_forwards_registry_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    request_path = tmp_path / "request.json"
    _write_registry(registry_path, runtime_root=runtime_root)
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        periodic_report_cli,
        "resolve_extension_activation",
        lambda *_args, **_kwargs: _extension_activation(),
    )

    def deliver(request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        assert request["schema_version"] == GOAL_CHANNEL_DELIVERY_REQUEST_SCHEMA
        return {
            "ok": True,
            "schema_version": "periodic_report_goal_channel_delivery_result_v0",
            "status": "pending_execution",
        }

    monkeypatch.setattr(
        periodic_report_cli,
        "deliver_periodic_report_to_goal_channel",
        deliver,
    )
    from loopx.cli import build_parser

    args = build_parser().parse_args(
        [
            "periodic-report",
            "deliver-goal-channel",
            "--goal-id",
            GOAL_ID,
            "--request-json",
            str(request_path),
        ]
    )
    printed: list[dict[str, object]] = []

    result = periodic_report_cli.handle_lark_periodic_report_command(
        args,
        runtime_root_arg=None,
        registry_path=registry_path,
        output_format=lambda _args: "json",
        print_payload=lambda payload, _format, _renderer: printed.append(payload),
    )

    assert result == 0
    assert printed[0]["status"] == "pending_execution"
    assert captured["registry_path"] == registry_path.resolve()
    assert captured["runtime_root"] == runtime_root.resolve()
    assert captured["goal_id"] == GOAL_ID
    assert captured["execute"] is False


def test_calendar_delivery_recovers_from_verified_provider_cursor_without_resend(tmp_path: Path) -> None:
    from datetime import datetime
    from loopx.capabilities.periodic_report.cadence_journal import (
        admit_cadence_window, read_cadence_journal, cadence_intent,
    )
    from loopx.capabilities.periodic_report.post_writeback_hook import (
        evaluate_periodic_report_trigger_evaluation_intent,
    )

    registry = tmp_path / ".loopx" / "registry.json"
    runtime = tmp_path / "runtime"
    goal = _goal()
    goal["control_plane"]["periodic_report"].update({
        "timezone": "UTC",
        "schedule": {
            "schema_version": "periodic_report_schedule_v0",
            "schedule_id": "daily-report",
            "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
            "timezone": "UTC",
        },
    })
    _write_registry(registry, goal=goal, runtime_root=runtime)
    _write_binding(registry)
    subscription = resolve_goal_periodic_report_subscription(goal, None)
    admission_args = dict(runtime_root=runtime, goal_id=GOAL_ID,
                          agent_id="example-agent", subscription=subscription,
                          now=datetime.fromisoformat("2026-08-30T09:10:00+00:00"))
    window = admit_cadence_window(**admission_args)["window"]
    trigger = evaluate_periodic_report_trigger_evaluation_intent(cadence_intent(window))
    trigger_id = trigger["selected_trigger_id"]
    request = _request(delivery_authority=_authority(goal=goal))
    request["generation_bundle"] = _generation_bundle(period_window={
        "start_at": window["start_at"], "end_at": window["due_at"],
    })
    request["delivery_intent"]["idempotency_key"] = window["window_id"]
    _write_publication_candidate(runtime, request["generation_bundle"],
                                 trigger_ids=[trigger_id])
    calls: list[list[str]] = []
    runner = _runner(calls)
    args = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                extension_activation=_extension_activation(), runner=runner)
    preview = deliver_periodic_report_to_goal_channel(request, **args)
    assert preview["publication_cursor"] is None
    assert not any("+messages-send" in call for call in calls)
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)["publication"] is None

    delivered = deliver_periodic_report_to_goal_channel(request, execute=True, **args)
    assert delivered["status"] == "satisfied"
    cursor = delivered["publication_cursor"]
    assert cursor["covered_trigger_ids"] == [trigger_id]
    # Simulate restart after provider readback, before cadence acknowledgement.
    assert read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)["publication"] is None
    restarted = admit_cadence_window(**admission_args)
    assert restarted["window"] == window
    publication = read_cadence_journal(runtime_root=runtime, goal_id=GOAL_ID)["publication"]
    assert publication["publication_id"] == cursor["publication_id"]
    sent = sum("+messages-send" in call for call in calls)
    assert sent > 0
    replay = deliver_periodic_report_to_goal_channel(request, execute=True, **args)
    assert replay["status"] == "satisfied"
    assert sum("+messages-send" in call for call in calls) == sent
    assert replay["publication_cursor"] == cursor
