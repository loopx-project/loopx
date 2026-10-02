"""Authorized Lark delivery for typed blocked Todo notices."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.quota.blocked_transition_notice import (
    blocked_transition_notice_identity,
    blocked_transition_notice_owner_reason,
    build_blocked_transition_notice,
)
from ...control_plane.todos.contract import (
    TODO_STATUS_OPEN,
    TODO_TERMINAL_STATUS_VALUES,
    normalize_todo_status,
)
from .goal_channel_contracts import (
    BLOCKED_NOTICE_RETIRED_STATES,
    BlockedNoticeReceiptState,
    blocked_notice_receipt_matches_target,
    binding_for_goal,
    blocked_notice_auto_notify_enabled,
    now_iso,
    provider_idempotency_key,
    read_goal_channel_binding,
    save_goal_binding,
    semantic_key,
    serialize_goal_binding_mutation,
)
from .goal_channel_transport import (
    APP_ID_PATTERN,
    CHAT_ID_PATTERN,
    MESSAGE_ID_PATTERN,
    auth_verified,
    call,
    chat_verified,
    find_first_string,
    json_payload,
    lark_args,
    message_readback_verified,
    verified_app_id,
)
from .presentation.kanban import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    default_subprocess_runner,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _active_notices(
    status: Mapping[str, Any], goal_id: str, quota_packet: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    fallback = _mapping(quota_packet.get("blocked_priority_fallback"))
    selected = _mapping(fallback.get("selected_executable"))
    notices = {
        str(value["blocker_identity"]): dict(value)
        for value in fallback.get("blocked_transition_notices", [])
        if isinstance(value, Mapping)
        and value.get("blocker_identity") and value.get("blocker_revision")
        and not value.get("superseded_by")
    }
    observed: dict[str, dict[str, Any]] = {}
    queue = _mapping(status.get("attention_queue"))
    for goal in queue.get("items", []):
        if not isinstance(goal, Mapping) or str(goal.get("goal_id") or "") != goal_id:
            continue
        for lane in ("agent_todos", "user_todos"):
            group = _mapping(goal.get(lane))
            for item in group.get("items", []):
                if not isinstance(item, Mapping):
                    continue
                identity = blocked_transition_notice_identity(item)
                if identity is None:
                    continue
                observed[identity] = dict(item)
                notice = build_blocked_transition_notice(
                    item, selected_executable=selected or None
                )
                # Explicit canonical rows supersede an older quota projection.
                if notice is not None and not notice.get("superseded_by"):
                    notices[identity] = notice
                else:
                    notices.pop(identity, None)
    return list(notices.values()), observed


MAX_BLOCKED_NOTICE_EFFECTS_PER_REFRESH = 8


def _reconcile_receipts(
    receipts: dict[str, Any], observed: Mapping[str, Mapping[str, Any]],
) -> bool:
    changed = False
    for key, receipt in list(receipts.items()):
        if (not isinstance(receipt, Mapping)
                or receipt.get("kind") != "blocked_notice"
                or receipt.get("state") in BLOCKED_NOTICE_RETIRED_STATES):
            continue
        item = observed.get(str(receipt.get("blocker_identity") or ""))
        if item is None:
            continue  # A missing or paginated row is not a recovery observation.
        status = normalize_todo_status(item.get("status"))
        notice = build_blocked_transition_notice(item)
        state = None
        if item.get("superseded_by"):
            state = BlockedNoticeReceiptState.SUPERSEDED
        elif notice is not None:
            if notice["blocker_revision"] != receipt.get("blocker_revision"):
                state = BlockedNoticeReceiptState.SUPERSEDED
        elif status == TODO_STATUS_OPEN:
            state = BlockedNoticeReceiptState.RESUMED
        elif status in TODO_TERMINAL_STATUS_VALUES:
            state = BlockedNoticeReceiptState.RESOLVED
        if state is not None:
            receipts[key] = {**receipt, "state": state, "reconciled_at": now_iso()}
            changed = True
    return changed


def _counter(receipt: Mapping[str, Any], name: str) -> int:
    value = receipt.get(name)
    return value if type(value) is int and value >= 0 else 0


def _receipt_for_notice(
    receipts: Mapping[str, Any], notice: Mapping[str, Any], *, goal_id: str, chat_id: str,
) -> tuple[str, dict[str, Any]]:
    identity, revision = str(notice["blocker_identity"]), str(notice["blocker_revision"])
    previous = [
        (key, receipt) for key, receipt in receipts.items()
        if isinstance(receipt, Mapping)
        and receipt.get("blocker_identity") == identity
        and receipt.get("blocker_revision") == revision
        and blocked_notice_receipt_matches_target(key, receipt, goal_id=goal_id, chat_id=chat_id)
    ]
    if previous:
        key, latest = max(previous, key=lambda pair: _counter(pair[1], "generation"))
        if latest.get("state") not in BLOCKED_NOTICE_RETIRED_STATES:
            return key, dict(latest)
        generation = _counter(latest, "generation") + 1
    else:
        generation = 0
    parts = [goal_id, "lark", "blocked_notice", identity, revision, chat_id]
    key = semantic_key(*parts, "generation", str(generation)) if generation else semantic_key(*parts)
    return key, {
        "kind": "blocked_notice", "blocker_identity": identity, "blocker_revision": revision,
        "chat_id": chat_id, "generation": generation,
        "state": BlockedNoticeReceiptState.PENDING, "readback_verified": False,
    }


def _notice_message(goal_id: str, notice: Mapping[str, Any]) -> str:
    reason = blocked_transition_notice_owner_reason(notice) or "A Goal task is blocked."
    evidence = notice.get("evidence")
    evidence_text = (
        "; ".join(str(value) for value in evidence[:4])
        if isinstance(evidence, list)
        else ""
    )
    responsible = str(notice.get("responsible_party") or "unknown")
    recovery = _mapping(notice.get("recovery_condition"))
    return "\n".join(
        filter(
            None,
            (
                f"LoopX Goal {goal_id}: blocked task",
                reason,
                f"Evidence: {evidence_text}" if evidence_text else "",
                f"Responsible: {responsible}",
                f"Recovery: {recovery.get('description') or 'clear the blocker'}",
                "Owner action required."
                if notice.get("owner_must_act") is True
                else "No owner action required.",
            ),
        )
    )


@serialize_goal_binding_mutation
def deliver_blocked_notices(
    *,
    goal_id: str,
    binding_path: Path,
    status: Mapping[str, Any],
    quota_packet: Mapping[str, Any],
    provider_target: Mapping[str, Any] | None = None,
    external_sink_delivery_authorized: bool,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    payload = read_goal_channel_binding(binding_path)
    raw_binding = binding_for_goal(payload, goal_id)
    binding = binding_for_goal(payload, goal_id, provider_target=provider_target)
    enabled = blocked_notice_auto_notify_enabled(raw_binding)
    notices, observed = _active_notices(status, goal_id, quota_packet)
    result: dict[str, Any] = {
        "schema_version": "loopx_goal_channel_blocked_notice_delivery_v0",
        "ok": True,
        "enabled": enabled,
        "status": "not_configured" if raw_binding is None else "disabled",
        "notice_count": len(notices),
        "delivered_count": 0,
        "pending_count": len(notices),
        "external_write_performed": False,
        "readback_verified": False,
        "delivery_postcondition": {"satisfied": True, "blocks_delivery": False},
    }
    if not enabled:
        return result
    result["delivery_postcondition"]["satisfied"] = not notices
    if not external_sink_delivery_authorized:
        result["status"] = "external_sink_suppressed"
        return result
    if binding is None or binding.get("enabled") is not True:
        result.update(
            ok=False,
            status="channel_binding_incomplete",
            blocker="channel_binding_incomplete",
        )
        return result
    if raw_binding.get("target_ref") and provider_target is None:
        result.update(
            ok=False,
            status="provider_target_missing",
            blocker="provider_target_missing",
        )
        return result
    channel = _mapping(binding.get("channel"))
    identity_config = _mapping(binding.get("identity"))
    chat_id = str(channel.get("chat_id") or "")
    cli_bin = str(identity_config.get("cli_bin") or DEFAULT_CLI_BIN)
    profile = str(identity_config.get("sender_profile") or "") or None
    app_id = str(identity_config.get("bot_app_id") or "")
    if notices and not (
        identity_config.get("sender_identity") == "bot"
        and APP_ID_PATTERN.fullmatch(app_id)
        and auth_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            expected_bot_name=str(identity_config.get("bot_display_name") or "")
            or None,
        )
        and verified_app_id(runner=runner, cli_bin=cli_bin, profile=profile) == app_id
    ):
        result.update(
            ok=False,
            status="provider_identity_unverified",
            blocker="provider_identity_unverified",
        )
        return result
    if notices and (
        not CHAT_ID_PATTERN.fullmatch(chat_id)
        or not chat_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            chat_id=chat_id,
        )
    ):
        result.update(
            ok=False,
            status="channel_membership_unverified",
            blocker="channel_membership_unverified",
        )
        return result
    receipts = _mapping(raw_binding.get("receipts"))
    changed = _reconcile_receipts(receipts, observed)
    candidates = []
    for notice in notices:
        key, receipt = _receipt_for_notice(receipts, notice, goal_id=goal_id, chat_id=chat_id)
        if key not in receipts:
            receipts[key] = receipt
            changed = True
        candidates.append((notice, key, receipt))
    # Unattempted effects precede retries, so a persistent failure cannot starve
    # later candidates. Already verified receipts never consume the send budget.
    candidates.sort(key=lambda candidate: _counter(candidate[2], "attempt_count"))
    delivered = attempts = deferred = 0
    for notice, key, existing in candidates:
        if (existing.get("readback_verified") is True
                and existing.get("state") == BlockedNoticeReceiptState.DELIVERED):
            delivered += 1
            continue
        if attempts >= MAX_BLOCKED_NOTICE_EFFECTS_PER_REFRESH:
            deferred += 1
            continue
        attempts += 1
        existing = {
            **existing, "chat_id": chat_id,
            "attempt_count": _counter(existing, "attempt_count") + 1,
        }
        message = _notice_message(goal_id, notice)
        send = call(
            runner,
            lark_args(
                cli_bin=cli_bin, profile=profile,
                tail=[
                    "im", "+messages-send", "--chat-id", chat_id,
                    "--text", message, "--idempotency-key", provider_idempotency_key(key),
                    "--as", "bot", "--format", "json",
                ],
            ),
        )
        message_id = find_first_string(json_payload(send), {"message_id"}, MESSAGE_ID_PATTERN) or ""
        if send.get("returncode") != 0 or not message_id:
            receipts[key] = {
                **existing, "state": BlockedNoticeReceiptState.PENDING,
                "readback_verified": False, "failure_code": "provider_api_failed",
            }
            result.update(ok=False, status="provider_api_failed", blocker="provider_api_failed")
        else:
            result["external_write_performed"] = True
            verified = message_readback_verified(
                runner=runner, cli_bin=cli_bin, profile=profile, identity="bot",
                message_id=message_id, expected_text=message,
            )
            sent_at = now_iso()
            receipts[key] = {
                **existing, "message_id": message_id, "sent_at": sent_at,
                "verified_at": sent_at if verified else None, "readback_verified": verified,
                "state": (BlockedNoticeReceiptState.DELIVERED if verified
                          else BlockedNoticeReceiptState.SENT_UNVERIFIED),
            }
            receipts[key].pop("failure_code", None)
            if verified:
                delivered += 1
            else:
                result.update(ok=False, status="sent_unverified", blocker="readback_mismatch")
        mutable = {**raw_binding, "receipts": receipts}
        save_goal_binding(
            binding_path=binding_path, payload=payload, goal_id=goal_id, binding=mutable,
        )
        changed = False
    if changed:
        save_goal_binding(
            binding_path=binding_path, payload=payload, goal_id=goal_id,
            binding={**raw_binding, "receipts": receipts},
        )
    result["delivered_count"] = delivered
    result["pending_count"] = len(notices) - delivered
    result["deferred_count"] = deferred
    result["readback_verified"] = bool(notices and delivered == len(notices))
    result["delivery_postcondition"]["satisfied"] = not result["pending_count"]
    if result["ok"]:
        result["status"] = (
            "pending" if result["pending_count"] else "sent_verified" if notices else "no_active_blocker"
        )
    return result
