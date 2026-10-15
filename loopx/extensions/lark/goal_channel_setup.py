from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...control_plane.runtime.public_safety import public_safe_compact_text
from .goal_channel_contracts import (
    HUMAN_GATE_AUTO_NOTIFY_SETTING,
    binding_for_goal,
    clear_human_gate_auto_notify_marker,
    control_message,
    goal_from_registry,
    goal_objective,
    human_gate_auto_notify_marker_path,
    now_iso,
    operation_packet,
    provider_idempotency_key,
    read_goal_channel_binding,
    save_goal_binding,
    serialize_goal_binding_mutation,
    semantic_key,
    write_human_gate_auto_notify_marker,
)
from .goal_channel_transport import (
    APP_ID_PATTERN,
    CHAT_ID_PATTERN,
    MESSAGE_ID_PATTERN,
    BotChatMembershipResult,
    auth_verified,
    call,
    chat_verified,
    ensure_bot_chat_membership,
    find_first_string,
    json_payload,
    lark_args,
    message_readback_verified,
    pin_readback_verified,
    verified_app_id,
)
from .cli_transport import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    default_subprocess_runner,
)
from .presentation.kanban import (
    default_lark_kanban_config_path,
    read_lark_kanban_local_config,
    save_lark_kanban_board_config,
    setup_lark_kanban_board,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _default_goal_channel_automation(
    raw_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Human-gate auto-notify is on by default for newly configured channels."""
    automation = _mapping(raw_binding.get("automation"))
    automation.setdefault(HUMAN_GATE_AUTO_NOTIFY_SETTING, True)
    return automation


def _base_url_from_payload(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    data = data if isinstance(data, Mapping) else {}
    base = data.get("base")
    base = base if isinstance(base, Mapping) else {}
    value = str(base.get("url") or "").strip()
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _save_recovery_binding(
    *,
    binding_path: Path,
    bindings_payload: Mapping[str, Any],
    goal_id: str,
    existing: Mapping[str, Any],
    target_name: str | None,
    chat_id: str,
    chat_name: str,
    identity_mode: str,
    sender_profile: str,
    sender_identity: str,
    bot_display_name: str,
    bot_app_id: str,
    cli_bin: str,
) -> None:
    recovery = dict(existing)
    channel = _mapping(existing.get("channel"))
    identity = _mapping(existing.get("identity"))
    if target_name:
        channel = {
            **(
                {"pinned_message_id": channel["pinned_message_id"]}
                if channel.get("pinned_message_id")
                else {}
            )
        }
        identity = {}
    else:
        channel.update({"chat_id": chat_id, "chat_name": chat_name})
        identity = {
            "mode": identity_mode,
            "sender_profile": sender_profile,
            "sender_identity": sender_identity,
            "bot_app_id": bot_app_id,
            "bot_display_name": bot_display_name,
            "cli_bin": cli_bin,
        }
    recovery.update(
        {
            "goal_id": goal_id,
            "provider": "lark",
            "enabled": False,
            "channel": channel,
            "identity": identity,
            "automation": _mapping(existing.get("automation")),
            "receipts": _mapping(existing.get("receipts")),
        }
    )
    if target_name:
        recovery["target_ref"] = target_name
    save_goal_binding(
        binding_path=binding_path,
        payload=bindings_payload,
        goal_id=goal_id,
        binding=recovery,
    )


def _goal_kanban_config_path(registry_path: Path, goal_id: str) -> Path:
    default_path = default_lark_kanban_config_path(registry_path)
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", goal_id).strip("-._") or "goal"
    digest = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()[:8]
    return (
        default_path.parent
        / "goal-channels"
        / f"{slug[:48]}-{digest}"
        / default_path.name
    )


def _resolved_binding(
    *,
    raw_binding: Mapping[str, Any],
    goal_id: str,
    target_name: str | None,
    provider_target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not target_name:
        return dict(raw_binding)
    target_binding = {
        **raw_binding,
        "provider": "lark",
        "target_ref": target_name,
    }
    return (
        binding_for_goal(
            {"bindings": {goal_id: target_binding}},
            goal_id,
            provider_target=provider_target,
        )
        or {}
    )


@serialize_goal_binding_mutation
def setup_lark_goal_channel(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    target_name: str | None = None,
    provider_target: Mapping[str, Any] | None = None,
    kanban_config_path: Path | None = None,
    chat_id: str | None = None,
    chat_name: str | None = None,
    base_url: str | None = None,
    base_token: str | None = None,
    table_id: str | None = None,
    identity_mode: str | None = None,
    sender_profile: str | None = None,
    sender_identity: str | None = None,
    bot_app_id: str | None = None,
    bot_display_name: str | None = None,
    cli_bin: str | None = None,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    goal = goal_from_registry(registry, goal_id)
    bindings_payload = read_goal_channel_binding(binding_path)
    raw_existing = binding_for_goal(bindings_payload, goal_id) or {}
    existing_target_name = str(raw_existing.get("target_ref") or "")
    effective_target_name = str(target_name or existing_target_name or "") or None
    if effective_target_name:
        if provider_target is None:
            raise ValueError("Goal Channel shared target is required")
        if any(
            value is not None
            for value in (
                chat_id,
                chat_name,
                identity_mode,
                sender_profile,
                sender_identity,
                bot_app_id,
                bot_display_name,
                cli_bin,
            )
        ):
            raise ValueError(
                "Goal Channel --target cannot be combined with channel or identity flags"
            )
    existing = _resolved_binding(
        raw_binding=raw_existing,
        goal_id=goal_id,
        target_name=effective_target_name,
        provider_target=provider_target,
    )
    existing_channel = _mapping(existing.get("channel"))
    existing_identity = _mapping(existing.get("identity"))

    effective_mode = str(
        identity_mode or existing_identity.get("mode") or "local_user"
    )
    if effective_mode not in {"local_user", "project_bot"}:
        raise ValueError("Lark Goal Channel identity mode must be local_user or project_bot")
    existing_sender_identity = str(existing_identity.get("sender_identity") or "")
    effective_identity = str(
        sender_identity
        or (existing_sender_identity if existing_sender_identity == "bot" else "bot")
    )
    if effective_identity != "bot":
        raise ValueError("Lark Goal Channel messages require bot sender identity")

    effective_profile = str(
        sender_profile
        if sender_profile is not None
        else existing_identity.get("sender_profile") or ""
    )
    effective_bot_name = str(
        bot_display_name
        if bot_display_name is not None
        else existing_identity.get("bot_display_name") or ""
    )
    effective_cli_bin = str(
        cli_bin or existing_identity.get("cli_bin") or DEFAULT_CLI_BIN
    )
    effective_bot_app_id = str(
        bot_app_id
        if bot_app_id is not None
        else existing_identity.get("bot_app_id") or ""
    ).strip()
    if effective_bot_app_id and not APP_ID_PATTERN.fullmatch(effective_bot_app_id):
        raise ValueError("Lark Goal Channel bot app id must begin with cli_")
    if effective_mode == "project_bot" and (
        not effective_profile or not effective_bot_name
    ):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=execute,
            status="gate_required",
            blocker="provider_identity_unverified",
            public_summary="configure a dedicated Lark bot profile before setup",
        )

    effective_chat_id = str(chat_id or existing_channel.get("chat_id") or "").strip()
    effective_chat_name = public_safe_compact_text(
        chat_name or existing_channel.get("chat_name") or f"LoopX - {goal_id}",
        limit=60,
    )
    if execute and not auth_verified(
        runner=runner,
        cli_bin=effective_cli_bin,
        profile=None,
        identity="user",
        expected_bot_name=None,
    ):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=True,
            status="gate_required",
            blocker="provider_identity_unverified",
            public_summary="the local Lark user identity could not be verified",
        )
    if execute and not auth_verified(
        runner=runner,
        cli_bin=effective_cli_bin,
        profile=effective_profile or None,
        identity="bot",
        expected_bot_name=effective_bot_name or None,
    ):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=True,
            status="gate_required",
            blocker="provider_identity_unverified",
            public_summary="the configured Lark sender identity could not be verified",
        )
    verified_bot_app_id = (
        verified_app_id(
            runner=runner,
            cli_bin=effective_cli_bin,
            profile=effective_profile or None,
        )
        if execute
        else "cli_preview"
    )
    if execute and not verified_bot_app_id:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=True,
            status="gate_required",
            blocker="provider_identity_unverified",
            public_summary="the configured Lark bot app identity could not be verified",
        )
    if execute and not effective_bot_app_id:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=True,
            status="gate_required",
            blocker="provider_identity_selection_required",
            public_summary=(
                "select the intended Lark bot explicitly with --bot-app-id "
                "before setup"
            ),
        )
    if execute and effective_bot_app_id != verified_bot_app_id:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=True,
            status="gate_required",
            blocker="provider_identity_unverified",
            public_summary="the selected Lark bot does not match the verified profile",
        )
    effective_app_id = effective_bot_app_id or str(verified_bot_app_id)

    external_writes = 0
    if not effective_chat_id:
        if execute:
            create = call(
                runner,
                lark_args(
                    cli_bin=effective_cli_bin,
                    profile=None,
                    tail=[
                        "im",
                        "+chat-create",
                        "--name",
                        effective_chat_name,
                        "--description",
                        f"LoopX Goal Channel for {goal_id}"[:100],
                        "--chat-mode",
                        "group",
                        "--type",
                        "private",
                        "--bots",
                        str(effective_app_id),
                        "--as",
                        "user",
                        "--format",
                        "json",
                    ],
                ),
            )
            effective_chat_id = (
                find_first_string(
                    json_payload(create),
                    {"chat_id", "open_chat_id"},
                    CHAT_ID_PATTERN,
                )
                or ""
            )
            if create.get("returncode") != 0 or not effective_chat_id:
                return operation_packet(
                    ok=False,
                    goal_id=goal_id,
                    operation="setup",
                    execute=True,
                    status="failed",
                    blocker="provider_api_failed",
                    public_summary="Lark group creation failed",
                )
            external_writes += 1
            if not chat_verified(
                runner=runner,
                cli_bin=effective_cli_bin,
                profile=None,
                identity="user",
                chat_id=effective_chat_id,
            ):
                return operation_packet(
                    ok=False,
                    goal_id=goal_id,
                    operation="setup",
                    execute=True,
                    status="sent_unverified",
                    blocker="readback_mismatch",
                    public_summary="the created Lark channel could not be read back",
                    external_write_performed=True,
                )
            _save_recovery_binding(
                binding_path=binding_path,
                bindings_payload=bindings_payload,
                goal_id=goal_id,
                existing=raw_existing,
                target_name=effective_target_name,
                chat_id=effective_chat_id,
                chat_name=effective_chat_name,
                identity_mode=effective_mode,
                sender_profile=effective_profile,
                sender_identity=effective_identity,
                bot_display_name=effective_bot_name,
                bot_app_id=effective_app_id,
                cli_bin=effective_cli_bin,
            )
            bindings_payload = read_goal_channel_binding(binding_path)
            raw_existing = binding_for_goal(bindings_payload, goal_id) or {}
            existing = _resolved_binding(
                raw_binding=raw_existing,
                goal_id=goal_id,
                target_name=effective_target_name,
                provider_target=provider_target,
            )
        else:
            effective_chat_id = "oc_preview"
    elif execute:
        if not chat_verified(
            runner=runner,
            cli_bin=effective_cli_bin,
            profile=None,
            identity="user",
            chat_id=effective_chat_id,
        ):
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="setup",
                execute=True,
                status="gate_required",
                blocker="channel_membership_unverified",
                public_summary="the configured Lark channel is not reachable by the user",
            )

    if execute:
        membership_result = ensure_bot_chat_membership(
            runner=runner,
            cli_bin=effective_cli_bin,
            membership_profile=None,
            bot_profile=effective_profile or None,
            chat_id=effective_chat_id,
            app_id=str(effective_app_id),
        )
        if membership_result is BotChatMembershipResult.ADD_FAILED:
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="setup",
                execute=True,
                status="failed",
                blocker="provider_api_failed",
                public_summary="the Lark bot could not be added to the Goal Channel",
                external_write_performed=bool(external_writes),
            )
        if membership_result.external_write_performed:
            external_writes += 1
        if membership_result in {
            BotChatMembershipResult.ALREADY_UNVERIFIED,
            BotChatMembershipResult.ADDED_UNVERIFIED,
        }:
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="setup",
                execute=True,
                status="gate_required",
                blocker="channel_membership_unverified",
                public_summary="the configured Lark bot is not a verified channel member",
                external_write_performed=bool(external_writes),
            )

    existing_kanban = _mapping(raw_existing.get("kanban"))
    existing_kanban_path = str(existing_kanban.get("config_path") or "")
    effective_kanban_path = kanban_config_path or (
        Path(existing_kanban_path).expanduser()
        if existing_kanban_path
        else _goal_kanban_config_path(registry_path, goal_id)
        if effective_target_name
        else default_lark_kanban_config_path(registry_path)
    )
    kanban = setup_lark_kanban_board(
        config_path=effective_kanban_path,
        base_name=f"LoopX - {goal_id}",
        base_url=base_url,
        base_token=base_token,
        table_id=table_id,
        cli_bin=effective_cli_bin,
        identity="user",
        execute=execute,
        runner=runner,
    )
    if not kanban.get("ok"):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=execute,
            status="failed",
            blocker="provider_api_failed",
            public_summary="the Lark Kanban board could not be prepared",
            external_write_performed=bool(
                execute and (external_writes or kanban.get("created_base"))
            ),
        )
    if execute and (kanban.get("created_base") or kanban.get("created_table")):
        external_writes += 1
    board_payload = read_lark_kanban_local_config(effective_kanban_path)
    board = _mapping(board_payload.get("board"))
    kanban_url = str(board.get("base_url") or "").strip()
    if execute and not kanban_url:
        base_get = call(
            runner,
            [
                effective_cli_bin,
                "base",
                "+base-get",
                "--as",
                "user",
                "--base-token",
                str(board.get("base_token") or ""),
                "--format",
                "json",
            ],
        )
        kanban_url = _base_url_from_payload(json_payload(base_get))
        if base_get.get("returncode") != 0 or not kanban_url:
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="setup",
                execute=True,
                status="sent_unverified",
                blocker="readback_mismatch",
                public_summary="the Lark Kanban URL could not be read back",
                external_write_performed=bool(external_writes),
            )
        save_lark_kanban_board_config(
            effective_kanban_path,
            base_token=str(board.get("base_token") or ""),
            table_id=str(board.get("table_id") or ""),
            view_id=str(board.get("view_id") or "") or None,
            base_url=kanban_url,
            base_name=str(board.get("base_name") or "") or None,
            table_name=str(board.get("table_name") or "") or None,
            cli_bin=effective_cli_bin,
            identity="user",
            view_ids=_mapping(board.get("view_ids")),
        )
        board_payload = read_lark_kanban_local_config(effective_kanban_path)
        board = _mapping(board_payload.get("board"))
    message = control_message(
        goal_id=goal_id,
        objective=goal_objective(goal),
        kanban_url=kanban_url,
    )
    control_key = semantic_key(
        goal_id,
        "lark",
        "control_message",
        message,
        effective_chat_id,
    )
    receipts = _mapping(existing.get("receipts"))
    control_receipt = _mapping(receipts.get(control_key))
    control_message_id = str(control_receipt.get("message_id") or "")
    if execute and control_message_id:
        control_verified = message_readback_verified(
            runner=runner,
            cli_bin=effective_cli_bin,
            profile=effective_profile or None,
            identity=effective_identity,
            message_id=control_message_id,
            expected_text=message,
        ) and pin_readback_verified(
            runner=runner,
            cli_bin=effective_cli_bin,
            profile=effective_profile or None,
            identity=effective_identity,
            chat_id=effective_chat_id,
            message_id=control_message_id,
        )
        if not control_verified:
            control_message_id = ""
    if execute and not control_message_id:
        send = call(
            runner,
            lark_args(
                cli_bin=effective_cli_bin,
                profile=effective_profile or None,
                tail=[
                    "im",
                    "+messages-send",
                    "--chat-id",
                    effective_chat_id,
                    "--text",
                    message,
                    "--idempotency-key",
                    provider_idempotency_key(control_key),
                    "--as",
                    effective_identity,
                    "--format",
                    "json",
                ],
            ),
        )
        control_message_id = (
            find_first_string(
                json_payload(send),
                {"message_id"},
                MESSAGE_ID_PATTERN,
            )
            or ""
        )
        if send.get("returncode") != 0 or not control_message_id:
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="setup",
                execute=True,
                status="failed",
                blocker="provider_api_failed",
                public_summary="the Goal Control message could not be sent",
                external_write_performed=bool(external_writes),
            )
        external_writes += 1
        pin = call(
            runner,
            lark_args(
                cli_bin=effective_cli_bin,
                profile=effective_profile or None,
                tail=[
                    "im",
                    "pins",
                    "create",
                    "--data",
                    json.dumps({"message_id": control_message_id}),
                    "--as",
                    effective_identity,
                    "--format",
                    "json",
                ],
            ),
        )
        if pin.get("returncode") != 0:
            return operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="setup",
                execute=True,
                status="sent_unverified",
                blocker="provider_api_failed",
                public_summary="the Goal Control message was sent but could not be pinned",
                external_write_performed=True,
            )
        external_writes += 1
    readback_verified = bool(
        not execute
        or (
            control_message_id
            and message_readback_verified(
                runner=runner,
                cli_bin=effective_cli_bin,
                profile=effective_profile or None,
                identity=effective_identity,
                message_id=control_message_id,
                expected_text=message,
            )
            and pin_readback_verified(
                runner=runner,
                cli_bin=effective_cli_bin,
                profile=effective_profile or None,
                identity=effective_identity,
                chat_id=effective_chat_id,
                message_id=control_message_id,
            )
        )
    )
    if execute and not readback_verified:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="setup",
            execute=True,
            status="sent_unverified",
            blocker="readback_mismatch",
            public_summary="Goal Channel setup writes could not be read back",
            external_write_performed=bool(external_writes),
        )

    if execute:
        mutable_receipts = dict(receipts)
        mutable_receipts[control_key] = {
            "kind": "control_message",
            "message_id": control_message_id,
            "verified_at": now_iso(),
        }
        saved_channel = {"pinned_message_id": control_message_id}
        saved_identity: dict[str, Any] = {}
        if not effective_target_name:
            saved_channel.update(
                {
                    "chat_id": effective_chat_id,
                    "chat_name": effective_chat_name,
                }
            )
            saved_identity = {
                "mode": effective_mode,
                "sender_profile": effective_profile,
                "sender_identity": effective_identity,
                "bot_app_id": effective_app_id,
                "bot_display_name": effective_bot_name,
                "cli_bin": effective_cli_bin,
            }
        automation = _default_goal_channel_automation(raw_existing)
        saved_binding: dict[str, Any] = {
            "goal_id": goal_id,
            "provider": "lark",
            "enabled": True,
            "channel": saved_channel,
            "kanban": {
                "config_path": str(effective_kanban_path),
                "base_token": str(board.get("base_token") or ""),
                "table_id": str(board.get("table_id") or ""),
                "base_url": kanban_url,
            },
            "identity": saved_identity,
            "automation": automation,
            "receipts": mutable_receipts,
        }
        if effective_target_name:
            saved_binding["target_ref"] = effective_target_name
        save_goal_binding(
            binding_path=binding_path,
            payload=bindings_payload,
            goal_id=goal_id,
            binding=saved_binding,
        )
        marker_path = human_gate_auto_notify_marker_path(binding_path, goal_id)
        if automation.get(HUMAN_GATE_AUTO_NOTIFY_SETTING) is True:
            write_human_gate_auto_notify_marker(marker_path)
        else:
            clear_human_gate_auto_notify_marker(marker_path)
    return operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="setup",
        execute=execute,
        status="configured" if execute else "preview_ready",
        public_summary=(
            "configured one Lark Goal Channel with a Kanban and pinned control message"
            if execute
            else "previewed one Lark Goal Channel setup"
        ),
        external_write_performed=bool(execute and external_writes),
        readback_verified=readback_verified,
        idempotency_key=control_key,
        receipt_id=f"receipt_{control_key.removeprefix('sha256:')[:16]}",
        details={
            "channel_bound": bool(execute),
            "kanban_ready": bool(kanban.get("ok")),
            "control_message_ready": bool(not execute or control_message_id),
            "control_message_pinned": bool(not execute or readback_verified),
        },
    )
