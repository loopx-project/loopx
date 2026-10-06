from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from .event_inbox import (
    MESSAGE_ID_PATTERN,
    _event_from_file,
    load_lark_event_inbox_config,
)
from .inbox_reactions import complete_lark_event_inbox_reactions
from .presentation.markdown_post import (
    lark_markdown_post_content,
    lark_markdown_preview_matches,
    lark_markdown_readback_matches,
)

from .outbound import (
    DEFAULT_LARK_TEXT_LIMIT,
    LARK_POST_REQUEST_MAX_BYTES,
    expected_lark_mention_identities,
    lark_member_identities,
    lark_provider_preview_matches_outbound,
    lark_readback_matches_outbound,
    normalize_lark_outbound_text,
    validate_lark_text_request_size,
)

CommandRunner = Callable[[Sequence[str]], Mapping[str, Any]]
BOT_IDENTITY_VERIFY_ATTEMPTS = 3


class BotIdentityVerification(str, Enum):
    VERIFIED = "verified"
    RETRYABLE_VERIFY_FAILED = "retryable_verify_failed"
    REJECTED = "rejected"


def _default_runner(args: Sequence[str]) -> Mapping[str, Any]:
    result = subprocess.run(
        list(args),
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _call(runner: CommandRunner, args: Sequence[str]) -> Mapping[str, Any]:
    try:
        return runner(args)
    except (OSError, subprocess.SubprocessError):
        return {"returncode": 1}


def _json_object(value: Any) -> Mapping[str, Any]:
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _message_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("message_id")
        if isinstance(candidate, str) and MESSAGE_ID_PATTERN.fullmatch(candidate):
            return candidate
        return next(
            (found for child in value.values() if (found := _message_id(child))),
            None,
        )
    if isinstance(value, list):
        return next(
            (found for child in value if (found := _message_id(child))),
            None,
        )
    return None


def _message(value: Any, message_id: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if str(value.get("message_id") or "") == message_id:
            return value
        return next(
            (
                found
                for child in value.values()
                if (found := _message(child, message_id))
            ),
            None,
        )
    if isinstance(value, list):
        return next(
            (found for child in value if (found := _message(child, message_id))),
            None,
        )
    return None


def _intent_digest(profile: str, chat_id: str, receipt: str) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps([profile, chat_id, receipt]).encode("utf-8")
    ).hexdigest()


def _verified_mention_aliases(
    *,
    runner: CommandRunner,
    base: Sequence[str],
    chat_id: str,
    reply_text: str,
) -> tuple[dict[str, str] | None, str | None]:
    expected_mentions = expected_lark_mention_identities(reply_text)
    if not expected_mentions:
        return {}, None
    member_identity_sets: dict[str, set[str]] = {}
    bot_alias_candidates: dict[str, set[str]] = {}
    for identity_kind in sorted(set(expected_mentions.values())):
        expected_identities = {
            identity
            for identity, declared_kind in expected_mentions.items()
            if declared_kind == identity_kind
        }
        members = _call(
            runner,
            [
                *base,
                "im",
                "+chat-members-list",
                "--chat-id",
                chat_id,
                "--member-types",
                "user,bot",
                "--member-id-type",
                identity_kind,
                "--page-all",
                "--as",
                "bot",
                "--format",
                "json",
            ],
        )
        if members.get("returncode") != 0:
            return None, "lark_inbox_reply_mention_identity_unresolved"
        member_payload = _json_object(members.get("stdout"))
        member_identity_sets[identity_kind] = lark_member_identities(member_payload)
        member_data = member_payload.get("data", member_payload)
        bots = member_data.get("bots", []) if isinstance(member_data, Mapping) else []
        for member in bots if isinstance(bots, list) else []:
            if not isinstance(member, Mapping):
                continue
            app_id, member_id = member.get("app_id"), member.get("member_id")
            if (
                isinstance(app_id, str)
                and app_id
                and isinstance(member_id, str)
                and member_id in expected_identities
            ):
                bot_alias_candidates.setdefault(app_id, set()).add(member_id)
    if any(
        identity not in member_identity_sets.get(identity_kind, set())
        for identity, identity_kind in expected_mentions.items()
    ):
        return None, "lark_inbox_reply_mention_identity_unresolved"
    return {
        app_id: next(iter(identities))
        for app_id, identities in bot_alias_candidates.items()
        if len(identities) == 1 and next(iter(identities)) in expected_mentions
    }, None


def _bot_identity_verification(
    *,
    runner: CommandRunner,
    base: Sequence[str],
    expected_name: str,
) -> BotIdentityVerification:
    auth = _call(runner, [*base, "auth", "status", "--verify", "--json"])
    if auth.get("returncode") != 0:
        return BotIdentityVerification.REJECTED
    identities = _json_object(auth.get("stdout")).get("identities")
    identity = identities.get("bot") if isinstance(identities, Mapping) else None
    if not isinstance(identity, Mapping):
        return BotIdentityVerification.REJECTED
    if identity.get("available") is True and identity.get("verified") is True:
        return (
            BotIdentityVerification.VERIFIED
            if str(identity.get("appName") or "") == expected_name
            else BotIdentityVerification.REJECTED
        )
    if str(identity.get("status") or "") == "verify_failed":
        return BotIdentityVerification.RETRYABLE_VERIFY_FAILED
    return BotIdentityVerification.REJECTED


def _bot_identity_verified(
    *,
    runner: CommandRunner,
    base: Sequence[str],
    expected_name: str,
) -> bool:
    """Retry only the provider's explicit transient verification state."""

    for _attempt in range(BOT_IDENTITY_VERIFY_ATTEMPTS):
        result = _bot_identity_verification(
            runner=runner,
            base=base,
            expected_name=expected_name,
        )
        if result is BotIdentityVerification.VERIFIED:
            return True
        if result is BotIdentityVerification.REJECTED:
            return False
    return False


def _result(
    *,
    status: str,
    ok: bool,
    execute: bool,
    receipt: str | None,
    identity_verified: bool = False,
    membership_verified: bool = False,
    write_performed: bool = False,
    readback_performed: bool = False,
    reply_verified: bool = False,
    reaction_cleanup_verified: bool = False,
    placement: str | None = None,
    blocker: str | None = None,
    format_preflight_passed: bool = False,
    provider_preview_performed: bool = False,
    provider_preview_verified: bool = False,
) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "ok": ok,
        "schema_version": "lark_event_inbox_reply_v0",
        "status": status,
        "execute": execute,
        "idempotency_key": receipt,
        "external_write_authority_asserted": execute,
        "external_write_performed": write_performed,
        "verification_performed": readback_performed,
        "reply_verified": reply_verified,
        "reaction_cleanup_verified": reaction_cleanup_verified,
        "sender_identity_verified": identity_verified,
        "sender_chat_membership_verified": membership_verified,
        "format_preflight_passed": format_preflight_passed,
        "provider_preview_performed": provider_preview_performed,
        "provider_preview_verified": provider_preview_verified,
        "private_sender_profile_captured": False,
        "private_chat_id_captured": False,
        "private_message_id_captured": False,
        "private_reply_content_captured": False,
        "raw_provider_payload_captured": False,
    }
    if blocker:
        packet["blocker"] = blocker
    if placement:
        packet["placement"] = placement
    return packet


def _deliver_lark_inbox_outbound(
    *,
    project: str | Path,
    config_path: str | Path,
    message_id: str | None,
    text: str,
    content_format: str = "text",
    execute: bool = False,
    provider_preflight: bool = False,
    runner: CommandRunner = _default_runner,
    source_membership_verifier: Callable[[], bool] | None = None,
    before_send: Callable[[str], Mapping[str, Any]] | None = None,
    delivery_attempt_recorder: Callable[[Mapping[str, str | None]], None] | None = None,
    short_message_limit: int | None = DEFAULT_LARK_TEXT_LIMIT,
    finalize_reactions: bool = True,
) -> dict[str, Any]:
    """Deliver through one inbox-configured bot with exact provider readback.

    ``short_message_limit`` owns the compact, self-imposed length a delivery
    keeps when it has no source message to answer. Only the chat-root
    notification caller relies on it: 1200 is not a provider bound, so a
    delivery that answers a captured source message is bounded by the provider's
    own request limit instead of being cut by our own guess.
    """

    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        raise ValueError("lark event inbox is not enabled")
    source_message_id = str(message_id or "").strip()
    if source_message_id and not MESSAGE_ID_PATTERN.fullmatch(source_message_id):
        raise ValueError("lark inbox reply requires a valid message id")
    inbox = config["inbox_path"]
    source_event = (
        next(
            (
                event
                for path in (inbox.glob("*.json") if inbox.is_dir() else [])
                if path.name != "processed.json"
                if (event := _event_from_file(path)) is not None
                if event.get("message_id") == source_message_id
            ),
            None,
        )
        if source_message_id
        else None
    )
    if source_message_id and source_event is None:
        raise ValueError(
            "lark inbox reply source message is not captured by this inbox"
        )
    if content_format not in {"text", "markdown"}:
        raise ValueError("unsupported Lark reply content format")
    # Structured mentions retain the existing identity-verified text transport.
    markdown = content_format == "markdown" and not expected_lark_mention_identities(text)
    reply_text = normalize_lark_outbound_text(
        text,
        limit=None if source_event is not None else short_message_limit,
        preserve_format=markdown,
    )
    # Reject an oversized content lower bound before building a CLI argument.
    # The full rendered request body is checked again after provider preview.
    validate_lark_text_request_size({"content": json.dumps({"text": reply_text}, ensure_ascii=False, separators=(",", ":"))})
    if not reply_text:
        raise ValueError("lark inbox reply requires non-empty text")

    reply_config = config["reply"]
    if reply_config.get("enabled") is not True:
        return _result(
            status="gate_required",
            ok=False,
            execute=execute,
            receipt=None,
            blocker="lark_inbox_reply_sender_unconfigured",
        )

    profile = str(reply_config["sender_profile"])
    chat_id = str(reply_config["chat_id"])
    source_is_threaded = bool(
        source_event and (source_event.get("parent_id") or source_event.get("root_id"))
    )
    placement = (
        "chat_root"
        if source_event is None
        or (
            reply_config["placement_policy"] == "source_context"
            and not source_is_threaded
        )
        else "source_thread"
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "message_id": source_message_id,
                "placement": placement,
                "text": reply_text,
                **({"content_format": "markdown"} if markdown else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt = f"sha256:{digest}"
    if not execute and not provider_preflight:
        return _result(
            status="preview_ready",
            ok=True,
            execute=False,
            receipt=receipt,
            placement=placement,
            format_preflight_passed=True,
        )

    base = ["lark-cli", "--profile", profile]
    identity_verified = _bot_identity_verified(
        runner=runner,
        base=base,
        expected_name=str(reply_config["bot_display_name"]),
    )
    if not identity_verified:
        return _result(
            status="gate_required",
            ok=False,
            execute=execute,
            receipt=receipt,
            blocker="lark_inbox_reply_sender_identity_mismatch",
            format_preflight_passed=True,
        )

    if source_membership_verifier is not None:
        membership = {"returncode": 0 if source_membership_verifier() else 1}
    else:
        membership = _call(
            runner,
            base
            + [
                "im",
                "chats",
                "get",
                "--chat-id",
                chat_id,
                "--as",
                "bot",
                "--format",
                "json",
            ],
        )
    if membership.get("returncode") != 0:
        return _result(
            status="gate_required",
            ok=False,
            execute=execute,
            receipt=receipt,
            identity_verified=True,
            blocker="lark_inbox_reply_sender_not_in_configured_chat",
            format_preflight_passed=True,
        )

    bot_aliases, mention_error = _verified_mention_aliases(
        runner=runner, base=base, chat_id=chat_id, reply_text=reply_text
    )
    if mention_error:
        return _result(
            status="gate_required",
            ok=False,
            execute=execute,
            receipt=receipt,
            identity_verified=True,
            membership_verified=True,
            placement=placement,
            blocker=mention_error,
            format_preflight_passed=True,
        )

    content_args = (
        ["--msg-type", "post", "--content", lark_markdown_post_content(reply_text)]
        if markdown else ["--text", reply_text]
    )
    destination = (
        [
            "im",
            "+messages-send",
            "--chat-id",
            chat_id,
            *content_args,
        ]
        if placement == "chat_root"
        else [
            "im",
            "+messages-reply",
            "--message-id",
            source_message_id,
            *content_args,
            "--reply-in-thread",
        ]
    )
    provider_args = (
        base
        + destination
        + [
            "--idempotency-key",
            f"loopx-{digest[:32]}",
            "--as",
            "bot",
            "--format",
            "json",
        ]
    )
    preview = _call(runner, provider_args + ["--dry-run"])
    provider_preview_verified = bool(
        preview.get("returncode") == 0
        and (
            lark_markdown_preview_matches(text=reply_text, payload=_json_object(preview.get("stdout")))
            if markdown else lark_provider_preview_matches_outbound(
                outbound_text=reply_text, payload=_json_object(preview.get("stdout")),
            )
        )
    )
    if not provider_preview_verified:
        return _result(
            status="gate_required",
            ok=False,
            execute=execute,
            receipt=receipt,
            identity_verified=True,
            membership_verified=True,
            placement=placement,
            blocker="lark_inbox_reply_provider_preview_mismatch",
            format_preflight_passed=True,
            provider_preview_performed=True,
        )
    preview_payload = _json_object(preview.get("stdout"))
    preview_data = preview_payload.get("data")
    api_calls = preview_payload.get("api")
    if not isinstance(api_calls, list) and isinstance(preview_data, Mapping):
        api_calls = preview_data.get("api")
    for call in api_calls if isinstance(api_calls, list) else []:
        if isinstance(call, Mapping) and isinstance(call.get("body"), Mapping):
            validate_lark_text_request_size(call["body"])
            body_bytes = len(json.dumps(call["body"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if markdown and body_bytes > LARK_POST_REQUEST_MAX_BYTES:
                # No write has occurred. Preserve the full answer via the existing
                # larger text transport rather than truncate or retry after send.
                result = _deliver_lark_inbox_outbound(
                    project=project, config_path=config_path, message_id=message_id,
                    text=text, content_format="text", execute=execute,
                    provider_preflight=provider_preflight, runner=runner, before_send=before_send,
                    source_membership_verifier=source_membership_verifier,
                    delivery_attempt_recorder=delivery_attempt_recorder,
                )
                result.update(content_format="text", format_fallback="post_size_limit")
                return result
    guidance = None
    if before_send is not None:
        # Bind review to destination/profile as well as content and placement.
        # The optional binder hashes the verified destination before retrieval.
        intent_digest = _intent_digest(profile, chat_id, receipt)
        bind_destination = getattr(before_send, "for_destination", None)
        scoped_hook = bind_destination(chat_id) if bind_destination else before_send
        guidance = scoped_hook(intent_digest)
        if guidance.get("continue_delivery") is not True or not execute:
            return _result(
                status="agent_review_required"
                if guidance.get("agent_review_required")
                else "preview_ready",
                ok=True,
                execute=execute,
                receipt=receipt,
                identity_verified=True,
                membership_verified=True,
                placement=placement,
                format_preflight_passed=True,
                provider_preview_performed=True,
                provider_preview_verified=True,
            ) | {"outbound_guidance": dict(guidance)}
    if not execute:
        return _result(
            status="preview_ready",
            ok=True,
            execute=False,
            receipt=receipt,
            identity_verified=True,
            membership_verified=True,
            placement=placement,
            format_preflight_passed=True,
            provider_preview_performed=True,
            provider_preview_verified=True,
        )

    send = _call(
        runner,
        provider_args,
    )
    if send.get("returncode") != 0:
        return _result(
            status="gate_required",
            ok=False,
            execute=True,
            receipt=receipt,
            identity_verified=True,
            membership_verified=True,
            placement=placement,
            blocker="lark_inbox_reply_provider_failed",
            format_preflight_passed=True,
            provider_preview_performed=True,
            provider_preview_verified=True,
        )

    intent_digest = _intent_digest(profile, chat_id, receipt)
    reply_message_id = _message_id(_json_object(send.get("stdout")))
    if not reply_message_id:
        # The provider accepted the write and reported no message id, so there is
        # nothing a later readback could key on. Recording the attempt with an
        # empty locator is what keeps a retry from posting the same text again:
        # the durable record proves a write happened even though it cannot be
        # located, and a locator nothing can verify must not be re-sent blindly.
        if delivery_attempt_recorder is not None:
            try:
                delivery_attempt_recorder(
                    {
                        "schema_version": "manager_return_delivery_attempt_v0",
                        "provider": "lark",
                        "message_ref": None,
                        "intent_digest": intent_digest,
                        "provider_receipt": receipt,
                    }
                )
            except (OSError, TypeError, ValueError):
                return _result(
                    status="sent_unverified",
                    ok=False,
                    execute=True,
                    receipt=receipt,
                    identity_verified=True,
                    membership_verified=True,
                    write_performed=True,
                    placement=placement,
                    blocker="lark_inbox_reply_delivery_attempt_not_persisted",
                    format_preflight_passed=True,
                    provider_preview_performed=True,
                    provider_preview_verified=True,
                )
        return _result(
            status="sent_unverified",
            ok=False,
            execute=True,
            receipt=receipt,
            identity_verified=True,
            membership_verified=True,
            write_performed=True,
            placement=placement,
            blocker="lark_inbox_reply_not_verified",
            format_preflight_passed=True,
            provider_preview_performed=True,
            provider_preview_verified=True,
        )
    if delivery_attempt_recorder is not None:
        try:
            delivery_attempt_recorder(
                {
                    "schema_version": "manager_return_delivery_attempt_v0",
                    "provider": "lark",
                    "message_ref": reply_message_id,
                    "intent_digest": intent_digest,
                    "provider_receipt": receipt,
                }
            )
        except (OSError, TypeError, ValueError):
            return _result(
                status="sent_unverified",
                ok=False,
                execute=True,
                receipt=receipt,
                identity_verified=True,
                membership_verified=True,
                write_performed=True,
                placement=placement,
                blocker="lark_inbox_reply_delivery_attempt_not_persisted",
                format_preflight_passed=True,
                provider_preview_performed=True,
                provider_preview_verified=True,
            )
    readback = _call(
        runner,
        base
        + [
            "im",
            "+messages-mget",
            "--message-ids",
            reply_message_id,
            "--as",
            "bot",
            "--no-reactions",
            "--format",
            "json",
        ],
    )
    readback_payload = _json_object(readback.get("stdout"))
    readback_message = _message(readback_payload, reply_message_id)
    verified = bool(
        readback.get("returncode") == 0
        and readback_message is not None
        and (lark_markdown_readback_matches(text=reply_text, message=readback_message)
             if markdown else lark_readback_matches_outbound(
            outbound_text=reply_text,
            message=readback_message,
            # mget may report a bot's app_id instead of its member_id. Only
            # accept an unambiguous mapping verified for the mention's declared kind.
            verified_bot_aliases=bot_aliases or {},
        ))
    )
    reaction_cleanup = (
        complete_lark_event_inbox_reactions(
            project=project,
            config_path=config_path,
            message_id=source_message_id,
            execute=True,
            runner=runner,
        )
        if verified and source_message_id and finalize_reactions
        else {"ok": True}
        if verified
        else None
    )
    reaction_cleanup_verified = bool(finalize_reactions and
        reaction_cleanup is not None and reaction_cleanup.get("ok") is True
    )
    completed = bool(verified and (not finalize_reactions or reaction_cleanup_verified))
    result = _result(
        status=(
            "sent_verified"
            if completed
            else "sent_verified_cleanup_pending"
            if verified
            else "sent_unverified"
        ),
        ok=completed,
        execute=True,
        receipt=receipt,
        identity_verified=True,
        membership_verified=True,
        write_performed=True,
        readback_performed=True,
        reply_verified=verified,
        reaction_cleanup_verified=reaction_cleanup_verified,
        placement=placement,
        blocker=(
            None
            if completed
            else "lark_inbox_reply_reaction_cleanup_pending"
            if verified
            else "lark_inbox_reply_not_verified"
        ),
        format_preflight_passed=True,
        provider_preview_performed=True,
        provider_preview_verified=True,
    )
    if guidance is not None:
        result["outbound_guidance"] = dict(guidance)
    result["reaction_cleanup_deferred"] = not finalize_reactions
    return result


def reply_lark_event_inbox(
    *,
    project: str | Path,
    config_path: str | Path,
    message_id: str,
    text: str,
    content_format: str = "text",
    execute: bool = False,
    provider_preflight: bool = False,
    runner: CommandRunner = _default_runner,
    source_membership_verifier: Callable[[], bool] | None = None,
    before_send: Callable[[str], Mapping[str, Any]] | None = None,
    delivery_attempt_recorder: Callable[[Mapping[str, str | None]], None] | None = None,
    short_message_limit: int | None = DEFAULT_LARK_TEXT_LIMIT,
    finalize_reactions: bool = True,
) -> dict[str, Any]:
    """Reply with the explicit inbox-configured bot and placement policy.

    An answer delivery passes ``short_message_limit=None`` to declare that it is
    bounded by the provider's request limit rather than by the compact
    notification length.

    Intermediate admission/progress replies pass ``finalize_reactions=False``;
    their verified delivery does not settle the source's processing lifecycle.
    """

    result = _deliver_lark_inbox_outbound(
        project=project,
        config_path=config_path,
        message_id=message_id,
        text=text,
        content_format=content_format,
        execute=execute,
        provider_preflight=provider_preflight,
        runner=runner,
        source_membership_verifier=source_membership_verifier,
        before_send=before_send,
        delivery_attempt_recorder=delivery_attempt_recorder,
        short_message_limit=short_message_limit,
        finalize_reactions=finalize_reactions,
    )

    result.setdefault("content_format", "markdown" if content_format == "markdown"
                      and not expected_lark_mention_identities(text) else "text")
    return result


def verify_lark_inbox_reply(
    *,
    project: str | Path,
    config_path: str | Path,
    message_id: str,
    text: str,
    attempt: Mapping[str, Any],
    runner: CommandRunner = _default_runner,
    source_membership_verifier: Callable[[], bool] | None = None,
    finalize_reactions: bool = True,
) -> dict[str, Any]:
    """Read back one prior Lark reply without sending another message."""

    if (
        set(attempt) != {
            "schema_version",
            "provider",
            "message_ref",
            "intent_digest",
            "provider_receipt",
        }
        or attempt.get("schema_version") != "manager_return_delivery_attempt_v0"
        or attempt.get("provider") != "lark"
        or not MESSAGE_ID_PATTERN.fullmatch(str(attempt.get("message_ref") or ""))
    ):
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_delivery_intent_conflict",
        }
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    reply_config = config["reply"]
    profile = str(reply_config.get("sender_profile") or "")
    chat_id = str(reply_config.get("chat_id") or "")
    preview = None
    for content_format in ("markdown", "text"):
        candidate = reply_lark_event_inbox(
            project=project,
            config_path=config_path,
            message_id=message_id,
            text=text,
            content_format=content_format,
            execute=False,
            runner=runner,
        )
        receipt = str(candidate.get("idempotency_key") or "")
        if (
            attempt.get("provider_receipt") == receipt
            and attempt.get("intent_digest")
            == _intent_digest(profile, chat_id, receipt)
        ):
            preview = candidate
            break
    if preview is None:
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_delivery_intent_conflict",
        }
    base = ["lark-cli", "--profile", profile]
    if not _bot_identity_verified(
        runner=runner,
        base=base,
        expected_name=str(reply_config.get("bot_display_name") or ""),
    ):
        return {
            "ok": False,
            "verification_performed": False,
            "reply_verified": False,
            "blocker": "provider_verification_unavailable",
        }
    if source_membership_verifier is not None:
        membership = {"returncode": 0 if source_membership_verifier() else 1}
    else:
        membership = _call(
            runner,
            [
                *base,
                "im",
                "chats",
                "get",
                "--chat-id",
                chat_id,
                "--as",
                "bot",
                "--format",
                "json",
            ],
        )
    if membership.get("returncode") != 0:
        return {
            "ok": False,
            "verification_performed": False,
            "reply_verified": False,
            "blocker": "provider_verification_unavailable",
        }
    markdown = preview.get("content_format") == "markdown"
    reply_text = normalize_lark_outbound_text(text, limit=None, preserve_format=markdown)
    bot_aliases, mention_error = _verified_mention_aliases(
        runner=runner, base=base, chat_id=chat_id, reply_text=reply_text
    )
    if mention_error:
        return {
            "ok": False,
            "verification_performed": False,
            "reply_verified": False,
            "blocker": "provider_verification_unavailable",
        }
    provider_message_id = str(attempt["message_ref"])
    readback = _call(
        runner,
        [
            *base,
            "im",
            "+messages-mget",
            "--message-ids",
            provider_message_id,
            "--as",
            "bot",
            "--no-reactions",
            "--format",
            "json",
        ],
    )
    if readback.get("returncode") != 0:
        return {
            "ok": False,
            "verification_performed": False,
            "reply_verified": False,
            "blocker": "provider_verification_unavailable",
        }
    message = _message(_json_object(readback.get("stdout")), provider_message_id)
    if message is None:
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_message_missing",
        }
    verified = (
        lark_markdown_readback_matches(text=reply_text, message=message)
        if markdown
        else lark_readback_matches_outbound(
            outbound_text=reply_text,
            message=message,
            verified_bot_aliases=bot_aliases or {},
        )
    )
    if not verified:
        return {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_delivery_mismatch",
        }
    return _verified_reply_result(project=project, config_path=config_path, message_id=message_id,
                                  runner=runner, finalize_reactions=finalize_reactions)


def _verified_reply_result(*, project: str | Path, config_path: str | Path, message_id: str,
                           runner: CommandRunner, finalize_reactions: bool) -> dict[str, Any]:
    """Completion still waits for the existing reaction-cleanup owner."""
    cleanup = complete_lark_event_inbox_reactions(
        project=project,
        config_path=config_path,
        message_id=message_id,
        execute=True,
        runner=runner,
    ) if finalize_reactions else {"ok": True}
    return {
        "ok": cleanup.get("ok") is True,
        "verification_performed": True,
        "reply_verified": True,
        "reaction_cleanup_verified": finalize_reactions and cleanup.get("ok") is True,
        "reaction_cleanup_deferred": not finalize_reactions,
    }


def update_lark_inbox_reply(
    *, project: str | Path, config_path: str | Path, message_id: str,
    text: str, previous_text: str, attempt: Mapping[str, Any],
    runner: CommandRunner = _default_runner,
    source_membership_verifier: Callable[[], bool],
    before_send: Callable[[str], Any],
    delivery_attempt_recorder: Callable[[Any], None],
    finalize_reactions: bool = False,
) -> dict[str, Any]:
    """Replace one known Bot reply, verifying source and exact provider readback.

    Replacing the same message is idempotent: a lost update acknowledgement is
    recovered by reading the requested body before another edit. The original
    creation receipt binds the target to this App and source; it is not a new
    message or a completion receipt until readback and cleanup succeed. The
    required callback verifies the current native Core App identity, grants and
    exact source audience, not only group membership. Reuse that one fresh
    verification throughout this bounded provider operation.
    """
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    reply = config["reply"]
    profile, chat_id = str(reply["sender_profile"]), str(reply["chat_id"])
    base = ["lark-cli", "--profile", profile]
    prior = reply_lark_event_inbox(project=project, config_path=config_path,
        message_id=message_id, text=previous_text, content_format="markdown", execute=False)
    prior_receipt = str(prior.get("idempotency_key") or "")
    if (set(attempt) != {"schema_version", "provider", "message_ref", "intent_digest", "provider_receipt"}
            or attempt.get("schema_version") != "manager_return_delivery_attempt_v0"
            or attempt.get("provider") != "lark"
            or not MESSAGE_ID_PATTERN.fullmatch(str(attempt.get("message_ref") or ""))
            or attempt.get("provider_receipt") != prior_receipt
            or attempt.get("intent_digest") != _intent_digest(profile, chat_id, prior_receipt)):
        return {"ok": False, "reply_verified": False, "blocker": "provider_delivery_intent_conflict"}
    desired = reply_lark_event_inbox(project=project, config_path=config_path,
        message_id=message_id, text=text, content_format="markdown", execute=False)
    # Rich mentions use the existing identity-verified text sender instead.
    if desired.get("content_format") != "markdown":
        return {"ok": False, "reply_verified": False, "blocker": "provider_update_format_unsupported"}
    text = normalize_lark_outbound_text(text, limit=None, preserve_format=True)
    content = lark_markdown_post_content(text)
    body = {"msg_type": "post", "content": content}
    if len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > LARK_POST_REQUEST_MAX_BYTES:
        return {"ok": False, "reply_verified": False, "blocker": "provider_update_too_large"}
    if not source_membership_verifier():
        return {"ok": False, "reply_verified": False, "blocker": "provider_verification_unavailable"}
    ref = str(attempt["message_ref"])
    read_args = [*base, "im", "+messages-mget", "--message-ids", ref, "--as", "bot", "--no-reactions", "--format", "json"]
    readback = _call(runner, read_args)
    message = _message(_json_object(readback.get("stdout")), ref)
    if readback.get("returncode") != 0 or message is None:
        return {"ok": False, "reply_verified": False, "blocker": "provider_verification_unavailable"}
    if (message.get("chat_id") != chat_id
            or not isinstance(message.get("sender"), Mapping)
            or message["sender"].get("sender_type") != "app"):
        return {"ok": False, "reply_verified": False, "blocker": "provider_delivery_intent_conflict"}
    receipt = str(desired["idempotency_key"])
    updated_attempt = {**attempt, "provider_receipt": receipt,
                       "intent_digest": _intent_digest(profile, chat_id, receipt)}
    if not lark_markdown_readback_matches(text=text, message=message):
        edit_args = [*base, "im", "+messages-edit", "--message-id", ref,
                     "--msg-type", "post", "--content", content, "--as", "bot", "--format", "json"]
        preview = _call(runner, [*edit_args, "--dry-run"])
        if preview.get("returncode") != 0 or not lark_markdown_preview_matches(text=text, payload=_json_object(preview.get("stdout"))):
            return {"ok": False, "reply_verified": False, "blocker": "provider_update_preview_mismatch"}
        if before_send(receipt).get("continue_delivery") is not True:
            return {"ok": False, "reply_verified": False, "blocker": "provider_update_deferred"}
        _call(runner, edit_args)
        # An error or timeout may follow an applied edit. Readback, not the
        # command return code, decides whether this exact body reached Lark.
        readback = _call(runner, read_args)
        message = _message(_json_object(readback.get("stdout")), ref)
    delivery_attempt_recorder(updated_attempt)
    if readback.get("returncode") != 0 or message is None:
        return {"ok": False, "reply_verified": False, "blocker": "provider_verification_unavailable"}
    if not lark_markdown_readback_matches(text=text, message=message):
        return {"ok": False, "reply_verified": False, "blocker": "provider_delivery_mismatch"}
    return _verified_reply_result(project=project, config_path=config_path, message_id=message_id,
                                  runner=runner, finalize_reactions=finalize_reactions)


def send_lark_inbox_message(
    *,
    project: str | Path,
    config_path: str | Path,
    text: str,
    execute: bool = False,
    provider_preflight: bool = False,
    runner: CommandRunner = _default_runner,
    before_send: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send one verified chat-root message through the configured inbox bot.

    A chat-root notification keeps the compact self-imposed length: it is a
    notice on the channel, not an answer, and the reader expects it to stay
    short.
    """

    result = _deliver_lark_inbox_outbound(
        project=project,
        config_path=config_path,
        message_id=None,
        text=text,
        execute=execute,
        provider_preflight=provider_preflight,
        runner=runner,
        before_send=before_send,
        short_message_limit=DEFAULT_LARK_TEXT_LIMIT,
    )
    result["schema_version"] = "lark_outbound_message_v0"
    blocker = result.get("blocker")
    if isinstance(blocker, str):
        result["blocker"] = blocker.replace(
            "lark_inbox_reply_", "lark_outbound_message_"
        )
    return result
