"""Local-private Lark App, group connection, and Goal Topic bindings.

The module layers the workspace UI on top of the existing Goal Channel target
and binding stores.  App credentials remain owned by ``lark-cli`` profiles;
LoopX stores only a safe profile reference, group target, and per-Goal topic
root message needed for routing.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import partial
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...chat_manager import MANAGER_AGENT_GOAL_ID
from ...control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
)
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...control_plane.todos.contract import normalize_todo_claimed_by
from ..external_connector_runtime import (
    ExternalCapturePolicy,
    ExternalConnectorCapability,
    ExternalConnectorLifecycle,
    ExternalIngressPolicy,
    ExternalResponsePolicy,
    ExternalSourceKind,
    build_external_connector_binding,
    normalize_external_connector_binding,
    project_external_connector_status,
)
from .goal_channel_contracts import (
    binding_for_goal,
    bindings_for_goal,
    goal_channel_connection_id,
    GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION,
    LarkTopicEventDecisionReason,
    goal_from_registry,
    goal_objective,
    now_iso,
    normalize_lark_topic_event_rejection_reason,
    operation_packet,
    provider_idempotency_key,
    read_goal_channel_binding,
    reusable_goal_topic_root,
    save_goal_connection,
    serialize_goal_binding_mutation,
    semantic_key,
    write_goal_channel_binding,
)
from .goal_channel_targets import (
    add_lark_goal_channel_target,
    goal_channel_target_for_name,
    read_goal_channel_targets,
)
from .goal_channel_transport import (
    APP_ID_PATTERN,
    BotChatMembershipResult,
    CHAT_ID_PATTERN,
    MESSAGE_ID_PATTERN,
    OPEN_ID_PATTERN,
    SAFE_PROFILE_PATTERN,
    bot_group_history_permission_guidance,
    bot_membership_verified,
    call,
    ensure_bot_chat_membership,
    find_first_string,
    json_payload,
    lark_args,
    message_readback_verified,
)
from .goal_topic_edit import (
    _unregister_async_inbox,
    GoalTopicUpgradeError,
    save_retiring_async_inbox,
    resolve_existing_goal_topic,
    resolve_conversation_policy,
)
from .goal_topic_inbox import (
    _agent_inbox_config,
    _write_agent_inbox_config,
    agent_inbox_binding_conflict_packet,
)
from .manager_routing import decide_manager_event
from .goal_topic_routing import (
    CaptureScope,
    IngressMode,
    ReplyMode,
    _routing_value,
    _connection_routing_modes,
    decide_lark_topic_route_event,
)
from .presentation.kanban import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    default_subprocess_runner,
)

INCOMING_MODES = {"mentions", "all"}


CAPTURE_SCOPES = {item.value for item in CaptureScope}
INGRESS_MODES = {item.value for item in IngressMode}
REPLY_MODES = {item.value for item in ReplyMode}


class LarkGroupChatLookupError(RuntimeError):
    """Content-free failure raised when a profile cannot list its bot groups."""

    error_code = "lark_group_lookup_failed"

    def __init__(self) -> None:
        super().__init__("Unable to list groups joined by the selected Lark App")


@serialize_goal_binding_mutation
def rebind_lark_manager_session(
    *,
    binding_path: Path,
    goal_id: str,
    connection_id: str,
    expected_session_id: str,
    session_id: str,
    executor_endpoint_id: str,
    executor_endpoint_source: str,
) -> dict[str, Any]:
    """Atomically move one durable manager route to a replacement Session.

    The provider audience and Topic remain unchanged. The caller must open and
    validate the replacement Session before this compare-and-swap changes the
    local execution binding.
    """

    payload = read_goal_channel_binding(binding_path)
    current = binding_for_goal(payload, goal_id, connection_id=connection_id)
    if not current or current.get("enabled") is not True:
        raise ValueError("manager connection is no longer durably configured")
    routing = current.get("routing")
    routing = dict(routing) if isinstance(routing, Mapping) else {}
    if routing.get("conversation_kind") != "manager":
        raise ValueError("connection is not a manager route")
    current_session_id = str(current.get("session_id") or "")
    if current_session_id not in {expected_session_id, session_id}:
        raise ValueError("manager connection changed during Session rebind")
    connector = current.get("connector")
    if not isinstance(connector, Mapping):
        raise ValueError("manager connection has no durable Connector binding")
    rebound_connector = normalize_external_connector_binding(
        {**dict(connector), "session_ref": session_id}
    )
    updated = {
        **current,
        "session_id": session_id,
        "routing": {
            **routing,
            "executor_endpoint_id": executor_endpoint_id,
            "executor_endpoint_source": executor_endpoint_source,
        },
        "connector": rebound_connector,
    }
    saved_connection_id = save_goal_connection(
        binding_path=binding_path,
        payload=payload,
        goal_id=goal_id,
        binding=updated,
    )
    readback = binding_for_goal(
        read_goal_channel_binding(binding_path),
        goal_id,
        connection_id=saved_connection_id,
    )
    readback_routing = readback.get("routing") if readback else {}
    readback_connector = readback.get("connector") if readback else {}
    if (
        not readback
        or readback.get("session_id") != session_id
        or not isinstance(readback_routing, Mapping)
        or readback_routing.get("executor_endpoint_id") != executor_endpoint_id
        or not isinstance(readback_connector, Mapping)
        or readback_connector.get("session_ref") != session_id
    ):
        raise OSError("manager Session rebind did not verify")
    return dict(readback)


def _json_value(result: Mapping[str, Any]) -> Any:
    try:
        return json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        return None


def _profile_ref(value: Any) -> str:
    profile = str(value or "").strip()
    if not SAFE_PROFILE_PATTERN.fullmatch(profile):
        raise ValueError("Lark App reference must be a safe lark-cli profile name")
    return profile


def _app_identity(
    *,
    app_ref: str,
    runner: CommandRunner,
    cli_bin: str,
) -> dict[str, Any] | None:
    profile = _profile_ref(app_ref)
    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=["auth", "status", "--verify", "--json"],
        ),
    )
    payload = json_payload(result)
    identities = payload.get("identities")
    bot = identities.get("bot") if isinstance(identities, Mapping) else None
    app_id = str(payload.get("appId") or "")
    open_id = str(bot.get("openId") or "") if isinstance(bot, Mapping) else ""
    if (
        result.get("returncode") != 0
        or not isinstance(bot, Mapping)
        or not APP_ID_PATTERN.fullmatch(app_id)
    ):
        return None
    identity = {
        "app_id": app_id,
        "label": public_safe_compact_text(bot.get("appName") or profile, limit=60),
        "ready": bot.get("available") is True and bot.get("verified") is True,
    }
    if OPEN_ID_PATTERN.fullmatch(open_id):
        identity["open_id"] = open_id
    return identity


def list_lark_apps(
    *,
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
) -> list[dict[str, Any]]:
    result = call(runner, [cli_bin, "profile", "list"])
    raw_profiles = _json_value(result)
    if result.get("returncode") != 0 or not isinstance(raw_profiles, list):
        return []
    apps: list[dict[str, Any]] = []
    for raw in raw_profiles:
        if not isinstance(raw, Mapping):
            continue
        try:
            app_ref = _profile_ref(raw.get("name"))
        except ValueError:
            continue
        identity = _app_identity(app_ref=app_ref, runner=runner, cli_bin=cli_bin)
        bot_ready = bool(identity and identity.get("ready"))
        apps.append(
            {
                "app_ref": app_ref,
                "label": str((identity or {}).get("label") or app_ref),
                "brand": public_safe_compact_text(raw.get("brand") or "lark", limit=20),
                "active": raw.get("active") is True,
                "ready": bool(identity and identity.get("ready")),
                "reply_ready": bot_ready,
                "health_error_code": None if bot_ready else "lark_app_not_ready",
            }
        )
    return apps


def _chat_items(payload: Any) -> list[dict[str, str]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    items = data.get("chats") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    chats: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        chat_id = str(item.get("chat_id") or "")
        if not CHAT_ID_PATTERN.fullmatch(chat_id) or chat_id in seen:
            continue
        seen.add(chat_id)
        chats.append(
            {
                "chat_id": chat_id,
                "chat_name": public_safe_compact_text(
                    item.get("name") or "Unnamed group", limit=60
                ),
            }
        )
    return chats[:50]


def list_lark_group_chats(
    *,
    app_ref: str,
    query: str | None = None,
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
) -> list[dict[str, str]]:
    profile = _profile_ref(app_ref)
    keyword = str(query or "").strip()
    tail = [
        "im",
        "+chat-search" if keyword else "+chat-list",
        *(["--query", keyword] if keyword else ["--types", "group"]),
        "--page-size",
        "50",
        "--as",
        "bot",
        "--format",
        "json",
    ]
    result = call(runner, lark_args(cli_bin=cli_bin, profile=profile, tail=tail))
    if result.get("returncode") != 0:
        raise LarkGroupChatLookupError()
    return _chat_items(json_payload(result))


def _target_for_connection(
    payload: Mapping[str, Any],
    *,
    app_ref: str,
    chat_id: str,
) -> tuple[str, dict[str, Any]] | None:
    targets = payload.get("targets")
    if not isinstance(targets, Mapping):
        return None
    for name, target in targets.items():
        if not isinstance(target, Mapping):
            continue
        channel = target.get("channel")
        identity = target.get("identity")
        if (
            isinstance(channel, Mapping)
            and isinstance(identity, Mapping)
            and str(channel.get("chat_id") or "") == chat_id
            and str(identity.get("sender_profile") or "") == app_ref
        ):
            return str(name), dict(target)
    return None


def _target_name(app_ref: str, chat_id: str) -> str:
    prefix = re.sub(r"[^a-z0-9._-]+", "-", app_ref.casefold()).strip("-._") or "lark"
    digest = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:10]
    return f"{prefix[:48]}-{digest}"


@serialize_goal_binding_mutation
def connect_lark_goal_topic(
    *,
    registry: Mapping[str, Any],
    goal_id: str,
    target_path: Path,
    binding_path: Path,
    app_ref: str = "",
    chat_id: str = "",
    chat_name: str = "",
    connection_id: str | None = None,
    incoming_mode: str = "mentions",
    agent_id: str | None = None,
    session_id: str | None = None,
    capture_scope: str | None = None,
    ingress_mode: str | None = None,
    conversation_kind: str | None = None,
    executor_endpoint_id: str | None = None,
    runtime_root: str | Path | None = None,
    reply_mode: str = "topic_reply",
    registry_path: Path | None = None,
    execute: bool = True,
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
) -> dict[str, Any]:
    goal = goal_from_registry(registry, goal_id)
    editing = None
    if connection_id:
        edit = resolve_existing_goal_topic(
            goal=goal,
            goal_id=goal_id,
            binding_path=binding_path,
            target_path=target_path,
            connection_id=connection_id,
            app_ref=app_ref,
            chat_id=chat_id,
            agent_id=agent_id,
            capture_scope=capture_scope,
        )
        editing = edit.binding
        app_ref, chat_id, chat_name = edit.app_ref, edit.chat_id, edit.chat_name
        agent_id, capture_scope = edit.agent_id, edit.capture_scope
    (
        conversation_kind,
        executor_endpoint_id,
        executor_endpoint_source,
        ingress_mode,
    ) = resolve_conversation_policy(
        editing=editing,
        conversation_kind=conversation_kind,
        executor_endpoint_id=executor_endpoint_id,
        ingress_mode=ingress_mode,
        runtime_root=runtime_root,
    )
    if conversation_kind == "manager":
        agent_id = MANAGER_AGENT_GOAL_ID
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if agent_id and not normalized_agent_id:
        raise ValueError("agent_id must be a public-safe registered Agent id")
    if (
        conversation_kind != "manager"
        and normalized_agent_id
        and normalized_agent_id not in registered_agent_ids_for_goal(goal)
    ):
        raise ValueError("agent_id must name an Agent registered for the Goal")
    connection_id = connection_id or goal_channel_connection_id(
        goal_id, normalized_agent_id
    )
    ingress_mode = _routing_value(
        IngressMode,
        ingress_mode,
        default=IngressMode.ASYNC_INBOX.value,
        field="ingress_mode",
    )
    if ingress_mode == IngressMode.DIRECT_SESSION.value:
        raise ValueError(
            "direct_session is read-only compatibility; use async_inbox, session_queue, or live_steering"
        )
    if not normalized_agent_id:
        raise ValueError(f"{ingress_mode} requires a registered agent_id")
    normalized_session_id = (
        ""
        if ingress_mode == IngressMode.ASYNC_INBOX.value
        else str(session_id or "").strip()
    )
    if (
        ingress_mode
        in {
            IngressMode.LIVE_STEERING.value,
            IngressMode.SESSION_QUEUE.value,
        }
        and not normalized_session_id
        and (execute or conversation_kind != "manager")
    ):
        raise ValueError(f"{ingress_mode} requires an exact active Agent session")
    reply_mode = _routing_value(
        ReplyMode,
        reply_mode,
        default=ReplyMode.TOPIC_REPLY.value,
        field="reply_mode",
    )
    profile = _profile_ref(app_ref)
    safe_chat_id = str(chat_id or "").strip()
    if not CHAT_ID_PATTERN.fullmatch(safe_chat_id):
        raise ValueError("Lark group chat id must begin with oc_")
    if incoming_mode not in INCOMING_MODES:
        raise ValueError("incoming_mode must be mentions or all")
    effective_capture_scope = _routing_value(
        CaptureScope,
        capture_scope
        or ("configured_chat_all" if incoming_mode == "all" else "addressed_only"),
        default=CaptureScope.ADDRESSED_ONLY.value,
        field="capture_scope",
    )
    effective_incoming_mode = (
        "all" if effective_capture_scope == "configured_chat_all" else "mentions"
    )
    inbox_config: tuple[Path, str, dict[str, Any]] | None = None
    if ingress_mode == IngressMode.ASYNC_INBOX.value:
        if registry_path is None:
            raise ValueError("async_inbox requires the source registry path")
        inbox_config = _agent_inbox_config(
            goal=goal,
            agent_id=str(normalized_agent_id),
            app_ref=profile,
            chat_id=safe_chat_id,
            bot_display_name=profile,
            capture_scope=effective_capture_scope,
        )
        conflict = agent_inbox_binding_conflict_packet(
            goal=goal,
            goal_id=goal_id,
            agent_id=str(normalized_agent_id),
            intended_config_path=inbox_config[0],
            execute=execute,
        )
        if conflict:
            return conflict

    target_payload = read_goal_channel_targets(target_path)
    matched = _target_for_connection(
        target_payload,
        app_ref=profile,
        chat_id=safe_chat_id,
    )
    if editing is not None and not reusable_goal_topic_root(
        read_goal_channel_binding(binding_path),
        goal_id,
        connection_id=connection_id,
        provider_target=matched[1] if matched is not None else None,
        chat_id=safe_chat_id,
    ):
        raise ValueError(
            "the existing Topic is unavailable; upgrade cannot create a replacement"
        )
    target_identity = matched[1].get("identity") if matched is not None else None
    target_cli_bin = (
        str(target_identity.get("cli_bin") or "").strip()
        if isinstance(target_identity, Mapping)
        else ""
    )
    effective_cli_bin = target_cli_bin or cli_bin
    identity = _app_identity(
        app_ref=profile,
        runner=runner,
        cli_bin=effective_cli_bin,
    )
    if not identity or not identity.get("ready"):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="connect_topic",
            execute=execute,
            status="blocked",
            blocker="lark_app_not_ready",
            public_summary="the selected Lark App is not ready",
        )
    if not execute:
        return operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="connect_topic",
            execute=False,
            status="preview_ready",
            public_summary="previewed one Goal Topic connection",
            details={
                "app_ref": profile,
                "chat_name": public_safe_compact_text(chat_name, limit=60),
                "topic_name": public_safe_compact_text(
                    (
                        f"{goal_objective(goal)} · {normalized_agent_id}"
                        if normalized_agent_id
                        else goal_objective(goal)
                    ),
                    limit=120,
                ),
                "incoming_mode": effective_incoming_mode,
                "capture_scope": effective_capture_scope,
                "ingress_mode": ingress_mode,
                "reply_mode": reply_mode,
                "agent_id": normalized_agent_id,
                "connection_id": connection_id,
            },
        )
    if editing is not None:
        # Upgrades verify the existing group; they never add members or create topics.
        membership_result = (
            BotChatMembershipResult.ALREADY_VERIFIED
            if bot_membership_verified(
                runner=runner,
                cli_bin=effective_cli_bin,
                profile=profile,
                chat_id=safe_chat_id,
                app_id=str(identity["app_id"]),
            )
            else BotChatMembershipResult.ALREADY_UNVERIFIED
        )
    else:
        membership_result = ensure_bot_chat_membership(
            runner=runner,
            cli_bin=effective_cli_bin,
            membership_profile=profile,
            bot_profile=profile,
            chat_id=safe_chat_id,
            app_id=str(identity["app_id"]),
        )
    membership_added = membership_result.external_write_performed
    if membership_result is BotChatMembershipResult.ADD_FAILED:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="connect_topic",
            execute=True,
            status="failed",
            blocker="provider_api_failed",
            public_summary="the selected Lark App could not be added to the group",
        )
    if membership_result in {
        BotChatMembershipResult.ALREADY_UNVERIFIED,
        BotChatMembershipResult.ADDED_UNVERIFIED,
    }:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="connect_topic",
            execute=True,
            status="blocked",
            blocker="channel_membership_unverified",
            public_summary="the selected Lark App is not a verified group member",
            external_write_performed=membership_result.external_write_performed,
        )

    if inbox_config is not None:
        _config_path, _preflight_config_ref, inbox_payload = inbox_config
        reply = inbox_payload.get("reply")
        if isinstance(reply, dict):
            reply["bot_display_name"] = str(identity["label"])
            reply["bot_app_id"] = str(identity["app_id"])
            if identity.get("open_id"):
                reply["bot_open_id"] = str(identity["open_id"])

    target_name = (
        matched[0] if matched is not None else _target_name(profile, safe_chat_id)
    )
    matched_identity = matched[1].get("identity") if matched is not None else None
    added = add_lark_goal_channel_target(
        target_path=target_path,
        target_name=target_name,
        chat_id=safe_chat_id,
        chat_name=chat_name,
        identity_mode=(
            str(matched_identity.get("mode") or "local_user")
            if isinstance(matched_identity, Mapping)
            else "local_user"
        ),
        sender_profile=profile,
        bot_app_id=str(identity["app_id"]),
        bot_open_id=str(identity.get("open_id") or "") or None,
        bot_display_name=str(identity["label"]),
        cli_bin=effective_cli_bin,
        execute=True,
    )
    if not added.get("ok"):
        return added

    objective = goal_objective(goal)
    topic_name = public_safe_compact_text(
        f"{objective} · {normalized_agent_id}" if normalized_agent_id else objective,
        limit=120,
    )
    topic_text = (
        f"LoopX Agent Topic: {topic_name}\n"
        f"Goal ID: {goal_id}\n"
        f"Agent ID: {normalized_agent_id}"
        if normalized_agent_id
        else f"LoopX Goal Topic: {objective}\nGoal ID: {goal_id}"
    )
    reusable_root = reusable_goal_topic_root(
        read_goal_channel_binding(binding_path),
        goal_id,
        connection_id=connection_id,
        provider_target=matched[1] if matched is not None else None,
        chat_id=safe_chat_id,
    )
    key = provider_idempotency_key(
        semantic_key(
            goal_id,
            connection_id,
            "lark",
            "goal_topic",
            profile,
            safe_chat_id,
            topic_text,
        )
    )
    if reusable_root:
        root_message_id = reusable_root
    else:
        sent = call(
            runner,
            lark_args(
                cli_bin=effective_cli_bin,
                profile=profile,
                tail=[
                    "im",
                    "+messages-send",
                    "--chat-id",
                    safe_chat_id,
                    "--text",
                    topic_text,
                    "--idempotency-key",
                    key,
                    "--as",
                    "bot",
                    "--format",
                    "json",
                ],
            ),
        )
        root_message_id = find_first_string(
            json_payload(sent),
            {"message_id"},
            MESSAGE_ID_PATTERN,
        )
        if sent.get("returncode") != 0 or not root_message_id:
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="connect_topic",
                execute=True,
                status="failed",
                blocker="provider_api_failed",
                public_summary="the Goal Topic root message could not be sent",
                external_write_performed=membership_added,
            )
    if not message_readback_verified(
        runner=runner,
        cli_bin=effective_cli_bin,
        profile=profile,
        identity="bot",
        message_id=root_message_id,
        expected_text=f"Goal ID: {goal_id}" if reusable_root else topic_text,
        expected_chat_id=safe_chat_id if reusable_root else None,
        expected_goal_id=goal_id if reusable_root else None,
    ):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="connect_topic",
            execute=True,
            status="blocked" if reusable_root else "sent_unverified",
            blocker="readback_mismatch",
            public_summary="the Goal Topic could not be verified; binding preserved",
            external_write_performed=membership_added or not bool(reusable_root),
        )

    if inbox_config is not None:
        config_path, preflight_config_ref, inbox_payload = inbox_config
        inbox_payload["topic_root_message_id"] = root_message_id
        _write_agent_inbox_config(
            config_path=config_path,
            config_ref=preflight_config_ref,
            payload=inbox_payload,
        )

    connector_binding: dict[str, Any] | None = None
    if normalized_agent_id and ingress_mode != IngressMode.DIRECT_SESSION.value:
        if inbox_config is not None:
            connector_inbox_ref = inbox_config[1]
            connector_cursor_ref = f"{inbox_config[2]['inbox_dir']}/processed.json"
        else:
            connector_inbox_ref = None
            runtime_digest = hashlib.sha256(
                f"{profile}\0{safe_chat_id}\0{root_message_id}".encode()
            ).hexdigest()[:20]
            connector_cursor_ref = (
                f".loopx/inbox/lark-goal-topics/{runtime_digest}/processed.json"
            )
        connector_binding = build_external_connector_binding(
            goal_ref=goal_id,
            agent_ref=normalized_agent_id,
            provider_kind="lark",
            source_kind=ExternalSourceKind.GROUP_MESSAGE.value,
            source_ref=safe_chat_id,
            capture_policy=(
                ExternalCapturePolicy.CONFIGURED_SOURCE_ALL.value
                if effective_capture_scope == CaptureScope.CONFIGURED_CHAT_ALL.value
                else ExternalCapturePolicy.ADDRESSED_ONLY.value
            ),
            ingress_policy=ExternalIngressPolicy(ingress_mode).value,
            response_policy=ExternalResponsePolicy.TOPIC_REPLY.value,
            cursor_ref=connector_cursor_ref,
            lifecycle=ExternalConnectorLifecycle.CONNECTED.value,
            capabilities=[
                ExternalConnectorCapability.REALTIME_RECEIVE.value,
                ExternalConnectorCapability.RESPONSE_WRITE.value,
                ExternalConnectorCapability.RESPONSE_READBACK.value,
                ExternalConnectorCapability.ACKNOWLEDGE.value,
            ],
            session_ref=normalized_session_id or None,
            inbox_ref=connector_inbox_ref,
        )

    inbox_config_ref: str | None = None
    if inbox_config is not None:
        inbox_config_ref = inbox_config[1]
        configured = configure_goal_with_global_sync(
            registry_path=registry_path,
            goal_id=goal_id,
            runtime_root_override=None,
            execute=True,
            lark_event_inbox_config=inbox_config_ref,
            lark_event_inbox_agent_id=normalized_agent_id,
        )
        if not configured.get("ok"):
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="connect_topic",
                execute=True,
                status="sent_verified_registration_failed",
                blocker="agent_inbox_registration_failed",
                public_summary="the Agent-scoped inbox could not be registered",
                external_write_performed=membership_added or not bool(reusable_root),
                readback_verified=True,
            )

    payload = read_goal_channel_binding(binding_path)
    existing = binding_for_goal(payload, goal_id, connection_id=connection_id) or {}
    receipts = dict(existing.get("receipts") or {})
    receipts[semantic_key(goal_id, "lark", "goal_topic_receipt", root_message_id)] = {
        "kind": "goal_topic",
        "message_id": root_message_id,
        "verified_at": now_iso(),
    }
    save_connection = partial(
        save_goal_connection,
        binding_path=binding_path,
        payload=payload,
        goal_id=goal_id,
        binding={
            **existing,
            "goal_id": goal_id,
            "agent_id": normalized_agent_id,
            "connection_id": connection_id,
            "session_id": normalized_session_id or None,
            "provider": "lark",
            "enabled": True,
            "target_ref": target_name,
            "channel": {
                **dict(existing.get("channel") or {}),
                "pinned_message_id": root_message_id,
            },
            "topic": {
                **dict(existing.get("topic") or {}),
                "name": (existing.get("topic") or {}).get("name", topic_name)
                if editing
                else topic_name,
                "root_message_id": root_message_id,
                "created_automatically": True,
            },
            "routing": {
                "incoming_mode": effective_incoming_mode,
                "capture_scope": effective_capture_scope,
                "ingress_mode": ingress_mode,
                "reply_mode": reply_mode,
                **(
                    {
                        "conversation_kind": "manager",
                        "executor_endpoint_id": executor_endpoint_id,
                        "executor_endpoint_source": executor_endpoint_source,
                    }
                    if conversation_kind == "manager"
                    else {}
                ),
                **({"inbox_config_ref": inbox_config_ref} if inbox_config_ref else {}),
            },
            **({"connector": connector_binding} if connector_binding else {}),
            "automation": dict(existing.get("automation") or {}),
            "receipts": receipts,
        },
    )
    try:
        saved_connection_id = (
            save_retiring_async_inbox(
                previous=editing,
                registry_path=registry_path,
                binding_path=binding_path,
                goal_id=goal_id,
                save=save_connection,
            )
            if conversation_kind == "manager" and editing is not None
            else save_connection()
        )
    except GoalTopicUpgradeError as exc:
        return exc.operation_packet(goal_id=goal_id)
    return operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="connect_topic",
        execute=True,
        status="connected",
        public_summary="connected one Goal to a dedicated Lark topic",
        external_write_performed=membership_added or not bool(reusable_root),
        readback_verified=True,
        idempotency_key=key,
        details={
            "app_ref": profile,
            "chat_name": public_safe_compact_text(chat_name, limit=60),
            "target_ref": target_name,
            "topic_name": topic_name,
            "incoming_mode": effective_incoming_mode,
            "capture_scope": effective_capture_scope,
            "ingress_mode": ingress_mode,
            "reply_mode": reply_mode,
            "agent_id": normalized_agent_id,
            "connection_id": saved_connection_id,
            **({"session_id": normalized_session_id} if normalized_session_id else {}),
            **(
                {
                    "connector_status": project_external_connector_status(
                        connector_binding
                    )
                }
                if connector_binding
                else {}
            ),
        },
    )


def list_lark_connections(
    *,
    registry: Mapping[str, Any],
    target_path: Path,
    binding_paths: Mapping[str, Path],
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
    runtime_health: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    target_payload = read_goal_channel_targets(target_path)
    rows: list[dict[str, Any]] = []
    health_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for goal_id, binding_path in binding_paths.items():
        try:
            goal = goal_from_registry(registry, goal_id)
            goal_bindings = bindings_for_goal(
                read_goal_channel_binding(binding_path), goal_id
            )
        except (OSError, ValueError):
            continue
        for binding in goal_bindings:
            target_ref = str(binding.get("target_ref") or "")
            target = goal_channel_target_for_name(target_payload, target_ref)
            if target is None:
                continue
            channel = (
                target.get("channel")
                if isinstance(target.get("channel"), Mapping)
                else {}
            )
            identity = (
                target.get("identity")
                if isinstance(target.get("identity"), Mapping)
                else {}
            )
            app_ref = str(identity.get("sender_profile") or "default")
            effective_cli_bin = str(identity.get("cli_bin") or "").strip() or cli_bin
            health_key = (app_ref, effective_cli_bin)
            if health_key not in health_cache:
                app_identity = _app_identity(
                    app_ref=app_ref, runner=runner, cli_bin=effective_cli_bin
                )
                health_cache[health_key] = {
                    "ready": bool(app_identity and app_identity.get("ready")),
                    "error_code": None
                    if app_identity and app_identity.get("ready")
                    else "lark_app_not_ready",
                }
            health = health_cache[health_key]
            listener = (
                runtime_health.get(app_ref, {})
                if isinstance(runtime_health, Mapping)
                else {}
            )
            listener_status = str(listener.get("status") or "")
            listener_ready = runtime_health is None or listener_status == "listening"
            last_event_status = str(listener.get("last_event_status") or "")
            last_event_reason = (
                normalize_lark_topic_event_rejection_reason(
                    listener.get("last_event_reason")
                )
                or ""
            )
            event_count = int(listener.get("event_count") or 0)
            event_blocker = (
                last_event_status
                if last_event_status
                in {
                    "message_context_permission_required",
                    "message_context_lookup_failed",
                    "message_context_unavailable",
                    "topic_context_ambiguous",
                    "topic_context_missing",
                    "processing_failed",
                    "answer_empty",
                    "reply_failed",
                }
                else None
            )
            if (
                event_blocker is None
                and last_event_status == "ignored"
                and last_event_reason
                in {
                    "invalid_event",
                    "binding_unavailable",
                    "chat_mismatch",
                    "topic_mismatch",
                    "route_ambiguous",
                }
            ):
                event_blocker = "lark_event_route_mismatch"
            event_delivery_unverified = bool(
                runtime_health is not None
                and listener_status == "listening"
                and event_count == 0
                and not last_event_status
            )
            reply_ready = bool(
                health.get("ready")
                and listener_ready
                and event_blocker is None
                and not event_delivery_unverified
            )
            health_error_code = health.get("error_code")
            if health_error_code is None and not listener_ready:
                health_error_code = str(
                    listener.get("error_code")
                    or (
                        "lark_event_listener_starting"
                        if listener_status == "starting"
                        else "lark_event_listener_inactive"
                    )
                )
            if health_error_code is None and event_blocker is not None:
                health_error_code = event_blocker
            if health_error_code is None and event_delivery_unverified:
                health_error_code = "lark_event_delivery_unverified"
            history_permission_guidance = bot_group_history_permission_guidance(
                str(identity.get("bot_app_id") or "")
            )
            topic = (
                binding.get("topic")
                if isinstance(binding.get("topic"), Mapping)
                else {}
            )
            routing = (
                binding.get("routing")
                if isinstance(binding.get("routing"), Mapping)
                else {}
            )
            connector_status: dict[str, Any] | None = None
            try:
                capture_scope, ingress_mode, reply_mode = _connection_routing_modes(routing)
                raw_connector = binding.get("connector")
                if raw_connector is not None:
                    if not isinstance(raw_connector, Mapping):
                        raise ValueError("connector binding must be an object")
                    connector_status = project_external_connector_status(raw_connector)
                    if (
                        connector_status["goal_ref"] != goal_id
                        or connector_status["agent_ref"]
                        != str(binding.get("agent_id") or "")
                        or connector_status["ingress_policy"] != ingress_mode
                    ):
                        raise ValueError(
                            "connector binding does not match the Lark route"
                        )
            except ValueError:
                capture_scope = CaptureScope.ADDRESSED_ONLY.value
                ingress_mode = IngressMode.DIRECT_SESSION.value
                reply_mode = ReplyMode.TOPIC_REPLY.value
                reply_ready = False
                health_error_code = "invalid_routing_state"
            legacy_root = (
                binding.get("channel", {}).get("pinned_message_id")
                if isinstance(binding.get("channel"), Mapping)
                else None
            )
            rows.append(
                {
                    "app_ref": app_ref,
                    "app_label": public_safe_compact_text(
                        identity.get("bot_display_name")
                        or identity.get("sender_profile")
                        or "Lark App",
                        limit=60,
                    ),
                    "chat_name": public_safe_compact_text(
                        channel.get("chat_name") or target_ref, limit=60
                    ),
                    "enabled": binding.get("enabled") is True,
                    "goal_id": goal_id,
                    "connection_id": str(binding.get("connection_id") or ""),
                    "goal_title": goal_objective(goal),
                    "agent_id": str(binding.get("agent_id") or "") or None,
                    "conversation_kind": str(
                        routing.get("conversation_kind") or "goal"
                    ),
                    "session_bound": bool(binding.get("session_id")),
                    "incoming_mode": str(routing.get("incoming_mode") or "mentions"),
                    "capture_scope": capture_scope,
                    "ingress_mode": ingress_mode,
                    "reply_mode": reply_mode,
                    **(
                        {"connector_status": connector_status}
                        if connector_status is not None
                        else {}
                    ),
                    "target_ref": target_ref,
                    "topic_name": public_safe_compact_text(
                        topic.get("name") or goal_objective(goal), limit=120
                    ),
                    "topic_setup_required": not bool(
                        topic.get("root_message_id") or legacy_root
                    ),
                    "reply_ready": reply_ready,
                    "health_error_code": health_error_code,
                    "history_permission_guidance": history_permission_guidance,
                    "listener_status": listener_status or None,
                    "listener_error_code": listener.get("error_code"),
                    "last_event_status": last_event_status or None,
                    "last_event_reason": last_event_reason or None,
                    "event_count": event_count,
                    "replied_count": int(listener.get("replied_count") or 0),
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            str(row["app_label"]).casefold(),
            str(row["goal_title"]).casefold(),
        ),
    )


def decide_lark_topic_event(
    *,
    target_payload: Mapping[str, Any],
    binding_payloads: Mapping[str, Mapping[str, Any]],
    event: Mapping[str, Any],
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return a content-free routing decision for one provider event."""

    manager_decision = decide_manager_event(
        target_payload=target_payload,
        binding_payloads=binding_payloads,
        event=event,
        runtime_root=runtime_root,
    )
    if manager_decision is not None:
        return manager_decision
    chat_id = str(event.get("chat_id") or "")
    root_id = str(event.get("root_id") or "")
    message_id = str(event.get("message_id") or "")
    if not (
        CHAT_ID_PATTERN.fullmatch(chat_id)
        and MESSAGE_ID_PATTERN.fullmatch(root_id)
        and MESSAGE_ID_PATTERN.fullmatch(message_id)
    ):
        return {
            "matched": False,
            "reason": LarkTopicEventDecisionReason.INVALID_EVENT.value,
            "route": None,
        }
    has_binding = False
    matched_chat = False
    candidates: list[dict[str, Any]] = []
    for goal_id, payload in binding_payloads.items():
        for binding in bindings_for_goal(payload, goal_id):
            if binding.get("enabled") is not True:
                continue
            has_binding = True
            target_ref = str(binding.get("target_ref") or "")
            target = goal_channel_target_for_name(target_payload, target_ref)
            if target is None:
                continue
            channel = (
                target.get("channel")
                if isinstance(target.get("channel"), Mapping)
                else {}
            )
            if str(channel.get("chat_id") or "") != chat_id:
                continue
            matched_chat = True
            identity = (
                target.get("identity")
                if isinstance(target.get("identity"), Mapping)
                else {}
            )
            topic = (
                binding.get("topic")
                if isinstance(binding.get("topic"), Mapping)
                else {}
            )
            binding_channel = (
                binding.get("channel")
                if isinstance(binding.get("channel"), Mapping)
                else {}
            )
            topic_root = str(
                topic.get("root_message_id")
                or binding_channel.get("pinned_message_id")
                or ""
            )
            routing = (
                binding.get("routing")
                if isinstance(binding.get("routing"), Mapping)
                else {}
            )
            try:
                capture_scope, ingress_mode, reply_mode = _connection_routing_modes(routing)
                connector = binding.get("connector")
                if connector is not None:
                    if not isinstance(connector, Mapping):
                        raise ValueError("connector binding must be an object")
                    connector_status = project_external_connector_status(connector)
                    if (
                        connector_status["goal_ref"] != goal_id
                        or connector_status["agent_ref"]
                        != str(binding.get("agent_id") or "")
                        or connector_status["ingress_policy"] != ingress_mode
                    ):
                        raise ValueError(
                            "connector binding does not match the Lark route"
                        )
            except ValueError:
                return {
                    "matched": False,
                    "reason": LarkTopicEventDecisionReason.INVALID_ROUTING_STATE.value,
                    "route": None,
                }
            candidates.append(
                {
                    "goal_id": goal_id,
                    "connection_id": str(binding.get("connection_id") or ""),
                    "binding": binding,
                    "identity": identity,
                    "target_ref": target_ref,
                    "topic_root": topic_root,
                    "capture_scope": capture_scope,
                    "ingress_mode": ingress_mode,
                    "reply_mode": reply_mode,
                    "routing": routing,
                    "connector": connector,
                }
            )

    exact_topic_candidates = [
        candidate for candidate in candidates if candidate["topic_root"] == root_id
    ]
    eligible = exact_topic_candidates or [
        candidate
        for candidate in candidates
        if candidate["capture_scope"] == CaptureScope.CONFIGURED_CHAT_ALL.value
    ]
    if len(eligible) > 1:
        return {
            "matched": False,
            "reason": LarkTopicEventDecisionReason.ROUTE_AMBIGUOUS.value,
            "route": None,
        }
    if not eligible:
        reason = (
            LarkTopicEventDecisionReason.BINDING_UNAVAILABLE
            if not has_binding
            else LarkTopicEventDecisionReason.CHAT_MISMATCH
            if not matched_chat
            else LarkTopicEventDecisionReason.TOPIC_MISMATCH
        )
        return {"matched": False, "reason": reason.value, "route": None}

    selected = eligible[0]
    identity = selected["identity"]
    capture_scope = str(selected["capture_scope"])
    route_decision = decide_lark_topic_route_event(
        event=event,
        chat_id=chat_id,
        topic_root_message_id=str(selected["topic_root"]),
        capture_scope=capture_scope,
        identity=identity,
    )
    if route_decision is not LarkTopicEventDecisionReason.MATCHED:
        return {
            "matched": False,
            "reason": route_decision.value,
            "route": None,
        }
    binding = selected["binding"]
    ingress_mode = str(selected["ingress_mode"])
    routing = selected["routing"]
    connector = selected["connector"]
    route = {
        "app_ref": str(identity.get("sender_profile") or "default"),
        "goal_id": str(selected["goal_id"]),
        "connection_id": str(selected["connection_id"]),
        "message_id": message_id,
        "reply_mode": str(selected["reply_mode"]),
        "target_ref": str(selected["target_ref"]),
        "topic_root_message_id": str(selected["topic_root"]),
    }
    if ingress_mode != IngressMode.DIRECT_SESSION.value or binding.get("agent_id"):
        route.update(
            {
                "agent_id": str(binding.get("agent_id") or ""),
                "session_id": str(binding.get("session_id") or ""),
                "capture_scope": capture_scope,
                "ingress_mode": ingress_mode,
                "inbox_config_ref": str(routing.get("inbox_config_ref") or ""),
                **(
                    {"connector": dict(connector)}
                    if isinstance(connector, Mapping)
                    else {}
                ),
                **(
                    {"event_id": str(event.get("event_id") or message_id)}
                    if isinstance(connector, Mapping)
                    else {}
                ),
            }
        )
    return {
        "matched": True,
        "reason": LarkTopicEventDecisionReason.MATCHED.value,
        "route": route,
    }


def route_lark_topic_event(
    *,
    target_payload: Mapping[str, Any],
    binding_payloads: Mapping[str, Mapping[str, Any]],
    event: Mapping[str, Any],
    runtime_root: str | Path | None = None,
) -> dict[str, str] | None:
    decision = decide_lark_topic_event(
        target_payload=target_payload,
        binding_payloads=binding_payloads,
        event=event,
        runtime_root=runtime_root,
    )
    route = decision.get("route")
    return dict(route) if isinstance(route, Mapping) else None


def reply_lark_goal_topic(
    *,
    route: Mapping[str, Any],
    text: str,
    runner: CommandRunner = default_subprocess_runner,
    cli_bin: str = DEFAULT_CLI_BIN,
) -> dict[str, Any]:
    profile = _profile_ref(route.get("app_ref"))
    message_id = str(route.get("message_id") or "")
    reply_text = public_safe_compact_text(text, limit=1200)
    if not MESSAGE_ID_PATTERN.fullmatch(message_id) or not reply_text:
        raise ValueError("a valid source message and non-empty reply are required")
    key = provider_idempotency_key(
        semantic_key(
            str(route.get("goal_id") or ""),
            "lark",
            "topic_reply",
            message_id,
            reply_text,
        )
    )
    result = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "+messages-reply",
                "--message-id",
                message_id,
                "--text",
                reply_text,
                "--reply-in-thread",
                "--idempotency-key",
                key,
                "--as",
                "bot",
                "--format",
                "json",
            ],
        ),
    )
    reply_id = find_first_string(
        json_payload(result), {"message_id"}, MESSAGE_ID_PATTERN
    )
    return operation_packet(
        ok=bool(result.get("returncode") == 0 and reply_id),
        goal_id=str(route.get("goal_id") or "") or None,
        operation="topic_reply",
        execute=True,
        status="sent" if result.get("returncode") == 0 and reply_id else "failed",
        blocker=None
        if result.get("returncode") == 0 and reply_id
        else "provider_api_failed",
        public_summary=(
            "replied in the bound Goal topic"
            if result.get("returncode") == 0 and reply_id
            else "the Goal topic reply could not be sent"
        ),
        external_write_performed=bool(result.get("returncode") == 0 and reply_id),
        idempotency_key=key,
    )


def _without_goal_topic_connection(
    payload: Mapping[str, Any], *, goal_id: str, connection_id: str
) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    bindings = payload.get("bindings")
    mutable = dict(bindings) if isinstance(bindings, Mapping) else {}
    stored = mutable.get(goal_id)
    removed: Mapping[str, Any] | None = None
    if (
        isinstance(stored, Mapping)
        and stored.get("schema_version") == GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION
        and isinstance(stored.get("connections"), Mapping)
    ):
        connections = dict(stored["connections"])
        removed_value = connections.pop(connection_id, None)
        removed = removed_value if isinstance(removed_value, Mapping) else None
        if connections:
            default_id = str(stored.get("default_connection_id") or "")
            if default_id not in connections:
                default_id = min(connections)
            mutable[goal_id] = {
                "schema_version": GOAL_CHANNEL_CONNECTION_SET_SCHEMA_VERSION,
                "default_connection_id": default_id,
                "connections": connections,
            }
        else:
            mutable.pop(goal_id, None)
    elif isinstance(stored, Mapping):
        legacy_id = goal_channel_connection_id(
            goal_id, str(stored.get("agent_id") or "") or None
        )
        if connection_id == legacy_id:
            removed = stored
            mutable.pop(goal_id, None)
    return mutable, removed


@serialize_goal_binding_mutation
def disconnect_lark_goal_topic(
    *,
    binding_path: Path,
    goal_id: str,
    connection_id: str,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    payload = read_goal_channel_binding(binding_path)
    mutable, removed = _without_goal_topic_connection(
        payload,
        goal_id=goal_id,
        connection_id=connection_id,
    )
    existed = removed is not None
    if existed:
        write_goal_channel_binding(
            binding_path,
            {"schema_version": payload["schema_version"], "bindings": mutable},
        )
    cleanup, removed_agent_id = _unregister_async_inbox(
        removed=removed,
        registry_path=registry_path,
        goal_id=goal_id,
    )
    cleanup_ok = cleanup is None or cleanup.get("ok") is True
    readback_verified = (
        binding_for_goal(
            read_goal_channel_binding(binding_path),
            goal_id,
            connection_id=connection_id,
        )
        is None
    )
    return operation_packet(
        ok=cleanup_ok,
        goal_id=goal_id,
        operation="disconnect_topic",
        execute=True,
        status=(
            "disconnected"
            if existed and cleanup_ok
            else "disconnected_inbox_cleanup_failed"
            if existed
            else "already_disconnected"
        ),
        blocker=None if cleanup_ok else "agent_inbox_unregistration_failed",
        public_summary=(
            "disconnected the selected Goal topic"
            if existed and cleanup_ok
            else "the Goal topic was disconnected but its Agent inbox registration could not be cleared"
            if existed
            else "the selected Goal has no Lark topic connection"
        ),
        readback_verified=readback_verified and cleanup_ok,
        details={
            "connection_id": connection_id,
            **(
                {
                    "agent_inbox_unregistered": cleanup_ok,
                    "agent_id": removed_agent_id,
                }
                if cleanup is not None
                else {}
            ),
        },
    )
