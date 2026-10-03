from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

from ...capabilities.issue_fix.reviewer_notification import (
    SAFE_LOCAL_KEY_PATTERN,
    CommandRunner,
    build_reviewer_notification_sink_result,
    reviewer_notification_idempotency_key,
)
from ...control_plane.runtime.public_safety import public_safe_compact_text
from .identity_shapes import (
    LARK_CHAT_ID_PATTERN,
    LARK_MESSAGE_ID_PATTERN,
    LARK_OPEN_ID_PATTERN,
)
from .outbound import (
    LarkMention,
    build_lark_mention_prefix,
    lark_provider_preview_matches_outbound,
    lark_readback_matches_outbound,
    normalize_lark_outbound_text,
)

LARK_PERMISSION_PATTERN = re.compile(
    r"(?:missing\s+scope|permission|not\s+in\s+(?:the\s+)?chat|"
    r"lacks?\s+authority|99991672|230027|232033)",
    re.IGNORECASE,
)
LARK_SEARCH_PERMISSION_PATTERN = re.compile(
    r"search:message|missing\s+scope[^\n]*(?:message|search)",
    re.IGNORECASE,
)


def _normalise_handle(value: Any) -> str | None:
    text = public_safe_compact_text(value, limit=100).strip().lstrip("@").lower()
    return f"@{text}" if text else None


def _find_message_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("message_id")
        if isinstance(candidate, str) and LARK_MESSAGE_ID_PATTERN.fullmatch(candidate):
            return candidate
        for nested in value.values():
            found = _find_message_id(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_message_id(nested)
            if found:
                return found
    return None


def _find_message(value: Any, message_id: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if str(value.get("message_id") or "") == message_id:
            return value
        for nested in value.values():
            found = _find_message(nested, message_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_message(nested, message_id)
            if found is not None:
                return found
    return None


def _parse_json_object(value: Any) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _payload_contains_text(value: Any, text: str) -> bool:
    if isinstance(value, Mapping):
        return any(_payload_contains_text(child, text) for child in value.values())
    if isinstance(value, list):
        return any(_payload_contains_text(child, text) for child in value)
    return isinstance(value, str) and text in value


def _lark_member_ids(value: Any) -> set[str]:
    """Collect exact member ids from a provider response without retaining it."""

    member_ids: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if key in {"member_id", "open_id"} and isinstance(child, str):
                    member_id = child.strip()
                    if LARK_OPEN_ID_PATTERN.fullmatch(member_id):
                        member_ids.add(member_id)
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return member_ids


def lark_reviewer_notification_sink(
    *,
    repo: str,
    pr_number: int,
    pr_url: str,
    pr_title: str,
    linked_issue_refs: Sequence[str],
    reviewer_handles: Sequence[str],
    sink: Mapping[str, Any],
    receipts: set[str],
    execute: bool,
    runner: CommandRunner,
) -> dict[str, Any]:
    sink_kind = "lark_chat"
    if sink.get("identity_scope") != "project_dedicated":
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=[],
            idempotency_key=None,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=execute,
            blocker="dedicated_bot_identity_required",
        )

    explicit_profile_bindings = any(
        sink.get(field) is not None
        for field in (
            "reader_profile",
            "reader_identity",
            "sender_profile",
            "sender_identity",
        )
    )
    reader_profile = str(sink.get("reader_profile") or "").strip()
    reader_identity = str(sink.get("reader_identity") or "").strip()
    profile = str(sink.get("sender_profile") or sink.get("bot_profile") or "").strip()
    sender_identity = str(sink.get("sender_identity") or "bot").strip()
    expected_bot_name = public_safe_compact_text(
        sink.get("bot_display_name"),
        limit=100,
    )
    destination_id = str(sink.get("destination_id") or "").strip()
    instance_key = str(sink.get("sink_instance_key") or "").strip()
    if (
        not SAFE_LOCAL_KEY_PATTERN.fullmatch(profile)
        or profile.lower() == "default"
        or not SAFE_LOCAL_KEY_PATTERN.fullmatch(instance_key)
        or not expected_bot_name
        or sender_identity != "bot"
        or (
            explicit_profile_bindings
            and (
                not SAFE_LOCAL_KEY_PATTERN.fullmatch(reader_profile)
                or reader_identity != "user"
                or not sink.get("sender_profile")
            )
        )
    ):
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=[],
            idempotency_key=None,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=execute,
            blocker="dedicated_bot_identity_required",
        )
    if not LARK_CHAT_ID_PATTERN.fullmatch(destination_id):
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=[],
            idempotency_key=None,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=execute,
            blocker="reviewer_notification_destination_unavailable",
        )

    raw_identities = sink.get("reviewer_identities")
    identities = {
        handle: value
        for raw_handle, value in (
            raw_identities.items() if isinstance(raw_identities, Mapping) else []
        )
        if (handle := _normalise_handle(raw_handle)) is not None
    }
    resolved: list[tuple[str, str, str]] = []
    for handle in reviewer_handles:
        value = identities.get(handle)
        identity = value if isinstance(value, Mapping) else {}
        member_id = str(identity.get("member_id") or "").strip()
        display_name = public_safe_compact_text(
            identity.get("display_name") or handle,
            limit=80,
        )
        if not LARK_OPEN_ID_PATTERN.fullmatch(member_id):
            return build_reviewer_notification_sink_result(
                sink_kind=sink_kind,
                reviewer_handles=[],
                idempotency_key=None,
                status="gate_required",
                ok=False,
                external_write_authority_asserted=execute,
                blocker="reviewer_notification_identity_unresolved",
            )
        resolved.append((handle, member_id, display_name or handle))

    key = reviewer_notification_idempotency_key(
        repo=repo,
        pr_number=pr_number,
        sink_kind=sink_kind,
        sink_instance_key=instance_key,
        reviewer_handles=reviewer_handles,
    )
    if key in receipts:
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="already_notified",
            ok=True,
            external_write_authority_asserted=execute,
            notification_verified=True,
            semantic_dedupe_status="persisted_evidence_match",
        )
    if not execute:
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="preview_ready",
            ok=True,
            external_write_authority_asserted=False,
        )

    reader_verified = False
    semantic_dedupe_status = "not_configured"
    if explicit_profile_bindings:
        try:
            reader_status = runner(
                [
                    "lark-cli",
                    "--profile",
                    reader_profile,
                    "auth",
                    "status",
                    "--verify",
                    "--json",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            reader_status = {"returncode": 1}
        reader_payload = _parse_json_object(reader_status.get("stdout"))
        reader = (
            (reader_payload.get("identities") or {}).get("user")
            if isinstance(reader_payload, Mapping)
            and isinstance(reader_payload.get("identities"), Mapping)
            else {}
        )
        reader = reader if isinstance(reader, Mapping) else {}
        reader_verified = bool(
            reader_status.get("returncode") == 0
            and reader.get("available") is True
            and reader.get("verified") is True
        )
        if not reader_verified:
            return build_reviewer_notification_sink_result(
                sink_kind=sink_kind,
                reviewer_handles=reviewer_handles,
                idempotency_key=key,
                status="gate_required",
                ok=False,
                external_write_authority_asserted=True,
                blocker="reviewer_notification_reader_auth_required",
            )
        try:
            members = runner(
                [
                    "lark-cli",
                    "--profile",
                    reader_profile,
                    "im",
                    "chat.members",
                    "get",
                    "--chat-id",
                    destination_id,
                    "--member-id-type",
                    "open_id",
                    "--page-all",
                    "--as",
                    "user",
                    "--format",
                    "json",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            members = {"returncode": 1}
        if members.get("returncode") != 0:
            provider_error = " ".join(
                str(members.get(field) or "") for field in ("stderr", "stdout")
            )
            return build_reviewer_notification_sink_result(
                sink_kind=sink_kind,
                reviewer_handles=reviewer_handles,
                idempotency_key=key,
                status="gate_required",
                ok=False,
                external_write_authority_asserted=True,
                reader_identity_verified=True,
                blocker=(
                    "lark_bot_group_access_required"
                    if LARK_PERMISSION_PATTERN.search(provider_error)
                    else "reviewer_notification_provider_failed"
                ),
            )

        try:
            semantic_search = runner(
                [
                    "lark-cli",
                    "--profile",
                    reader_profile,
                    "im",
                    "+messages-search",
                    "--chat-id",
                    destination_id,
                    "--query",
                    pr_url,
                    "--page-size",
                    "20",
                    "--as",
                    "user",
                    "--no-reactions",
                    "--format",
                    "json",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            semantic_search = {"returncode": 1}
        semantic_payload = _parse_json_object(semantic_search.get("stdout"))
        if semantic_search.get("returncode") == 0:
            if semantic_payload is None:
                return build_reviewer_notification_sink_result(
                    sink_kind=sink_kind,
                    reviewer_handles=reviewer_handles,
                    idempotency_key=key,
                    status="gate_required",
                    ok=False,
                    external_write_authority_asserted=True,
                    reader_identity_verified=True,
                    semantic_dedupe_status="provider_failed",
                    blocker="reviewer_notification_dedupe_readback_failed",
                )
            if _payload_contains_text(semantic_payload, pr_url):
                return build_reviewer_notification_sink_result(
                    sink_kind=sink_kind,
                    reviewer_handles=reviewer_handles,
                    idempotency_key=key,
                    status="already_notified",
                    ok=True,
                    external_write_authority_asserted=True,
                    verification_performed=True,
                    notification_verified=True,
                    reader_identity_verified=True,
                    semantic_dedupe_status="configured_chat_match",
                )
            semantic_dedupe_status = "configured_chat_no_match"
        else:
            provider_error = " ".join(
                str(semantic_search.get(field) or "") for field in ("stderr", "stdout")
            )
            if LARK_SEARCH_PERMISSION_PATTERN.search(provider_error):
                semantic_dedupe_status = "permission_fallback"
            else:
                return build_reviewer_notification_sink_result(
                    sink_kind=sink_kind,
                    reviewer_handles=reviewer_handles,
                    idempotency_key=key,
                    status="gate_required",
                    ok=False,
                    external_write_authority_asserted=True,
                    reader_identity_verified=True,
                    semantic_dedupe_status="provider_failed",
                    blocker="reviewer_notification_dedupe_readback_failed",
                )

    try:
        identity_status = runner(
            [
                "lark-cli",
                "--profile",
                profile,
                "auth",
                "status",
                "--verify",
                "--json",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        identity_status = {"returncode": 1}
    identity_payload = _parse_json_object(identity_status.get("stdout"))
    bot_identity = (
        (identity_payload.get("identities") or {}).get("bot")
        if isinstance(identity_payload, Mapping)
        and isinstance(identity_payload.get("identities"), Mapping)
        else {}
    )
    bot_identity = bot_identity if isinstance(bot_identity, Mapping) else {}
    if not (
        identity_status.get("returncode") == 0
        and bot_identity.get("available") is True
        and bot_identity.get("verified") is True
        and str(bot_identity.get("appName") or "") == expected_bot_name
    ):
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=True,
            blocker="dedicated_bot_identity_mismatch",
            reader_identity_verified=reader_verified,
        )

    if explicit_profile_bindings:
        try:
            sender_members = runner(
                [
                    "lark-cli",
                    "--profile",
                    profile,
                    "im",
                    "chat.members",
                    "get",
                    "--chat-id",
                    destination_id,
                    "--member-id-type",
                    "open_id",
                    "--page-all",
                    "--as",
                    "bot",
                    "--format",
                    "json",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            sender_members = {"returncode": 1}
        if sender_members.get("returncode") != 0:
            provider_error = " ".join(
                str(sender_members.get(field) or "") for field in ("stderr", "stdout")
            )
            return build_reviewer_notification_sink_result(
                sink_kind=sink_kind,
                reviewer_handles=reviewer_handles,
                idempotency_key=key,
                status="gate_required",
                ok=False,
                external_write_authority_asserted=True,
                bot_identity_verified=True,
                reader_identity_verified=reader_verified,
                blocker=(
                    "lark_bot_group_access_required"
                    if LARK_PERMISSION_PATTERN.search(provider_error)
                    else "reviewer_notification_provider_failed"
                ),
            )
        sender_member_ids = _lark_member_ids(
            _parse_json_object(sender_members.get("stdout"))
        )
        if any(member_id not in sender_member_ids for _, member_id, _ in resolved):
            return build_reviewer_notification_sink_result(
                sink_kind=sink_kind,
                reviewer_handles=reviewer_handles,
                idempotency_key=key,
                status="gate_required",
                ok=False,
                external_write_authority_asserted=True,
                bot_identity_verified=True,
                reader_identity_verified=reader_verified,
                blocker="reviewer_notification_identity_unresolved",
            )

    provider_idempotency_key = f"loopx-{key.partition(':')[2][:32]}"
    mentions = build_lark_mention_prefix(
        [
            LarkMention(
                identity_kind="open_id",
                identity=member_id,
                display_name=display_name,
            )
            for _, member_id, display_name in resolved
        ]
    )
    issue_clause = (
        f"（修复 {', '.join(linked_issue_refs)}）" if linked_issue_refs else ""
    )
    summary = f"：{pr_title}" if pr_title else ""
    notification_text = normalize_lark_outbound_text(
        f"{mentions} 请帮忙 review PR #{pr_number}{issue_clause}{summary}。{pr_url}"
    )
    content = json.dumps(
        {"text": notification_text},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    send_args = [
        "lark-cli",
        "--profile",
        profile,
        "im",
        "+messages-send",
        "--chat-id",
        destination_id,
        "--content",
        content,
        "--msg-type",
        "text",
        "--idempotency-key",
        provider_idempotency_key,
        "--as",
        "bot",
        "--format",
        "json",
    ]
    try:
        preview = runner(send_args + ["--dry-run"])
    except (OSError, subprocess.SubprocessError):
        preview = {"returncode": 1}
    preview_payload = _parse_json_object(preview.get("stdout"))
    if not (
        preview.get("returncode") == 0
        and preview_payload is not None
        and lark_provider_preview_matches_outbound(
            outbound_text=notification_text,
            payload=preview_payload,
        )
    ):
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=True,
            blocker="lark_notification_provider_preview_mismatch",
            bot_identity_verified=True,
            reader_identity_verified=reader_verified,
        )
    try:
        send = runner(send_args)
    except (OSError, subprocess.SubprocessError):
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=True,
            blocker="reviewer_notification_provider_unavailable",
            bot_identity_verified=True,
            reader_identity_verified=reader_verified,
        )
    if send.get("returncode") != 0:
        provider_error = " ".join(
            str(send.get(field) or "") for field in ("stderr", "stdout")
        )
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="gate_required",
            ok=False,
            external_write_authority_asserted=True,
            blocker=(
                "lark_bot_group_access_required"
                if LARK_PERMISSION_PATTERN.search(provider_error)
                else "reviewer_notification_provider_failed"
            ),
            bot_identity_verified=True,
            reader_identity_verified=reader_verified,
        )

    send_payload = _parse_json_object(send.get("stdout"))
    message_id = _find_message_id(send_payload)
    if not message_id:
        return build_reviewer_notification_sink_result(
            sink_kind=sink_kind,
            reviewer_handles=reviewer_handles,
            idempotency_key=key,
            status="sent_unverified",
            ok=False,
            external_write_authority_asserted=True,
            external_write_performed=True,
            blocker="lark_notification_not_verified",
            bot_identity_verified=True,
            reader_identity_verified=reader_verified,
        )
    try:
        readback = runner(
            [
                "lark-cli",
                "--profile",
                profile,
                "im",
                "+messages-mget",
                "--message-ids",
                message_id,
                "--as",
                "bot",
                "--no-reactions",
                "--format",
                "json",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        readback = {"returncode": 1}
    readback_payload = _parse_json_object(readback.get("stdout"))
    readback_message = (
        _find_message(readback_payload, message_id)
        if readback_payload is not None
        else None
    )
    verified = bool(
        readback.get("returncode") == 0
        and readback_message is not None
        and lark_readback_matches_outbound(
            outbound_text=notification_text,
            message=readback_message,
        )
    )
    return build_reviewer_notification_sink_result(
        sink_kind=sink_kind,
        reviewer_handles=reviewer_handles,
        idempotency_key=key,
        status="sent_verified" if verified else "sent_unverified",
        ok=verified,
        external_write_authority_asserted=True,
        external_write_performed=True,
        verification_performed=True,
        notification_verified=verified,
        bot_identity_verified=True,
        reader_identity_verified=reader_verified,
        semantic_dedupe_status=semantic_dedupe_status,
        blocker=None if verified else "lark_notification_not_verified",
    )
