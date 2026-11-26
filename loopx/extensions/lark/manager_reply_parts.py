"""Bounded multi-message delivery for a manager answer that does not fit once.

A persisted manager answer that the provider rejects for length used to be left
on the channel as nothing at all. This module owns the alternative: split the
already-validated body into ordered parts, send the parts the provider has not
accepted yet, and record that progress in the same durable delivery state the
single-message path uses, so a retry resumes instead of re-sending.

The counter is not the whole record. A part is counted only after the transport
accepted it, so a state that already shows every part accepted means the reader
has the whole answer even when the caller never got to write its own receipt
(a failed settle write, or a process that stopped right after the last part).
That case settles from the record instead of re-sending nothing forever.

A send the provider accepted but did not read back leaves its provider locator
in the same record. The next attempt verifies that locator before sending
any further part, so an ambiguous send is reconciled instead of repeated.

A partly delivered answer also has to look partly delivered: once the sequence
has stopped mid-answer three times, one bounded notice tells the reader how much
went out and where the rest is. That notice records and verifies its own
locator the same way, so a reader is never told the same thing twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .inbox_reply import reply_lark_event_inbox, verify_lark_inbox_reply
from .manager_reply_parts_state import (
    MANAGER_REPLY_STALL_NOTICE as MANAGER_REPLY_STALL_NOTICE,
    MESSAGE_ID_PATTERN as MESSAGE_ID_PATTERN,
    PART_ATTEMPT_KEY as PART_ATTEMPT_KEY,
    PART_DELIVERY_COMPLETE_KEY as PART_DELIVERY_COMPLETE_KEY,
    PART_DELIVERY_COMPLETION_UNVERIFIED as PART_DELIVERY_COMPLETION_UNVERIFIED,
    PART_DELIVERY_INCOMPLETE as PART_DELIVERY_INCOMPLETE,
    PART_DELIVERY_VERIFIED_KEY as PART_DELIVERY_VERIFIED_KEY,
    PART_STALL_COUNT_KEY as PART_STALL_COUNT_KEY,
    PART_STALL_NOTICE_ATTEMPT_KEY as PART_STALL_NOTICE_ATTEMPT_KEY,
    PART_STALL_NOTICE_KEY as PART_STALL_NOTICE_KEY,
    PART_STALL_NOTICE_MIN_STALLS as PART_STALL_NOTICE_MIN_STALLS,
    CommandRunnerLike,
    CompletedPartDeliveryReceipt,
    ManagerPartDeliveryPendingResult as ManagerPartDeliveryPendingResult,
    PartReplyOptionsFactory,
    PartSendInboxOptions,
    SendInboxOptions,
    VerifyInboxOptions,
    WriteDelivery,
    _accepted_reply_facts,
    _locator_unavailable_result,
    _part_accepted,
    _recorded_attempt as _recorded_attempt,
    _reply_on_channel,
    _reply_verified as _reply_verified,
    _strict_int,
    attempt_provider_locator as attempt_provider_locator,
    coerce_int_default_zero,
    completed_part_delivery_receipt as completed_part_delivery_receipt,
    manager_part_delivery_pending_result as manager_part_delivery_pending_result,
    manager_part_delivery_readback as manager_part_delivery_readback,
    part_delivery_incomplete_reason as part_delivery_incomplete_reason,
    plan_stalled_part_notice as plan_stalled_part_notice,
    recorded_part_attempt as recorded_part_attempt,
    recorded_stall_notice_attempt as recorded_stall_notice_attempt,
    strip_before_send_option,
    utc_now_iso,
)
from .presentation.markdown_parts import split_lark_markdown_posts


def plan_manager_reply_parts(reply_text: str) -> tuple[list[str], bool]:
    """Return complete rich parts; the legacy truncation flag is always false."""

    return (
        split_lark_markdown_posts(reply_text, {"msg_type": "post", "uuid": "loopx-" + "0" * 32}),
        False,
    )


def _reconcile_recorded_attempt(
    *,
    attempt: Mapping[str, object] | None,
    reconciled_key: str,
    text: str,
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
    verify_options: Mapping[str, object],
) -> Mapping[str, object] | None:
    if attempt is None:
        return None
    if attempt_provider_locator(attempt) is None:
        return _locator_unavailable_result(reconciled_key=reconciled_key)
    verified = verify_lark_inbox_reply(
        project=root,
        config_path=config_path,
        message_id=message_id,
        text=text,
        attempt=attempt,
        runner=cast(CommandRunnerLike, reply_runner),
        **cast(VerifyInboxOptions, dict(verify_options)),
    )
    if verified.get("reply_verified") is not True:
        # A known write with unavailable/mismatched readback is never unsent.
        return verified
    return {**dict(verified), reconciled_key: True}


def reconciled_part_reply(
    *,
    parts: list[str],
    index: int,
    delivery_state: Mapping[str, object],
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
    reply_options: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    """Confirm a previously attempted part instead of sending it twice.

    Return ``None`` only when no write was recorded. A known write stays pending
    until readback confirms it; verification performs no write of its own.
    """

    # Preserve exact reconciliation guard for locator-less parts and pass options verbatim.
    return _reconcile_recorded_attempt(
        attempt=recorded_part_attempt(delivery_state, index),
        reconciled_key="part_reconciled",
        text=parts[index],
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
        verify_options=dict(reply_options or {}),
    )


def reconciled_stall_notice(
    *,
    notice: str,
    delivery_state: Mapping[str, object],
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
    reply_options: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    """Confirm a previously sent stall notice instead of posting it twice.

    The provider can accept the notice and still fail the readback that proves
    it, and this sequence keeps retrying until the remaining parts go through.
    The recorded locator is what keeps such a notice from being sent again on
    every later attempt, exactly as a part is reconciled before it is re-sent.
    """

    return _reconcile_recorded_attempt(
        attempt=recorded_stall_notice_attempt(delivery_state),
        reconciled_key="notice_reconciled",
        text=notice,
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
        verify_options=strip_before_send_option(reply_options),
    )


def deliver_stall_notice(
    *,
    delivery_state: dict[str, object],
    delivery_path: Path,
    write_delivery: WriteDelivery,
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
    reply_options: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    """Post the once-per-sequence stall notice, confirming a prior send first.

    Returns the transport result of the send or reconciliation, and ``None``
    when this sequence has nothing to tell the reader.
    """

    notice = plan_stalled_part_notice(delivery_state)
    if notice is None:
        return None
    reconciled = reconciled_stall_notice(
        notice=notice,
        delivery_state=delivery_state,
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
        reply_options=reply_options,
    )
    if reconciled is not None:
        if reconciled.get("notice_reconciled") is not True:
            # The provider accepted this notice and nothing can read it back, so
            # this record is the only evidence the reader may already have it.
            # Dropping it here would let the next retry post the same notice
            # again; a confirmed notice, below, is the case that settles it.
            return reconciled
        # A confirmed notice is settled: the stall flag carries that fact from
        # here on, so the attempt that proved it is no longer needed.
        delivery_state.pop(PART_STALL_NOTICE_ATTEMPT_KEY, None)
        return reconciled
    if delivery_state.get("delivery_part_stall_notice_started") is True:
        return _locator_unavailable_result(reconciled_key="notice_reconciled")
    # The locator of the send being attempted now replaces any older one, so the
    # record always points at the most recent unconfirmed notice. This runs only
    # on the path that is about to call the provider, where the old record is
    # genuinely superseded.
    delivery_state.pop(PART_STALL_NOTICE_ATTEMPT_KEY, None)

    def record_attempt(attempt: Mapping[str, object]) -> None:
        # The locator has to survive the attempt that produced it: a retry
        # reloads the record and verifies it instead of posting the notice again.
        delivery_state[PART_STALL_NOTICE_ATTEMPT_KEY] = {"attempt": dict(attempt)}
        delivery_state["updated_at"] = utc_now_iso()
        write_delivery(delivery_path, delivery_state)

    delivery_state["delivery_part_stall_notice_started"] = True
    write_delivery(delivery_path, delivery_state)
    result = reply_lark_event_inbox(
        project=root,
        config_path=config_path,
        message_id=message_id,
        text=notice,
        content_format="markdown",
        execute=True,
        runner=cast(CommandRunnerLike, reply_runner),
        delivery_attempt_recorder=record_attempt,
        **cast(SendInboxOptions, dict(reply_options or {})),
    )
    if (
        result.get("external_write_performed") is not True
        and result.get("provider_write_attempted") is not True
    ):
        delivery_state.pop("delivery_part_stall_notice_started", None)
    write_delivery(delivery_path, delivery_state)
    return result


def _deliver_single_part(
    *,
    parts: list[str],
    index: int,
    delivery_state: dict[str, object],
    delivery_path: Path,
    write_delivery: WriteDelivery,
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
    content_format: str,
    options: Mapping[str, object],
) -> Mapping[str, object] | None:
    reconciled = reconciled_part_reply(
        parts=parts,
        index=index,
        delivery_state=delivery_state,
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
        reply_options=strip_before_send_option(options),
    )
    if reconciled is not None:
        return reconciled
    if delivery_state.get("delivery_part_started") == index:
        # A crash between a provider write and its locator is ambiguous.
        # The original intent stays recoverable; do not post it again.
        return None
    # The locator of the part being sent now replaces any older one, so
    # the record always points at the most recent unconfirmed attempt.
    delivery_state.pop(PART_ATTEMPT_KEY, None)

    def record_attempt(attempt: Mapping[str, object]) -> None:
        delivery_state[PART_ATTEMPT_KEY] = {"index": index, "attempt": dict(attempt)}
        attempts_raw = delivery_state.get("delivery_part_attempts")
        attempts = attempts_raw if isinstance(attempts_raw, dict) else {}
        delivery_state["delivery_part_attempts"] = attempts
        attempts[str(index)] = dict(attempt)
        delivery_state["updated_at"] = utc_now_iso()
        write_delivery(delivery_path, delivery_state)

    delivery_state["delivery_part_started"] = index
    write_delivery(delivery_path, delivery_state)
    sent = reply_lark_event_inbox(
        project=root,
        config_path=config_path,
        message_id=message_id,
        text=parts[index],
        content_format=content_format,
        execute=True,
        runner=cast(CommandRunnerLike, reply_runner),
        delivery_attempt_recorder=record_attempt,
        # Part delivery is the fallback for a body the provider itself
        # cannot take; a part is never cut by the notification length.
        short_message_limit=None,
        **cast(PartSendInboxOptions, dict(options)),
    )
    if (
        sent.get("external_write_performed") is not True
        and sent.get("provider_write_attempted") is not True
    ):
        delivery_state.pop("delivery_part_started", None)
    elif sent.get("provider_write_attempted") is True:
        delivery_state["delivery_write_attempted"] = True
    return sent


def deliver_manager_reply_parts(
    *,
    parts: list[str],
    delivery_state: dict[str, object],
    delivery_path: Path,
    write_delivery: WriteDelivery,
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
    content_format: str,
    reply_options: PartReplyOptionsFactory | None = None,
) -> Mapping[str, object] | CompletedPartDeliveryReceipt | None:
    """Send the remaining parts in order, resuming from the recorded count.

    The durable state, not the return value, is the record of what the provider
    accepted: each accepted part advances ``delivery_parts_sent`` before the next
    send, and a rejected part stops the sequence there.
    """

    recorded_count = _strict_int(delivery_state.get("delivery_part_count"))
    sent = _strict_int(delivery_state.get("delivery_parts_sent"))
    if recorded_count != len(parts) or sent is None or not 0 <= sent <= len(parts):
        # A different split than the one on record cannot be resumed safely.
        sent = 0
        # The stall count describes this sequence's retries, so it goes with the
        # split that produced them instead of counting toward a different one.
        delivery_state.pop(PART_STALL_COUNT_KEY, None)
    elif sent == len(parts):
        # Every part is already on the channel. Settle from the recorded
        # acceptance instead of reporting an incomplete sequence that no retry
        # could ever finish (re-sending would duplicate the whole answer).
        return completed_part_delivery_receipt(delivery_state)
    delivery_state.update(
        delivery_part_count=len(parts),
        delivery_parts_sent=sent,
        format_degraded=content_format != "markdown",
        content_format=content_format,
        updated_at=utc_now_iso(),
    )
    # A sequence that is still being sent is not a complete one, even when a
    # previous attempt recorded a verified completion for a different split.
    delivery_state[PART_DELIVERY_COMPLETE_KEY] = False
    write_delivery(delivery_path, delivery_state)
    last: Mapping[str, object] | None = None
    for index in range(sent, len(parts)):
        options = dict(reply_options(index) if reply_options is not None else {})
        last = _deliver_single_part(
            parts=parts,
            index=index,
            delivery_state=delivery_state,
            delivery_path=delivery_path,
            write_delivery=write_delivery,
            reply_runner=reply_runner,
            root=root,
            config_path=config_path,
            message_id=message_id,
            content_format=content_format,
            options=options,
        )
        if last is None:
            return None
        if not _part_accepted(last):
            delivery_state.update(
                delivery_parts_sent=index,
                last_delivery_status=str(last.get("status") or "reply_failed"),
                updated_at=utc_now_iso(),
            )
            write_delivery(delivery_path, delivery_state)
            return None
        delivery_state.pop(PART_ATTEMPT_KEY, None)
        delivery_state.pop("delivery_part_started", None)
        delivery_state.update(
            delivery_parts_sent=index + 1,
            **(
                {PART_DELIVERY_COMPLETE_KEY: True, **_accepted_reply_facts(last)}
                if index + 1 == len(parts)
                else _accepted_reply_facts(last)
            ),
            updated_at=utc_now_iso(),
        )
        write_delivery(delivery_path, delivery_state)
    return last


def deliver_manager_reply_after_length_failure(
    *,
    reply_text: str,
    delivery_state: dict[str, object],
    delivery_path: Path,
    write_delivery: WriteDelivery,
    reply_runner: object,
    root: Path,
    config_path: Path,
    message_id: str,
) -> tuple[Mapping[str, object] | None, str | None]:
    """Deliver the complete answer as ordered rich posts, with recovery."""

    parts, _ = plan_manager_reply_parts(reply_text)
    reply = deliver_manager_reply_parts(
        parts=parts,
        delivery_state=delivery_state,
        delivery_path=delivery_path,
        write_delivery=write_delivery,
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
        content_format="markdown",
    )
    if reply is not None:
        return reply, None
    # The answer is only partly on the channel. Tell the reader once, after the
    # sequence has already failed more than one attempt, instead of leaving the
    # delivered parts looking like the whole answer.
    delivery_state[PART_STALL_COUNT_KEY] = (
        coerce_int_default_zero(delivery_state.get(PART_STALL_COUNT_KEY)) + 1
    )
    spoken = deliver_stall_notice(
        delivery_state=delivery_state,
        delivery_path=delivery_path,
        write_delivery=write_delivery,
        reply_runner=reply_runner,
        root=root,
        config_path=config_path,
        message_id=message_id,
    )
    if spoken is not None:
        if _reply_on_channel(spoken):
            delivery_state[PART_STALL_NOTICE_KEY] = True
        delivery_state["last_delivery_notice_status"] = str(
            spoken.get("status") or "reply_failed"
        )
    # The stall count has to survive this attempt either way. A retry reloads the
    # record from disk, so an unwritten increment would restart at zero and the
    # notice would never be reached no matter how often the sequence stalled.
    delivery_state["updated_at"] = utc_now_iso()
    write_delivery(delivery_path, delivery_state)
    return None, part_delivery_incomplete_reason(delivery_state)
