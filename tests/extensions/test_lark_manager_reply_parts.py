"""The part sequence must resume, settle, and never double-send an answer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loopx.extensions.lark.manager_reply_parts as parts_module
from loopx.extensions.lark.manager_reply_parts import (
    MANAGER_REPLY_STALL_NOTICE,
    PART_ATTEMPT_KEY,
    PART_DELIVERY_COMPLETE_KEY,
    PART_DELIVERY_COMPLETION_UNVERIFIED,
    PART_DELIVERY_INCOMPLETE,
    PART_DELIVERY_VERIFIED_KEY,
    PART_STALL_NOTICE_ATTEMPT_KEY,
    PART_STALL_COUNT_KEY,
    PART_STALL_NOTICE_KEY,
    completed_part_delivery_receipt,
    deliver_manager_reply_after_length_failure,
    deliver_manager_reply_parts,
    part_delivery_incomplete_reason,
    plan_manager_reply_parts,
    plan_stalled_part_notice,
    recorded_part_attempt,
    recorded_stall_notice_attempt,
)


BODY = "\n".join(f"- 条目 {index} " + "x" * 60 for index in range(1, 2000))


@pytest.fixture
def delivery(tmp_path: Path):
    """A fake provider boundary plus the durable state a retry would reload."""

    state: dict = {}
    writes: list[dict] = []
    sends: list[str] = []

    def install(*, fail_at: int | None = None, verified: bool = True):
        def fake_reply(**kwargs):
            sends.append(kwargs["text"])
            if fail_at is not None and len(sends) == fail_at:
                return {"ok": False, "status": "reply_failed", "idempotency_key": None}
            return {
                "ok": True,
                "status": "sent_verified",
                "idempotency_key": f"sha256:part-{len(sends)}",
                "content_format": kwargs.get("content_format", "text"),
                "external_write_performed": True,
                "verification_performed": True,
                "reply_verified": verified,
            }

        return fake_reply

    def deliver(parts: list[str]):
        return deliver_manager_reply_parts(
            parts=parts,
            delivery_state=state,
            delivery_path=tmp_path / "delivery.json",
            write_delivery=lambda path, payload: writes.append(
                json.loads(json.dumps(payload))
            ),
            reply_runner=object(),
            root=tmp_path,
            config_path=tmp_path / "config.json",
            message_id="om_fixture",
            content_format="text",
        )

    return {
        "state": state,
        "writes": writes,
        "sends": sends,
        "install": install,
        "deliver": deliver,
        "tmp": tmp_path,
    }


def test_a_partially_accepted_sequence_resumes_at_the_first_unsent_part(
    monkeypatch, delivery
):
    parts, _ = plan_manager_reply_parts(BODY)
    assert len(parts) > 3

    monkeypatch.setattr(
        parts_module, "reply_lark_event_inbox", delivery["install"](fail_at=3)
    )
    assert delivery["deliver"](parts) is None
    assert delivery["state"]["delivery_parts_sent"] == 2

    accepted = list(delivery["sends"])
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())
    assert delivery["deliver"](parts)["ok"] is True

    # The retry starts at the first part the provider never accepted and never
    # repeats a part the reader already has.
    assert delivery["sends"][len(accepted) :][0] == parts[2]
    assert delivery["state"]["delivery_parts_sent"] == len(parts)
    assert delivery["state"][PART_DELIVERY_COMPLETE_KEY] is True
    assert delivery["state"][PART_DELIVERY_VERIFIED_KEY] is True
    assert delivery["state"]["reply_idempotency_key"].startswith("sha256:")


def test_an_already_delivered_sequence_settles_from_the_record(monkeypatch, delivery):
    parts, _ = plan_manager_reply_parts(BODY)
    delivery["state"].update(
        delivery_part_count=len(parts),
        delivery_parts_sent=len(parts),
        reply_idempotency_key="sha256:last-part",
        **{
            PART_DELIVERY_COMPLETE_KEY: True,
            PART_DELIVERY_VERIFIED_KEY: True,
        },
    )
    # The provider must not be asked to send anything again.
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    receipt = delivery["deliver"](parts)

    assert delivery["sends"] == []
    assert receipt == {
        "ok": True,
        "status": "sent_verified",
        "idempotency_key": "sha256:last-part",
        "content_format": "text",
        "external_write_performed": True,
        "verification_performed": True,
        "reply_verified": True,
        "part_delivery_reused": True,
    }


def test_a_complete_but_unverified_record_reports_its_own_reason(monkeypatch, delivery):
    parts, _ = plan_manager_reply_parts(BODY)
    delivery["state"].update(
        delivery_part_count=len(parts),
        delivery_parts_sent=len(parts),
        reply_idempotency_key="sha256:last-part",
        **{PART_DELIVERY_COMPLETE_KEY: True},
    )
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    assert delivery["deliver"](parts) is None
    # Nothing is re-sent, and the reason says why the record cannot settle.
    assert delivery["sends"] == []
    assert (
        part_delivery_incomplete_reason(delivery["state"])
        == PART_DELIVERY_COMPLETION_UNVERIFIED
    )


def test_a_changed_split_restarts_instead_of_resuming_mid_answer(monkeypatch, delivery):
    parts, _ = plan_manager_reply_parts(BODY)
    delivery["state"].update(
        delivery_part_count=len(parts) + 1,
        delivery_parts_sent=len(parts) + 1,
        reply_idempotency_key="sha256:another-split",
        **{PART_STALL_COUNT_KEY: 3},
        **{
            PART_DELIVERY_COMPLETE_KEY: True,
            PART_DELIVERY_VERIFIED_KEY: True,
        },
    )
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    assert delivery["deliver"](parts)["ok"] is True

    assert delivery["sends"][0] == parts[0]
    assert delivery["state"]["delivery_part_count"] == len(parts)
    assert delivery["state"]["delivery_parts_sent"] == len(parts)
    # Stalls counted for the abandoned split do not speak for this one.
    assert PART_STALL_COUNT_KEY not in delivery["state"]


def test_an_unkeyed_or_unfinished_record_never_claims_verification():
    assert (
        completed_part_delivery_receipt(
            {
                PART_DELIVERY_COMPLETE_KEY: True,
                PART_DELIVERY_VERIFIED_KEY: True,
                "reply_idempotency_key": "reply-fixture",
            }
        )
        is None
    )
    assert (
        completed_part_delivery_receipt(
            {PART_DELIVERY_COMPLETE_KEY: True, "reply_idempotency_key": "sha256:x"}
        )
        is None
    )
    assert part_delivery_incomplete_reason({}) == PART_DELIVERY_INCOMPLETE


def test_a_verified_part_with_pending_cleanup_is_never_sent_twice(monkeypatch, delivery):
    """``ok`` also requires the reaction cleanup, which must not re-send a part.

    A part the provider already read back is on the channel; stopping the
    sequence on a pending cleanup makes the retry post the same text again.
    """

    parts, _ = plan_manager_reply_parts(BODY)

    def cleanup_pending_then_ok(**kwargs):
        delivery["sends"].append(kwargs["text"])
        index = len(delivery["sends"])
        facts = {
            "external_write_performed": True,
            "verification_performed": True,
            "reply_verified": True,
        }
        if index == 1:
            return {
                "ok": False,
                "status": "sent_verified_cleanup_pending",
                "idempotency_key": "sha256:part-1",
                **facts,
            }
        return {
            "ok": True,
            "status": "sent_verified",
            "idempotency_key": f"sha256:part-{index}",
            **facts,
        }

    monkeypatch.setattr(
        parts_module, "reply_lark_event_inbox", cleanup_pending_then_ok
    )

    receipt = delivery["deliver"](parts)

    assert receipt["ok"] is True
    assert delivery["sends"].count(parts[0]) == 1
    assert delivery["sends"][1] == parts[1]
    assert delivery["state"]["delivery_parts_sent"] == len(parts)


ATTEMPT = {
    "schema_version": "manager_return_delivery_attempt_v0",
    "provider": "lark",
    "message_ref": "om_reply_fixture",
    "intent_digest": "sha256:" + "a" * 64,
    "provider_receipt": "sha256:" + "b" * 64,
}

# The provider accepted the write and reported no message id, so the attempt
# carries its intent instead of a locator nothing could read back.
LOCATOR_LESS_ATTEMPT = {**ATTEMPT, "message_ref": None}


def test_a_recorded_send_without_a_locator_is_never_sent_again(
    monkeypatch, delivery,
):
    """A write that reported no message id must not be repeated blindly."""

    parts, _ = plan_manager_reply_parts(BODY)
    delivery["state"].update(
        delivery_part_count=len(parts),
        delivery_parts_sent=0,
        **{PART_ATTEMPT_KEY: {"index": 0, "attempt": LOCATOR_LESS_ATTEMPT}},
    )
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    assert delivery["deliver"](parts) is None

    # The part stays where it stopped: no second write, and the record is kept
    # so the reader is told the answer is incomplete rather than duplicated.
    assert delivery["sends"] == []
    assert delivery["state"]["delivery_parts_sent"] == 0
    assert delivery["state"][PART_ATTEMPT_KEY] == {
        "index": 0,
        "attempt": LOCATOR_LESS_ATTEMPT,
    }
    assert delivery["state"]["last_delivery_status"] == "sent_unverified"
    assert delivery["state"].get(PART_DELIVERY_COMPLETE_KEY) is not True


def test_a_send_without_a_readback_records_its_provider_locator(
    monkeypatch, delivery,
):
    """An unverified send must leave the locator a later attempt can check."""

    parts, _ = plan_manager_reply_parts(BODY)

    def unverified_send(**kwargs):
        delivery["sends"].append(kwargs["text"])
        recorder = kwargs.get("delivery_attempt_recorder")
        if recorder is not None:
            recorder(dict(ATTEMPT))
        return {
            "ok": False,
            "status": "sent_unverified",
            "idempotency_key": ATTEMPT["provider_receipt"],
            "external_write_performed": True,
            "verification_performed": False,
            "reply_verified": False,
        }

    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", unverified_send)

    assert delivery["deliver"](parts) is None

    assert delivery["state"]["delivery_parts_sent"] == 0
    assert delivery["state"][PART_ATTEMPT_KEY] == {"index": 0, "attempt": ATTEMPT}
    assert recorded_part_attempt(delivery["state"], 0) == ATTEMPT
    # A different part has no locator to reconcile.
    assert recorded_part_attempt(delivery["state"], 1) is None


def test_a_recorded_locator_is_confirmed_instead_of_sending_the_part_again(
    monkeypatch, delivery,
):
    """The reader must not receive the same part twice after an ambiguous send."""

    parts, _ = plan_manager_reply_parts(BODY)
    delivery["state"].update(
        delivery_part_count=len(parts),
        delivery_parts_sent=0,
        **{PART_ATTEMPT_KEY: {"index": 0, "attempt": ATTEMPT}},
    )
    verified_with: list[dict] = []

    def readback(**kwargs):
        verified_with.append(dict(kwargs))
        return {
            "ok": True,
            "verification_performed": True,
            "reply_verified": True,
            "part_reconciled": True,
        }

    monkeypatch.setattr(parts_module, "verify_lark_inbox_reply", readback)
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    assert delivery["deliver"](parts)["ok"] is True

    # No write happened for the reconciled part, and the sequence continued at
    # the part after it.
    assert verified_with[0]["attempt"] == ATTEMPT
    assert verified_with[0]["text"] == parts[0]
    assert delivery["sends"][0] == parts[1]
    assert delivery["sends"].count(parts[0]) == 0
    assert delivery["state"]["delivery_parts_sent"] == len(parts)
    assert PART_ATTEMPT_KEY not in delivery["state"]


def test_an_unconfirmed_locator_stays_pending_without_resending(monkeypatch, delivery):
    """Missing readback is not proof that a recorded write never happened."""

    parts, _ = plan_manager_reply_parts(BODY)
    delivery["state"].update(
        delivery_part_count=len(parts),
        delivery_parts_sent=0,
        **{PART_ATTEMPT_KEY: {"index": 0, "attempt": ATTEMPT}},
    )

    monkeypatch.setattr(
        parts_module,
        "verify_lark_inbox_reply",
        lambda **kwargs: {
            "ok": False,
            "verification_performed": True,
            "reply_verified": False,
            "blocker": "provider_message_missing",
        },
    )
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    assert delivery["deliver"](parts) is None

    assert delivery["sends"] == []
    assert delivery["state"][PART_ATTEMPT_KEY]["attempt"] == ATTEMPT


def test_a_confirmed_locator_settles_a_sequence_with_no_new_write(
    monkeypatch, delivery,
):
    parts, _ = plan_manager_reply_parts("短答复")
    assert len(parts) == 1
    delivery["state"].update(
        delivery_part_count=1,
        delivery_parts_sent=0,
        **{PART_ATTEMPT_KEY: {"index": 0, "attempt": ATTEMPT}},
    )
    monkeypatch.setattr(
        parts_module,
        "verify_lark_inbox_reply",
        lambda **kwargs: {
            "ok": True,
            "verification_performed": True,
            "reply_verified": True,
            "part_reconciled": True,
        },
    )
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", delivery["install"]())

    assert delivery["deliver"](parts)["ok"] is True

    assert delivery["sends"] == []
    assert delivery["state"][PART_DELIVERY_COMPLETE_KEY] is True
    assert delivery["state"][PART_DELIVERY_VERIFIED_KEY] is True


def _stalled_state(parts: list[str], *, stalls: int, sent: int) -> dict:
    return {
        "delivery_part_count": len(parts),
        "delivery_parts_sent": sent,
        PART_STALL_COUNT_KEY: stalls,
        "delivery_text": BODY,
        "content_format": "text",
    }


def test_a_stalled_sequence_tells_the_reader_once_after_real_retries(
    monkeypatch, delivery,
):
    """Silence is the worst outcome: say what was delivered and where the rest is."""

    parts, _ = plan_manager_reply_parts(BODY)
    state = _stalled_state(parts, stalls=3, sent=2)
    sent_texts: list[str] = []

    def failing_parts(**kwargs):
        sent_texts.append(kwargs["text"])
        if kwargs["text"].startswith("（"):
            return {"ok": False, "status": "reply_provider_failed",
                    "idempotency_key": None}
        return {"ok": True, "status": "sent_verified", "idempotency_key": "sha256:n",
                "verification_performed": True, "reply_verified": True}

    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", failing_parts)

    reply, reason = deliver_manager_reply_after_length_failure(
        reply_text=BODY,
        delivery_state=state,
        delivery_path=delivery["tmp"] / "delivery.json",
        write_delivery=lambda path, payload: None,
        reply_runner=object(),
        root=delivery["tmp"],
        config_path=delivery["tmp"] / "config.json",
        message_id="om_fixture",
    )

    assert reply is None
    assert reason == PART_DELIVERY_INCOMPLETE
    notices = [text for text in sent_texts if text.startswith("本条答复")]
    assert notices == [MANAGER_REPLY_STALL_NOTICE.format(sent=2, count=len(parts))]
    assert state[PART_STALL_NOTICE_KEY] is True

    # The reader is told once. A later attempt that still cannot finish the
    # sequence does not repeat the notice.
    sent_texts.clear()
    state[PART_STALL_COUNT_KEY] = 4
    deliver_manager_reply_after_length_failure(
        reply_text=BODY,
        delivery_state=state,
        delivery_path=delivery["tmp"] / "delivery.json",
        write_delivery=lambda path, payload: None,
        reply_runner=object(),
        root=delivery["tmp"],
        config_path=delivery["tmp"] / "config.json",
        message_id="om_fixture",
    )
    assert [text for text in sent_texts if text.startswith("本条答复")] == []


def test_a_sequence_that_has_not_retried_yet_stays_quiet(delivery):
    parts, _ = plan_manager_reply_parts(BODY)

    assert plan_stalled_part_notice(_stalled_state(parts, stalls=1, sent=2)) is None
    assert plan_stalled_part_notice(_stalled_state(parts, stalls=2, sent=2)) is None
    assert plan_stalled_part_notice(_stalled_state(parts, stalls=3, sent=2)) is not None


def test_a_sequence_that_delivered_nothing_stays_quiet(delivery):
    """Nothing was delivered, so the last-attempt answer is still the whole story."""

    parts, _ = plan_manager_reply_parts(BODY)

    assert plan_stalled_part_notice(_stalled_state(parts, stalls=9, sent=0)) is None
    assert plan_stalled_part_notice(_stalled_state(parts, stalls=9, sent=len(parts))) is None


def test_a_rejected_notice_is_offered_again_on_the_next_attempt(
    monkeypatch, delivery,
):
    parts, _ = plan_manager_reply_parts(BODY)
    state = _stalled_state(parts, stalls=3, sent=2)
    attempts: list[str] = []

    def rejecting_everything(**kwargs):
        attempts.append(kwargs["text"])
        return {"ok": False, "status": "reply_provider_failed", "idempotency_key": None}

    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", rejecting_everything)

    deliver_manager_reply_after_length_failure(
        reply_text=BODY,
        delivery_state=state,
        delivery_path=delivery["tmp"] / "delivery.json",
        write_delivery=lambda path, payload: None,
        reply_runner=object(),
        root=delivery["tmp"],
        config_path=delivery["tmp"] / "config.json",
        message_id="om_fixture",
    )

    assert state.get(PART_STALL_NOTICE_KEY) is not True
    assert state["last_delivery_notice_status"] == "reply_provider_failed"
    assert any(text.startswith("本条答复") for text in attempts)

    state[PART_STALL_COUNT_KEY] = 4
    attempts.clear()
    deliver_manager_reply_after_length_failure(
        reply_text=BODY,
        delivery_state=state,
        delivery_path=delivery["tmp"] / "delivery.json",
        write_delivery=lambda path, payload: None,
        reply_runner=object(),
        root=delivery["tmp"],
        config_path=delivery["tmp"] / "config.json",
        message_id="om_fixture",
    )
    assert any(text.startswith("本条答复") for text in attempts)


def test_a_notice_the_provider_took_but_did_not_read_back_is_confirmed(
    monkeypatch, delivery,
):
    """A notice the provider accepted but could not read back is not repeated.

    The transport records the provider locator of the notice before it reads the
    message back, so a send that comes back ``sent_unverified`` has to be
    confirmed on the next attempt instead of being posted to the reader twice.
    """

    parts, _ = plan_manager_reply_parts(BODY)
    state = _stalled_state(parts, stalls=2, sent=2)
    notice = MANAGER_REPLY_STALL_NOTICE.format(sent=2, count=len(parts))
    sends: list[str] = []
    verifications: list[str] = []

    def ambiguous_send(**kwargs):
        sends.append(kwargs["text"])
        if kwargs["text"].startswith("（"):
            return {
                "ok": False,
                "status": "reply_provider_failed",
                "idempotency_key": None,
            }
        kwargs["delivery_attempt_recorder"](
            {
                "schema_version": "manager_return_delivery_attempt_v0",
                "provider": "lark",
                "message_ref": "om_notice",
                "intent_digest": "sha256:notice-intent",
                "provider_receipt": "sha256:notice-receipt",
            }
        )
        return {
            "ok": False,
            "status": "sent_unverified",
            "idempotency_key": "sha256:notice-receipt",
            "write_performed": True,
            "verification_performed": True,
            "reply_verified": False,
        }

    def confirmed_notice(**kwargs):
        verifications.append(kwargs["text"])
        return {
            "ok": True,
            "status": "sent_verified",
            "idempotency_key": "sha256:notice-receipt",
            "verification_performed": True,
            "reply_verified": True,
        }

    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", ambiguous_send)
    monkeypatch.setattr(parts_module, "verify_lark_inbox_reply", confirmed_notice)

    def deliver_with_stalls():
        return deliver_manager_reply_after_length_failure(
            reply_text=BODY,
            delivery_state=state,
            delivery_path=delivery["tmp"] / "delivery.json",
            write_delivery=lambda path, payload: None,
            reply_runner=object(),
            root=delivery["tmp"],
            config_path=delivery["tmp"] / "config.json",
            message_id="om_fixture",
        )

    deliver_with_stalls()

    # The notice went out but its readback did not confirm it, so the reader may
    # already have it and the record keeps that attempt's locator.
    assert [text for text in sends if text.startswith("本条答复")] == [notice]
    assert state.get(PART_STALL_NOTICE_KEY) is not True
    assert recorded_stall_notice_attempt(state) == {
        "schema_version": "manager_return_delivery_attempt_v0",
        "provider": "lark",
        "message_ref": "om_notice",
        "intent_digest": "sha256:notice-intent",
        "provider_receipt": "sha256:notice-receipt",
    }

    deliver_with_stalls()

    assert verifications == [notice]
    assert [text for text in sends if text.startswith("本条答复")] == [notice]
    assert state[PART_STALL_NOTICE_KEY] is True
    assert PART_STALL_NOTICE_ATTEMPT_KEY not in state


def test_a_stall_notice_without_a_locator_survives_its_own_writeback(
    monkeypatch, delivery,
):
    """A notice the provider accepted without a locator is never sent twice.

    The notice attempt is the only durable evidence that the reader may already
    have the notice. Dropping it while the send is still being reconciled makes
    the next retry post the same text again, so the record has to survive the
    writeback that follows a reconciliation result.
    """

    parts, _ = plan_manager_reply_parts(BODY)
    state = _stalled_state(parts, stalls=2, sent=2)
    notice = MANAGER_REPLY_STALL_NOTICE.format(sent=2, count=len(parts))
    sends: list[str] = []
    verifications: list[str] = []
    written: list[dict] = []

    def locator_less_send(**kwargs):
        sends.append(kwargs["text"])
        if kwargs["text"].startswith("（"):
            return {
                "ok": False,
                "status": "reply_provider_failed",
                "idempotency_key": None,
            }
        kwargs["delivery_attempt_recorder"](
            dict(LOCATOR_LESS_ATTEMPT)
        )
        return {
            "ok": False,
            "status": "sent_unverified",
            "idempotency_key": "sha256:notice-receipt",
            "external_write_performed": True,
            "verification_performed": False,
            "reply_verified": False,
        }

    def readback(**kwargs):
        verifications.append(kwargs["text"])
        raise AssertionError("a notice without a locator has nothing to verify")

    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", locator_less_send)
    monkeypatch.setattr(parts_module, "verify_lark_inbox_reply", readback)

    def deliver_with_stalls(current: dict):
        return deliver_manager_reply_after_length_failure(
            reply_text=BODY,
            delivery_state=current,
            delivery_path=delivery["tmp"] / "delivery.json",
            write_delivery=lambda path, payload: written.append(
                json.loads(json.dumps(payload))
            ),
            reply_runner=object(),
            root=delivery["tmp"],
            config_path=delivery["tmp"] / "config.json",
            message_id="om_fixture",
        )

    deliver_with_stalls(state)
    first = written[-1]

    # The notice left the provider once, and the attempt that proves it is on
    # disk even though nothing can read the message back.
    assert [text for text in sends if text.startswith("本条答复")] == [notice]
    assert first[PART_STALL_NOTICE_ATTEMPT_KEY] == {"attempt": LOCATOR_LESS_ATTEMPT}
    assert first.get(PART_STALL_NOTICE_KEY) is not True
    assert first["last_delivery_notice_status"] == "sent_unverified"

    # A retry reloads the record that was just written: it reconciles instead of
    # posting the notice again, and the evidence of the first send stays.
    deliver_with_stalls(json.loads(json.dumps(first)))

    assert [text for text in sends if text.startswith("本条答复")] == [notice]
    assert verifications == []
    assert written[-1][PART_STALL_NOTICE_ATTEMPT_KEY] == {
        "attempt": LOCATOR_LESS_ATTEMPT
    }
    assert written[-1]["last_delivery_notice_status"] == "sent_unverified"


def test_stall_notice_unknown_write_is_not_repeated(monkeypatch, delivery):
    parts, _ = plan_manager_reply_parts(BODY)
    state = _stalled_state(parts, stalls=3, sent=2)
    writes = []
    def unknown(**kwargs):
        writes.append(kwargs["text"])
        return {"ok": False, "reply_verified": False, "provider_write_attempted": True}
    monkeypatch.setattr(parts_module, "reply_lark_event_inbox", unknown)
    for _ in range(2):
        result = parts_module.deliver_stall_notice(delivery_state=state,
            delivery_path=delivery["tmp"] / "delivery.json", write_delivery=lambda *_: None,
            reply_runner=object(), root=delivery["tmp"], config_path=delivery["tmp"] / "config.json",
            message_id="om_fixture")
        assert not result["reply_verified"]
    assert len(writes) == 1
