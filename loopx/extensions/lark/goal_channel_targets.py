from __future__ import annotations

import json
import re
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...file_lock import exclusive_file_lock
from .goal_channel_contracts import operation_packet
from .goal_channel_transport import APP_ID_PATTERN, CHAT_ID_PATTERN, OPEN_ID_PATTERN
from .cli_transport import DEFAULT_CLI_BIN
from .private_json import write_private_json_atomic

GOAL_CHANNEL_TARGETS_SCHEMA_VERSION = "loopx_goal_channel_provider_targets_v0"
TARGET_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def default_goal_channel_target_path(runtime_root: Path) -> Path:
    return runtime_root.expanduser().resolve() / "goal-channel-targets.json"


def read_goal_channel_targets(path: Path) -> dict[str, Any]:
    target_path = path.expanduser()
    if not target_path.exists():
        return {
            "schema_version": GOAL_CHANNEL_TARGETS_SCHEMA_VERSION,
            "targets": {},
        }
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Goal Channel target root must be a JSON object")
    if payload.get("schema_version") != GOAL_CHANNEL_TARGETS_SCHEMA_VERSION:
        raise ValueError(
            f"Goal Channel targets must use {GOAL_CHANNEL_TARGETS_SCHEMA_VERSION}"
        )
    if not isinstance(payload.get("targets"), dict):
        raise ValueError("Goal Channel targets require an object `targets`")
    return payload


def goal_channel_target_for_name(
    payload: Mapping[str, Any],
    target_name: str,
) -> dict[str, Any] | None:
    targets = payload.get("targets")
    target = targets.get(target_name) if isinstance(targets, Mapping) else None
    return dict(target) if isinstance(target, Mapping) else None


def _normalized_target_name(value: str) -> str:
    target_name = str(value or "").strip()
    if not TARGET_NAME_PATTERN.fullmatch(target_name):
        raise ValueError(
            "Goal Channel target name must use lowercase letters, digits, '.', '_', "
            "or '-' and begin with a letter or digit"
        )
    return target_name


def _normalized_lark_target(
    *,
    target_name: str,
    chat_id: str,
    chat_name: str | None,
    identity_mode: str | None,
    sender_profile: str | None,
    sender_identity: str | None,
    bot_app_id: str | None,
    bot_open_id: str | None,
    bot_display_name: str | None,
    cli_bin: str | None,
) -> dict[str, Any]:
    safe_name = _normalized_target_name(target_name)
    safe_chat_id = str(chat_id or "").strip()
    if not CHAT_ID_PATTERN.fullmatch(safe_chat_id):
        raise ValueError("Lark Goal Channel target chat id must begin with oc_")
    mode = str(identity_mode or "local_user")
    if mode not in {"local_user", "project_bot"}:
        raise ValueError(
            "Lark Goal Channel identity mode must be local_user or project_bot"
        )
    identity = str(sender_identity or "bot")
    if identity != "bot":
        raise ValueError("Lark Goal Channel messages require bot sender identity")
    app_id = str(bot_app_id or "").strip()
    if not APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError("Lark Goal Channel target bot app id must begin with cli_")
    open_id = str(bot_open_id or "").strip()
    if open_id and not OPEN_ID_PATTERN.fullmatch(open_id):
        raise ValueError("Lark Goal Channel target bot open id must begin with ou_")
    profile = str(sender_profile or "")
    bot_name = str(bot_display_name or "")
    if mode == "project_bot" and (not profile or not bot_name):
        raise ValueError(
            "project_bot targets require sender profile and bot display name"
        )
    target = {
        "name": safe_name,
        "provider": "lark",
        "enabled": True,
        "channel": {
            "chat_id": safe_chat_id,
            "chat_name": public_safe_compact_text(chat_name or safe_name, limit=60),
        },
        "identity": {
            "mode": mode,
            "sender_profile": profile,
            "sender_identity": identity,
            "bot_app_id": app_id,
            "bot_display_name": bot_name,
            "cli_bin": str(cli_bin or DEFAULT_CLI_BIN),
        },
    }
    if open_id:
        target["identity"]["bot_open_id"] = open_id
    return target


def add_lark_goal_channel_target(
    *,
    target_path: Path,
    target_name: str,
    chat_id: str,
    chat_name: str | None = None,
    identity_mode: str | None = None,
    sender_profile: str | None = None,
    sender_identity: str | None = None,
    bot_app_id: str | None = None,
    bot_open_id: str | None = None,
    bot_display_name: str | None = None,
    cli_bin: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    target = _normalized_lark_target(
        target_name=target_name,
        chat_id=chat_id,
        chat_name=chat_name,
        identity_mode=identity_mode,
        sender_profile=sender_profile,
        sender_identity=sender_identity,
        bot_app_id=bot_app_id,
        bot_open_id=bot_open_id,
        bot_display_name=bot_display_name,
        cli_bin=cli_bin,
    )
    mutation_lock = (
        exclusive_file_lock(target_path, operation="lark_goal_channel_target")
        if execute
        else nullcontext()
    )
    with mutation_lock:
        payload = read_goal_channel_targets(target_path)
        targets = dict(payload.get("targets") or {})
        existing = targets.get(target["name"])
        changed = existing != target
        if execute and changed:
            targets[str(target["name"])] = target
            write_private_json_atomic(
                target_path,
                {
                    "schema_version": GOAL_CHANNEL_TARGETS_SCHEMA_VERSION,
                    "targets": targets,
                },
            )
            readback = read_goal_channel_targets(target_path)
            if goal_channel_target_for_name(readback, str(target["name"])) != target:
                return operation_packet(
                    ok=False,
                    goal_id=None,
                    operation="target_add",
                    execute=True,
                    status="failed",
                    blocker="readback_mismatch",
                    public_summary=(
                        "the shared Goal Channel target could not be read back"
                    ),
                )
    status = (
        "configured"
        if execute and changed
        else "already_configured"
        if execute
        else "preview_ready"
    )
    return operation_packet(
        ok=True,
        goal_id=None,
        operation="target_add",
        execute=execute,
        status=status,
        public_summary=(
            "configured one local-private shared Goal Channel target"
            if execute and changed
            else "the shared Goal Channel target is already configured"
            if execute
            else "previewed one shared Goal Channel target"
        ),
        readback_verified=bool(execute),
        details={
            "target_name": target["name"],
            "changed": changed,
            "target_count": len(set(targets) | {str(target["name"])}),
        },
    )


def list_goal_channel_targets(*, target_path: Path) -> dict[str, Any]:
    payload = read_goal_channel_targets(target_path)
    targets = payload.get("targets")
    items = [
        {
            "target_name": name,
            "provider": str(target.get("provider") or ""),
            "enabled": target.get("enabled") is True,
        }
        for name, target in sorted(
            targets.items() if isinstance(targets, Mapping) else []
        )
        if isinstance(target, Mapping)
    ]
    return operation_packet(
        ok=True,
        goal_id=None,
        operation="target_list",
        execute=False,
        status="ready",
        public_summary="listed configured shared Goal Channel targets",
        readback_verified=True,
        details={"target_count": len(items), "items": items},
    )
