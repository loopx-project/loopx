"""Durable state, receipts, and stall planning for bounded multi-part replies."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from .identity_shapes import LARK_MESSAGE_ID_PATTERN

MESSAGE_ID_PATTERN = LARK_MESSAGE_ID_PATTERN

PART_DELIVERY_COMPLETE_KEY = "delivery_parts_complete"
PART_DELIVERY_VERIFIED_KEY = "delivery_parts_verified"
PART_ATTEMPT_KEY = "delivery_part_attempt"
PART_STALL_NOTICE_KEY = "delivery_part_stall_notice"
PART_STALL_COUNT_KEY = "delivery_part_stall_count"
PART_STALL_NOTICE_ATTEMPT_KEY = "delivery_part_stall_notice_attempt"
PART_DELIVERY_INCOMPLETE = "reply_part_delivery_incomplete"
PART_DELIVERY_COMPLETION_UNVERIFIED = "reply_part_delivery_completion_unverified"
# The notice is a last resort, not the outcome: the sequence counts the attempts
# that stopped the answer mid-sequence (progress does not reset that count, only
# a different split does) and only speaks after three of them, so a transient
# provider hiccup never turns into a message the reader did not need.
PART_STALL_NOTICE_MIN_STALLS = 3
MANAGER_REPLY_STALL_NOTICE = (
    "本条答复已拆分，目前已核验前面的 {sent}/{count} 段；"
    "完整答复保存在 LoopX 管家会话中，剩余分段会继续重试。"
)

WriteDelivery = Callable[[Path, dict[str, object]], object]
PartReplyOptionsFactory = Callable[[int], Mapping[str, object]]
CommandRunnerLike = Callable[[Sequence[str]], Mapping[str, object]]


class _BaseInboxOptions(TypedDict, total=False):
    """Shared transport keyword options for Lark inbox reply/verify helpers."""

    reply_placement: str | None
    delivery_id: str | None
    source_membership_verifier: Callable[[], bool] | None
    source_authority_verifier: Callable[[str, str, str], tuple[str | None, bool]] | None
    finalize_reactions: bool
    attachment_keys: tuple[str, ...]
    attachment_names: tuple[str, ...]
    image_keys: tuple[str, ...]
    before_send: Callable[[str], Mapping[str, object]] | None


class VerifyInboxOptions(_BaseInboxOptions, total=False):
    """Typed keyword options accepted by ``verify_lark_inbox_reply``."""

    resume_parts: bool


class PartSendInboxOptions(_BaseInboxOptions, total=False):
    """Typed keyword options accepted by ``reply_lark_event_inbox`` for parts."""

    provider_preflight: bool
    overlap_source_preflight: bool
    recover_ambiguous_send: bool
    before_provider_send: Callable[[], None] | None


class SendInboxOptions(PartSendInboxOptions, total=False):
    """Typed keyword options accepted by ``reply_lark_event_inbox``."""

    short_message_limit: int | None


class CompletedPartDeliveryReceipt(TypedDict):
    """Verified receipt synthesized when all planned parts are confirmed."""

    ok: bool
    status: str
    idempotency_key: str
    content_format: object
    external_write_performed: bool
    verification_performed: bool
    reply_verified: bool
    part_delivery_reused: bool


class ManagerPartDeliveryPendingResult(TypedDict):
    """Typed pending outcome for an interrupted multi-part reply sequence."""

    ok: bool
    status: str
    reason: str
    delivery_part_count: object
    delivery_parts_sent: object
    delivery_notice_sent: bool
    format_degraded: bool
    goal_id: str
    inbox_config_ref: str
    source_acknowledged: bool


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat()


def _strict_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def coerce_int_default_zero(value: object) -> int:
    """Convert numeric counters like ``int(...) or 0`` safely without ``Any``."""

    if not value:
        return 0
    if isinstance(value, (int, str, bytes, bytearray)):
        return int(value)
    return int(str(value))


def _reply_verified(reply: Mapping[str, object]) -> bool:
    """Whether the provider reported this reply present on the channel.

    A reply can be confirmed either by the readback that follows its own send or
    by the reconciliation of an earlier send the provider accepted but did not
    read back. Both mean the reader has that text, which is the fact the record
    keeps; a reconciled send has no new write of its own.
    """

    return bool(
        reply.get("verification_performed") is True
        and reply.get("reply_verified") is True
    )


def _reply_on_channel(reply: Mapping[str, object]) -> bool:
    """Whether the reader may already have this reply's text.

    ``ok`` also requires the source reaction cleanup to have finished, so a
    reply the provider already accepted can come back not-ok with a cleanup
    still pending. Treating that as delivered is what keeps a retry from sending
    the reader the same text twice, and the pending cleanup stays the
    transport's own business.
    """

    return reply.get("ok") is True or _reply_verified(reply)


def _part_accepted(reply: Mapping[str, object]) -> bool:
    """Whether this part may be counted as delivered."""

    return _reply_on_channel(reply)


def _accepted_reply_facts(reply: Mapping[str, object]) -> dict[str, object]:
    """The durable facts of one part the provider confirmed."""

    return {
        "reply_idempotency_key": reply.get("idempotency_key"),
        PART_DELIVERY_VERIFIED_KEY: _reply_verified(reply),
    }


def completed_part_delivery_receipt(
    delivery_state: Mapping[str, object],
) -> CompletedPartDeliveryReceipt | None:
    """The verified receipt for a sequence whose every part was accepted.

    Returns ``None`` unless the durable record proves both that the sequence
    finished and that the provider verified the last part, so a caller never
    marks a delivery verified on the strength of an unfinished or unverified
    record.
    """

    if delivery_state.get(PART_DELIVERY_COMPLETE_KEY) is not True:
        return None
    if delivery_state.get(PART_DELIVERY_VERIFIED_KEY) is not True:
        return None
    key = delivery_state.get("reply_idempotency_key")
    if not isinstance(key, str) or not key.startswith("sha256:"):
        return None
    return {
        "ok": True,
        "status": "sent_verified",
        "idempotency_key": key,
        "content_format": delivery_state.get("content_format", "text"),
        "external_write_performed": True,
        "verification_performed": True,
        "reply_verified": True,
        "part_delivery_reused": True,
    }


def part_delivery_incomplete_reason(delivery_state: Mapping[str, object]) -> str:
    """Name why a sequence stopped, when the record already says all parts went.

    A recorded counter that reached the part count without a verified
    completion cannot be re-sent (the parts are already on the channel) and
    cannot be settled either, so it gets its own reason instead of the generic
    incomplete one.
    """

    recorded = _strict_int(delivery_state.get("delivery_part_count"))
    sent = _strict_int(delivery_state.get("delivery_parts_sent"))
    if recorded is not None and sent is not None and recorded > 0 and sent == recorded:
        return PART_DELIVERY_COMPLETION_UNVERIFIED
    return PART_DELIVERY_INCOMPLETE


def _recorded_attempt(recorded: object) -> Mapping[str, object] | None:
    """The provider locator inside one recorded attempt, when it is well formed."""

    if not isinstance(recorded, Mapping):
        return None
    attempt = recorded.get("attempt")
    return attempt if isinstance(attempt, Mapping) else None


def recorded_part_attempt(
    delivery_state: Mapping[str, object], index: int
) -> Mapping[str, object] | None:
    """The provider locator of the part that was attempted but not confirmed."""

    recorded = delivery_state.get(PART_ATTEMPT_KEY)
    if not isinstance(recorded, Mapping) or recorded.get("index") != index:
        return None
    return _recorded_attempt(recorded)


def attempt_provider_locator(attempt: Mapping[str, object]) -> str | None:
    """The message id a recorded attempt can be verified against, when it has one.

    A send the provider accepted without reporting a message id records its
    intent instead of a locator. That record still proves a write happened, and
    ``None`` here is what tells the sequence to stop instead of posting the same
    text a second time.
    """

    message_ref = str(attempt.get("message_ref") or "").strip()
    return message_ref if MESSAGE_ID_PATTERN.fullmatch(message_ref) else None


def _locator_unavailable_result(*, reconciled_key: str) -> dict[str, object]:
    """The typed outcome for an attempt no readback can key on."""

    return {
        "ok": False,
        "status": "sent_unverified",
        "idempotency_key": None,
        "external_write_performed": True,
        "verification_performed": False,
        "reply_verified": False,
        "blocker": "lark_inbox_reply_not_verified",
        reconciled_key: False,
        "part_locator_unavailable": True,
    }


def recorded_stall_notice_attempt(
    delivery_state: Mapping[str, object],
) -> Mapping[str, object] | None:
    """The provider locator of a stall notice that was sent but not confirmed."""

    return _recorded_attempt(delivery_state.get(PART_STALL_NOTICE_ATTEMPT_KEY))


def plan_stalled_part_notice(delivery_state: Mapping[str, object]) -> str | None:
    """The bounded notice a stalled sequence posts once, after real retries.

    A reader who received only the first parts needs to know the answer is
    incomplete. This notice states what was delivered and where the rest is, and
    it waits for three stalled attempts so an ordinary hiccup stays quiet.
    """

    if delivery_state.get(PART_STALL_NOTICE_KEY) is True:
        return None
    stalls = _strict_int(delivery_state.get(PART_STALL_COUNT_KEY))
    if stalls is None or stalls < PART_STALL_NOTICE_MIN_STALLS:
        return None
    sent = _strict_int(delivery_state.get("delivery_parts_sent"))
    count = _strict_int(delivery_state.get("delivery_part_count"))
    if sent is None or count is None or not 0 < sent < count:
        return None
    return MANAGER_REPLY_STALL_NOTICE.format(sent=sent, count=count)


def strip_before_send_option(
    reply_options: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return a shallow copy of ``reply_options`` excluding ``before_send``."""

    if not reply_options:
        return {}
    return {key: value for key, value in reply_options.items() if key != "before_send"}


def manager_part_delivery_pending_result(
    *,
    reason: str,
    delivery_state: Mapping[str, object],
    goal_id: str,
    inbox_config_ref: str,
) -> ManagerPartDeliveryPendingResult:
    """The typed pending result for a part sequence the provider interrupted."""

    return {
        "ok": False,
        "status": "reply_delivery_pending",
        "reason": reason,
        "delivery_part_count": delivery_state.get("delivery_part_count"),
        "delivery_parts_sent": delivery_state.get("delivery_parts_sent"),
        "delivery_notice_sent": delivery_state.get(PART_STALL_NOTICE_KEY) is True,
        "format_degraded": delivery_state.get("content_format") != "markdown",
        "goal_id": goal_id,
        "inbox_config_ref": inbox_config_ref,
        "source_acknowledged": False,
    }


def manager_part_delivery_readback(delivery_state: Mapping[str, object]) -> dict[str, object]:
    """Part accounting for a delivered answer, empty when it was one message."""

    if not (
        isinstance(delivery_state.get("delivery_part_count"), int)
        and isinstance(delivery_state.get("delivery_parts_sent"), int)
    ):
        return {}
    return {
        "delivery_part_count": delivery_state["delivery_part_count"],
        "delivery_parts_sent": delivery_state["delivery_parts_sent"],
        "delivery_truncated": bool(delivery_state.get("delivery_truncated")),
    }
